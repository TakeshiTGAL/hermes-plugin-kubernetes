"""Allowlist, pages, secrets, and failure sentences."""

from __future__ import annotations

import pytest

from bridge.gate import called, listed
from bridge.launch import dispatch
from bridge.text import (
    AUTH_NEXT,
    BAD_CONTINUE,
    BAD_LIMIT,
    BAD_NAME,
    BAD_TAIL,
    CONTEXT_ARG,
    CONTROL,
    DENIED_NEXT,
    DOWN_NEXT,
    EMPTY_UPSTREAM,
    EXTRA_KEY,
    METHOD,
    MISSING_NEXT,
    NAMESPACE_MISMATCH,
    PAGE,
    PERM_NEXT,
    PROMPTS,
    RESOURCES,
    TIMEOUT_NEXT,
    WITHHELD,
    ZERO_EVENTS,
    ZERO_PODS,
)
from tests.support import TOKEN

NAMESPACE = "shop"


def _exchange(text: str, *, error: bool = False, structured=None, sink: list | None = None):
    def exchange(name, arguments):
        if sink is not None:
            sink.append((name, arguments))
        body = {"content": [{"type": "text", "text": text}], "isError": error}
        if structured is not None:
            body["structuredContent"] = structured
        return body

    return exchange


def _boom(name, arguments):
    raise AssertionError("sent")


def test_list_pods_sends_the_namespace() -> None:
    sink: list = []
    result = called(
        "pods_list_in_namespace",
        {},
        _exchange("kind: PodList\nitems:\n- kind: Pod\n  metadata:\n    name: broken\n", structured=[{"kind": "Pod", "metadata": {"name": "broken"}}], sink=sink),
        NAMESPACE,
        [TOKEN],
    )
    assert sink == [("pods_list_in_namespace", {"namespace": "shop"})]
    assert result["isError"] is False
    assert "broken" in result["content"][0]["text"]
    assert ZERO_PODS not in result["content"][0]["text"]


def test_namespace_is_injected_when_omitted() -> None:
    sink: list = []
    called("events_list", {}, _exchange("items:\n- kind: Event\n", structured=[{"kind": "Event"}], sink=sink), NAMESPACE, [])
    assert sink[0][1]["namespace"] == "shop"


def test_list_empty_items_says_zero() -> None:
    result = called("pods_list_in_namespace", {}, _exchange("items: []\n", structured=[]), NAMESPACE, [])
    assert result["content"][0]["text"].startswith(ZERO_PODS)


def test_list_continue_is_not_zero() -> None:
    text = "# continue: tok\nitems: []\n"
    result = called("pods_list_in_namespace", {}, _exchange(text, structured={"items": [], "continue": "tok"}), NAMESPACE, [])
    body = result["content"][0]["text"]
    assert body.startswith(PAGE)
    assert ZERO_PODS not in body


def test_list_error_is_not_zero() -> None:
    result = called("pods_list_in_namespace", {}, _exchange("failed to list pods", error=True), NAMESPACE, [])
    assert ZERO_PODS not in result["content"][0]["text"]
    assert result["isError"] is True


def test_events_sends_the_namespace() -> None:
    sink: list = []
    called("events_list", {"namespace": "shop"}, _exchange("items:\n- kind: Event\n", structured=[{"kind": "Event"}], sink=sink), NAMESPACE, [])
    assert sink[0] == ("events_list", {"namespace": "shop"})


def test_events_zero_only_when_complete() -> None:
    result = called("events_list", {}, _exchange("items: []\n", structured=[]), NAMESPACE, [])
    assert result["content"][0]["text"].startswith(ZERO_EVENTS)


def test_events_page_is_not_the_whole_list() -> None:
    result = called("events_list", {}, _exchange("# continue: next\nitems: []\n", structured={"continue": "next", "items": []}), NAMESPACE, [])
    assert PAGE in result["content"][0]["text"]
    assert ZERO_EVENTS not in result["content"][0]["text"]


def test_get_sends_the_name() -> None:
    sink: list = []
    text = "kind: Pod\nmetadata:\n  name: Broken\nstatus:\n  phase: Weird\n"
    result = called("pods_get", {"name": "broken"}, _exchange(text, sink=sink), NAMESPACE, [])
    assert sink[0][1] == {"namespace": "shop", "name": "broken"}
    assert "Weird" in result["content"][0]["text"]
    assert "down" not in result["content"][0]["text"].lower()


def test_get_missing_name_is_refused() -> None:
    result = called("pods_get", {}, _boom, NAMESPACE, [])
    assert result["content"][0]["text"] == BAD_NAME


