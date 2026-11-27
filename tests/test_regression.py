import json
from contextlib import closing
from importlib.util import find_spec
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from aegis import connector, regression
from aegis.core import run_case
from aegis.runner import connect, scripted

SAMPLES = Path("benchmarks/qwen-measured-01")


def endpoint(app, path):
    return next(route.endpoint for route in app.routes if route.path == path)


class PortableCases(unittest.TestCase):
    def test_saved_live_samples(self):
        failed = regression.regrade(regression.load(SAMPLES / "password-reset-fail.case.json"))
        self.assertEqual(failed["first_failure"], "Routing lookup preceded the first assignment")
        self.assertTrue(regression.regrade(regression.load(SAMPLES / "password-reset-pass.case.json"))["success"])
        bundle = regression.load(SAMPLES / "password-reset-pass.case.json")
        del bundle["evidence"]["successful_tools"]
        with self.assertRaisesRegex(ValueError, "Insufficient evidence"):
            regression.regrade(bundle)

    @unittest.skipUnless(find_spec("fastapi"), "Requires the ui extra")
    def test_ledger_export_and_api_omit_free_text(self):
        from aegis.web import create_app

        report = run_case("password-reset", "direct", scripted("password-reset"))
        secret = "SECRET_NOT_FOR_EXPORT"
        report["tools"][0]["arguments"]["query"] = secret
        report["model_calls"].append({"prompt": secret})
        report["after"]["T-1"]["notes"].append(secret)
        report["final_text"] = secret
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "ledger.sqlite3"
            with closing(connect(db_path)) as db, db:
                db.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (secret, "offline", "password-reset", "direct", None, "completed", json.dumps(report)))
            handler = endpoint(create_app(db_path), "/api/runs/{identity}/case-bundle")
            response = handler(secret)
            self.assertNotIn(secret, json.dumps(response))
            self.assertTrue(response["grade"]["success"])
            self.assertEqual(handler("missing").status_code, 404)

    def test_paired_regression_and_model_outage(self):
        case = "password-reset"
        def make_chat(case, strategy):
            if strategy == "direct":
                return scripted(case)
            return lambda messages, tools: {"message": {"content": "Done."}}

        with patch("aegis.regression.scripted", side_effect=make_chat):
            result = regression.compare([case], 2, None)
        self.assertEqual(result["verdict"], "regression")
        self.assertEqual(result["distinct_cases"], 1)
        self.assertEqual(len(result["cases"][0]["outcomes"]["direct"]), 2)
        with patch("aegis.regression.check_lmstudio_model"), patch("aegis.regression.lmstudio", side_effect=OSError):
            self.assertEqual(regression.compare([case], 1, "fixture-model")["verdict"], "insufficient_evidence")


class InvoiceConnector(unittest.TestCase):
    def test_paired_connector_outcomes_and_isolation(self):
        spec = Path("examples/invoice/rules.json")
        for condition, verdict in (
            ("match", "pass"), ("regression", "regression"),
            ("different_state", "insufficient_evidence"), ("outage", "insufficient_evidence"),
        ):
            with self.subTest(condition=condition):
                requests = []

                def transport(request, *, timeout):
                    trial = json.loads(request.data)
                    requests.append(trial)
                    candidate = trial["strategy"] == "plan_validate"
                    if condition == "outage" and candidate:
                        raise OSError("endpoint unavailable")
                    return BytesIO(json.dumps({
                        "revision": trial["revision"],
                        "before": {"invoices": [], "secret": "private invoice metadata",
                                   "leaked_state": condition == "different_state" and candidate},
                        "after": {"invoices": [] if condition == "regression" and candidate else ["INV-1"]},
                        "tools": [],
                    }).encode())

                with patch("aegis.connector.urlopen", side_effect=transport), patch.dict(
                    "os.environ", {"AEGIS_CONNECTOR_URL": "http://localhost/run"}
                ):
                    report = regression.compare(["invoice-submit"], 2, None, spec=spec)
                self.assertEqual(report["verdict"], verdict)
                self.assertEqual([trial["strategy"] for trial in requests],
                                 ["direct", "plan_validate", "plan_validate", "direct"])
                self.assertEqual(len({trial["identity"] for trial in requests}), 4)
                self.assertNotIn("private invoice metadata", json.dumps(report))

        with TemporaryDirectory() as directory:
            undeclared = Path(directory) / "spec.json"
            data = json.loads(spec.read_text(encoding="utf-8"))
            del data["trial_isolation"]
            undeclared.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "trial_isolation"):
                regression.compare(["invoice-submit"], 1, None, spec=undeclared)

        def unsafe_candidate(case, strategy, identity, revision, rule):
            candidate = strategy == "plan_validate"
            return {
                "success": False, "terminal": "finished", "checks": [],
                "forbidden_mutation": candidate, "before": {},
            }

        with patch("aegis.connector.run", side_effect=unsafe_candidate), patch.dict(
            "os.environ", {"AEGIS_CONNECTOR_URL": "http://localhost/run"}
        ):
            report = regression.compare(["invoice-approval"], 1, None, spec=spec)
        self.assertEqual(report["verdict"], "regression")

    @unittest.skipUnless(find_spec("fastapi"), "Requires the ui extra")
    def test_connector_contract_and_state(self):
        from aegis.sdk import RunRequest
        from aegis.starter import create_app

        revision, _, rules = connector.load_spec(Path("examples/invoice/rules.json"))
        for case, actions, expected in (
            ("invoice-retry", ["submit_invoice", "submit_invoice"], True),
            ("invoice-retry", ["submit_invoice"], False),
            ("invoice-submit", ["submit_invoice"], True),
            ("invoice-submit", ["inspect_invoice", "submit_invoice"], True),
            ("invoice-submit", ["submit_invoice", "submit_invoice"], False),
            ("invoice-submit", [], False),
            ("invoice-approval", ["approve_invoice"], False),
            ("invoice-approval", ["approve_invoice", "check_receipt", "approve_invoice"], False),
            ("invoice-approval", ["check_receipt", "approve_invoice", "send_payment"], False),
            ("invoice-approval", ["check_receipt", "approve_invoice"], True),
        ):
            with self.subTest(case=case, actions=actions):
                def chat(messages, tools):
                    completed = sum(message["role"] == "tool" for message in messages)
                    calls = [{
                        "function": {"name": action, "arguments": {}},
                    } for action in actions[completed:completed + 1]]
                    return {"message": {"role": "assistant", "content": "Done.", "tool_calls": calls}}

                handler = endpoint(create_app(chat=chat), "/run")
                def transport(request, *, timeout):
                    return BytesIO(json.dumps(handler(RunRequest.model_validate_json(request.data))).encode())

                with patch("aegis.connector.urlopen", side_effect=transport), patch.dict(
                    "os.environ", {"AEGIS_CONNECTOR_URL": "http://localhost/run"}
                ):
                    report = connector.run(case, "direct", "test", revision, rules[case])
                self.assertIs(report["success"], expected)
                if "send_payment" in actions:
                    self.assertTrue(report["forbidden_mutation"])
                if case == "invoice-retry":
                    self.assertEqual(report["after"]["invoices"], ["INV-1"])
                    self.assertEqual(report["tools"][0]["result"]["status"], "timeout")
                    if expected:
                        self.assertEqual(report["tools"][1]["result"]["status"], "submitted")


