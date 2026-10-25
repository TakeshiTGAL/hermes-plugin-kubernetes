"""Allowlist in front of kubernetes-mcp-server 0.0.68. The pinned server is not modified."""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

from . import text

READ_ORDER = ("pods_list_in_namespace", "pods_get", "events_list", "pods_log")
READS = frozenset(READ_ORDER)
_LISTS = frozenset({"pods_list_in_namespace", "events_list"})
_KINDS = frozenset({"Secret", "ConfigMap", "ServiceAccount"})
_SECRET_NAME = re.compile(
    r"(?i)(?:^|[^a-z])(?:password|passwd|token|secret|api[-_]?key|credential|private[-_]?key|access[-_]?key)(?:[^a-z]|$)"
)
_APPLIED = "kubectl.kubernetes.io/last-applied-configuration"
_KIND_LINE = re.compile(r"(?m)^kind:\s*(Secret|ConfigMap|ServiceAccount)\s*$")
_CONTINUE = re.compile(r"(?m)^# continue: \S")
_NAME = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
_POD = re.compile(r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$")
_SELECTOR = re.compile(r"^[A-Za-z0-9._/=,!() -]{1,256}$")
_BLURB = {
    "pods_list_in_namespace": " what is down in the namespace. list pods and their status.",
    "pods_get": " pod status and restart count.",
    "events_list": " pod events.",
    "pods_log": " why is this pod restarting. pod logs.",
}
_KEYS = {
    "pods_list_in_namespace": {"namespace", "labelSelector", "fieldSelector", "limit", "continue"},
    "pods_get": {"namespace", "name"},
    "events_list": {"namespace", "fieldSelector", "limit", "continue"},
    "pods_log": {"namespace", "name", "container", "tail", "previous"},
}
def public_texts(root: Path):
    paths = [
        root / "README.md",
        root / "skills" / "kubernetes" / "SKILL.md",
        root / "plugin.json",
        root / "mcp.json",
    ]
    for path in paths:
        yield path, path.read_text(encoding="utf-8")


def listed(message: dict) -> dict:
    """Keep the four read tools and mark each one readOnlyHint true."""
    if not isinstance(message, dict):
        return message
    result = message.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
        return message
    by_name = {}
    for tool in result["tools"]:
        if isinstance(tool, dict) and isinstance(tool.get("name"), str):
            by_name.setdefault(tool["name"], tool)
    kept = []
    for name in READ_ORDER:
        tool = by_name.get(name)
        if tool is None:
            continue
        copied = dict(tool)
        annotations = dict(copied.get("annotations") or {})
        annotations["readOnlyHint"] = True
        copied["annotations"] = annotations
        description = copied.get("description")
        if not isinstance(description, str):
            description = ""
        extra = _BLURB[name]
        if extra.strip() not in description:
            copied["description"] = description.rstrip() + extra
        kept.append(copied)
    reply = dict(message)
    body = dict(result)
    body["tools"] = kept
    reply["result"] = body
    return reply


def called(name: str, arguments: dict, exchange, namespace: str, secrets: list[str] | None = None) -> dict:
    """Run one allowlisted read, or refuse. A name outside the four is not passed to exchange."""
    secrets = list(secrets or [])
    if name not in READS:
        shown = name or "this tool"
        return _result(f"Refused {shown}. This plugin only reads one namespace. Nothing was sent.", True, secrets)
    if not isinstance(arguments, dict):
        return _result(text.BAD_ARGUMENTS, True, secrets)
    problem = _check(name, arguments, namespace)
    if isinstance(problem, str):
        return _result(problem, True, secrets)
    result = exchange(name, problem)
    if not isinstance(result, dict):
        return _result(text.NO_OBJECT, True, secrets)
    is_error = result.get("isError") is True
    blocks = []
    content = result.get("content")
    structured = result.get("structuredContent")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            raw = block.get("text")
            if not isinstance(raw, str):
                continue
            blocks.append(raw)
    if not blocks:
        return _result(text.EMPTY_UPSTREAM, True, secrets)
    combined = "\n".join(blocks)
    if _blocked_kind(combined, structured):
        return _result(text.WITHHELD, True, secrets)
    cleaned = []
    for raw in blocks:
        piece = _scrub_applied_text(raw)
        piece = _scrub_env_text(piece)
        piece = _scrub_json_env(piece)
        piece = redact(piece, secrets)
        if is_error:
            piece = _failure(piece)
        cleaned.append(piece)
    if not is_error and name in _LISTS:
        cleaned[0] = _page(cleaned[0], structured, name) + cleaned[0]
    structured = _scrub_applied_obj(_scrub_env_obj(_scrub_obj(structured, secrets)))
    body = {"content": [{"type": "text", "text": piece} for piece in cleaned], "isError": is_error}
    if structured is not None:
        body["structuredContent"] = structured
    return body


def redact(value: str, secrets: list[str]) -> str:
    """Drop secret strings that are long enough to strip without eating ordinary text."""
    for secret in sorted({item for item in secrets if isinstance(item, str) and len(item) >= 8}, key=len, reverse=True):
        value = _token_pattern(secret).sub("[redacted]", value)
    return value


def _check(name: str, arguments: dict, namespace: str):
    if "context" in arguments:
        return text.CONTEXT_ARG
    for value in arguments.values():
        if isinstance(value, str) and _controls(value):
            return text.CONTROL
    if set(arguments) - _KEYS[name]:
        return text.EXTRA_KEY
    prepared = {"namespace": namespace}
    given = arguments.get("namespace", namespace)
    if not isinstance(given, str) or given != namespace:
        return text.NAMESPACE_MISMATCH
    if name in {"pods_get", "pods_log"}:
        pod = arguments.get("name")
        if not _pod_name(pod):
            return text.BAD_NAME
        prepared["name"] = pod
    if name in _LISTS and "limit" in arguments:
        limit = arguments["limit"]
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            return text.BAD_LIMIT
        prepared["limit"] = limit
    if name == "pods_log" and "tail" in arguments:
        tail = arguments["tail"]
        if isinstance(tail, bool) or not isinstance(tail, int) or not 1 <= tail <= 200:
            return text.BAD_TAIL
        prepared["tail"] = tail
    if name == "pods_log" and "previous" in arguments:
        previous = arguments["previous"]
        if not isinstance(previous, bool):
            return text.BAD_PREVIOUS
        prepared["previous"] = previous
    if name == "pods_log" and "container" in arguments:
        container = arguments["container"]
        if not _dns(container, 63):
            return text.BAD_CONTAINER
        prepared["container"] = container
    for key in ("labelSelector", "fieldSelector"):
        if key not in arguments:
            continue
        selector = arguments[key]
        if not isinstance(selector, str) or _controls(selector) or not _SELECTOR.fullmatch(selector):
            return text.BAD_SELECTOR
        prepared[key] = selector
    if "continue" in arguments:
        token = arguments["continue"]
        if not isinstance(token, str) or token == "" or len(token) > 512 or _controls(token):
            return text.BAD_CONTINUE
        prepared["continue"] = token
    return prepared


def _pod_name(value: object) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 253 and ".." not in value and _POD.fullmatch(value) is not None


def _dns(value: object, limit: int) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= limit and _NAME.fullmatch(value) is not None


def _controls(value: str) -> bool:
    for char in value:
        if ord(char) < 32 or char == "\x7f" or char in {"\u2028", "\u2029"}:
            return True
    return False


def _page(body: str, structured, name: str) -> str:
    if _CONTINUE.search(body) or _structured_continue(structured):
        return text.PAGE + "\n"
    items = _items(body, structured)
    if items is None:
        return ""
    if len(items) == 0:
        line = text.ZERO_PODS if name == "pods_list_in_namespace" else text.ZERO_EVENTS
        return line + "\n"
    return ""


def _items(body: str, structured):
    if isinstance(structured, list):
        return structured
    if isinstance(structured, dict) and isinstance(structured.get("items"), list) and "continue" not in structured:
        return structured["items"]
    if (
        re.search(r"(?m)^items:\s*\[\]\s*$", body)
        and not _CONTINUE.search(body)
        and not re.search(r"(?m)^kind:\s*Pod\s*$", body)
        and not re.search(r"(?m)^kind:\s*Event\s*$", body)
    ):
        return []
    return None


def _structured_continue(structured) -> bool:
    return isinstance(structured, dict) and isinstance(structured.get("continue"), str) and structured["continue"] != ""


def _blocked_kind(body: str, structured) -> bool:
    found = _KIND_LINE.search(body)
    if found and found.group(1) in _KINDS:
        return True
    if isinstance(structured, dict) and structured.get("kind") in _KINDS:
        return True
    if isinstance(structured, list):
        return any(isinstance(item, dict) and item.get("kind") in _KINDS for item in structured)
    return False


def _failure(body: str) -> str:
    lowered = body.lower()
    if "resource not allowed" in lowered:
        return _add(body, text.DENIED_NEXT)
    if re.search(r"\bunauthorized\b", lowered):
        return _add(body, text.AUTH_NEXT)
    if re.search(r"\bforbidden\b", lowered):
        return _add(body, text.PERM_NEXT)
    if "connection refused" in lowered or "no such host" in lowered or "connection reset" in lowered:
        return _add(body, text.DOWN_NEXT)
    if "context deadline exceeded" in lowered or "i/o timeout" in lowered or "request timed out" in lowered:
        return _add(body, text.TIMEOUT_NEXT)
    if re.search(r"\bnot found\b", lowered):
        return _add(body, text.MISSING_NEXT)
    return body


def _add(body: str, sentence: str) -> str:
    if sentence in body:
        return body
    return body.rstrip() + "\n" + sentence


def _result(sentence: str, is_error: bool, secrets: list[str]) -> dict:
    return {"content": [{"type": "text", "text": redact(sentence, secrets)}], "isError": is_error}


def _token_pattern(secret: str) -> re.Pattern[str]:
    parts: list[str] = []
    for char in secret:
        raw = re.escape(char)
        quoted = urllib.parse.quote(char, safe="")
        if quoted == char:
            parts.append(raw)
            continue
        hexdigits = quoted[1:]
        classes = "".join(
            f"[{digit.lower()}{digit.upper()}]" if digit.isalpha() else re.escape(digit)
            for digit in hexdigits
        )
        parts.append(f"(?:{raw}|%{classes})")
    return re.compile("".join(parts))


def _scrub_obj(value, secrets: list[str]):
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, list):
        return [_scrub_obj(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_obj(item, secrets) for key, item in value.items()}
    return value


def _scrub_env_obj(value):
    if isinstance(value, list):
        return [_scrub_env_obj(item) for item in value]
    if not isinstance(value, dict):
        return value
    copied = {key: _scrub_env_obj(item) for key, item in value.items()}
    env = copied.get("env")
    if isinstance(env, list):
        cleaned = []
        for item in env:
            if isinstance(item, dict) and isinstance(item.get("name"), str) and _SECRET_NAME.search(item["name"]):
                item = dict(item)
                if isinstance(item.get("value"), str):
                    item["value"] = "[redacted]"
            cleaned.append(item)
        copied["env"] = cleaned
    return copied


def _scrub_applied_text(body: str) -> str:
    """Drop kubectl's copy of the submitted object. It repeats env values."""
    lines = body.splitlines(keepends=True)
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.lstrip(" \t")
        if stripped.startswith(_APPLIED + ":"):
            indent = len(line) - len(stripped)
            ending = "\n" if line.endswith("\n") else ""
            output.append(" " * indent + _APPLIED + ": [redacted]" + ending)
            index += 1
            while index < len(lines):
                nxt = lines[index]
                if nxt.strip() == "":
                    look = index + 1
                    while look < len(lines) and lines[look].strip() == "":
                        look += 1
                    if look >= len(lines):
                        break
                    nxt_indent = len(lines[look]) - len(lines[look].lstrip(" \t"))
                    if nxt_indent > indent:
                        index = look
                        continue
                    break
                if len(nxt) - len(nxt.lstrip(" \t")) > indent:
                    index += 1
                    continue
                break
            continue
        output.append(line)
        index += 1
    return "".join(output)


def _scrub_applied_obj(value):
    if isinstance(value, list):
        return [_scrub_applied_obj(item) for item in value]
    if not isinstance(value, dict):
        return value
    copied = {key: _scrub_applied_obj(item) for key, item in value.items()}
    annotations = copied.get("annotations")
    if isinstance(annotations, dict) and _APPLIED in annotations:
        annotations = dict(annotations)
        annotations[_APPLIED] = "[redacted]"
        copied["annotations"] = annotations
    return copied


def _scrub_json_env(body: str) -> str:
    name = r"[^\"\\]*(?:password|passwd|token|secret|api[-_]?key|credential|private[-_]?key|access[-_]?key)[^\"\\]*"
    patterns = (
        re.compile(rf'("name"\s*:\s*"{name}"\s*,\s*"value"\s*:\s*")([^"]*)(")', re.IGNORECASE),
        re.compile(rf'("value"\s*:\s*")([^"]*)("\s*,\s*"name"\s*:\s*"{name}")', re.IGNORECASE),
    )
    for pattern in patterns:
        body = pattern.sub(lambda match: match.group(1) + "[redacted]" + match.group(3), body)
    return body


def _scrub_env_text(body: str) -> str:
    lines = body.splitlines(keepends=True)
    output: list[str] = []
    index = 0
    while index < len(lines):
        match = re.match(r"^([ \t]*)- name: (.+?)\s*$", lines[index].rstrip("\n"))
        if match and _SECRET_NAME.search(match.group(2)) and index + 1 < len(lines):
            value = re.match(r"^([ \t]*)value: .*$", lines[index + 1].rstrip("\n"))
            if value and len(value.group(1)) > len(match.group(1)):
                ending = "\n" if lines[index + 1].endswith("\n") else ""
                output.append(lines[index] if lines[index].endswith("\n") else lines[index] + "\n")
                output.append(f"{value.group(1)}value: [redacted]{ending}")
                index += 2
                continue
        output.append(lines[index])
        index += 1
    return "".join(output)

