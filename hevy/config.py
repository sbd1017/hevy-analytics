"""Settings from the environment (systemd units in production, scripts/dev.sh in development).

The Hevy and OpenAI API keys are never settings: the sync worker reads them from files (systemd LoadCredential in
production, HEVY_API_KEY_FILE in development). The web app never has them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENVIRONMENTS = ("production", "development", "test")


@dataclass
class Settings:
    env: str = "production"                      # production | development | test
    data_dir: Path = PROJECT_ROOT / "var"
    host: str = "127.0.0.1"
    port: int = 8778
    base_path: str = "/hevy"                     # the app always lives under this prefix
    public_origins: tuple = ("https://example.com",)   # set HEVY_PUBLIC_ORIGINS to your site
    timezone: str = "Europe/London"              # days and weeks are grouped in this zone
    api_base: str = "https://api.hevyapp.com"
    api_key_file: Path | None = None             # development only; production uses $CREDENTIALS_DIRECTORY
    sync_interval_s: int = 600                   # incremental check every ~10 min (no webhooks exist)
    request_gap_s: float = 0.4                   # pause between paged API calls (be a polite client)
    openai_base: str = "https://api.openai.com"
    openai_key_file: Path | None = None          # development only; production uses $CREDENTIALS_DIRECTORY
    coach_model: str | None = None               # None: the newest suitable model the key can use
    coach_daily_calls: int = 8                   # OpenAI calls per 24 hours, at most
    demo: bool = False                           # public demo: made-up data, no login, nothing connects anywhere

    @property
    def db_path(self) -> Path:
        return self.data_dir / "hevy.sqlite3"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def api_key_path(self) -> Path | None:
        cred = os.environ.get("CREDENTIALS_DIRECTORY")
        if cred:
            return Path(cred) / "api-key"
        return self.api_key_file

    def openai_key_path(self) -> Path | None:
        cred = os.environ.get("CREDENTIALS_DIRECTORY")
        if cred:
            return Path(cred) / "openai-api-key"
        return self.openai_key_file


def load_settings() -> Settings:
    env = os.environ
    s = Settings()
    s.env = env.get("HEVY_ENV", "production")
    if s.env not in ENVIRONMENTS:
        raise SystemExit(f"HEVY_ENV must be one of {', '.join(ENVIRONMENTS)}")
    if env.get("HEVY_DATA_DIR"):
        s.data_dir = Path(env["HEVY_DATA_DIR"])
    s.host = env.get("HEVY_HOST", s.host)
    s.port = int(env.get("HEVY_PORT", s.port))
    if env.get("HEVY_PUBLIC_ORIGINS"):
        s.public_origins = tuple(o.strip().rstrip("/") for o in env["HEVY_PUBLIC_ORIGINS"].split(",") if o.strip())
    if env.get("HEVY_TIMEZONE"):
        from zoneinfo import ZoneInfo
        ZoneInfo(env["HEVY_TIMEZONE"])                   # fail at start on an unknown zone, not mid-sync
        s.timezone = env["HEVY_TIMEZONE"]
    s.api_base = env.get("HEVY_API_BASE", s.api_base).rstrip("/")
    if env.get("HEVY_API_KEY_FILE") and not s.is_production:
        s.api_key_file = Path(env["HEVY_API_KEY_FILE"])
    s.openai_base = env.get("HEVY_OPENAI_BASE", s.openai_base).rstrip("/")
    if env.get("HEVY_OPENAI_KEY_FILE") and not s.is_production:
        s.openai_key_file = Path(env["HEVY_OPENAI_KEY_FILE"])
    s.coach_model = env.get("HEVY_COACH_MODEL") or None
    s.demo = env.get("HEVY_DEMO") == "1"
    if env.get("HEVY_COACH_DAILY_CALLS"):
        s.coach_daily_calls = max(0, int(env["HEVY_COACH_DAILY_CALLS"]))
    if env.get("HEVY_SYNC_INTERVAL_S"):
        s.sync_interval_s = max(120, int(env["HEVY_SYNC_INTERVAL_S"]))     # floor: never hammer Hevy
    return s
