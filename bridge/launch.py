"""Start the pinned kubernetes-mcp-server 0.0.68 binary and keep every other tool from leaving this process."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _refuse_windows() -> None:
    """Answer the first JSON-RPC line before safe_write's O_NOFOLLOW is evaluated."""
    message = "Refused this OS or CPU. Nothing was sent."
    line = sys.stdin.buffer.readline()
    if not line:
        return
    try:
        incoming = json.loads(line.decode())
    except json.JSONDecodeError:
        return
    if not isinstance(incoming, dict) or "id" not in incoming:
        return
    reply = {"jsonrpc": "2.0", "id": incoming["id"], "error": {"code": -32000, "message": message}}
    sys.stdout.buffer.write(json.dumps(reply).encode() + b"\n")
    sys.stdout.buffer.flush()


if __name__ == "__main__" and platform.system().lower() == "windows":
    _refuse_windows()
    raise SystemExit(1)

from bridge.binary import open_checked, poll_binary, spawn_error
from bridge.gate import READS, catalog, called, listed, redact
from bridge.plan import prepare
from bridge.safe_write import _close_all
from bridge.text import (
    BAD_ARGUMENTS,
    BAD_PARAMS,
    BINARY_MISMATCH,
    CLOSED,
    METHOD,
    PROMPTS,
    READ_INSTRUCTIONS,
    RESOURCES,
)


def rpc_result(rpc_id, payload: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": payload}


def rpc_error(rpc_id, message: str, code: int = -32000) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def _redact_reply(reply, secrets: list[str]):
    if isinstance(reply, str):
        return redact(reply, secrets)
    if isinstance(reply, list):
        return [_redact_reply(item, secrets) for item in reply]
    if isinstance(reply, dict):
        return {key: _redact_reply(value, secrets) for key, value in reply.items()}
    return reply


class _Upstream:
    def __init__(self, proc: subprocess.Popen, stderr_parts: list[bytes], stderr_thread: threading.Thread, secrets: list[str]) -> None:
        self.proc = proc
        self.ident = 1_000_000
        self.stderr_parts = stderr_parts
        self.stderr_thread = stderr_thread
        self.secrets = secrets

    def closed_message(self) -> str:
        self.stderr_thread.join(timeout=2)
        raw = b"".join(self.stderr_parts).decode("utf-8", "replace")
        raw = redact(raw, self.secrets)
        line = ""
        for row in raw.splitlines():
            if row.strip():
                line = row.strip()
                break
        if line:
            return f"upstream closed. {line[:500]} Nothing was sent."
        return CLOSED

    def __call__(self, name: str, arguments: dict) -> dict:
        self.ident += 1
        message = {
            "jsonrpc": "2.0",
            "id": self.ident,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        try:
            self._write(message)
        except (BrokenPipeError, OSError):
            return {"content": [{"type": "text", "text": self.closed_message()}], "isError": True}
        reply = self._read_id(self.ident)
        result = reply.get("result")
        if isinstance(result, dict):
            return result
        error = reply.get("error")
        detail = error.get("message") if isinstance(error, dict) else CLOSED
        return {"content": [{"type": "text", "text": redact(str(detail), self.secrets)}], "isError": True}

    def forward(self, message: dict) -> dict | None:
        try:
            self._write(message)
        except (BrokenPipeError, OSError):
            if "id" not in message:
                return None
            return rpc_error(message["id"], self.closed_message())
        if "id" not in message:
            return None
        return self._read_id(message["id"])

    def _write(self, message: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message).encode() + b"\n")
        self.proc.stdin.flush()

    def _read_id(self, wanted) -> dict:
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                return rpc_error(wanted, self.closed_message())
            try:
                parsed = json.loads(line.decode())
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and parsed.get("id") == wanted:
                return parsed


def _prepare_initialize(reply: dict) -> dict:
    result = reply.get("result")
    if not isinstance(result, dict):
        return reply
    result = dict(result)
    result["instructions"] = READ_INSTRUCTIONS
    capabilities = result.get("capabilities")
    if isinstance(capabilities, dict):
        capabilities = dict(capabilities)
        capabilities.pop("prompts", None)
        capabilities.pop("resources", None)
        result["capabilities"] = capabilities
    rewritten = dict(reply)
    rewritten["result"] = result
    return rewritten


def dispatch(message: dict, upstream, namespace: str, secrets: list[str]) -> dict | None:
    """Answer the read allowlist. None means the caller forwards the message."""
    method = message.get("method")
    if method == "tools/list" and "id" in message:
        reply = upstream.forward(message)
        if not isinstance(reply, dict):
            return rpc_error(message["id"], CLOSED)
        if isinstance(reply.get("error"), dict) and "result" not in reply:
            return reply if reply.get("jsonrpc") == "2.0" else rpc_error(message["id"], CLOSED)
        return listed(reply)
    if method == "initialize" and "id" in message:
        reply = upstream.forward(message)
        if not isinstance(reply, dict):
            return rpc_error(message["id"], CLOSED)
        if isinstance(reply.get("error"), dict) and "result" not in reply:
            if reply.get("jsonrpc") == "2.0" and "id" in reply:
                return reply
            detail = reply["error"].get("message") if isinstance(reply.get("error"), dict) else ""
            return rpc_error(message["id"], str(detail or CLOSED))
        return _prepare_initialize(reply)
    if method == "ping":
        return None
    if isinstance(method, str) and method.startswith("prompts/"):
        if "id" not in message:
            return {"_drop": True}
        return rpc_error(message["id"], PROMPTS)
    if isinstance(method, str) and method.startswith("resources/"):
        if "id" not in message:
            return {"_drop": True}
        return rpc_error(message["id"], RESOURCES)
    if method != "tools/call":
        if "id" not in message:
            return {"_drop": True}
        return rpc_error(message["id"], METHOD)
    if "id" not in message:
        return {"_drop": True}
    params = message.get("params")
    if not isinstance(params, dict):
        payload = {"content": [{"type": "text", "text": "Refused tools/call because params is not an object. Nothing was sent."}], "isError": True}
        return rpc_result(message["id"], payload)
    name = str(params.get("name") or "")
    if "arguments" not in params:
        arguments: dict = {}
    elif not isinstance(params.get("arguments"), dict):
        payload = {"content": [{"type": "text", "text": "Refused tools/call because arguments is not an object. Nothing was sent."}], "isError": True}
        return rpc_result(message["id"], payload)
    else:
        arguments = params["arguments"]
    payload = called(name, arguments, upstream, namespace, secrets)
    return rpc_result(message["id"], payload)


def settle(message: dict, upstream, namespace: str, secrets: list[str]):
    reply = dispatch(message, upstream, namespace, secrets)
    if isinstance(reply, dict) and reply.get("_drop") is True:
        return None
    if reply is None:
        if upstream is None:
            return None
        reply = upstream.forward(message)
    if reply is None:
        return None
    return _redact_reply(reply, secrets)


def _local_initialize(message: dict) -> dict:
    params = message.get("params")
    version = "2025-06-18"
    if isinstance(params, dict) and isinstance(params.get("protocolVersion"), str) and params["protocolVersion"]:
        version = params["protocolVersion"]
    return rpc_result(
        message["id"],
        {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "kubernetes", "version": "0.0.68"},
            "instructions": READ_INSTRUCTIONS,
        },
    )


