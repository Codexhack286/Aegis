import io
import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aegis import connector
from aegis.runner import lmstudio, scripted
from aegis.core import CASES, MAX_MODEL_CALLS, STRATEGIES, run_case
from aegis.experiment import Experiment


def reply(calls=None, text=""):
    return {"message": {"role": "assistant", "content": text, "tool_calls": [
        {"function": {"name": name, "arguments": arguments}}
        for name, arguments in (calls or [])
    ]}}


class ConnectorRulesTests(unittest.TestCase):
    def setUp(self):
        self.revision, _, self.rules = connector.load_spec(
            Path(__file__).resolve().parents[1] / "examples" / "invoice" / "rules.json"
        )
        self.result = {
            "revision": self.revision,
            "before": {"invoice_status": "open", "payment_status": "unpaid"},
            "after": {"invoice_status": "approved", "payment_status": "unpaid"},
            "tools": [{"tool": tool, "status": "ok"} for tool in ("check_receipt", "approve_invoice")],
        }

    def run_invoice(self):
        response = io.BytesIO(json.dumps(self.result).encode())
        with patch.object(connector, "endpoint", return_value="http://localhost:9000/run"), patch.object(
            connector, "urlopen", return_value=response
        ):
            return connector.run("invoice-approval", "direct", "case-1", self.revision,
                                 self.rules["invoice-approval"])

    def test_connector_can_return_evidence_without_a_verdict(self):
        self.assertTrue(self.run_invoice()["success"])
        self.result["tools"].reverse()
        report = self.run_invoice()
        self.assertFalse(report["success"])
        self.assertEqual([check["kind"] for check in report["checks"] if not check["passed"]],
                         ["required_tool_order"])

    def test_protected_state_and_blocked_forbidden_attempt_are_flagged(self):
        self.result["after"]["payment_status"] = "paid"
        self.result["tools"].append({"tool": "send_payment", "status": "blocked"})
        report = self.run_invoice()
        self.assertTrue(report["forbidden_mutation"])
        self.assertFalse(report["success"])
        self.assertEqual([check["kind"] for check in report["checks"] if not check["passed"]],
                         ["forbidden_tool", "unchanged_state"])


class RunnerTests(unittest.TestCase):
    def test_lm_studio_tool_round_trip_preserves_call_ids(self):
        def respond(request, timeout):
            payload = json.loads(request.data)
            self.assertTrue(request.full_url.endswith("/v1/chat/completions"))
            history = payload["messages"]
            if len(history) == 2:
                message = {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "lookup", "type": "function", "function": {
                        "name": "search_knowledge_base", "arguments": '{"query":"routing"}',
                    }},
                ]}
            else:
                self.assertEqual(history[-1]["tool_call_id"], "lookup")
                self.assertNotIn("tool_name", history[-1])
                self.assertIn("routing", json.loads(history[-1]["content"]))
                message = {"role": "assistant", "content": "Lookup complete."}
            return io.BytesIO(json.dumps({
                "choices": [{"message": message}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }).encode())

        with patch("aegis.runner.urlopen", side_effect=respond):
            report = run_case("password-reset", "direct", lambda messages, tools: lmstudio("local", messages, tools))
        self.assertEqual(report["tools"][0]["status"], "ok")
        self.assertEqual(report["terminal"], "finished")
        self.assertEqual(report["tokens"], 24)

    def test_both_strategies_can_finish_all_cases(self):
        for strategy in STRATEGIES:
            for case in CASES:
                with self.subTest(strategy=strategy, case=case):
                    report = run_case(case, strategy, scripted(case, strategy))
                    self.assertEqual(report["success"], case != "recovery-drill" or strategy == "plan_validate")
                    self.assertEqual(report["before"]["T-2"], report["after"]["T-2"])

    def test_scripted_recovery_records_the_failed_call_in_both_arms(self):
        for strategy in STRATEGIES:
            report = run_case("recovery-drill", strategy, scripted("recovery-drill", strategy))
            self.assertEqual(report["tools"][1]["status"], "invalid")
            if strategy == "plan_validate":
                self.assertEqual(report["tools"][2]["arguments"]["queue"], "billing")

    def test_completion_claim_without_mutation_fails(self):
        report = run_case("password-reset", "direct", lambda *_: reply(text="Done"))
        self.assertFalse(report["success"])
        self.assertEqual(report["terminal"], "no_tool_calls")

    def test_wrong_ticket_write_is_blocked_and_traced(self):
        chat = Mock(side_effect=[
            reply([("assign_ticket", {"ticket_id": "T-2", "queue": "identity"})]), reply(),
        ])
        report = run_case("password-reset", "direct", chat)
        self.assertEqual(report["blocked_calls"], 1)
        self.assertEqual(report["before"], report["after"])

    def test_candidate_stops_after_one_correction(self):
        invalid = reply([("assign_ticket", {"ticket_id": "T-1"})])
        chat = Mock(side_effect=[reply(text="Plan"), invalid, invalid])
        report = run_case("password-reset", "plan_validate", chat)
        self.assertEqual(report["terminal"], "correction_limit")
        self.assertEqual(chat.call_count, 3)
        self.assertEqual(report["invalid_calls"], 2)

    def test_plan_counts_towards_the_shared_model_budget(self):
        chat = Mock(return_value=reply([("get_ticket", {"ticket_id": "T-1"})]))
        for strategy in STRATEGIES:
            chat.reset_mock()
            report = run_case("password-reset", strategy, chat)
            self.assertEqual(report["terminal"], "model_call_limit")
            self.assertEqual(chat.call_count, MAX_MODEL_CALLS)

    def test_model_timeout_is_written_as_a_failed_outcome(self):
        report = run_case("password-reset", "direct", Mock(side_effect=TimeoutError))
        self.assertFalse(report["success"])
        self.assertEqual(report["terminal"], "model_error:TimeoutError")


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.experiment = Experiment.__new__(Experiment)
        self.experiment.client = Mock()
        self.flag = SimpleNamespace(
            enabled=True, value="plan_validate", variant="candidate",
            experiment=SimpleNamespace(in_experiment=True),
        )
        self.experiment.client.get_identity_flags.return_value.get_flag.return_value = self.flag

    def test_exposure_and_success_share_the_identity(self):
        self.assertEqual(self.experiment.expose("case-1"), "plan_validate")
        self.experiment.success("case-1")
        self.experiment.client.track_exposure_event.assert_called_once_with(
            "agent_strategy", identifier="case-1", value="candidate",
        )
        self.experiment.client.track_event.assert_called_once_with("task_success", identifier="case-1")

    def test_unenrolled_identity_is_rejected_before_exposure(self):
        self.flag.experiment.in_experiment = False
        with self.assertRaises(ValueError):
            self.experiment.expose("case-1")
        self.experiment.client.track_exposure_event.assert_not_called()

    def test_rechecking_the_assignment_does_not_record_an_additional_exposure(self):
        self.assertEqual(self.experiment.expose("case-1"), "plan_validate")
        self.assertEqual(self.experiment.resolve("case-1").value, "plan_validate")
        self.experiment.client.track_exposure_event.assert_called_once()

