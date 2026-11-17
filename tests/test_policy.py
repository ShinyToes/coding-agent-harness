"""Policy：验收第 4 条的四组判定 + 审批降级 + session 白名单。"""

from __future__ import annotations

from _helpers import ctx_for, new_session, open_store, settings, tool_call
from harness.policy import Policy, deny_approver, make_approver


def decide(tmp_path, name: str, **args):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    ctx = ctx_for(store, cfg, sid)
    return Policy(cfg).decide(tool_call(name, **args), ctx)


def test_bash_rm_rf_root_is_denied(tmp_path):
    ruling = decide(tmp_path, "bash", command="rm -rf /")
    assert ruling.decision == "deny"
    assert ruling.rule == "cmd.destructive"


def test_bash_ls_is_allowed(tmp_path):
    ruling = decide(tmp_path, "bash", command="ls -la")
    assert ruling.decision == "allow"
    assert ruling.rule == "cmd.safe_readonly"


def test_bash_cat_env_asks_instead_of_bypassing_sensitive_rule(tmp_path):
    """回归 B-008：只读白名单不能绕过敏感路径检查。"""
    ruling = decide(tmp_path, "bash", command="cat .env")
    assert ruling.decision == "ask"
    assert ruling.rule == "path.sensitive"


def test_bash_cat_ssh_key_asks(tmp_path):
    ruling = decide(tmp_path, "bash", command="type .ssh/id_rsa")
    assert ruling.decision == "ask"
    assert ruling.rule == "path.sensitive"


def test_read_outside_workspace_is_denied(tmp_path):
    ruling = decide(tmp_path, "read", path="/etc/passwd")
    assert ruling.decision == "deny"
    assert ruling.rule == "path.read_outside"


def test_read_outside_can_be_enabled(tmp_path):
    cfg = settings(tmp_path, policy__allow_read_outside=True)
    store = open_store(tmp_path)
    ctx = ctx_for(store, cfg, new_session(store))
    ruling = Policy(cfg).decide(tool_call("read", path="/etc/hosts"), ctx)
    assert ruling.decision == "allow"
    assert ruling.rule == "read.default"


def test_read_inside_workspace_still_allowed(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    ruling = decide(tmp_path, "read", path="a.py")
    assert ruling.decision == "allow"


def test_bash_curl_asks(tmp_path):
    ruling = decide(tmp_path, "bash", command="curl -s https://example.com")
    assert ruling.decision == "ask"
    assert ruling.rule == "cmd.network"


def test_write_outside_workspace_is_denied(tmp_path):
    ruling = decide(tmp_path, "write", path="/etc/passwd", content="x")
    assert ruling.decision == "deny"
    assert ruling.rule == "path.escape"


def test_write_inside_workspace_asks_when_overwriting(tmp_path):
    (tmp_path / "notes.txt").write_text("old")
    ruling = decide(tmp_path, "write", path="notes.txt", content="new")
    assert ruling.decision == "ask"
    assert ruling.rule == "fs.overwrite"


def test_new_file_write_falls_through_to_effect_default(tmp_path):
    ruling = decide(tmp_path, "write", path="fresh.txt", content="new")
    assert ruling.decision == "ask"
    assert ruling.rule == "effect.default"


def test_sensitive_path_asks(tmp_path):
    ruling = decide(tmp_path, "read", path=".env")
    assert ruling.decision == "ask"
    assert ruling.rule == "path.sensitive"


def test_chained_command_asks(tmp_path):
    ruling = decide(tmp_path, "bash", command="ls && rm -rf build")
    assert ruling.decision == "ask"
    assert ruling.rule == "cmd.chained"


def test_git_write_asks(tmp_path):
    ruling = decide(tmp_path, "bash", command="git commit -m wip")
    assert ruling.decision == "ask"
    assert ruling.rule == "cmd.git_write"


def test_read_default_allow(tmp_path):
    ruling = decide(tmp_path, "read", path="src.py")
    assert ruling.decision == "allow"


def test_allowlist_shortcut(tmp_path):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    ctx = ctx_for(store, cfg, new_session(store))
    policy = Policy(cfg)
    call = tool_call("write", path="out.txt", content="x")
    policy.remember(call)
    ruling = policy.decide(call, ctx)
    assert ruling.decision == "allow"
    assert ruling.rule == "session.allowlist"


def test_non_interactive_approver_denies(tmp_path):
    cfg = settings(tmp_path, policy__approval_mode="deny")
    approver = make_approver(cfg)
    assert approver(tool_call("bash", command="curl x"), None) == "deny"  # type: ignore[arg-type]
    assert deny_approver(tool_call("bash", command="x"), None) == "deny"  # type: ignore[arg-type]


def test_unknown_tool_is_denied(tmp_path):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    ctx = ctx_for(store, cfg, new_session(store))
    ruling = Policy(cfg).decide(tool_call("rm_rf", path="."), ctx)
    assert ruling.decision == "deny"
    assert ruling.rule == "tool.unknown"