def test_get_unparsed_does_not_guess() -> None:
    result = called("pods_get", {"name": "broken"}, _exchange("not yaml at all"), NAMESPACE, [])
    assert result["content"][0]["text"] == "not yaml at all"
    assert ZERO_PODS not in result["content"][0]["text"]


def test_unknown_phase_adds_no_conclusion() -> None:
    text = "kind: Pod\nstatus:\n  phase: Weird\n"
    result = called("pods_get", {"name": "broken"}, _exchange(text), NAMESPACE, [])
    assert result["content"][0]["text"] == text


def test_log_sends_the_name() -> None:
    sink: list = []
    called("pods_log", {"name": "broken"}, _exchange("boom\n", sink=sink), NAMESPACE, [])
    assert sink[0][1]["name"] == "broken"
    assert sink[0][1]["namespace"] == "shop"


def test_tail_bounds() -> None:
    assert called("pods_log", {"name": "broken", "tail": 0}, _boom, NAMESPACE, [])["content"][0]["text"] == BAD_TAIL
    assert called("pods_log", {"name": "broken", "tail": 201}, _boom, NAMESPACE, [])["content"][0]["text"] == BAD_TAIL
    sink: list = []
    called("pods_log", {"name": "broken", "tail": 200}, _exchange("boom\n", sink=sink), NAMESPACE, [])
    assert sink[0][1]["tail"] == 200


def test_limit_bounds() -> None:
    assert called("pods_list_in_namespace", {"limit": 0}, _boom, NAMESPACE, [])["content"][0]["text"] == BAD_LIMIT
    assert called("pods_list_in_namespace", {"limit": 101}, _boom, NAMESPACE, [])["content"][0]["text"] == BAD_LIMIT
    assert called("pods_list_in_namespace", {"limit": True}, _boom, NAMESPACE, [])["content"][0]["text"] == BAD_LIMIT


def test_continue_token_bounds() -> None:
    assert called("events_list", {"continue": ""}, _boom, NAMESPACE, [])["content"][0]["text"] == BAD_CONTINUE
    assert called("events_list", {"continue": "x" * 513}, _boom, NAMESPACE, [])["content"][0]["text"] == BAD_CONTINUE


def test_namespace_mismatch_is_not_sent() -> None:
    result = called("pods_list_in_namespace", {"namespace": "other"}, _boom, NAMESPACE, [])
    assert result["content"][0]["text"] == NAMESPACE_MISMATCH


def test_context_argument_is_not_sent() -> None:
    result = called("pods_get", {"name": "broken", "context": "desk"}, _boom, NAMESPACE, [])
    assert result["content"][0]["text"] == CONTEXT_ARG


def test_extra_key_is_refused() -> None:
    result = called("pods_get", {"name": "broken", "image": "busybox"}, _boom, NAMESPACE, [])
    assert result["content"][0]["text"] == EXTRA_KEY


def test_control_char_is_refused() -> None:
    result = called("pods_list_in_namespace", {"labelSelector": "app=a\nb"}, _boom, NAMESPACE, [])
    assert result["content"][0]["text"] == CONTROL


def test_unicode_name_is_refused() -> None:
    result = called("pods_get", {"name": "ポッド"}, _boom, NAMESPACE, [])
    assert result["content"][0]["text"] == BAD_NAME


def test_log_html_comment_is_not_a_conclusion() -> None:
    text = "<!-- secret -->\nboom\n"
    result = called("pods_log", {"name": "broken"}, _exchange(text), NAMESPACE, [])
    assert result["content"][0]["text"] == text
    assert ZERO_PODS not in result["content"][0]["text"]


def test_log_word_adds_no_conclusion() -> None:
    text = "CrashLoopBackOff connection refused\n"
    result = called("pods_log", {"name": "broken"}, _exchange(text), NAMESPACE, [])
    assert result["content"][0]["text"] == text
    assert DOWN_NEXT not in result["content"][0]["text"]


def test_log_redacts_token() -> None:
    result = called("pods_log", {"name": "broken"}, _exchange(f"saw {TOKEN}\n"), NAMESPACE, [TOKEN])
    assert TOKEN not in result["content"][0]["text"]
    assert "[redacted]" in result["content"][0]["text"]


def test_token_is_stripped() -> None:
    result = called("events_list", {}, _exchange(f"msg {TOKEN}", structured=[]), NAMESPACE, [TOKEN])
    assert TOKEN not in result["content"][0]["text"]


