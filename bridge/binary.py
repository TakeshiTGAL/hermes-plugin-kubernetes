"""Pinned kubernetes-mcp-server 0.0.68 GitHub release binaries and their sha256 digests.

Digests are the GitHub release asset `digest` values for tag v0.0.68:
https://api.github.com/repos/containers/kubernetes-mcp-server/releases/tags/v0.0.68
Checked against the darwin-arm64 asset bytes on 2026-10-26.
"""

from __future__ import annotations

import hashlib
import os
import platform
import stat
import threading
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

from .safe_write import WriteRefused, _close_all, _open_dir, write_bytes
from .text import (
    BINARY_DOWNLOAD,
    BINARY_EXECUTE,
    BINARY_MISMATCH,
    BINARY_NOT_FOUND,
    BINARY_START,
    BINARY_SYMLINK,
    BINARY_UNREADABLE,
    BINARY_UNSUPPORTED,
    BINARY_WRITE,
    downloading,
)

VERSION = "0.0.68"
RELEASE_TAG = "v0.0.68"
RELEASE_DOWNLOAD = (
    "https://github.com/containers/kubernetes-mcp-server/releases/download/v0.0.68/"
)
MAX_BYTES = 120_000_000

# GitHub asset digest (sha256) for each kubernetes-mcp-server-<os>-<arch> binary.
# Windows digests are recorded. This plugin does not start those files.
BINARY_SHA256 = {
    "kubernetes-mcp-server-darwin-arm64": "891c5ab7e4c9e48582b39693112fcb1e87780f2122dbed7344ec6926fbc4d96f",
    "kubernetes-mcp-server-darwin-amd64": "a3eead03f6dbc24b3cd954fc1be7ac257db0bb0983ebcb58557697ece35d001f",
    "kubernetes-mcp-server-linux-amd64": "1e0c561ad28f13c37434e60a3d00355f8f4b629cc51288cbbfb5d986aaa7f01a",
    "kubernetes-mcp-server-linux-arm64": "586b726c0a95bc1aa6cb94a7ba3f7d56bd07ddad061f40ca8022a49536d3ec00",
    "kubernetes-mcp-server-windows-amd64.exe": "54d6589ff0b89a5ccdc6ec7e891c5fa68ed0465c8a67b456e186107729fe9b06",
    "kubernetes-mcp-server-windows-arm64.exe": "523d959d161c30999ecb2b7015e667500c008b91a8718b5a0c47be0ea06d9a6b",
}

_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}


def _fail(error_type: str, message: str) -> dict:
    return {"ok": False, "error_type": error_type, "message": message}


def _write_refused_message(exc: WriteRefused) -> str:
    if exc.kind == "symlink":
        return BINARY_SYMLINK
    return BINARY_WRITE


