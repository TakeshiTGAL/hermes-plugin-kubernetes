"""Judge one kubeconfig. Do not resolve names. Do not print secrets."""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from .local_host import host_is_local
from .text import KUBECONFIG_UNREADABLE, PROD_REFUSED, PUBLIC_HOST, USERINFO

_PROD = re.compile(r"(?i)(?:^|[^a-z0-9])(?:prod|production|live|prd)(?:[^a-z0-9]|$)")
_NAMES = {"localhost", "host.docker.internal", "kubernetes.docker.internal"}
_SECRET_FIELDS = (
    "token",
    "password",
    "client-key-data",
    "client-certificate-data",
    "certificate-authority-data",
)


class Unreadable(Exception):
    pass


def parse_config(text: str) -> dict:
    if "\x00" in text:
        raise Unreadable("char")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise Unreadable("json") from exc
        if not isinstance(data, dict):
            raise Unreadable("json")
        return data
    return _parse_yaml(text)


def judge(data: dict, *, allow_production: bool) -> dict:
    """Return the current server and the secret strings, or a refusal that quotes none of them."""
    if not isinstance(data, dict):
        return _fail(KUBECONFIG_UNREADABLE)
    current = data.get("current-context")
    contexts = data.get("contexts")
    clusters = data.get("clusters")
    users = data.get("users")
    if not isinstance(current, str) or current == "" or not isinstance(contexts, list):
        return _fail(KUBECONFIG_UNREADABLE)
    if not isinstance(clusters, list) or not isinstance(users, list):
        return _fail(KUBECONFIG_UNREADABLE)
    context_map: dict[str, dict] = {}
    names: list[str] = [current]
    for item in contexts:
        if not isinstance(item, dict):
            return _fail(KUBECONFIG_UNREADABLE)
        name = item.get("name")
        body = item.get("context")
        if not isinstance(name, str) or name == "" or not isinstance(body, dict):
            return _fail(KUBECONFIG_UNREADABLE)
        if name in context_map:
            return _fail(KUBECONFIG_UNREADABLE)
        context_map[name] = body
        names.append(name)
    if current not in context_map:
        return _fail(KUBECONFIG_UNREADABLE)
    cluster_map: dict[str, dict] = {}
    for item in clusters:
        if not isinstance(item, dict):
            return _fail(KUBECONFIG_UNREADABLE)
        name = item.get("name")
        body = item.get("cluster")
        if not isinstance(name, str) or name == "" or not isinstance(body, dict):
            return _fail(KUBECONFIG_UNREADABLE)
        if name in cluster_map:
            return _fail(KUBECONFIG_UNREADABLE)
        cluster_map[name] = body
        names.append(name)
    user_map: dict[str, dict] = {}
    for item in users:
        if not isinstance(item, dict):
            return _fail(KUBECONFIG_UNREADABLE)
        name = item.get("name")
        body = item.get("user")
        if not isinstance(name, str) or not isinstance(body, dict):
            return _fail(KUBECONFIG_UNREADABLE)
        if name in user_map:
            return _fail(KUBECONFIG_UNREADABLE)
        user_map[name] = body
        names.append(name)
    if not allow_production and any(_PROD.search(name) for name in names):
        return _fail(PROD_REFUSED)
    servers: list[str] = []
    for body in cluster_map.values():
        server = body.get("server")
        if not isinstance(server, str) or server == "":
            return _fail(KUBECONFIG_UNREADABLE)
        servers.append(server)
    current_body = context_map[current]
    cluster_name = current_body.get("cluster")
    if not isinstance(cluster_name, str) or cluster_name not in cluster_map:
        return _fail(KUBECONFIG_UNREADABLE)
    current_server = cluster_map[cluster_name].get("server")
    if not isinstance(current_server, str):
        return _fail(KUBECONFIG_UNREADABLE)
    hosts: list[str] = []
    for server in servers:
        host, problem = _host(server)
        if problem:
            return _fail(problem)
        hosts.append(host)
    secrets: list[str] = []
    for body in list(user_map.values()) + list(cluster_map.values()):
        for field in _SECRET_FIELDS:
            value = body.get(field)
            if isinstance(value, str) and len(value) >= 8:
                secrets.append(value)
    return {
        "ok": True,
        "context": current,
        "server": current_server,
        "host": hosts[servers.index(current_server)],
        "secrets": secrets,
    }


