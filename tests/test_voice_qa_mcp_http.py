from __future__ import annotations

import http.client
import importlib.util
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


QA_DIR = Path(__file__).parents[1] / "qa" / "voice-testing"
sys.path.insert(0, str(QA_DIR))
SPEC = importlib.util.spec_from_file_location("qa_mcp_http", QA_DIR / "qa_mcp_http.py")
qa_http = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(qa_http)


class HttpMcpTests(unittest.TestCase):
    def setUp(self):
        self.server = qa_http.ThreadingHTTPServer(("127.0.0.1", 0), qa_http.McpHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def post(self, payload, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        conn.request("POST", "/mcp", body=json.dumps(payload), headers={
            "Content-Type": "application/json",
            "Cf-Access-Jwt-Assertion": "test-jwt",
            **(headers or {}),
        })
        response = conn.getresponse()
        body = response.read()
        result = (response.status, response.getheaders(), body)
        conn.close()
        return result

    def test_initialize_and_tools_list_over_streamable_http(self):
        with patch.object(qa_http, "verify_access_token", return_value={"email": "karl@prismlink.tech"}):
            status, headers, body = self.post({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            })
            self.assertEqual(status, 200)
            self.assertIn(("MCP-Protocol-Version", "2025-06-18"), headers)
            self.assertEqual(json.loads(body)["result"]["protocolVersion"], "2025-06-18")

            status, _, body = self.post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                                        {"MCP-Protocol-Version": "2025-06-18"})
        self.assertEqual(status, 200)
        tools = json.loads(body)["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["qa_status", "qa_run_t001"])

    def test_rejects_requests_without_valid_access_identity(self):
        with patch.object(qa_http, "verify_access_token", side_effect=qa_http.AuthenticationError("bad token")):
            status, _, _ = self.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(status, 401)

    def test_fixed_run_tool_uses_only_the_task_unit(self):
        proc = type("Process", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        result = {"test_id": "T001", "status": "PASS"}
        with patch.object(qa_http, "verify_access_token", return_value={"email": "karl@prismlink.tech"}), \
             patch("qa_mcp_server.systemctl", return_value=proc) as systemctl, \
             patch("qa_mcp_server.latest_result", return_value=result):
            status, _, body = self.post({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "qa_run_t001", "arguments": {}},
            })
        self.assertEqual(status, 200)
        systemctl.assert_called_once_with("start", "prism-link-voice-qa.service", timeout=180)
        self.assertEqual(json.loads(body)["result"]["isError"], False)


if __name__ == "__main__":
    unittest.main()
