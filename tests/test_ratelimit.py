import pytest

from watchbot.ratelimit import BudgetExhausted, InCooldown, Limiter


def test_gap_per_domain_is_8_to_15_seconds(lim, clock):
    lim.acquire("web", "a.sg")
    t0 = clock.t
    lim.acquire("web", "a.sg")
    assert 8 <= clock.t - t0 <= 15


def test_other_domain_does_not_wait(lim, clock):
    lim.acquire("web", "a.sg")
    t0 = clock.t
    lim.acquire("web", "b.sg")
    assert clock.t == t0


def test_daily_web_budget_rolls_over_at_singapore_midnight(lim, clock):
    for i in range(120):
        lim.acquire("web", f"d{i}.sg")
    with pytest.raises(BudgetExhausted):
        lim.acquire("web", "another.sg")
    # move to 23:59 Singapore time, still the same day, then one minute later a new day
    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Singapore")
    now = datetime.fromtimestamp(clock.t, tz)
    clock.t = now.replace(hour=23, minute=59, second=0).timestamp()
    with pytest.raises(BudgetExhausted):
        lim.acquire("web", "another.sg")
    clock.t += 61
    lim.acquire("web", "another.sg")


def test_ebay_and_claude_caps(lim, s):
    s.limits.daily_calls_ebay = 3
    for _ in range(3):
        lim.acquire("ebay", "browse")
    with pytest.raises(BudgetExhausted):
        lim.acquire("ebay", "browse")
    for _ in range(s.limits.claude.max_calls_per_day):
        lim.acquire_claude()
    with pytest.raises(BudgetExhausted):
        lim.acquire_claude()


def test_chrono24_pages_cap_counts_alongside_web(lim, s):
    s.limits.web.chrono24_pages_per_day = 2
    lim.acquire("web", "www.chrono24.sg", extra_caps=("chrono24",))
    lim.acquire("web", "www.chrono24.sg", extra_caps=("chrono24",))
    with pytest.raises(BudgetExhausted):
        lim.acquire("web", "www.chrono24.sg", extra_caps=("chrono24",))
    assert lim.used("web") == 2


def test_cooldown_after_429_lasts_24h(lim, clock):
    lim.trip("web", "a.sg", "HTTP 429")
    with pytest.raises(InCooldown):
        lim.acquire("web", "a.sg")
    lim.acquire("web", "b.sg")
    clock.t += 24 * 3600 + 1
    lim.acquire("web", "a.sg")


def test_three_failures_in_a_row_cool_down(lim):
    assert not lim.failed("web", "a.sg", "HTTP 500")
    assert not lim.failed("web", "a.sg", "timeout")
    assert lim.failed("web", "a.sg", "HTTP 502")
    with pytest.raises(InCooldown):
        lim.acquire("web", "a.sg")


def test_success_resets_failures(lim):
    lim.failed("web", "a.sg", "x")
    lim.failed("web", "a.sg", "x")
    lim.succeeded("web", "a.sg")
    assert not lim.failed("web", "a.sg", "x")


def test_telegram_gap_and_17_per_minute(lim, clock):
    times = []
    for _ in range(20):
        lim.acquire("telegram", "bot")
        times.append(clock.t)
    assert all(b - a >= 3.5 for a, b in zip(times, times[1:]))
    for i in range(len(times)):
        assert sum(1 for t in times if times[i] <= t < times[i] + 60) <= 17


def test_state_survives_a_restart(db, s, clock):
    Limiter(db, s, clock=clock, sleep=clock.sleep).trip("web", "a.sg", "HTTP 403")
    with pytest.raises(InCooldown):
        Limiter(db, s, clock=clock, sleep=clock.sleep).acquire("web", "a.sg")