def test_last_applied_annotation_is_redacted() -> None:
    text = (
        "kind: Pod\n"
        "metadata:\n"
        "  annotations:\n"
        "    kubectl.kubernetes.io/last-applied-configuration: |\n"
        '      {"containers":[{"env":[{"name":"DB_PASSWORD","value":"canary-env-secret"}]}]}\n'
        "spec:\n"
        "  containers:\n"
        "  - env:\n"
        "    - name: DB_PASSWORD\n"
        "      value: canary-env-secret\n"
    )
    result = called("pods_get", {"name": "broken"}, _exchange(text), NAMESPACE, [])
    body = result["content"][0]["text"]
    assert "canary-env-secret" not in body
    assert "kubectl.kubernetes.io/last-applied-configuration: [redacted]" in body
    assert "value: [redacted]" in body


def test_secret_env_value_is_redacted() -> None:
    text = "kind: Pod\nspec:\n  containers:\n  - env:\n    - name: DB_PASSWORD\n      value: canary-env-secret\n"
    result = called("pods_get", {"name": "broken"}, _exchange(text), NAMESPACE, [])
    body = result["content"][0]["text"]
    assert "canary-env-secret" not in body
    assert "value: [redacted]" in body
    assert "DB_PASSWORD" in body


def test_secret_kind_is_withheld() -> None:
    text = f"kind: Secret\ndata:\n  token: {TOKEN}\n"
    result = called("pods_get", {"name": "broken"}, _exchange(text), NAMESPACE, [TOKEN])
    assert result["content"][0]["text"] == WITHHELD
    assert TOKEN not in result["content"][0]["text"]
    assert "kind: Secret" not in result["content"][0]["text"]


def test_configmap_and_serviceaccount_are_withheld() -> None:
    for kind in ("ConfigMap", "ServiceAccount"):
        result = called("pods_get", {"name": "broken"}, _exchange(f"kind: {kind}\n"), NAMESPACE, [])
        assert result["content"][0]["text"] == WITHHELD


def test_token_not_in_error_text() -> None:
    result = called("nope", {"token": TOKEN}, _boom, NAMESPACE, [TOKEN])
    assert TOKEN not in result["content"][0]["text"]


def test_configuration_view_is_not_sent() -> None:
    result = called("configuration_view", {}, _boom, NAMESPACE, [TOKEN])
    assert "Nothing was sent" in result["content"][0]["text"]
    assert TOKEN not in result["content"][0]["text"]


def test_helm_and_writes_are_not_sent() -> None:
    for name in ("helm_install", "helm_uninstall", "pods_delete", "pods_exec", "pods_run", "resources_get", "resources_delete", "pods_list"):
        result = called(name, {}, _boom, NAMESPACE, [])
        assert result["isError"] is True
        assert "Nothing was sent" in result["content"][0]["text"]


def test_forbidden_is_not_an_empty_list() -> None:
    result = called("events_list", {}, _exchange('events is forbidden: User "reader" cannot list', error=True), NAMESPACE, [])
    body = result["content"][0]["text"]
    assert PERM_NEXT in body
    assert ZERO_EVENTS not in body


def test_denied_kind_is_not_an_empty_list() -> None:
    result = called("events_list", {}, _exchange("resource not allowed: /v1, Kind=Secret", error=True), NAMESPACE, [])
    assert DENIED_NEXT in result["content"][0]["text"]
    assert ZERO_EVENTS not in result["content"][0]["text"]


def test_connection_refused_names_the_next_step() -> None:
    result = called("pods_list_in_namespace", {}, _exchange("dial tcp 127.0.0.1:6444: connection refused", error=True), NAMESPACE, [])
    assert DOWN_NEXT in result["content"][0]["text"]
    assert ZERO_PODS not in result["content"][0]["text"]


def test_timeout_is_not_called_connection_refused() -> None:
    result = called("pods_get", {"name": "broken"}, _exchange("context deadline exceeded", error=True), NAMESPACE, [])
    body = result["content"][0]["text"]
    assert TIMEOUT_NEXT in body
    assert DOWN_NEXT not in body
    result = called("pods_get", {"name": "broken"}, _exchange("i/o timeout", error=True), NAMESPACE, [])
    assert TIMEOUT_NEXT in result["content"][0]["text"]
    assert DOWN_NEXT not in result["content"][0]["text"]


def test_unauthorized_does_not_print_the_token() -> None:
    result = called("pods_get", {"name": "broken"}, _exchange(f"Unauthorized {TOKEN}", error=True), NAMESPACE, [TOKEN])
    body = result["content"][0]["text"]
    assert AUTH_NEXT in body
    assert TOKEN not in body


