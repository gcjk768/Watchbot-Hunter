import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from watchbot import config  # noqa: E402
from watchbot.db import connect  # noqa: E402
from watchbot.ratelimit import Limiter  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


class Clock:
    def __init__(self, t=1_790_000_000.0):   # 2026-09-21 Asia/Singapore
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, secs):
        self.t += max(0, secs)


@pytest.fixture
def s(tmp_path):
    s = config.load(ROOT / "config.yaml", env=False)
    s.data_dir = tmp_path
    s.db_path = tmp_path / "watchbot.db"
    s.bot_token = "T"
    s.secrets.ebay_client_id, s.secrets.ebay_client_secret = "id", "secret"
    return s


@pytest.fixture
def db():
    return connect(":memory:")


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def lim(db, s, clock):
    return Limiter(db, s, clock=clock, sleep=clock.sleep, rng=random.Random(1))


def fixture_json(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))
