"""The pinned GitHub binary is checked by sha256 before it is started."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from bridge import binary
from bridge.plan import plan
from bridge.text import (
    BINARY_MISMATCH,
    BINARY_SYMLINK,
    BINARY_UNREADABLE,
    BINARY_UNSUPPORTED,
    downloading,
)
from tests.support import write_home

pytestmark = pytest.mark.no_binary_stub

SCRIPT = b"#!/bin/sh\nprintf 'started\\n'\nexec cat\n"
SCRIPT_SHA = hashlib.sha256(SCRIPT).hexdigest()


def _asset_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        raise AssertionError(machine)
    if system == "windows":
        return f"kubernetes-mcp-server-windows-{arch}.exe"
    return f"kubernetes-mcp-server-{system}-{arch}"


def _dest(root: Path, name: str | None = None) -> Path:
    return root / ".kubernetes-mcp-server" / "bin" / "0.0.68" / (name or _asset_name())


def _pin_script(monkeypatch) -> str:
    name = _asset_name()
    table = {key: SCRIPT_SHA for key in binary.BINARY_SHA256}
    table[name] = SCRIPT_SHA
    monkeypatch.setattr(binary, "BINARY_SHA256", table)
    return name


def _boom_urlopen(*args, **kwargs):
    raise AssertionError("downloaded")


def test_matching_sha256_starts(tmp_path, monkeypatch) -> None:
    name = _pin_script(monkeypatch)
    monkeypatch.setattr(binary, "open_release", _boom_urlopen)
    dest = _dest(tmp_path, name)
    dest.parent.mkdir(parents=True)
    dest.write_bytes(SCRIPT)
    dest.chmod(0o755)
    work = write_home(tmp_path)
    result = plan(work)
    assert result["ok"] is True, result
    assert result["argv"][0] == str(dest)
    assert result["argv"][1:] == ["--config", str(work / "server.toml")]
    assert "uvx" not in result["argv"][0]
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == SCRIPT_SHA
    proc = subprocess.Popen(
        result["argv"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=result["env"],
        cwd=str(work),
    )
    try:
        line = proc.stdout.readline()
        assert line == b"started\n"
        assert proc.poll() is None
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_downloaded_wrong_bytes_are_refused(tmp_path, monkeypatch) -> None:
    _pin_script(monkeypatch)

    class _Wrong:
        def __init__(self):
            self._data = b"not-the-release-binary"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size=-1):
            data, self._data = self._data, b""
            return data

        def geturl(self):
            return "https://example.invalid"

        @property
        def headers(self):
            return {"Content-Length": "22"}

        status = 200

    monkeypatch.setattr(binary, "open_release", lambda *args, **kwargs: _Wrong())
    result = plan(write_home(tmp_path))
    assert result["ok"] is False
    assert result["message"] == BINARY_MISMATCH
    assert not _dest(tmp_path).exists()


def test_existing_wrong_file_is_refused(tmp_path, monkeypatch) -> None:
    name = _pin_script(monkeypatch)
    monkeypatch.setattr(binary, "open_release", _boom_urlopen)
    dest = _dest(tmp_path, name)
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"planted")
    dest.chmod(0o755)
    result = plan(write_home(tmp_path))
    assert result["ok"] is False
    assert result["message"] == BINARY_MISMATCH
    assert dest.read_bytes() == b"planted"


def test_symlink_binary_is_refused(tmp_path, monkeypatch) -> None:
    name = _pin_script(monkeypatch)
    monkeypatch.setattr(binary, "open_release", _boom_urlopen)
    dest = _dest(tmp_path, name)
    dest.parent.mkdir(parents=True)
    target = tmp_path / "elsewhere"
    target.write_bytes(SCRIPT)
    target.chmod(0o755)
    dest.symlink_to(target)
    result = plan(write_home(tmp_path))
    assert result["ok"] is False
    assert result["message"] == BINARY_SYMLINK
    assert dest.is_symlink()
    assert target.read_bytes() == SCRIPT


def test_unknown_os_cpu_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "SunOS")
    monkeypatch.setattr(platform, "machine", lambda: "sparc")
    monkeypatch.setattr(binary, "open_release", _boom_urlopen)
    result = plan(write_home(tmp_path))
    assert result["ok"] is False
    assert result["message"] == BINARY_UNSUPPORTED
    assert not (tmp_path / ".kubernetes-mcp-server").exists()


def test_matching_download_is_written_then_started(tmp_path, monkeypatch) -> None:
    name = _pin_script(monkeypatch)

    class _Body:
        def __init__(self):
            self._data = SCRIPT

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size=-1):
            data, self._data = self._data, b""
            return data

        def geturl(self):
            return "https://release-assets.githubusercontent.com/example"

        @property
        def headers(self):
            return {"Content-Length": str(len(SCRIPT))}

        status = 200

    monkeypatch.setattr(binary, "open_release", lambda *args, **kwargs: _Body())
    work = write_home(tmp_path)
    result = plan(work)
    dest = _dest(tmp_path, name)
    assert result["ok"] is True, result
    assert result["argv"][0] == str(dest)
    assert dest.is_file()
    assert not dest.is_symlink()
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == SCRIPT_SHA
    assert os.stat(dest).st_mode & 0o111
    proc = subprocess.Popen(
        result["argv"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=result["env"],
        cwd=str(work),
    )
    try:
        assert proc.stdout.readline() == b"started\n"
        assert proc.poll() is None
    finally:
        proc.terminate()
        proc.wait(timeout=5)


_ASSET_NAME = re.compile(
    r"^kubernetes-mcp-server-(darwin|linux)-(amd64|arm64)$"
    r"|^kubernetes-mcp-server-windows-(amd64|arm64)\.exe$"
)


def test_published_binary_sha256_shape() -> None:
    source = Path(binary.__file__).read_text(encoding="utf-8")
    start = source.index("BINARY_SHA256 = {")
    end = source.index("\n}", start)
    block = source[start:end]
    lines = [line.strip() for line in block.splitlines() if "kubernetes-mcp-server-" in line]
    assert len(lines) == 6
    assert len(binary.BINARY_SHA256) == 6
    for name, digest in binary.BINARY_SHA256.items():
        assert _ASSET_NAME.fullmatch(name)
        assert re.fullmatch(r"[0-9a-f]{64}", digest)
        assert f'"{name}": "{digest}"' in block


def test_windows_is_refused_before_open(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "machine", lambda: "amd64")

    def boom(*args, **kwargs):
        raise AssertionError("opened")

    monkeypatch.setattr(binary.os, "open", boom)
    monkeypatch.setattr(binary, "open_release", boom)
    result = binary.ensure_binary(tmp_path)
    assert result["ok"] is False
    assert result["message"] == BINARY_UNSUPPORTED
    assert binary.platform_asset() is None
    assert binary.poll_binary(tmp_path)["message"] == BINARY_UNSUPPORTED
    assert not (tmp_path / ".kubernetes-mcp-server").exists()


def test_unreadable_existing_file_is_a_fixed_sentence(tmp_path, monkeypatch) -> None:
    name = _pin_script(monkeypatch)
    dest = _dest(tmp_path, name)
    dest.parent.mkdir(parents=True)
    dest.write_bytes(SCRIPT)
    real_open = os.open

    def guarded(path, flags, mode=0o777, *, dir_fd=None):
        if path == name:
            raise PermissionError(13, "Permission denied")
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(binary.os, "open", guarded)
    monkeypatch.setattr(binary, "open_release", _boom_urlopen)
    result = plan(write_home(tmp_path))
    assert result["ok"] is False
    assert result["message"] == BINARY_UNREADABLE


class _Resp:
    def __init__(self, status: int, body: bytes, headers: dict) -> None:
        self.status = status
        self.headers = headers
        self._body = body

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            data, self._body = self._body, b""
            return data
        data, self._body = self._body[:size], self._body[size:]
        return data

    def close(self) -> None:
        return None


def test_partial_download_resumes_with_range(tmp_path, monkeypatch) -> None:
    name = _asset_name()
    body = b"abcdefghij" * 2
    digest = hashlib.sha256(body).hexdigest()
    table = {key: digest for key in binary.BINARY_SHA256}
    monkeypatch.setattr(binary, "BINARY_SHA256", table)
    calls = []

    def open_release(req):
        calls.append(req.get_header("Range"))
        if len(calls) == 1:
            return _Resp(200, body[:10], {"Content-Length": "20"})
        return _Resp(206, body[10:], {"Content-Range": "bytes 10-19/20", "Content-Length": "10"})

    monkeypatch.setattr(binary, "open_release", open_release)
    first = binary.transfer_partial(tmp_path, name)
    partial = _dest(tmp_path, name + ".partial")
    assert first.get("resume") is True
    assert partial.read_bytes() == body[:10]
    assert not _dest(tmp_path, name).exists()
    second = binary.transfer_partial(tmp_path, name)
    assert second["ok"] is True, second
    assert calls == [None, "bytes=10-"]
    assert _dest(tmp_path, name).read_bytes() == body
    assert not partial.exists()
    assert downloading(10) == "downloading the pinned binary (0 MB of ~90 MB). Nothing was sent. Call again in a minute."
    assert "2 MB of ~90 MB" in downloading(2_000_000)


def test_full_response_replaces_a_partial(tmp_path, monkeypatch) -> None:
    name = _asset_name()
    body = b"ready-bytes"
    digest = hashlib.sha256(body).hexdigest()
    monkeypatch.setattr(binary, "BINARY_SHA256", {key: digest for key in binary.BINARY_SHA256})
    partial = _dest(tmp_path, name + ".partial")
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"ZZZZ")

    def open_release(req):
        assert req.get_header("Range") == "bytes=4-"
        return _Resp(200, body, {"Content-Length": str(len(body))})

    monkeypatch.setattr(binary, "open_release", open_release)
    result = binary.transfer_partial(tmp_path, name)
    assert result["ok"] is True, result
    assert _dest(tmp_path, name).read_bytes() == body


def test_complete_wrong_bytes_are_not_started(tmp_path, monkeypatch) -> None:
    name = _pin_script(monkeypatch)
    body = b"not-the-release"

    def open_release(req):
        return _Resp(200, body, {"Content-Length": str(len(body))})

    monkeypatch.setattr(binary, "open_release", open_release)
    result = binary.transfer_partial(tmp_path, name)
    assert result["message"] == BINARY_MISMATCH
    assert not _dest(tmp_path, name).exists()


def test_poll_returns_the_downloading_sentence_before_the_body(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(binary, "BINARY_SHA256", {key: "ab" * 32 for key in binary.BINARY_SHA256})
    gate = threading.Event()

    def open_release(req):
        gate.wait(3)
        return _Resp(200, b"x", {"Content-Length": "1"})

    monkeypatch.setattr(binary, "open_release", open_release)
    result = binary.poll_binary(tmp_path)
    assert result["ok"] is False
    assert result["message"] == downloading(0)
    gate.set()
    again = result
    for _ in range(100):
        again = binary.poll_binary(tmp_path)
        if again.get("message") != downloading(0):
            break
        threading.Event().wait(0.01)
    assert again["message"] == BINARY_MISMATCH


def test_release_opener_ignores_proxy_env(monkeypatch) -> None:
    def boom():
        raise AssertionError("getproxies")

    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setattr(urllib.request, "getproxies", boom)
    opener = binary.release_opener()
    for handler in opener.handlers:
        proxies = getattr(handler, "proxies", None)
        if proxies:
            assert proxies == {}
