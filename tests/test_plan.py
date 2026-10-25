"""cluster.json, the generated TOML, and the child environment."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bridge.kube import judge, parse_config
from bridge.plan import plan, toml_text
from bridge.safe_write import WriteRefused, write_bytes
from bridge.text import (
    BAD_KUBECONFIG,
    NOT_FOUND,
    PROD_REFUSED,
    PUBLIC_HOST,
    UNKNOWN_SETTING,
    USERINFO,
    WRITE_HARDLINK,
    WRITE_OUTSIDE,
    WRITE_SYMLINK,
)
from tests.support import TOKEN, kube_text, write_home

PROXIES = ["http_proxy", "https_proxy", "all_proxy", "ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY"]


def test_kubeconfig_round_trip() -> None:
    data = parse_config(kube_text())
    judged = judge(data, allow_production=False)
    assert judged["ok"] is True
    assert judged["context"] == "desk"
    assert judged["host"] == "127.0.0.1"
    assert TOKEN in judged["secrets"]


def test_plan_local_host(tmp_path) -> None:
    work = write_home(tmp_path)
    result = plan(work)
    assert result["ok"] is True
    assert result["namespace"] == "shop"
    assert result["env"]["KUBECONFIG"] == str(tmp_path / "kube.config")
    assert result["env"]["HOME"] == str(tmp_path)
    text = (work / "server.toml").read_text(encoding="utf-8")
    assert "read_only = true" in text
    assert 'toolsets = ["core"]' in text
    assert "pods_log" in text
    assert "configuration_view" not in text
    assert "helm" not in text
    assert 'kind = "Secret"' in text
    assert 'kind = "ConfigMap"' in text
    assert 'kind = "ServiceAccount"' in text
    assert "port" not in text.split("exporter")[0]


def test_toml_locks_the_server(tmp_path) -> None:
    work = write_home(tmp_path)
    plan(work)
    text = (work / "server.toml").read_text(encoding="utf-8")
    assert 'enabled_tools = ["pods_list_in_namespace", "pods_get", "events_list", "pods_log"]' in text
    assert 'cluster_provider_strategy = "kubeconfig"' in text
    assert "enabled = false" in text


def test_toml_has_no_token(tmp_path) -> None:
    work = write_home(tmp_path)
    plan(work)
    assert TOKEN not in (work / "server.toml").read_text(encoding="utf-8")


def test_toml_quotes_the_path() -> None:
    quoted = toml_text('/tmp/kube "config"')
    assert json.dumps('/tmp/kube "config"') in quoted


def test_stdio_has_no_port(tmp_path) -> None:
    work = write_home(tmp_path)
    plan(work)
    text = (work / "server.toml").read_text(encoding="utf-8")
    assert "\nport " not in text and not text.startswith("port ")
    assert 'log_file = "stderr"' in text


def test_namespace_bounds(tmp_path) -> None:
    work = write_home(tmp_path, namespace="Bad_Name")
    # write_home already wrote Bad_Name; plan should refuse
    result = plan(work)
    assert result["ok"] is False
    assert "Nothing was sent" in result["message"]


def test_plan_does_not_resolve_names(tmp_path, monkeypatch) -> None:
    def explode(*args, **kwargs):
        raise AssertionError("resolved")

    monkeypatch.setattr("socket.getaddrinfo", explode)
    work = write_home(tmp_path, server="https://shop.local:6443")
    result = plan(work)
    assert result["ok"] is True
    assert result["server"] == "https://shop.local:6443"


def test_host_with_userinfo_refused(tmp_path) -> None:
    work = write_home(tmp_path, server="https://user:secret@127.0.0.1:6444")
    result = plan(work)
    assert result["message"] == USERINFO
    assert "secret" not in result["message"]


def test_public_host_is_refused(tmp_path) -> None:
    work = write_home(tmp_path, server="https://example.com:6443")
    assert plan(work)["message"] == PUBLIC_HOST


def test_production_context_is_refused(tmp_path) -> None:
    work = write_home(tmp_path, context="prod")
    result = plan(work)
    assert result["message"] == PROD_REFUSED
    assert TOKEN not in result["message"]


def test_allow_production_still_refuses_a_public_host(tmp_path) -> None:
    work = write_home(tmp_path, server="https://example.com", context="prod", allow=True)
    assert plan(work)["message"] == PUBLIC_HOST


def test_private_address_is_allowed(tmp_path) -> None:
    work = write_home(tmp_path, server="https://10.0.0.8:6443")
    assert plan(work)["ok"] is True
    work = write_home(tmp_path, server="https://192.168.1.9:6443")
    assert plan(work)["ok"] is True


def test_link_local_is_refused(tmp_path) -> None:
    work = write_home(tmp_path, server="https://169.254.169.254")
    assert plan(work)["message"] == PUBLIC_HOST


@pytest.mark.parametrize(
    "server",
    [
        "https://169.254.169.254",
        "https://0.0.0.0:6443",
        "https://192.0.2.1:6443",
        "https://198.51.100.1:6443",
        "https://203.0.113.1:6443",
        "https://198.18.0.1:6443",
        "https://[::ffff:192.0.2.1]:6443",
    ],
    ids=["link_local", "unspecified", "doc_192_0_2", "doc_198_51_100", "doc_203_0_113", "benchmark", "v4_mapped_doc"],
)
def test_nonlocal_hosts_are_refused(tmp_path, server: str) -> None:
    result = plan(write_home(tmp_path, server=server))
    assert result["ok"] is False
    assert result["message"] == PUBLIC_HOST
    assert "Nothing was sent" in result["message"]


@pytest.mark.parametrize(
    "server",
    [
        "https://127.0.0.1:6443",
        "https://10.1.2.3:6443",
        "https://172.16.0.1:6443",
        "https://192.168.1.9:6443",
        "https://localhost:6443",
        "https://[::1]:6443",
    ],
    ids=["loopback_v4", "rfc1918_10", "rfc1918_172", "rfc1918_192", "localhost", "loopback_v6"],
)
def test_local_destinations_are_allowed(tmp_path, server: str) -> None:
    result = plan(write_home(tmp_path, server=server))
    assert result["ok"] is True, result


@pytest.mark.parametrize(
    "server",
    [
        "https://shop.local:6443",
        "https://host.docker.internal:6443",
        "https://kubernetes.docker.internal:6443",
    ],
    ids=["dot_local", "docker_host", "docker_kubernetes"],
)
def test_documented_local_names_stay_allowed(tmp_path, server: str) -> None:
    result = plan(write_home(tmp_path, server=server))
    assert result["ok"] is True, result


def test_parent_kubeconfig_does_not_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KUBECONFIG", "/tmp/other-kubeconfig")
    work = write_home(tmp_path)
    result = plan(work)
    assert result["env"]["KUBECONFIG"] == str(tmp_path / "kube.config")
    assert result["env"]["KUBECONFIG"] != "/tmp/other-kubeconfig"


def test_incluster_env_not_passed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
    work = write_home(tmp_path)
    env = plan(work)["env"]
    assert "KUBERNETES_SERVICE_HOST" not in env
    assert "KUBERNETES_SERVICE_PORT" not in env


def test_proxy_is_not_passed(tmp_path, monkeypatch) -> None:
    for name in PROXIES:
        monkeypatch.setenv(name, "http://proxy.example:8080")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.example:4317")
    work = write_home(tmp_path)
    env = plan(work)["env"]
    for name in PROXIES:
        assert name not in env
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in env


def test_dotenv_is_not_loaded(tmp_path, monkeypatch) -> None:
    work = write_home(tmp_path)
    (work / ".env").write_text("HTTP_PROXY=http://proxy.example:8080\nTOKEN=from-dotenv-should-not-pass\n", encoding="utf-8")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    env = plan(work)["env"]
    assert "HTTP_PROXY" not in env
    assert "from-dotenv-should-not-pass" not in json.dumps(env)


def test_missing_config(tmp_path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    result = plan(work)
    assert result["message"] == NOT_FOUND
    assert result["error_type"] == "not_configured"
    assert result["ok"] is False


def test_unknown_setting(tmp_path) -> None:
    work = write_home(tmp_path)
    path = tmp_path / "cluster.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["token"] = TOKEN
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = plan(work)
    assert result["message"] == UNKNOWN_SETTING
    assert result["error_type"] == "refused"
    assert TOKEN not in result["message"]


def test_relative_kubeconfig_is_refused(tmp_path) -> None:
    work = write_home(tmp_path)
    path = tmp_path / "cluster.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["kubeconfig"] = "kube.config"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert plan(work)["message"] == BAD_KUBECONFIG


def test_symlink_toml_refuses(tmp_path) -> None:
    work = write_home(tmp_path)
    target = tmp_path / "elsewhere"
    target.write_text("keep", encoding="utf-8")
    (work / "server.toml").symlink_to(target)
    result = plan(work)
    assert result["message"] == WRITE_SYMLINK
    assert target.read_text(encoding="utf-8") == "keep"


def test_hardlink_toml_refuses(tmp_path) -> None:
    work = write_home(tmp_path)
    first = work / "server.toml"
    first.write_text("keep", encoding="utf-8")
    other = tmp_path / "alias"
    os.link(first, other)
    result = plan(work)
    assert result["message"] == WRITE_HARDLINK
    assert first.read_text(encoding="utf-8") == "keep"
    assert other.read_text(encoding="utf-8") == "keep"


def test_dotdot_refuses(tmp_path) -> None:
    with pytest.raises(WriteRefused) as caught:
        write_bytes(tmp_path, "../outside.txt", b"no", cut_hardlink=False)
    assert caught.value.kind == "outside"


def test_outside_toml_refuses(tmp_path) -> None:
    with pytest.raises(WriteRefused) as caught:
        write_bytes(tmp_path, "/etc/passwd", b"no", cut_hardlink=False)
    assert caught.value.kind == "outside"
    assert WRITE_OUTSIDE


def test_second_context_that_is_production_is_refused(tmp_path) -> None:
    extra = (
        "- name: prod\n"
        "  context:\n"
        "    cluster: desk\n"
        "    user: desk\n"
    )
    work = write_home(tmp_path, extra_context=extra)
    assert plan(work)["message"] == PROD_REFUSED


def test_any_public_cluster_is_refused(tmp_path) -> None:
    text = kube_text() + (
        "clusters:\n"
    )
    # The parser refuses duplicate keys, so build a file with two clusters directly.
    raw = """apiVersion: v1
kind: Config
current-context: desk
clusters:
- name: desk
  cluster:
    server: https://127.0.0.1:6444
- name: cloud
  cluster:
    server: https://example.com
contexts:
- name: desk
  context:
    cluster: desk
    user: desk
users:
- name: desk
  user:
    token: token-value-0123456789
preferences: {}
"""
    data = parse_config(raw)
    judged = judge(data, allow_production=True)
    assert judged["message"] == PUBLIC_HOST
    assert text