def _fail(message: str) -> dict:
    return {"ok": False, "error_type": "refused", "message": message}


def _host(server: str) -> tuple[str, str]:
    if any(ord(char) < 32 or char in {"\u2028", "\u2029"} for char in server):
        return "", PUBLIC_HOST
    try:
        parts = urlsplit(server)
    except ValueError:
        return "", PUBLIC_HOST
    if parts.scheme not in {"https", "http"} or parts.hostname is None:
        return "", PUBLIC_HOST
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return "", USERINFO
    host = parts.hostname
    if _local(host):
        return host, ""
    return "", PUBLIC_HOST


def _local(host: str) -> bool:
    """Names here are outside host_is_local. DNS is not resolved."""
    lowered = host.lower()
    if lowered in _NAMES or (lowered.endswith(".local") and lowered != ".local" and ".." not in lowered):
        return True
    return host_is_local(host)


def _parse_yaml(text: str) -> dict:
    if "\t" in text:
        raise Unreadable("tab")
    rows: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip(" ").startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        rows.append((indent, raw.strip()))
    value, index = _value(rows, 0, -1)
    if index != len(rows) or not isinstance(value, dict):
        raise Unreadable("shape")
    return value


def _value(rows: list[tuple[int, str]], index: int, key_indent: int):
    if index >= len(rows) or rows[index][0] < key_indent:
        return {}, index
    if rows[index][1].startswith("- "):
        return _list(rows, index, rows[index][0])
    if rows[index][0] <= key_indent:
        return {}, index
    return _map(rows, index, rows[index][0])


def _list(rows: list[tuple[int, str]], index: int, indent: int):
    items = []
    while index < len(rows) and rows[index][0] == indent and rows[index][1].startswith("- "):
        rest = rows[index][1][2:].strip()
        index += 1
        if rest == "":
            child, index = _value(rows, index, indent)
            items.append(child)
            continue
        key, scalar, nested = _key_rest(rest)
        if key is None:
            items.append(_scalar(rest))
            continue
        mapping: dict = {}
        if nested:
            child, index = _deeper(rows, index, indent + 2)
            mapping[key] = child
        else:
            mapping[key] = scalar
        while index < len(rows) and rows[index][0] == indent + 2 and not rows[index][1].startswith("- "):
            key, scalar, nested = _key_rest(rows[index][1])
            if key is None or key in mapping:
                raise Unreadable("item")
            index += 1
            if nested:
                child, index = _deeper(rows, index, indent + 2)
                mapping[key] = child
            else:
                mapping[key] = scalar
        items.append(mapping)
    return items, index


def _deeper(rows: list[tuple[int, str]], index: int, parent_indent: int):
    if index >= len(rows) or rows[index][0] <= parent_indent:
        return {}, index
    if rows[index][1].startswith("- "):
        return _list(rows, index, rows[index][0])
    return _map(rows, index, rows[index][0])


def _map(rows: list[tuple[int, str]], index: int, indent: int):
    mapping: dict = {}
    while index < len(rows) and rows[index][0] == indent and not rows[index][1].startswith("- "):
        key, scalar, nested = _key_rest(rows[index][1])
        if key is None or key in mapping:
            raise Unreadable("key")
        index += 1
        if nested:
            child, index = _value(rows, index, indent)
            mapping[key] = child
        else:
            mapping[key] = scalar
    return mapping, index


def _key_rest(line: str):
    if line.startswith("- ") or ":" not in line:
        return None, None, False
    key, _, rest = line.partition(":")
    key = key.strip()
    if not key or any(char.isspace() for char in key) or key[:1] in {"'", '"', "*", "&", "!"}:
        raise Unreadable("key")
    rest = rest.strip()
    if rest == "":
        return key, None, True
    if rest == "{}":
        return key, {}, False
    if rest == "[]":
        return key, [], False
    if rest[:1] in {"|", ">", "[", "{", "*", "&", "!"}:
        raise Unreadable("flow")
    return key, _scalar(rest), False


def _scalar(text: str):
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        body = text[1:-1]
        if text[0] == '"':
            body = body.replace('\\"', '"').replace("\\\\", "\\")
        return body
    if text in {"true", "True", "TRUE"}:
        return True
    if text in {"false", "False", "FALSE"}:
        return False
    if text in {"null", "Null", "NULL", "~"}:
        return None
    if text[:1] in {"'", '"', "[", "{", "*", "&"}:
        raise Unreadable("scalar")
    return text
