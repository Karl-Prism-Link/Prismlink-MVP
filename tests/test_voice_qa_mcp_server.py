from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "qa" / "voice-testing" / "qa_mcp_server.py"
SPEC = importlib.util.spec_from_file_location("qa_mcp_server", MODULE_PATH)
qa = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(qa)


class McpProtocolTests(unittest.TestCase):
    def test_lists_only_fixed_qa_tools(self):
        response = qa.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual([tool["name"] for tool in response["result"]["tools"]], ["qa_status", "qa_run_t001"])
        for tool in response["result"]["tools"]:
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
            self.assertEqual(tool["inputSchema"]["properties"], {})

    def test_rejects_any_tool_arguments(self):
        result = qa.call_tool("qa_run_t001", {"target": "sip:elsewhere"})
        self.assertTrue(result["isError"])
        self.assertIn("no arguments", result["content"][0]["text"])

    def test_status_reports_fixed_unit_and_latest_result(self):
        proc = type("Process", (), {"returncode": 0, "stdout": "ActiveState=inactive\n", "stderr": ""})()
        result_data = {"test_id": "T001", "status": "PASS"}
        with patch.object(qa, "systemctl", return_value=proc) as systemctl, patch.object(qa, "latest_result", return_value=result_data):
            result = qa.call_tool("qa_status", {})
        systemctl.assert_called_once_with("show", qa.UNIT, "--property=ActiveState")
        body = json.loads(result["content"][0]["text"])
        self.assertEqual(body["unit"], qa.UNIT)
        self.assertEqual(body["latest_result"], result_data)

    def test_run_starts_only_fixed_unit_and_returns_result(self):
        proc = type("Process", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        result_data = {"test_id": "T001", "status": "PASS"}
        with patch.object(qa, "systemctl", return_value=proc) as systemctl, patch.object(qa, "latest_result", return_value=result_data):
            result = qa.call_tool("qa_run_t001", {})
        systemctl.assert_called_once_with("start", qa.UNIT, timeout=180)
        self.assertFalse(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"]), result_data)

    def test_mcp_initialize_and_notification(self):
        response = qa.handle({"jsonrpc": "2.0", "id": 4, "method": "initialize"})
        self.assertEqual(response["result"]["serverInfo"]["name"], "prism-link-voice-qa")
        self.assertIsNone(qa.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))


if __name__ == "__main__":
    unittest.main()