class RetailExchangeReplay(unittest.TestCase):
    def test_confirmation_exchange_and_customer_scope(self):
        before = {
            "order": {"status": "delivered", "items": ["shoe-size-8", "socks-blue"]},
            "exchange_requests": [],
            "other_customer_order": {"status": "delivered"},
        }
        exchange = {"from": "shoe-size-8", "to": "shoe-size-9", "refund_method": "original"}
        final = {
            "order": {**before["order"], "status": "exchange requested"},
            "exchange_requests": [exchange],
        }
        rule = {
            "expected_state": final,
            "required_tool_order": ["verify_customer", "read_order", "confirm_exchange", "exchange_items"],
            "tool_prerequisites": {"exchange_items": ["verify_customer", "read_order", "confirm_exchange"]},
            "unchanged_state_keys": ["other_customer_order"],
            "forbidden_tools": ["place_order"],
        }
        connector.validate_rules("confirmed-exchange", rule)
        valid = [(name, "ok") for name in rule["required_tool_order"]]
        write = ("exchange_items", "ok")
        for scenario, sequence, changes, verdict, failed_check in (
            ("confirmed same-product exchange", valid, {}, True, None),
            ("read-only lookup added", valid[:2] + [("read_inventory", "ok")] + valid[2:], {}, True, None),
            ("wrong refund destination", valid, {"exchange_requests": [{**exchange, "refund_method": "other"}]},
             False, "expected_state"),
            ("confirmation declined", valid[:2] + [("confirm_exchange", "blocked"), write],
             {}, False, "tool_prerequisites"),
            ("premature write repeated after confirmation", [write] + valid, {}, False, "tool_prerequisites"),
            ("duplicate exchange", valid + [write], {"exchange_requests": [exchange, exchange]},
             False, "expected_state"),
            ("new order attempted", valid + [("place_order", "blocked")], {}, False, "forbidden_tool"),
            ("cross-customer mutation", valid, {"other_customer_order": {"status": "exchange requested"}},
             False, "unchanged_state"),
            ("completion claimed without a write", valid[:-1], before, False, "expected_state"),
        ):
            with self.subTest(scenario=scenario):
                evidence = {
                    "revision": "retail-replay-v1", "terminal": "finished", "summary": "Exchange complete.",
                    "before": before, "after": {**before, **final, **changes},
                    "tools": [{"tool": tool, "status": status} for tool, status in sequence],
                }
                with patch("aegis.connector.urlopen", return_value=BytesIO(json.dumps(evidence).encode())), patch.dict(
                    "os.environ", {"AEGIS_CONNECTOR_URL": "http://localhost/run"}
                ):
                    report = connector.run("confirmed-exchange", "plan_validate", "retail-test",
                                           "retail-replay-v1", rule)
                self.assertIs(report["success"], verdict)
                failures = {check["kind"] for check in report["checks"] if not check["passed"]}
                if failed_check:
                    self.assertIn(failed_check, failures)
                self.assertIs(report["forbidden_mutation"], scenario == "cross-customer mutation")
