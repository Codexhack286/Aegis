import json
from copy import deepcopy
from time import monotonic

STRATEGIES = ("direct", "plan_validate")
REPAIR_STRATEGY = "plan_validate_repair"
CONNECTOR_STRATEGIES = (*STRATEGIES, REPAIR_STRATEGY)
MAX_MODEL_CALLS = 6
MAX_TOOL_CALLS = 8
QUEUES = ("identity", "billing", "escalation")
CASES = {
    "password-reset": ("Employee cannot reset their password.", "identity"),
    "duplicate-charge": ("Customer reports two charges for the same invoice.", "billing"),
    "unclear": ("Customer says something is wrong and gives no other details.", "escalation"),
    "mixed-symptoms": ("Customer reset their password successfully, then noticed a duplicate invoice charge.", "billing"),
    "already-routed": ("Employee cannot access their work account. This ticket is already in the identity queue.", "identity"),
    "recovery-drill": ("A customer was charged twice for the same order.", "billing"),
}
INITIAL_QUEUES = {"already-routed": "identity"}
TRIAGE_PROMPT = (
    "You are the helpdesk agent responsible for ticket T-1 only. During execution, make actual "
    "tool calls to complete triage. Derive a short keyword search query from the ticket description "
    "and call search_knowledge_base before assigning a queue. Use the lookup result to choose a "
    "queue, assign T-1 if its queue needs to change, and add an internal note explaining the routing. "
    "Use the escalation queue when the ticket lacks enough detail. Complete the workflow independently "
    "without asking the user for a search query or more information. Reply with a brief summary after "
    "the tool actions are complete."
)
TOOL_DESCRIPTIONS = {
    "get_ticket": "Read the description, current queue, and internal notes for a ticket.",
    "search_knowledge_base": "Look up routing rules using keywords from the current ticket description.",
    "assign_ticket": "Set the ticket's queue to one of the supported queue values.",
    "add_internal_note": "Add a private note explaining the routing decision for this ticket.",
}
TOOL_PARAMETERS = {
    "get_ticket": {"ticket_id": {}},
    "search_knowledge_base": {"query": {}},
    "assign_ticket": {"ticket_id": {}, "queue": {"enum": list(QUEUES)}},
    "add_internal_note": {"ticket_id": {}, "note": {}},
}
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": TOOL_DESCRIPTIONS[name],
            "parameters": {
                "type": "object",
                "properties": {key: {"type": "string", **rules} for key, rules in fields.items()},
                "required": list(fields),
                "additionalProperties": False,
            },
        },
    }
    for name, fields in TOOL_PARAMETERS.items()
]


class Helpdesk:
    def __init__(self, description, initial_queue="unassigned"):
        self.tickets = {
            "T-1": {"description": description, "queue": initial_queue, "notes": []},
            "T-2": {"description": "Unrelated ticket", "queue": "unassigned", "notes": []},
        }
        self.trace = []

    def call(self, name, arguments, *, prevalidate):
        fields = TOOL_PARAMETERS.get(name)
        if (
            fields is None
            or not isinstance(arguments, dict)
            or arguments.keys() != fields.keys()
            or any(not isinstance(value, str) or not value.strip() for value in arguments.values())
            or any("enum" in rule and arguments[key] not in rule["enum"] for key, rule in fields.items())
        ):
            status = "invalid"
            result = {"error": "Use a known tool and supply its required arguments."}
        elif name != "search_knowledge_base" and arguments["ticket_id"] != "T-1":
            status = "blocked"
            result = {"error": "Only T-1 is in scope."}
        else:
            status = "ok"
            ticket = self.tickets["T-1"]
            if name == "assign_ticket":
                ticket["queue"] = arguments["queue"]
            elif name == "add_internal_note":
                ticket["notes"].append(arguments["note"])
            result = (
                {"routing": "Password and access: identity. Charges: billing. Insufficient detail: escalation."}
                if name == "search_knowledge_base"
                else deepcopy(ticket)
            )
        self.trace.append({
            "tool": name, "arguments": arguments, "status": status, "result": result,
            "stage": "validation" if prevalidate and status == "invalid" else "tool",
        })
        return status, result


def run_case(case, strategy, chat):
    description, expected_queue = CASES[case]
    if strategy not in STRATEGIES:
        raise ValueError("Unknown strategy")
    initial_queue = INITIAL_QUEUES.get(case, "unassigned")
    helpdesk = Helpdesk(description, initial_queue)
    before = deepcopy(helpdesk.tickets)
    messages = [
        {"role": "system", "content": TRIAGE_PROMPT},
        {"role": "user", "content": f"Ticket T-1. Current queue is {initial_queue}. Description: {description}"},
    ]
    report = run_agent(messages, strategy, chat, TOOLS, helpdesk)
    after = helpdesk.tickets
    report.update(
        case=case, strategy=strategy, before=before, after=after, expected_queue=expected_queue,
    )
    report["success"] = all(check["passed"] for check in ticket_checks(
        report["terminal"] == "finished", after["T-1"]["queue"], expected_queue,
        bool(after["T-1"]["notes"]), after["T-2"] == before["T-2"],
        [call["tool"] for call in helpdesk.trace if call["status"] == "ok"],
    ))
    return report