def release_opener():
    """Opener that does not read http_proxy, https_proxy, or ALL_PROXY."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def open_release(request):
    return release_opener().open(request, timeout=180)


def platform_asset() -> str | None:
    system = platform.system().lower()
    # Windows crashes on os.O_NOFOLLOW at import of safe_write. Refuse before any open.
    if system == "windows":
        return None
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        return None
    if system not in ("darwin", "linux"):
        return None
    name = f"kubernetes-mcp-server-{system}-{arch}"
    if name not in BINARY_SHA256:
        return None
    return name


def binary_relative(name: str) -> str:
    return f".kubernetes-mcp-server/bin/{VERSION}/{name}"


def _partial_relative(name: str) -> str:
    return binary_relative(name) + ".partial"


def fetch_release_bytes(url: str) -> bytes:
    if not url.startswith(RELEASE_DOWNLOAD) or ".." in url or "\n" in url or "\\" in url:
        raise OSError("unexpected url")
    req = urllib.request.Request(url, headers={"User-Agent": "hermes-plugin-kubernetes"})
    with open_release(req) as resp:
        length = resp.headers.get("Content-Length")
        if length is not None:
            try:
                announced = int(length)
            except ValueError:
                announced = 0
            if announced > MAX_BYTES:
                raise OSError("too large")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BYTES:
                raise OSError("too large")
            chunks.append(chunk)
    return b"".join(chunks)


def _split_relative(relative: str) -> tuple[tuple[str, ...], str]:
    parts = Path(relative).parts
    return parts[:-1], parts[-1]


def _hash_fd(fd: int) -> str:
    digest = hashlib.sha256()
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def _existing_sha256(root: Path, relative: str) -> str | None:
    dir_parts, name = _split_relative(relative)
    directory = Path(root).joinpath(*dir_parts) if dir_parts else Path(root)
    try:
        parent_fd, fds = _open_dir(directory, create=False)
    except WriteRefused as exc:
        if exc.kind == "missing":
            return None
        raise
    try:
        try:
            info = os.lstat(name, dir_fd=parent_fd)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode):
            raise WriteRefused("symlink", relative)
        if not stat.S_ISREG(info.st_mode):
            raise WriteRefused("outside", relative)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
        except PermissionError:
            raise
        except OSError as exc:
            raise PermissionError(exc.errno, "unreadable") from exc
        try:
            return _hash_fd(fd)
        finally:
            os.close(fd)
    finally:
        _close_all(fds)


def _chmod_exec(root: Path, relative: str) -> None:
    dir_parts, name = _split_relative(relative)
    directory = Path(root).joinpath(*dir_parts) if dir_parts else Path(root)
    parent_fd, fds = _open_dir(directory, create=False)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
        try:
            os.fchmod(fd, 0o755)
        finally:
            os.close(fd)
    finally:
        _close_all(fds)


def _check_existing(root: Path, name: str) -> dict | None:
    """None when the final file is absent. A result when it is present or unreadable."""
    expected = BINARY_SHA256[name]
    relative = binary_relative(name)
    try:
        digest = _existing_sha256(root, relative)
    except WriteRefused as exc:
        return _fail("refused", _write_refused_message(exc))
    except PermissionError:
        return _fail("unreadable", BINARY_UNREADABLE)
    except OSError:
        return _fail("unreadable", BINARY_UNREADABLE)
    if digest is None:
        return None
    if digest != expected:
        return _fail("refused", BINARY_MISMATCH)
    try:
        _chmod_exec(root, relative)
    except (OSError, WriteRefused) as exc:
        if isinstance(exc, WriteRefused):
            return _fail("refused", _write_refused_message(exc))
        return _fail("refused", BINARY_WRITE)
    return {"ok": True, "path": Path(root) / relative}


def _partial_size(root: Path, name: str) -> int:
    relative = _partial_relative(name)
    try:
        directory_parts, basename = _split_relative(relative)
        directory = Path(root).joinpath(*directory_parts) if directory_parts else Path(root)
        parent_fd, fds = _open_dir(directory, create=False)
    except WriteRefused:
        return 0
    try:
        info = os.lstat(basename, dir_fd=parent_fd)
    except OSError:
        return 0
    finally:
        _close_all(fds)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return 0
    return info.st_size


def _unlink_partial(root: Path, name: str) -> None:
    relative = _partial_relative(name)
    dir_parts, basename = _split_relative(relative)
    directory = Path(root).joinpath(*dir_parts) if dir_parts else Path(root)
    try:
        parent_fd, fds = _open_dir(directory, create=False)
    except WriteRefused:
        return
    try:
        info = os.lstat(basename, dir_fd=parent_fd)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            return
        os.unlink(basename, dir_fd=parent_fd)
    except OSError:
        return
    finally:
        _close_all(fds)


def _open_partial(root: Path, name: str, *, truncate: bool):
    relative = _partial_relative(name)
    dir_parts, basename = _split_relative(relative)
    directory = Path(root).joinpath(*dir_parts) if dir_parts else Path(root)
    parent_fd, fds = _open_dir(directory, create=True)
    try:
        try:
            info = os.lstat(basename, dir_fd=parent_fd)
        except FileNotFoundError:
            info = None
        if info is not None and stat.S_ISLNK(info.st_mode):
            raise WriteRefused("symlink", relative)
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
        flags |= os.O_TRUNC if truncate else os.O_APPEND
        fd = os.open(basename, flags, 0o600, dir_fd=parent_fd)
    except Exception:
        _close_all(fds)
        raise
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        os.close(fd)
        _close_all(fds)
        raise
    return fd, fds, os.fstat(fd).st_size


def _header_total(headers, status: int) -> int | None:
    raw = headers.get("Content-Range") if headers is not None else None
    if isinstance(raw, str) and "/" in raw:
        total = raw.rsplit("/", 1)[-1].strip()
        if total.isdigit():
            return int(total)
    if status == 200 and headers is not None:
        length = headers.get("Content-Length")
        if isinstance(length, str) and length.isdigit():
            return int(length)
    return None


def _range_start(headers) -> int | None:
    raw = headers.get("Content-Range") if headers is not None else None
    if not isinstance(raw, str) or not raw.startswith("bytes "):
        return None
    span = raw.split(" ", 1)[1].split("/", 1)[0]
    start = span.split("-", 1)[0]
    if start.isdigit():
        return int(start)
    return None


def _promote(root: Path, name: str) -> dict:
    expected = BINARY_SHA256[name]
    partial = _partial_relative(name)
    final = binary_relative(name)
    try:
        digest = _existing_sha256(root, partial)
    except WriteRefused as exc:
        return _fail("refused", _write_refused_message(exc))
    except (PermissionError, OSError):
        return _fail("unreadable", BINARY_UNREADABLE)
    if digest != expected:
        _unlink_partial(root, name)
        return _fail("refused", BINARY_MISMATCH)
    dir_parts, partial_name = _split_relative(partial)
    final_name = _split_relative(final)[1]
    directory = Path(root).joinpath(*dir_parts) if dir_parts else Path(root)
    try:
        parent_fd, fds = _open_dir(directory, create=False)
    except WriteRefused as exc:
        return _fail("refused", _write_refused_message(exc))
    try:
        info = os.lstat(partial_name, dir_fd=parent_fd)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return _fail("refused", BINARY_SYMLINK if stat.S_ISLNK(info.st_mode) else BINARY_WRITE)
        os.replace(partial_name, final_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    except OSError:
        return _fail("refused", BINARY_WRITE)
    finally:
        _close_all(fds)
    try:
        placed = _existing_sha256(root, final)
    except WriteRefused as exc:
        return _fail("refused", _write_refused_message(exc))
    except (PermissionError, OSError):
        return _fail("unreadable", BINARY_UNREADABLE)
    if placed != expected:
        return _fail("refused", BINARY_MISMATCH)
    try:
        _chmod_exec(root, final)
    except (OSError, WriteRefused) as exc:
        if isinstance(exc, WriteRefused):
            return _fail("refused", _write_refused_message(exc))
        return _fail("refused", BINARY_WRITE)
    return {"ok": True, "path": Path(root) / final}


def transfer_partial(root: Path, name: str, progress: dict | None = None) -> dict:
    """One HTTP attempt. A short body stays on disk so the next call can send Range."""
    url = RELEASE_DOWNLOAD + name
    if not url.startswith(RELEASE_DOWNLOAD) or ".." in url or "\n" in url or "\\" in url:
        return _fail("unreadable", BINARY_DOWNLOAD)
    have = _partial_size(root, name)
    if have > MAX_BYTES:
        _unlink_partial(root, name)
        return _fail("unreadable", BINARY_DOWNLOAD)
    headers = {"User-Agent": "hermes-plugin-kubernetes"}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = open_release(req)
    except HTTPError as exc:
        code = getattr(exc, "code", None)
        exc.close()
        if code == 416 and have:
            _unlink_partial(root, name)
            return {"ok": False, "resume": True, "error_type": "unreadable", "message": downloading(0)}
        return _fail("unreadable", BINARY_DOWNLOAD)
    except OSError:
        return {"ok": False, "resume": True, "error_type": "unreadable", "message": downloading(have)}
    fd = -1
    dir_fds: list[int] = []
    try:
        status = getattr(resp, "status", None) or getattr(resp, "code", 200)
        if status not in (200, 206):
            return _fail("unreadable", BINARY_DOWNLOAD)
        total = _header_total(resp.headers, status)
        if total is not None and total > MAX_BYTES:
            _unlink_partial(root, name)
            return _fail("unreadable", BINARY_DOWNLOAD)
        truncate = status == 200
        if status == 206:
            start = _range_start(resp.headers)
            if start is None or start != have:
                _unlink_partial(root, name)
                return {"ok": False, "resume": True, "error_type": "unreadable", "message": downloading(0)}
        try:
            fd, dir_fds, size = _open_partial(root, name, truncate=truncate)
        except WriteRefused as exc:
            return _fail("refused", _write_refused_message(exc))
        except PermissionError:
            return _fail("unreadable", BINARY_UNREADABLE)
        except OSError:
            return _fail("refused", BINARY_WRITE)
        if progress is not None:
            progress["nbytes"] = size
        while True:
            try:
                chunk = resp.read(1024 * 1024)
            except OSError:
                return {"ok": False, "resume": True, "error_type": "unreadable", "message": downloading(size)}
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                try:
                    wrote = os.write(fd, view)
                except OSError:
                    return _fail("refused", BINARY_WRITE)
                if wrote <= 0:
                    return _fail("refused", BINARY_WRITE)
                view = view[wrote:]
            size += len(chunk)
            if progress is not None:
                progress["nbytes"] = size
            if size > MAX_BYTES:
                _unlink_partial(root, name)
                return _fail("unreadable", BINARY_DOWNLOAD)
        if total is None or size != total:
            return {"ok": False, "resume": True, "error_type": "unreadable", "message": downloading(size)}
    finally:
        if fd >= 0:
            try:
                os.fsync(fd)
            except OSError:
                pass
            os.close(fd)
        _close_all(dir_fds)
        try:
            resp.close()
        except OSError:
            pass
    return _promote(root, name)


def poll_binary(root: Path) -> dict:
    """Ready path, a fixed refusal, or the downloading sentence. Does not block on the body."""
    name = platform_asset()
    if name is None:
        return _fail("refused", BINARY_UNSUPPORTED)
    existing = _check_existing(root, name)
    if existing is not None:
        return existing
    key = f"{Path(root)}|{name}"
    with _LOCK:
        job = _JOBS.get(key)
        if job is not None and job.get("fatal"):
            return _fail(job.get("error_type") or "unreadable", job["fatal"])
        thread = None if job is None else job.get("thread")
        if thread is not None and thread.is_alive():
            return _fail("unreadable", downloading(int(job.get("nbytes") or 0)))
        job = {"nbytes": _partial_size(root, name), "fatal": None, "error_type": "unreadable", "thread": None}

        def run() -> None:
            try:
                result = transfer_partial(root, name, job)
            except Exception:
                job["fatal"] = BINARY_DOWNLOAD
                job["error_type"] = "unreadable"
                return
            if result.get("ok"):
                job["ready"] = result.get("path")
                return
            if result.get("resume"):
                return
            job["fatal"] = result.get("message") or BINARY_DOWNLOAD
            job["error_type"] = result.get("error_type") or "unreadable"

        thread = threading.Thread(target=run, daemon=True)
        job["thread"] = thread
        _JOBS[key] = job
        thread.start()
        return _fail("unreadable", downloading(int(job["nbytes"])))


def ensure_binary(root: Path) -> dict:
    """Place the pinned binary under root and refuse it when the sha256 does not match."""
    name = platform_asset()
    if name is None:
        return _fail("refused", BINARY_UNSUPPORTED)
    existing = _check_existing(root, name)
    if existing is not None:
        return existing
    url = RELEASE_DOWNLOAD + name
    try:
        data = fetch_release_bytes(url)
    except OSError:
        return _fail("unreadable", BINARY_DOWNLOAD)
    if hashlib.sha256(data).hexdigest() != BINARY_SHA256[name]:
        return _fail("refused", BINARY_MISMATCH)
    relative = binary_relative(name)
    try:
        write_bytes(root, relative, data, create=True)
        digest = _existing_sha256(root, relative)
        if digest != BINARY_SHA256[name]:
            return _fail("refused", BINARY_MISMATCH)
        _chmod_exec(root, relative)
    except WriteRefused as exc:
        return _fail("refused", _write_refused_message(exc))
    except PermissionError:
        return _fail("unreadable", BINARY_UNREADABLE)
    except OSError:
        return _fail("refused", BINARY_WRITE)
    return {"ok": True, "path": Path(root) / relative}


def open_checked(root: Path) -> dict:
    """Hash the open file. The caller starts that path on the next line and closes fd.

    This Mac's Python has no fexecve, and exec of /dev/fd/N returns EACCES, so the
    fd cannot be the process image. The caller compares the inode and then starts the path.
    """
    name = platform_asset()
    if name is None:
        return _fail("refused", BINARY_UNSUPPORTED)
    expected = BINARY_SHA256[name]
    relative = binary_relative(name)
    dir_parts, basename = _split_relative(relative)
    directory = Path(root).joinpath(*dir_parts) if dir_parts else Path(root)
    try:
        parent_fd, fds = _open_dir(directory, create=False)
    except WriteRefused as exc:
        if exc.kind == "missing":
            return _fail("unreadable", BINARY_NOT_FOUND)
        return _fail("refused", _write_refused_message(exc))
    fd = -1
    owned = True
    try:
        try:
            info = os.lstat(basename, dir_fd=parent_fd)
        except FileNotFoundError:
            return _fail("unreadable", BINARY_NOT_FOUND)
        except OSError:
            return _fail("unreadable", BINARY_UNREADABLE)
        if stat.S_ISLNK(info.st_mode):
            return _fail("refused", BINARY_SYMLINK)
        if not stat.S_ISREG(info.st_mode):
            return _fail("refused", BINARY_WRITE)
        try:
            fd = os.open(basename, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
        except PermissionError:
            return _fail("unreadable", BINARY_UNREADABLE)
        except OSError:
            return _fail("unreadable", BINARY_UNREADABLE)
        try:
            digest = _hash_fd(fd)
            os.lseek(fd, 0, os.SEEK_SET)
        except OSError:
            return _fail("unreadable", BINARY_UNREADABLE)
        if digest != expected:
            return _fail("refused", BINARY_MISMATCH)
        try:
            os.fchmod(fd, 0o755)
        except OSError:
            return _fail("refused", BINARY_WRITE)
        try:
            again = os.lstat(basename, dir_fd=parent_fd)
            held = os.fstat(fd)
        except OSError:
            return _fail("unreadable", BINARY_UNREADABLE)
        if (
            not stat.S_ISREG(again.st_mode)
            or stat.S_ISLNK(again.st_mode)
            or again.st_ino != held.st_ino
            or again.st_nlink != 1
            or held.st_nlink != 1
        ):
            return _fail("refused", BINARY_MISMATCH)
        owned = False
        return {"ok": True, "fd": fd, "dir_fds": fds, "path": Path(root) / relative}
    finally:
        if owned:
            if fd >= 0:
                os.close(fd)
            _close_all(fds)


def spawn_error(exc: BaseException) -> dict:
    if isinstance(exc, FileNotFoundError):
        return _fail("unreadable", BINARY_NOT_FOUND)
    if isinstance(exc, PermissionError):
        return _fail("unreadable", BINARY_EXECUTE)
    return _fail("unreadable", BINARY_START)
