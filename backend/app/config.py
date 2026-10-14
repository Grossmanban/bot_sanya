import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


ROOT = Path(__file__).resolve().parents[2]
_load_dotenv(ROOT / ".env")


def _parse_ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(";", ",").split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(default_factory=lambda: os.environ.get("BOT_TOKEN", ""))
    admin_ids: frozenset[int] = field(default_factory=lambda: _parse_ids(os.environ.get("ADMIN_IDS", "")))
    webapp_url: str = field(default_factory=lambda: os.environ.get("WEBAPP_URL", ""))
    db_path: str = field(default_factory=lambda: os.environ.get("DB_PATH", str(ROOT / "data" / "bot.db")))
    timezone: str = field(default_factory=lambda: os.environ.get("TIMEZONE", "Europe/Moscow"))
    # RUN_BOT=0 lets you run the API and Mini App locally without Telegram polling.
    run_bot: bool = field(default_factory=lambda: os.environ.get("RUN_BOT", "1") != "0")
    frontend_dist: str = field(default_factory=lambda: os.environ.get("FRONTEND_DIST", str(ROOT / "frontend" / "out")))
    init_data_max_age: int = 24 * 3600

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


settings = Settings()
