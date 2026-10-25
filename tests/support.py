"""Fixtures. The token is invented for these tests."""

from __future__ import annotations

import json
from pathlib import Path

TOKEN = "token-value-0123456789"


def kube_text(
    server: str = "https://127.0.0.1:6444",
    context: str = "desk",
    token: str = TOKEN,
    extra_context: str = "",
) -> str:
    return (
        "apiVersion: v1\n"
        "kind: Config\n"
        f"current-context: {context}\n"
        "clusters:\n"
        f"- name: {context}\n"
        "  cluster:\n"
        f"    server: {server}\n"
        "    certificate-authority-data: QUJDREVGR0hJSktM\n"
        "contexts:\n"
        f"- name: {context}\n"
        "  context:\n"
        f"    cluster: {context}\n"
        f"    user: {context}\n"
        f"{extra_context}"
        "users:\n"
        f"- name: {context}\n"
        "  user:\n"
        f"    token: {token}\n"
        "preferences: {}\n"
    )


def write_home(root: Path, server: str = "https://127.0.0.1:6444", context: str = "desk", namespace: str = "shop", allow: bool = False, token: str = TOKEN, extra_context: str = "") -> Path:
    kube = root / "kube.config"
    kube.write_text(kube_text(server, context, token, extra_context), encoding="utf-8")
    work = root / "work"
    work.mkdir(exist_ok=True)
    payload = {"kubeconfig": str(kube), "namespace": namespace, "allow_production": allow}
    (root / "cluster.json").write_text(json.dumps(payload), encoding="utf-8")
    return work
