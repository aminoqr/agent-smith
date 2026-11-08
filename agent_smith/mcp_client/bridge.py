"""A synchronous door onto an asynchronous MCP client.

The MCP SDK is built on asyncio; the agent loop is plain synchronous code.
Rather than making the whole agent async, the bridge runs one event loop in a
background thread and hands the main thread ordinary blocking methods.

The controller owns the bridge, never the worker: talking to a server means
starting a process or opening a socket, and those are exactly the things the
worker is not allowed to do.
"""
import asyncio
import concurrent.futures
import json
import shlex
import threading
from typing import Any, Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_smith.mcp_client.schema import ToolSpec, spec_from_tool

try:  # the SDK renamed this helper during the 1.x line
    from mcp.client.streamable_http import streamable_http_client as _http_client
except ImportError:  # pragma: no cover - older 1.x
    from mcp.client.streamable_http import streamablehttp_client as _http_client

CONNECT_TIMEOUT_SECONDS = 30
CALL_TIMEOUT_SECONDS = 120
CLOSE_TIMEOUT_SECONDS = 10


class MCPError(Exception):
    """The MCP server could not be reached, or refused what we asked."""


class MCPBridge:
    """Talk to one MCP server with blocking calls."""

    def __init__(self, call_timeout: float = CALL_TIMEOUT_SECONDS):
        self.call_timeout = call_timeout
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session: Optional[ClientSession] = None
        self._stop: Optional[asyncio.Event] = None
        self._running: Optional[concurrent.futures.Future] = None

    # ----- connecting -------------------------------------------------

    def connect_stdio(self, command: str) -> "MCPBridge":
        """Launch `command` as a child process and speak MCP over its pipes."""
        parts = shlex.split(command)
        if not parts:
            raise MCPError("no MCP command was given")
        parameters = StdioServerParameters(command=parts[0], args=parts[1:])
        return self._connect(lambda: stdio_client(parameters))

    def connect_http(self, url: str) -> "MCPBridge":
        """Connect to a server that is already running, over streamable HTTP."""
        return self._connect(lambda: _http_client(url))

    def _connect(self, open_transport) -> "MCPBridge":
        if self._loop is not None:
            raise MCPError("this bridge is already connected")
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="mcp-bridge", daemon=True
        )
        self._thread.start()
        ready: concurrent.futures.Future = concurrent.futures.Future()
        self._running = asyncio.run_coroutine_threadsafe(
            self._serve(open_transport, ready), self._loop
        )
        try:
            ready.result(timeout=CONNECT_TIMEOUT_SECONDS)
        except Exception as exc:
            self.close()
            raise MCPError(f"could not connect to the MCP server: {_describe(exc)}") from exc
        return self

    async def _serve(self, open_transport, ready: concurrent.futures.Future) -> None:
        """Hold the connection open until close() asks for it to end.

        Everything the SDK opens is opened and closed inside this one task:
        its context managers are not safe to split across tasks.
        """
        try:
            async with open_transport() as streams:
                incoming, outgoing = streams[0], streams[1]
                async with ClientSession(incoming, outgoing) as session:
                    await session.initialize()
                    self._session = session
                    self._stop = asyncio.Event()
                    ready.set_result(None)
                    await self._stop.wait()
        except BaseException as exc:  # noqa: BLE001 - reported to the caller
            if not ready.done():
                ready.set_exception(exc)
            raise
        finally:
            self._session = None

    # ----- calling ----------------------------------------------------

    def invoke(self, method: str, payload: Dict[str, Any]) -> str:
        """Run one MCP request and return its result as text.

        One entry point on purpose: the controller relays whatever the worker
        asked for without knowing anything about MCP.
        """
        actions = {
            "call_tool": self._call_tool,
            "list_tools": lambda _: json.dumps([spec.python_name for spec in self.list_tools()]),
            "list_resources": lambda _: json.dumps(self._run(self._list_resources())),
            "read_resource": lambda data: self._run(self._read_resource(data["uri"])),
            "list_prompts": lambda _: json.dumps(self._run(self._list_prompts())),
            "get_prompt": lambda data: self._run(
                self._get_prompt(data["name"], data.get("arguments") or {})
            ),
        }
        if method not in actions:
            raise MCPError(f"unknown MCP request '{method}'")
        return actions[method](payload)

    def list_tools(self) -> List[ToolSpec]:
        """What the server offers, translated into Python-callable shapes."""
        listed = self._run(self._session_call(lambda session: session.list_tools()))
        return [spec_from_tool(tool) for tool in listed.tools]

    def _call_tool(self, payload: Dict[str, Any]) -> str:
        result = self._run(
            self._session_call(
                lambda session: session.call_tool(payload["name"], payload.get("arguments") or {})
            )
        )
        text = _text_of(result.content)
        if result.isError:
            raise MCPError(text or f"tool '{payload['name']}' failed")
        return text

    async def _list_resources(self) -> List[str]:
        listed = await self._session.list_resources()
        return [str(resource.uri) for resource in listed.resources]

    async def _read_resource(self, uri: str) -> str:
        from pydantic import AnyUrl

        result = await self._session.read_resource(AnyUrl(uri))
        return "\n".join(
            item.text for item in result.contents if getattr(item, "text", None) is not None
        )

    async def _list_prompts(self) -> List[str]:
        listed = await self._session.list_prompts()
        return [prompt.name for prompt in listed.prompts]

    async def _get_prompt(self, name: str, arguments: Dict[str, str]) -> str:
        result = await self._session.get_prompt(name, arguments)
        return "\n".join(
            message.content.text
            for message in result.messages
            if getattr(message.content, "text", None) is not None
        )

    async def _session_call(self, make_request):
        if self._session is None:
            raise MCPError("the MCP connection is not open")
        return await make_request(self._session)

    def _run(self, coroutine):
        """Hand a coroutine to the background loop and wait for its answer."""
        if self._loop is None:
            coroutine.close()
            raise MCPError("the MCP connection is not open")
        if threading.current_thread() is self._thread:
            coroutine.close()
            raise MCPError("an MCP call cannot be made from inside the bridge's own thread")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=self.call_timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise MCPError(f"the MCP server did not answer within {self.call_timeout} s") from exc

    # ----- shutting down ----------------------------------------------

    def close(self) -> None:
        """End the session, stop the loop, join the thread. Safe to call twice."""
        if self._loop is None:
            return
        if self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._running is not None:
            try:
                self._running.result(timeout=CLOSE_TIMEOUT_SECONDS)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=CLOSE_TIMEOUT_SECONDS)
        self._loop.close()
        self._loop = None
        self._thread = None
        self._stop = None
        self._running = None

    def __enter__(self) -> "MCPBridge":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def _describe(exc: BaseException) -> str:
    """Readable text for one failure, unwrapping anyio's exception groups."""
    while getattr(exc, "exceptions", None):
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


def _text_of(content) -> str:
    """Join the text parts of a tool result, ignoring images and other blobs."""
    return "\n".join(
        item.text for item in content if getattr(item, "text", None) is not None
    )
