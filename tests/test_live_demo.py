from contextlib import contextmanager, redirect_stdout
from importlib.util import find_spec
from io import BytesIO, StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch


@unittest.skipUnless(find_spec("fastapi"), "Requires the ui extra")
class LiveDemo(unittest.TestCase):
    def test_retry_comparison_and_outage_are_saved_in_same_session(self):
        from aegis.sdk import RunRequest
        from aegis.starter import create_app
        from examples.invoice import live_demo

        def scripted_model(messages, tools):
            names = [tool["function"]["name"] for tool in tools]
            completed = [message for message in messages if message["role"] == "tool"]
            selected = None
            if "submit_invoice" in names and len(completed) < 2:
                selected = "submit_invoice"
            elif "check_receipt" in names and not completed:
                selected = "check_receipt"
            elif "approve_invoice" in names and len(completed) == 1:
                selected = "approve_invoice"
            calls = [{"function": {"name": selected, "arguments": {}}}] if selected else []
            return {"message": {"role": "assistant", "content": "Tool: None", "tool_calls": calls}}

        handler = next(route.endpoint for route in create_app(live_demo.CONFIG, chat=scripted_model).routes
                       if route.path == "/run")
        running = False

        @contextmanager
        def local_server(*args):
            nonlocal running
            running = True
            try:
                yield "http://127.0.0.1:9010"
            finally:
                running = False

        def transport(request, *, timeout):
            if not running:
                raise ConnectionRefusedError("Test connector stopped")
            return BytesIO(json.dumps(handler(RunRequest.model_validate_json(request.data))).encode())

        args = SimpleNamespace(model="test-model", flagsmith=False, serve=False, connector_port=9010, repeats=2)
        with (
            TemporaryDirectory() as directory,
            patch.dict("os.environ"),
            patch.object(live_demo, "ROOT", Path(directory)),
            patch.object(live_demo, "serve", side_effect=local_server),
            patch.object(live_demo, "check_lmstudio_model"),
            patch("aegis.connector.urlopen", side_effect=transport),
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(live_demo.run(args), 0)
            session, = (Path(directory) / "demo-runs").iterdir()
            retry = json.loads((session / "retry-trace.json").read_text(encoding="utf-8"))
            comparison = json.loads((session / "comparison.json").read_text(encoding="utf-8"))
            outage = json.loads((session / "outage.json").read_text(encoding="utf-8"))
            self.assertEqual(retry["tools"][0]["result"]["status"], "timeout")
            self.assertEqual(retry["after"]["invoices"], ["INV-1"])
            self.assertEqual((comparison["verdict"], comparison["repeats"], comparison["distinct_cases"]),
                             ("pass", 2, 3))
            self.assertEqual(outage["verdict"], "insufficient_evidence")
            self.assertTrue((session / "comparison.md").is_file())
            self.assertTrue((session / "outage.md").is_file())
