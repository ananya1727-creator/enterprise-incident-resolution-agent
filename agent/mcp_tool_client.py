"""
MCP-based tool client for the agent. This is what the Orchestrator uses as
`env` when running against the MCP server (mcp_server/server.py) instead of
importing agent.simulated_tools directly.

The agent process never imports mcp_server.server or agent.simulated_tools:
it only knows how to speak MCP (list tools, call tools) against whatever
process is on the other end of the stdio pipe -- the mock server today, a
real ServiceNow/SAP/observability-platform MCP server tomorrow, with zero
change to this file. See docs/MCP_ARCHITECTURE.md.

Orchestrator calls tools via `getattr(self.env, tool_name)(**params)`; this
class supports that through __getattr__, so no per-tool boilerplate is
needed here -- every tool call is transparently proxied over MCP.
"""
import asyncio
import json
import sys
import threading
from pathlib import Path
from typing import Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPToolError(Exception):
    pass


class MCPToolClient:
    """
    Synchronous facade over an async MCP ClientSession, running the session
    on a dedicated background event loop thread so the (synchronous)
    Orchestrator can call `client.some_tool(**params)` without itself being
    async. This bridging is the only MCP-specific plumbing the rest of the
    agent needs to know about.
    """

    def __init__(self, incident_id: str, server_command: Optional[list] = None):
        self.incident_id = incident_id
        self._server_command = server_command or [sys.executable, "-m", "mcp_server.server"]
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._ctx_stack = []
        self._session: Optional[ClientSession] = None

        self._run(self._connect())
        self._tool_names = {t.name for t in self._run(self._session.list_tools()).tools}

        # Mirror SimulatedEnvironment's constructor-time behavior (architecture
        # requires env.interface_id to be known before the orchestrator's
        # OBSERVE step runs) by making the one bootstrapping call every real
        # MCP-backed environment would also need: fetch the incident once to
        # learn which interface it concerns.
        incident = self.call_tool("get_incident", incident_id=incident_id)
        self.interface_id = incident["interface_id"]

    # -- connection lifecycle -------------------------------------------

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    async def _connect(self):
        params = StdioServerParameters(command=self._server_command[0], args=self._server_command[1:])
        stdio_ctx = stdio_client(params)
        read, write = await stdio_ctx.__aenter__()
        self._ctx_stack.append(stdio_ctx)

        session_ctx = ClientSession(read, write)
        self._session = await session_ctx.__aenter__()
        self._ctx_stack.append(session_ctx)

        await self._session.initialize()

    def close(self):
        async def _close():
            for ctx in reversed(self._ctx_stack):
                try:
                    await ctx.__aexit__(None, None, None)
                except Exception:
                    pass
        try:
            self._run(_close())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=2)

    # -- tool calling -----------------------------------------------------

    async def _call_tool_async(self, name: str, kwargs: dict) -> dict:
        result = await self._session.call_tool(name, arguments=kwargs)
        if result.isError:
            text = result.content[0].text if result.content else "unknown MCP tool error"
            raise MCPToolError(f"MCP tool '{name}' returned an error: {text}")
        if getattr(result, "structuredContent", None):
            return result.structuredContent
        # Fall back to parsing the tool's JSON-serialized text content --
        # this server always returns a single JSON object per call.
        text = result.content[0].text
        return json.loads(text)

    def call_tool(self, name: str, **kwargs) -> dict:
        return self._run(self._call_tool_async(name, kwargs))

    def __getattr__(self, name: str):
        # Only proxy names that are actually registered MCP tools -- anything
        # else (e.g. orchestrator code doing a defensive `getattr(env,
        # "interface", {})` for a simulation-only attribute) must raise
        # AttributeError like a normal object, not silently return a callable.
        tool_names = self.__dict__.get("_tool_names")
        if tool_names is None or name not in tool_names:
            raise AttributeError(name)

        def _proxy(**kwargs):
            return self.call_tool(name, **kwargs)
        return _proxy
