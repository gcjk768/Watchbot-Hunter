"""/watchask: its own daily budget, answers only from the data, rejects invented figures, and usage text without a question."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from watchbot import ai, claude, ratelimit, serve

CTX = "Rolex Submariner Date 126610LN retail S$15,950. Cheapest Singapore listing S$17,200."


def test_ask_has_its_own_budget(lim):
    assert lim.daily_cap("ask") == 10
    for _ in range(10):
        lim.acquire_ask()
    assert lim.left("ask") == 0
    lim.acquire_claude()          # the scheduled jobs are not affected
    with pytest.raises(ratelimit.BudgetExhausted):
        lim.acquire_ask()


def test_claude_call_counts_against_the_ask_bucket(s, lim):
    out = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "", "structured_output": {"answer": "ok"}})
    run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, out, "")   # noqa: E731
    system, schema = ai._prompt(s, "ask")
    claude.call(s, lim, system, {"question": "q", "data": "d"}, schema, bucket="ask", runner=run)
    assert lim.used("ask") == 1 and lim.used("claude") == 0


def test_ask_answers_from_the_data(s, db, lim, monkeypatch):
    monkeypatch.setattr(ai.claude, "call", lambda *a, **k: {"answer": "The cheapest Submariner 126610LN listing is S$17,200, above the S$15,950 retail."})
    assert "17,200" in ai.ask(s, db, lim, "which Submariner is cheapest?", CTX)


def test_ask_rejects_an_invented_figure(s, db, lim, monkeypatch):
    monkeypatch.setattr(ai.claude, "call", lambda *a, **k: {"answer": "It will be worth S$99,999 next year."})
    with pytest.raises(ValueError, match="rule check"):
        ai.ask(s, db, lim, "what will it be worth?", CTX)


def test_ask_rejects_a_promise(s, db, lim, monkeypatch):
    monkeypatch.setattr(ai.claude, "call", lambda *a, **k: {"answer": "This is a guaranteed profit."})
    with pytest.raises(ValueError, match="promise"):
        ai.ask(s, db, lim, "is it a good deal?", CTX)


def test_command_without_a_question_gives_usage(s):
    bot = serve.Bot(SimpleNamespace(s=s))
    out = bot.cmd_watchask("")
    assert len(out) == 1 and "/watchask" in out[0] and "example" in out[0]
