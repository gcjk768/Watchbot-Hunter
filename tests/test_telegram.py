import json

import httpx
import pytest

from watchbot import telegram as tgm


def client(s, clock, handler, lim=None):
    return tgm.Telegram("T", s, lim, transport=httpx.MockTransport(handler), clock=clock, sleep=clock.sleep)


def ok(result=True):
    return httpx.Response(200, json={"ok": True, "result": result})


def msgs(n, kind="deal"):
    return [{"kind": kind, "ref_id": f"d{i}", "body": f"body {i}"} for i in range(n)]


def test_one_message_per_item_with_gap(s, db, clock, lim):
    sent = []

    def h(req):
        sent.append((clock.t, json.loads(req.content)))
        return ok({"message_id": len(sent)})
    tg = client(s, clock, h, lim)
    tgm.create_posts(db, "r1", "-100", msgs(3))
    assert tgm.post_pending(db, tg, "r1", clock)["posted"] == 3
    assert len(sent) == 3 and all(b["text"].startswith("body") for _, b in sent)
    assert all(b - a >= 3.5 for (a, _), (b, _) in zip(sent, sent[1:]))


def test_never_double_posts(s, db, clock):
    n = []
    tg = client(s, clock, lambda req: (n.append(1), ok({"message_id": len(n)}))[1])
    tgm.create_posts(db, "r1", "-100", msgs(2))
    tgm.create_posts(db, "r1", "-100", msgs(2))
    tgm.post_pending(db, tg, "r1", clock)
    tgm.post_pending(db, tg, "r1", clock)
    assert len(n) == 2


def test_429_retry_after_is_honoured(s, db, clock):
    calls = []

    def h(req):
        calls.append(clock.t)
        if len(calls) == 1:
            return httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 30}})
        return ok({"message_id": 1})
    tg = client(s, clock, h)
    tgm.create_posts(db, "r1", "-100", msgs(1))
    assert tgm.post_pending(db, tg, "r1", clock)["posted"] == 1
    assert calls[1] - calls[0] >= 30


def test_retry_after_above_cap_pauses(s, db, clock):
    tg = client(s, clock, lambda r: httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 1000}}))
    tgm.create_posts(db, "r1", "-100", msgs(1))
    with pytest.raises(tgm.Paused):
        tgm.post_pending(db, tg, "r1", clock)
    assert db.execute("SELECT post_status FROM posts").fetchone()[0] == "pending"


def test_timeout_leaves_row_sending_and_is_not_resent(s, db, clock):
    def h(req):
        raise httpx.ReadTimeout("slow")
    tgm.create_posts(db, "r1", "-100", msgs(2))
    with pytest.raises(tgm.Ambiguous):
        tgm.post_pending(db, client(s, clock, h), "r1", clock)
    assert [r[0] for r in db.execute("SELECT post_status FROM posts ORDER BY id")] == ["sending", "pending"]


def post(db, clock, kind, mid, age_h, run="old"):
    db.execute("INSERT INTO posts(run_id, kind, ref_id, chat_id, body, post_status, message_id, posted_at) "
               "VALUES(?,?,?,'-100','b','posted',?,?)", (run, kind, str(mid), mid, clock.t - age_h * 3600))


def test_only_previous_deals_are_deleted_and_favourites_kept(s, db, clock):
    for kind, mid in (("deal", 11), ("deal", 12), ("deal", 13), ("lesson", 14), ("profile", 15), ("paper", 16), ("move", 17)):
        post(db, clock, kind, mid, 1)
    db.execute("INSERT INTO favorites(message_id, deal_id, saved_at) VALUES(13, 1, 'x')")
    rows = tgm.previous_deal_rows(db, "-100", "new")
    assert sorted(r["message_id"] for r in rows) == [11, 12]


def test_batches_of_100_and_48h_tombstone(s, db, clock):
    seen = []

    def h(req):
        seen.append((req.url.path.split("/")[-1], json.loads(req.content)))
        return ok()
    for i in range(150):
        post(db, clock, "deal", 1000 + i, 1)
    post(db, clock, "deal", 9, 60)
    counts = tgm.delete_posts(db, client(s, clock, h), tgm.previous_deal_rows(db, "-100", "new"), clock)
    assert counts == {"deleted": 150, "replaced": 1, "gone": 0, "failed": 0}
    batches = [b for m, b in seen if m == "deleteMessages"]
    assert [len(b["message_ids"]) for b in batches] == [100, 50]
    assert ("editMessageText", {"chat_id": "-100", "message_id": 9, "text": "Replaced"}) in seen
    assert tgm.previous_deal_rows(db, "-100", "new") == []


def test_alerts_never_go_to_the_channel():
    from watchbot.alerts import Alerts
    with pytest.raises(ValueError):
        Alerts(None, "-100", "-100")
