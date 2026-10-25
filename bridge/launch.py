"""Start kubernetes-mcp-server 0.0.68 and keep every other tool from leaving this process."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from bridge.gate import called, listed, redact
from bridge.plan import plan
from bridge.text import CLOSED, METHOD, PROMPTS, READ_INSTRUCTIONS, RESOURCES, UVX_MISSING


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
        reply = upstream.forward(message)
    if reply is None:
        return None
    return _redact_reply(reply, secrets)


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
    result = plan(Path.cwd())
    if not result["ok"]:
        _serve_static_error(result["message"])
        raise SystemExit(1)
    if not result["uvx"]:
        _serve_static_error(UVX_MISSING)
        raise SystemExit(1)
    try:
        proc = subprocess.Popen(
            result["argv"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=result["env"],
            cwd=os.getcwd(),
        )
    except FileNotFoundError:
        _serve_static_error(UVX_MISSING)
        raise SystemExit(1)
    stderr_parts: list[bytes] = []
    stderr_thread = threading.Thread(target=_drain_stderr, args=(proc, stderr_parts), daemon=True)
    stderr_thread.start()
    try:
        _proxy(_Upstream(proc, stderr_parts, stderr_thread, result["secrets"]), result["namespace"], result["secrets"])
    finally:
        _stop(proc)
        stderr_thread.join(timeout=2)


if __name__ == "__main__":
    main()