def test_not_found_is_not_zero_pods() -> None:
    result = called("pods_get", {"name": "broken"}, _exchange('pods "broken" not found', error=True), NAMESPACE, [])
    assert MISSING_NEXT in result["content"][0]["text"]
    assert ZERO_PODS not in result["content"][0]["text"]


def test_misspelled_forbidden_adds_no_conclusion() -> None:
    result = called("events_list", {}, _exchange("forbiden", error=True), NAMESPACE, [])
    assert result["content"][0]["text"] == "forbiden"
    assert PERM_NEXT not in result["content"][0]["text"]


def test_empty_upstream_is_not_zero() -> None:
    result = called("pods_list_in_namespace", {}, lambda name, arguments: {"content": [], "isError": False}, NAMESPACE, [])
    assert result["content"][0]["text"] == EMPTY_UPSTREAM
    assert ZERO_PODS not in result["content"][0]["text"]


def test_tools_keep_four_and_mark_readonly() -> None:
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "tools": [
                {"name": "configuration_view", "description": "kubeconfig", "annotations": {"readOnlyHint": True}},
                {"name": "helm_install", "description": "install"},
                {"name": "pods_list_in_namespace", "description": "List pods.", "annotations": {}},
                {"name": "pods_get", "description": "Get a pod.", "annotations": {"readOnlyHint": True}},
                {"name": "events_list", "description": "List events.", "annotations": {"readOnlyHint": True}},
                {"name": "pods_log", "description": "Get logs.", "annotations": {"readOnlyHint": True}},
                {"name": "resources_get", "description": "Get any resource.", "annotations": {"readOnlyHint": True}},
            ]
        },
    }
    tools = listed(message)["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["pods_list_in_namespace", "pods_get", "events_list", "pods_log"]
    assert all(tool["annotations"]["readOnlyHint"] is True for tool in tools)


def test_descriptions_include_ordinary_words() -> None:
    message = {
        "result": {
            "tools": [
                {"name": name, "description": "Base.", "annotations": {"readOnlyHint": True}}
                for name in ("pods_list_in_namespace", "pods_get", "events_list", "pods_log")
            ]
        }
    }
    text = " ".join(tool["description"] for tool in listed(message)["result"]["tools"])
    assert "what is down in the namespace" in text
    assert "list pods and their status" in text
    assert "why is this pod restarting" in text


def test_read_does_not_write(tmp_path) -> None:
    marker = tmp_path / "marker"
    marker.write_text("same", encoding="utf-8")
    before = marker.read_bytes()
    called("pods_log", {"name": "broken"}, _exchange("boom\n"), NAMESPACE, [])
    assert marker.read_bytes() == before
    assert list(tmp_path.iterdir()) == [marker]


def test_ping_is_forwarded() -> None:
    assert dispatch({"jsonrpc": "2.0", "id": 1, "method": "ping"}, object(), NAMESPACE, []) is None


def test_unknown_method_is_not_sent() -> None:
    reply = dispatch({"jsonrpc": "2.0", "id": 7, "method": "roots/list"}, object(), NAMESPACE, [])
    assert reply["error"]["message"] == METHOD


def test_resources_are_not_sent() -> None:
    reply = dispatch({"jsonrpc": "2.0", "id": 3, "method": "resources/list"}, object(), NAMESPACE, [])
    assert reply["error"]["message"] == RESOURCES


def test_prompts_are_not_sent() -> None:
    reply = dispatch({"jsonrpc": "2.0", "id": 4, "method": "prompts/list"}, object(), NAMESPACE, [])
    assert reply["error"]["message"] == PROMPTS


def test_notification_is_not_sent() -> None:
    reply = dispatch({"jsonrpc": "2.0", "method": "notifications/message"}, object(), NAMESPACE, [])
    assert reply == {"_drop": True}


def test_initialize_replaces_instructions() -> None:
    class Upstream:
        def forward(self, message):
            return {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "instructions": "manage the cluster",
                    "capabilities": {"tools": {}, "prompts": {}, "resources": {}},
                },
            }

    reply = dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, Upstream(), NAMESPACE, [])
    result = reply["result"]
    assert "one namespace" in result["instructions"]
    assert "prompts" not in result["capabilities"]
    assert "resources" not in result["capabilities"]


def test_short_token_is_left_in_place() -> None:
    result = called("pods_log", {"name": "broken"}, _exchange("about\n"), NAMESPACE, ["ab"])
    assert result["content"][0]["text"] == "about\n"
