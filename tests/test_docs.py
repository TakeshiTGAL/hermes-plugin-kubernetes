"""README and the skill say the same things the bridge does."""

import json
from pathlib import Path

from bridge import text
from bridge.gate import public_texts

ROOT = Path(__file__).resolve().parents[1]

SENTENCES = [
    text.NOT_FOUND,
    text.UNREADABLE,
    text.UNKNOWN_SETTING,
    text.BAD_NAMESPACE,
    text.BAD_ALLOW,
    text.BAD_KUBECONFIG,
    text.KUBECONFIG_UNREADABLE,
    text.PROD_REFUSED,
    text.PUBLIC_HOST,
    text.USERINFO,
    text.WORK_MISSING,
    text.BINARY_UNSUPPORTED,
    text.BINARY_MISMATCH,
    text.BINARY_SYMLINK,
    text.BINARY_DOWNLOAD,
    text.BINARY_WRITE,
    text.BINARY_UNREADABLE,
    text.BINARY_NOT_FOUND,
    text.BINARY_EXECUTE,
    text.BINARY_START,
    text.DOWNLOADING_EXAMPLE,
    text.WRITE_SYMLINK,
    text.WRITE_HARDLINK,
    text.WRITE_OUTSIDE,
    text.WRITE_OTHER,
    text.NAMESPACE_MISMATCH,
    text.CONTEXT_ARG,
    text.BAD_ARGUMENTS,
    text.BAD_PARAMS,
    text.EXTRA_KEY,
    text.BAD_NAME,
    text.BAD_LIMIT,
    text.BAD_TAIL,
    text.BAD_SELECTOR,
    text.BAD_CONTINUE,
    text.BAD_CONTAINER,
    text.BAD_PREVIOUS,
    text.CONTROL,
    text.WITHHELD,
    text.AUTH_NEXT,
    text.PERM_NEXT,
    text.DENIED_NEXT,
    text.DOWN_NEXT,
    text.TIMEOUT_NEXT,
    text.MISSING_NEXT,
    text.EMPTY_UPSTREAM,
    text.NO_OBJECT,
    text.PROMPTS,
    text.RESOURCES,
    text.METHOD,
    text.ZERO_PODS,
    text.ZERO_EVENTS,
    text.PAGE,
]


def test_public_text_is_english() -> None:
    found = []
    for path, body in public_texts(ROOT):
        if any("\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff" for char in body):
            found.append(path.name)
    assert found == []


def test_skill_names_the_namespace_before_the_call() -> None:
    skill = (ROOT / "skills" / "kubernetes" / "SKILL.md").read_text(encoding="utf-8")
    body = skill.split("---", 2)[-1]
    assert "Before you call" in body
    assert "namespace" in body
    assert "only reads" in body
    assert "tool_search" in body
    assert "web_search" in body
    assert "before web_search" in body or body.index("tool_search") < body.index("web_search")


def test_readme_shows_the_card() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "readOnlyHint" in readme
    assert "does not show an approval card" in readme
    assert "does not include the token" in readme
    assert "names the tool and the server" in readme
    assert "does not change the cluster" in readme
    assert "hermes plugins disable kubernetes" in readme
    assert "hermes tools disable mcp-kubernetes" in readme
    assert "gateway" in readme


def test_three_phrases() -> None:
    skill = (ROOT / "skills" / "kubernetes" / "SKILL.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for phrase in (
        "what is down in the namespace",
        "list pods and their status",
        "why is this pod restarting",
    ):
        assert phrase in skill
        assert phrase in readme


def test_failure_sentences_are_in_the_readme() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    missing = [sentence for sentence in SENTENCES if sentence not in readme]
    assert missing == []


def test_readme_records_launcher_results() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    skill = (ROOT / "skills" / "kubernetes" / "SKILL.md").read_text(encoding="utf-8")
    assert "error_type" not in skill
    pairs = [
        ("not_configured", text.NOT_FOUND),
        ("unreadable", text.UNREADABLE),
        ("refused", text.UNKNOWN_SETTING),
    ]
    for kind, message in pairs:
        blob = json.dumps({"ok": False, "error_type": kind, "message": message})
        assert blob in readme
    assert "does not print those objects" in readme


def test_readme_steps() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "hermes plugins install --no-enable file:///absolute/path/to/hermes-plugin-kubernetes" in readme
    assert "hermes plugins enable kubernetes" in readme
    assert 'mkdir -p "$HERMES_HOME/plugin-data/agent-plugin-kubernetes-94abcb2d/work"' in readme
    assert "agent-plugin-kubernetes-94abcb2d/cluster.json" in readme
    assert "Four steps above are the whole setup. Then ask." in readme
    assert "what is down in the namespace" in readme
    assert "verbs: [\"get\", \"list\"]" in readme
    assert "pip install" not in readme