def spawn_checked(root: Path, config: Path, env: dict, cwd: str) -> dict:
    """Hash the open file, confirm the directory entry, then start that path."""
    opened = open_checked(root)
    if not opened["ok"]:
        return opened
    fd = opened["fd"]
    dir_fds = opened["dir_fds"]
    argv = [str(opened["path"]), "--config", str(config)]
    try:
        try:
            held = os.fstat(fd)
            named = os.lstat(opened["path"])
        except OSError as exc:
            return spawn_error(exc)
        if held.st_ino != named.st_ino or held.st_nlink != 1:
            return {"ok": False, "error_type": "refused", "message": BINARY_MISMATCH}
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=cwd,
            )
        except OSError as exc:
            return spawn_error(exc)
        return {"ok": True, "proc": proc, "argv": argv}
    finally:
        os.close(fd)
        _close_all(dir_fds)


def _prime(upstream: _Upstream, params) -> bool:
    if not isinstance(params, dict):
        params = {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "kubernetes", "version": "0.1.0"},
        }
    try:
        reply = upstream.forward(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params}
        )
    except OSError:
        return False
    if not isinstance(reply, dict) or not isinstance(reply.get("result"), dict):
        return False
    try:
        upstream.forward({"jsonrpc": "2.0", "method": "notifications/initialized"})
    except OSError:
        return False
    return True


def _tool_text(rpc_id, message: str) -> dict:
    return rpc_result(rpc_id, {"content": [{"type": "text", "text": message}], "isError": True})


