"""MCP initialize answers before the binary download. Start errors stay fixed sentences."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from bridge import binary
from bridge.gate import READ_ORDER
from bridge.launch import Session, spawn_checked
from bridge.text import (
    BINARY_EXECUTE,
    BINARY_NOT_FOUND,
    BINARY_START,
    BINARY_UNSUPPORTED,
    READ_INSTRUCTIONS,
    downloading,
)
from tests.support import write_home

pytestmark = pytest.mark.no_binary_stub

SCRIPT = b"#!/bin/sh\nprintf 'started\\n'\nexec cat\n"


def _prepared(tmp_path: Path) -> dict:
    work = write_home(tmp_path)
    return {
        "ok": True,
        "root": tmp_path,
        "work": work,
        "namespace": "shop",
        "secrets": [],
        "env": {"PATH": os.environ.get("PATH", "")},
    }


def test_windows_guard_runs_before_safe_write_import() -> None:
    source = Path(binary.__file__).resolve().parent.joinpath("launch.py").read_text(encoding="utf-8")
    guard = source.index('platform.system().lower() == "windows"')
    imported = source.index("from bridge.plan import prepare")
    assert guard < imported
    assert "Refused this OS or CPU. Nothing was sent." in source
    assert BINARY_UNSUPPORTED == "Refused this OS or CPU. Nothing was sent."


def test_initialize_and_tools_list_do_not_download(tmp_path, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("download")

    monkeypatch.setattr(binary, "poll_binary", boom)
    monkeypatch.setattr("bridge.launch.poll_binary", boom)
    monkeypatch.setattr(binary, "open_release", boom)
    session = Session(_prepared(tmp_path))
    init = session.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
        }
    )
    assert "one namespace" in init["result"]["instructions"]
    assert init["result"]["instructions"] == READ_INSTRUCTIONS
    assert "prompts" not in init["result"]["capabilities"]
    assert init["result"]["protocolVersion"] == "2025-06-18"
    listed = session.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert [tool["name"] for tool in listed["result"]["tools"]] == list(READ_ORDER)
    assert all(tool["annotations"]["readOnlyHint"] is True for tool in listed["result"]["tools"])


def test_disallowed_tool_does_not_download_or_start(tmp_path, monkeypatch) -> None:
    calls = []

    def poll(root):
        calls.append("poll")
        return {"ok": True}

    def spawn(*args, **kwargs):
        calls.append("spawn")
        return {"ok": True, "proc": None}

    monkeypatch.setattr("bridge.launch.poll_binary", poll)
    monkeypatch.setattr("bridge.launch.spawn_checked", spawn)
    session = Session(_prepared(tmp_path))
    reply = session.handle(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "configuration_view", "arguments": {}},
        }
    )
    text = reply["result"]["content"][0]["text"]
    assert text == "Refused configuration_view. This plugin only reads one namespace. Nothing was sent."
    assert reply["result"]["isError"] is True
    assert calls == []
    assert session.upstream is None
    assert session.proc is None

    missing = session.handle(
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"arguments": {}}}
    )
    assert missing["result"]["content"][0]["text"] == (
        "Refused this tool. This plugin only reads one namespace. Nothing was sent."
    )
    shaped = session.handle({"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": []})
    assert shaped["result"]["content"][0]["text"] == (
        "Refused tools/call because params is not an object. Nothing was sent."
    )
    bad_args = session.handle(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "pods_log", "arguments": []},
        }
    )
    assert bad_args["result"]["content"][0]["text"] == (
        "Refused tools/call because arguments is not an object. Nothing was sent."
    )
    assert calls == []


def test_idless_notification_before_upstream_is_dropped(tmp_path, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("download")

    monkeypatch.setattr("bridge.launch.poll_binary", boom)
    monkeypatch.setattr("bridge.launch.spawn_checked", boom)
    session = Session(_prepared(tmp_path))
    assert session.handle({"jsonrpc": "2.0", "method": "ping"}) == {"_drop": True}
    assert session.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) == {"_drop": True}
    assert session.handle({"jsonrpc": "2.0", "method": "notifications/cancelled"}) == {"_drop": True}
    ping = session.handle({"jsonrpc": "2.0", "id": 4, "method": "ping"})
    assert ping == {"jsonrpc": "2.0", "id": 4, "result": {}}
    assert session.upstream is None


def test_tool_call_while_downloading_does_not_start(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "bridge.launch.poll_binary",
        lambda root: {"ok": False, "error_type": "unreadable", "message": downloading(2_000_000)},
    )

    def boom(*args, **kwargs):
        raise AssertionError("started")

    monkeypatch.setattr("bridge.launch.spawn_checked", boom)
    session = Session(_prepared(tmp_path))
    reply = session.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "pods_log", "arguments": {"name": "broken"}},
        }
    )
    text = reply["result"]["content"][0]["text"]
    assert text == "downloading the pinned binary (2 MB of ~90 MB). Nothing was sent. Call again in a minute."
    assert reply["result"]["isError"] is True


def _place_script(tmp_path: Path, monkeypatch) -> None:
    name = binary.platform_asset()
    assert name
    digest = hashlib.sha256(SCRIPT).hexdigest()
    monkeypatch.setattr(binary, "BINARY_SHA256", {key: digest for key in binary.BINARY_SHA256})
    dest = tmp_path / ".kubernetes-mcp-server" / "bin" / "0.0.68" / name
    dest.parent.mkdir(parents=True)
    dest.write_bytes(SCRIPT)
    dest.chmod(0o755)


def test_popen_errors_are_fixed_sentences(tmp_path, monkeypatch) -> None:
    _place_script(tmp_path, monkeypatch)
    prepared = _prepared(tmp_path)
    seen = {}

    def raise_not_found(*args, **kwargs):
        seen["argv"] = args[0]
        raise FileNotFoundError(2, "missing")

    monkeypatch.setattr("bridge.launch.subprocess.Popen", raise_not_found)
    missing = spawn_checked(prepared["root"], prepared["work"] / "server.toml", prepared["env"], str(prepared["work"]))
    assert missing["message"] == BINARY_NOT_FOUND
    assert seen["argv"][0].endswith(binary.platform_asset())

    def raise_permission(*args, **kwargs):
        raise PermissionError(13, "denied")

    monkeypatch.setattr("bridge.launch.subprocess.Popen", raise_permission)
    denied = spawn_checked(prepared["root"], prepared["work"] / "server.toml", prepared["env"], str(prepared["work"]))
    assert denied["message"] == BINARY_EXECUTE

    def raise_os(*args, **kwargs):
        raise OSError(5, "io")

    monkeypatch.setattr("bridge.launch.subprocess.Popen", raise_os)
    other = spawn_checked(prepared["root"], prepared["work"] / "server.toml", prepared["env"], str(prepared["work"]))
    assert other["message"] == BINARY_START


def test_checked_script_starts(tmp_path, monkeypatch) -> None:
    _place_script(tmp_path, monkeypatch)
    prepared = _prepared(tmp_path)
    spawned = spawn_checked(prepared["root"], prepared["work"] / "server.toml", prepared["env"], str(prepared["work"]))
    assert spawned["ok"] is True, spawned
    proc = spawned["proc"]
    try:
        assert proc.stdout.readline() == b"started\n"
        assert proc.poll() is None
    finally:
        proc.terminate()
        proc.wait(timeout=5)
