"""Read-only Streamable HTTP client for the official McDonald's China MCP."""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ENDPOINT = "https://mcp.mcd.cn"
READ_ONLY_TOOLS = frozenset({
    "now-time-info", "list-nutrition-foods", "query-nearby-stores",
    "query-store-coupons", "query-meals", "query-meal-detail",
    "calculate-price", "campaign-calendar",
})


class MCPError(RuntimeError):
    pass


class Client:
    def __init__(self, token: str | None = None, timeout: int = 30):
        self.token = token or os.environ.get("MCD_MCP_TOKEN", "")
        if not self.token:
            raise MCPError("缺少 MCD_MCP_TOKEN；请仅在本机配置。")
        self.timeout = timeout
        self.session: str | None = None
        self.protocol = "2025-06-18"
        self.sequence = 0

    def _send(self, method: str, params: dict | None = None, notify=False):
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            self.sequence += 1
            body["id"] = self.sequence
        headers = {
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if method != "initialize":
            headers["MCP-Protocol-Version"] = self.protocol
        request = Request(ENDPOINT, json.dumps(body).encode("utf-8"), headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                self.session = response.headers.get("Mcp-Session-Id", self.session)
                content_type = response.headers.get("Content-Type", "")
                if notify:
                    response.read()
                    return None
                if "text/event-stream" in content_type:
                    data_lines = []
                    message = None
                    for raw_line in response:
                        line = raw_line.decode("utf-8").rstrip("\r\n")
                        if line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
                        elif not line and data_lines:
                            event = json.loads("\n".join(data_lines))
                            data_lines = []
                            if event.get("id") == body["id"]:
                                message = event
                                break
                    if message is None:
                        raise MCPError("MCP 未返回对应请求的结果。")
                else:
                    message = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            # Never include headers, tokens or account-specific response bodies.
            raise MCPError(f"MCP HTTP {error.code}；请求未自动重试。") from None
        except (OSError, ValueError) as error:
            raise MCPError(f"MCP 连接或响应解析失败：{type(error).__name__}") from None
        if "error" in message:
            code = message["error"].get("code", "unknown")
            raise MCPError(f"MCP JSON-RPC 错误 {code}")
        return message.get("result", {})

    def initialize(self):
        result = self._send("initialize", {
            "protocolVersion": self.protocol,
            "capabilities": {},
            "clientInfo": {"name": "mcmeal-mate", "version": "0.1.0"},
        })
        self.protocol = result.get("protocolVersion", self.protocol)
        self._send("notifications/initialized", notify=True)
        return result

    def list_tools(self):
        collected = []
        cursor = None
        for _ in range(20):
            result = self._send("tools/list", {"cursor": cursor} if cursor else {})
            collected.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return collected
        raise MCPError("工具列表超过分页上限。")

    def call(self, name: str, arguments: dict):
        if name not in READ_ONLY_TOOLS:
            raise MCPError("此客户端只支持配餐所需的只读工具。")
        result = self._send("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise MCPError(f"工具 {name} 返回失败结果。")
        return result


def unwrap(result: dict):
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    texts = [part.get("text", "") for part in result.get("content", [])
             if part.get("type") == "text"]
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except ValueError:
            pass
    return "\n".join(texts)
