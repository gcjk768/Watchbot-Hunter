"""A lesson may quote the reference numbers and model names the bot gave it, but never an invented figure."""
from watchbot import ai, refs
from watchbot.textcheck import problems


def test_checker_still_rejects_invented_numbers():
    assert "number 999 not in input" in problems("it is worth 999", ["126610LN", "Black Bay 58"])


def test_lesson_may_quote_reference_numbers(s, db, monkeypatch):
    refs.seed(db, s)
    good = {"title": "Reading the Submariner 126610LN reference",
            "body": "The 126610LN is the Submariner Date. The Black Bay 58 is a Tudor.", "action": "Check the ref before you buy."}
    monkeypatch.setattr(ai.claude, "call", lambda *a, **k: dict(good))
    out = ai.lesson(s, db, None, "2026-10-05")
    assert out["title"].startswith("Reading") and out["topic"]


def test_lesson_still_rejects_an_invented_price(s, db, monkeypatch):
    import pytest
    refs.seed(db, s)
    bad = {"title": "Pricing", "body": "It sells for S$987,654 on average.", "action": "Wait."}
    monkeypatch.setattr(ai.claude, "call", lambda *a, **k: dict(bad))
    with pytest.raises(ValueError, match="rule check"):
        ai.lesson(s, db, None, "2026-10-05")