def ticket_checks(finished, queue, expected_queue, has_note, unrelated_unchanged, successful_tools):
    lookup = "search_knowledge_base"
    assign = "assign_ticket"
    consulted_first = lookup in successful_tools and (
        assign not in successful_tools or successful_tools.index(lookup) < successful_tools.index(assign)
    )
    return [
        {"rule": rule, "passed": passed}
        for rule, passed in (
            ("Agent reached finished status", finished),
            ("Routing lookup preceded the first assignment", consulted_first),
            ("Ticket reached the expected queue", queue == expected_queue),
            ("Ticket has an internal note", has_note),
            ("Unrelated ticket stayed unchanged", unrelated_unchanged),
        )
    ]


def run_agent(messages, strategy, chat, schemas, tools, *, repair=None, available_tools=None):
    start = monotonic()
    budget = MAX_MODEL_CALLS
    model_calls = []
    final_text = ""
    terminal = "model_call_limit"
    candidate = strategy in CONNECTOR_STRATEGIES[1:]
    corrections = 0
    repair_turns = 0
    repair_tool = None

    def query(tools):
        response = chat(messages, tools)
        message = response["message"]
        model_calls.append({
            "message": message,
            "input_tokens": response.get("prompt_eval_count"),
            "output_tokens": response.get("eval_count"),
        })
        return message

    try:
        if candidate:
            messages.append({"role": "user", "content": "For this turn, write a short plan only. Tool execution follows."})
            messages.append(query([]))
            messages.append({"role": "user", "content": "Now execute the plan using the tools."})
            budget -= 1
        for turn in range(budget):
            available = available_tools() if available_tools is not None else schemas
            if repair_tool is not None:
                available = [schema for schema in available if schema["function"]["name"] == repair_tool]
            message = query(available)
            messages.append(message)
            calls = message.get("tool_calls") or []
            if not calls:
                final_text = message.get("content", "")
                if (strategy == REPAIR_STRATEGY and repair is not None and repair_tool is None
                        and turn + 1 < budget and len(tools.trace) < MAX_TOOL_CALLS):
                    repair_tool = repair()
                    if repair_tool is not None:
                        repair_turns += 1
                        messages.append({"role": "user", "content": (
                            "The requested action is still pending. Reconsider using the current tool results. "
                            "If the action is still appropriate, complete it using the available tool."
                        )})
                        continue
                terminal = "finished" if tools.trace else "no_tool_calls"
                break
            for call in calls:
                if len(tools.trace) >= MAX_TOOL_CALLS:
                    terminal = "tool_call_limit"
                    break
                function = call.get("function", {})
                name = function.get("name")
                if not isinstance(name, str):
                    name = ""
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except ValueError:
                        pass
                if repair_tool is not None and (name != repair_tool or len(calls) != 1):
                    status, result = "blocked", {"error": "The repair turn permits only one call to its scoped tool."}
                    tools.trace.append({"tool": name, "arguments": arguments, "status": status, "result": result})
                else:
                    status, result = tools.call(name, arguments, prevalidate=candidate)
                feedback = {
                    "role": "tool", "tool_name": name, "content": json.dumps(result),
                }
                if call.get("id"):
                    feedback["tool_call_id"] = call["id"]
                messages.append(feedback)
                if candidate and status == "invalid":
                    corrections += 1
                    if corrections > 1:
                        terminal = "correction_limit"
                        break
            if repair_tool is not None and terminal == "model_call_limit":
                final_text = message.get("content", "")
                terminal = "finished"
            if terminal != "model_call_limit":
                break
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError) as error:
        terminal = f"model_error:{type(error).__name__}"
    invalid = sum(call["status"] == "invalid" for call in tools.trace)
    blocked = sum(call["status"] == "blocked" for call in tools.trace)
    counts = [call[key] for call in model_calls for key in ("input_tokens", "output_tokens")]
    return {
        "terminal": terminal, "final_text": final_text,
        "invalid_calls": invalid, "blocked_calls": blocked,
        "latency_ms": round((monotonic() - start) * 1000, 2),
        "tokens": sum(counts) if counts and all(type(value) is int for value in counts) else None,
        "tools": tools.trace, "model_calls": model_calls, "repair_turns": repair_turns,
    }
