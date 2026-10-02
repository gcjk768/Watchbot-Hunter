import json
import subprocess

import pytest

from watchbot import claude
from watchbot.textcheck import problems, undash

SCHEMA = {"type": "object"}


def ok_out(obj):
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "", "structured_output": obj})


def runner_seq(*outs):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        out = outs[min(len(calls), len(outs)) - 1]
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return run, calls


def test_parse_success():
    assert claude.parse_result(ok_out({"a": 1})) == {"a": 1}


def test_parse_error_result():
    with pytest.raises(claude.ClaudeFailure) as e:
        claude.parse_result(json.dumps({"subtype": "error_max_turns", "is_error": True, "result": ""}))
    assert e.value.kind == "error"


@pytest.mark.parametrize("text,kind", [
    ("You've hit your Sonnet limit · resets 3pm (Asia/Singapore)", "model_limit"),
    ("You've hit your Opus limit", "model_limit"),
    ("You've hit your session limit · resets 11pm", "account_limit"),
    ("You've hit your weekly limit · resets Oct 6, 9am", "account_limit"),
])
def test_usage_limits(text, kind):
    with pytest.raises(claude.ClaudeFailure) as e:
        claude.parse_result(json.dumps({"subtype": "success", "is_error": True, "result": text}))
    assert e.value.kind == kind
    if "resets" in text:
        assert e.value.resets


def test_flags_for_writing_disallow_every_tool(s):
    s.claude.fallback_model = "sonnet"   # config runs haiku for both; a distinct fallback must be passed
    cmd = claude.build_cmd(s, "sys.md", SCHEMA, web=False, max_turns=3)
    for flag in ("--output-format", "--json-schema", "--permission-mode", "--permission-prompts", "--strict-mcp-config",
                 "--no-session-persistence", "--fallback-model"):
        assert flag in cmd
    assert cmd[cmd.index("--model") + 1] == s.claude.model and cmd[cmd.index("--fallback-model") + 1] == s.claude.fallback_model

    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk" and cmd[cmd.index("--permission-prompts") + 1] == "none"
    assert cmd[cmd.index("--tools") + 1] == "" and "WebSearch" in cmd[cmd.index("--disallowedTools") + 1]
    assert "--bare" not in cmd and "--allowedTools" not in cmd


def test_flags_for_discovery(s):
    cmd = claude.build_cmd(s, "sys.md", SCHEMA, web=True, max_turns=40)
    assert cmd[cmd.index("--allowedTools") + 1] == "WebSearch,WebFetch"
    assert cmd[cmd.index("--disallowedTools") + 1] == "Bash,Edit,Write,Read,Glob,Grep,Agent,NotebookEdit"


def test_model_limit_retries_once_on_fallback(s, lim):
    limit = json.dumps({"subtype": "success", "is_error": True, "result": "You've hit your Sonnet limit"})
    run, calls = runner_seq(limit, ok_out({"x": 1}))
    assert claude.call(s, lim, "sys.md", {"a": 1}, SCHEMA, runner=run) == {"x": 1}
    assert calls[1][calls[1].index("--model") + 1] == s.claude.fallback_model and len(calls) == 2


def test_account_limit_is_not_retried(s, lim):
    limit = json.dumps({"subtype": "success", "is_error": True, "result": "You've hit your weekly limit · resets Mon 9am"})
    run, calls = runner_seq(limit)
    with pytest.raises(claude.ClaudeFailure) as e:
        claude.call(s, lim, "sys.md", {}, SCHEMA, runner=run)
    assert e.value.kind == "account_limit" and len(calls) == 1


def test_daily_cap(s, lim):
    run, calls = runner_seq(ok_out({}))
    for _ in range(6):
        claude.call(s, lim, "sys.md", {}, SCHEMA, runner=run)
    with pytest.raises(claude.ClaudeFailure) as e:
        claude.call(s, lim, "sys.md", {}, SCHEMA, runner=run)
    assert e.value.kind == "budget" and len(calls) == 6


def test_text_validation():
    nums = ["14,800", "8.2", "21"]
    assert problems("Market is S$14,800 with a 8.2% margin, about 21 days", nums) == []
    assert "number 15000 not in input" in problems("worth 15000", nums)
    assert problems("see https://evil.example", nums, urls=[]) == ["url https://evil.example not given"]
    assert problems("a fine watch — really", nums) == ["dash"]
    assert any("promise" in p for p in problems("Guaranteed profit", nums))
    assert problems("easy profit", nums)


def test_undash():
    assert undash("Rolex — a classic - nice") == "Rolex, a classic, nice"

def test_no_fallback_flag_when_it_equals_the_model(s):
    cmd = claude.build_cmd(s, "sys.md", SCHEMA, web=True, max_turns=3, model=s.claude.fallback_model)
    assert "--fallback-model" not in cmd
