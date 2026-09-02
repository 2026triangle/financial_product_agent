"""
================================================================================
표준 MCP (Model Context Protocol) 서버 엔드포인트 (endpoint/mcp_server.py)
--------------------------------------------------------------------------------
기능:
1. Claude Desktop, Cursor, Antigravity 등 MCP 호환 클라이언트와 stdin/stdout JSON-RPC 통신
2. search_finance_metadata 도구 등록 및 실행
================================================================================
"""

import sys
import json
import logging
from typing import Any, Dict

# 프로젝트 루트 경로 추가
import os
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from endpoint.meta_column_table import MCT

logging.basicConfig(level=logging.ERROR, stream=sys.stderr)


def run_stdio_mcp_server():
    """표준 입출력(stdio) 기반 MCP JSON-RPC 서버 루프"""
    mct = MCT()
    tool_def = MCT.get_mcp_tool_definition()

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break

            req = json.loads(line)
            req_id = req.get("id")
            method = req.get("method")
            params = req.get("params", {})

            if method == "tools/list":
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": [tool_def]
                    }
                }
            elif method == "tools/call":
                tool_name = params.get("name")
                tool_args = params.get("arguments", {})

                if tool_name == "search_finance_metadata":
                    exec_res = mct(**tool_args)
                    res = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(exec_res, ensure_ascii=False, indent=2)
                                }
                            ]
                        }
                    }
                else:
                    res = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32601,
                            "message": f"Unknown tool: {tool_name}"
                        }
                    }
            else:
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {}
                }

            sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
            sys.stdout.flush()

        except Exception as e:
            err_res = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32603,
                    "message": str(e)
                }
            }
            sys.stdout.write(json.dumps(err_res, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_stdio_mcp_server()
