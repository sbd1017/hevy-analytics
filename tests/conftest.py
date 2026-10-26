from datetime import datetime, timezone

import pytest

from hevy.client import HevyClient
from hevy.config import Settings
from hevy.db import Database
from hevy.sync import SyncEngine

from .fake_hevy import KEY, FakeHevy


@pytest.fixture
def settings(tmp_path):
    return Settings(env="test", data_dir=tmp_path / "var", public_origins=("https://example.com",), request_gap_s=0)


@pytest.fixture
def db(settings):
    d = Database(settings.db_path)
    d.migrate()
    return d


@pytest.fixture
def fake():
    return FakeHevy(datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc))


@pytest.fixture
def client(fake):
    return HevyClient(KEY, "https://api.test", transport=fake, gap_s=0, sleep=lambda s: None)


@pytest.fixture
def engine(db, client, settings, fake):
    return SyncEngine(db, client, settings, now=lambda: fake.now)
