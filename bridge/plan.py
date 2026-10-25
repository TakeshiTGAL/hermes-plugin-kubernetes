"""Read cluster.json and write the pinned server's TOML. The token stays in the kubeconfig file."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from .kube import Unreadable, judge, parse_config
from .safe_write import WriteRefused, write_bytes
from .text import (
    BAD_ALLOW,
    BAD_KUBECONFIG,
    BAD_NAMESPACE,
    KUBECONFIG_UNREADABLE,
    NOT_FOUND,
    UNKNOWN_SETTING,
    UNREADABLE,
    UVX_MISSING,
    WORK_MISSING,
    WRITE_HARDLINK,
    WRITE_OTHER,
    WRITE_OUTSIDE,
    WRITE_SYMLINK,
)

PIN = "kubernetes-mcp-server==0.0.68"
_KEYS = {"kubeconfig", "namespace", "allow_production"}
_EXACT_ENV = {
    "PATH",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "TZ",
    "USER",
    "SHELL",
    "TERM",
    "NO_COLOR",
    "CI",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "PYTHONPATH",
}
_PREFIX_ENV = ("LC_", "XDG_")
_NAMESPACE = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")


def _fail(error_type: str, message: str) -> dict:
    return {"ok": False, "error_type": error_type, "message": message}


def _controls(value: str) -> bool:
    for char in value:
        if ord(char) < 32 or char in {"\u2028", "\u2029"}:
            return True
    return False


def _namespace(value) -> str | dict:
    if not isinstance(value, str) or not 1 <= len(value) <= 63 or _NAMESPACE.fullmatch(value) is None:
        return _fail("refused", BAD_NAMESPACE)
    return value


def _base_env(root: Path, kubeconfig: str) -> dict:
    kept = {}
    for name, value in os.environ.items():
        if name in _EXACT_ENV or name.startswith(_PREFIX_ENV):
            kept[name] = value
    kept["HOME"] = str(root)
    kept["UV_CACHE_DIR"] = str(root / "uv-cache")
    kept["KUBECONFIG"] = kubeconfig
    return kept


def toml_text(kubeconfig: str) -> str:
    quoted = json.dumps(kubeconfig)
    return "\n".join(
        [
            "read_only = true",
            'toolsets = ["core"]',
            'enabled_tools = ["pods_list_in_namespace", "pods_get", "events_list", "pods_log"]',
            'list_output = "yaml"',
            f"kubeconfig = {quoted}",
            'cluster_provider_strategy = "kubeconfig"',
            "log_level = 0",
            'log_file = "stderr"',
            "",
            "[[denied_resources]]",
            'group = ""',
            'version = "v1"',
            'kind = "Secret"',
            "",
            "[[denied_resources]]",
            'group = ""',
            'version = "v1"',
            'kind = "ConfigMap"',
            "",
            "[[denied_resources]]",
            'group = ""',
            'version = "v1"',
            'kind = "ServiceAccount"',
            "",
            "[telemetry]",
            "enabled = false",
            'logs_exporter = "none"',
            'metrics_exporter = "none"',
            "",
        ]
    )


def plan(cwd: Path) -> dict:
    """Read cluster.json next to the work directory. Do not resolve the API host."""
    cwd_path = Path(cwd)
    root = cwd_path.parent
    if not cwd_path.is_dir() or cwd_path.is_symlink():
        return _fail("refused", WORK_MISSING)
    path = root / "cluster.json"
    if path.is_symlink():
        return _fail("refused", UNREADABLE)
    if not path.is_file():
        return _fail("not_configured", NOT_FOUND)
    try:
        raw_text = path.read_text(encoding="utf-8")
        raw = json.loads(raw_text)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _fail("unreadable", UNREADABLE)
    if not isinstance(raw, dict) or set(raw) - _KEYS:
        return _fail("refused", UNKNOWN_SETTING)
    namespace = _namespace(raw.get("namespace"))
    if isinstance(namespace, dict):
        return namespace
    allow = raw.get("allow_production", False)
    if not isinstance(allow, bool):
        return _fail("refused", BAD_ALLOW)
    kubeconfig = raw.get("kubeconfig")
    if (
        not isinstance(kubeconfig, str)
        or not Path(kubeconfig).is_absolute()
        or _controls(kubeconfig)
        or len(kubeconfig) > 4096
        or not Path(kubeconfig).is_file()
    ):
        return _fail("refused", BAD_KUBECONFIG)
    try:
        loaded = parse_config(Path(kubeconfig).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, Unreadable):
        return _fail("unreadable", KUBECONFIG_UNREADABLE)
    judged = judge(loaded, allow_production=allow)
    if not judged["ok"]:
        return judged
    body = toml_text(kubeconfig).encode("utf-8")
    if judged["secrets"] and any(secret.encode("utf-8") in body for secret in judged["secrets"]):
        return _fail("refused", WRITE_OTHER)
    try:
        write_bytes(root, f"{cwd_path.name}/server.toml", body, cut_hardlink=False)
    except WriteRefused as exc:
        return _fail("refused", _write_message(exc.kind))
    uvx = shutil.which("uvx")
    argv = [uvx or "uvx", "--from", PIN, "kubernetes-mcp-server", "--config", str(cwd_path / "server.toml")]
    env = _base_env(root, kubeconfig)
    if "http_proxy" in env or "HTTPS_PROXY" in env or "KUBERNETES_SERVICE_HOST" in env:
        return _fail("refused", WRITE_OTHER)
    return {
        "ok": True,
        "argv": argv,
        "env": env,
        "secrets": judged["secrets"],
        "namespace": namespace,
        "context": judged["context"],
        "server": judged["server"],
        "uvx": uvx,
        "root": root,
    }


def _write_message(kind: str) -> str:
    if kind == "symlink":
        return WRITE_SYMLINK
    if kind == "hardlink":
        return WRITE_HARDLINK
    if kind == "outside":
        return WRITE_OUTSIDE
    return WRITE_OTHER


def uvx_missing_message() -> str:
    return UVX_MISSING