def _not_sent(*_args, **_kwargs):
    raise AssertionError("sent")


def _refuse_before_binary(message: dict, namespace: str, secrets: list[str]):
    """Refuse a call that is not one of the four reads. None means the binary may start."""
    params = message.get("params")
    if not isinstance(params, dict):
        return _tool_text(message["id"], BAD_PARAMS)
    name = str(params.get("name") or "")
    arguments = params.get("arguments") if "arguments" in params else {}
    if name not in READS or not isinstance(arguments, dict):
        payload = called(name, arguments, _not_sent, namespace, secrets)
        return rpc_result(message["id"], payload)
    return None


class Session:
    """Answer initialize and tools/list before the binary exists. Download on an allowed tools/call."""

    def __init__(self, prepared: dict) -> None:
        self.prepared = prepared
        self.upstream = None
        self.init_params = None
        self.proc = None

    def handle(self, message: dict):
        method = message.get("method")
        if self.upstream is None and "id" not in message:
            return {"_drop": True}
        if method == "initialize" and "id" in message:
            params = message.get("params")
            if isinstance(params, dict):
                self.init_params = params
            return _local_initialize(message)
        if method == "tools/list" and "id" in message:
            return rpc_result(message["id"], {"tools": catalog()})
        if method == "tools/call" and self.upstream is None:
            return self._call_or_download(message)
        if self.upstream is None and method == "ping" and "id" in message:
            return rpc_result(message["id"], {})
        return settle(message, self.upstream, self.prepared["namespace"], self.prepared["secrets"])

    def _call_or_download(self, message: dict):
        if "id" not in message:
            return {"_drop": True}
        refused = _refuse_before_binary(message, self.prepared["namespace"], self.prepared["secrets"])
        if refused is not None:
            return refused
        state = poll_binary(self.prepared["root"])
        if not state["ok"]:
            return _tool_text(message["id"], state["message"])
        spawned = spawn_checked(
            self.prepared["root"],
            self.prepared["work"] / "server.toml",
            self.prepared["env"],
            str(self.prepared["work"]),
        )
        if not spawned["ok"]:
            return _tool_text(message["id"], spawned["message"])
        self.proc = spawned["proc"]
        stderr_parts: list[bytes] = []
        stderr_thread = threading.Thread(target=_drain_stderr, args=(self.proc, stderr_parts), daemon=True)
        stderr_thread.start()
        self.upstream = _Upstream(self.proc, stderr_parts, stderr_thread, self.prepared["secrets"])
        if not _prime(self.upstream, self.init_params):
            _stop(self.proc)
            self.upstream = None
            self.proc = None
            return _tool_text(message["id"], CLOSED)
        return settle(message, self.upstream, self.prepared["namespace"], self.prepared["secrets"])


def _proxy(upstream: _Upstream, namespace: str, secrets: list[str]) -> None:
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return
        try:
            message = json.loads(line.decode())
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        reply = settle(message, upstream, namespace, secrets)
        if reply is not None:
            sys.stdout.buffer.write(json.dumps(reply).encode() + b"\n")
            sys.stdout.buffer.flush()


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _serve_static_error(message: str) -> None:
    line = sys.stdin.buffer.readline()
    if not line:
        return
    try:
        incoming = json.loads(line.decode())
    except json.JSONDecodeError:
        return
    if not isinstance(incoming, dict) or "id" not in incoming:
        return
    sys.stdout.buffer.write(json.dumps(rpc_error(incoming["id"], message)).encode() + b"\n")
    sys.stdout.buffer.flush()


def _drain_stderr(proc: subprocess.Popen, parts: list[bytes]) -> None:
    if proc.stderr is None:
        return
    while True:
        data = proc.stderr.read(65536)
        if not data:
            return
        if sum(len(part) for part in parts) < 1_000_000:
            parts.append(data)


def main() -> None:
    result = prepare(Path.cwd())
    if not result["ok"]:
        _serve_static_error(result["message"])
        raise SystemExit(1)
    session = Session(result)
    try:
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                return
            try:
                message = json.loads(line.decode())
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            reply = session.handle(message)
            if isinstance(reply, dict) and reply.get("_drop") is True:
                continue
            if reply is None:
                continue
            sys.stdout.buffer.write(json.dumps(reply).encode() + b"\n")
            sys.stdout.buffer.flush()
    finally:
        if session.proc is not None:
            _stop(session.proc)


if __name__ == "__main__":
    main()
