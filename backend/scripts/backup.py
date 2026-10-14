"""Consistent online backup of the SQLite DB (safe while the bot is running).

    python scripts/backup.py /data/backups        # keeps the last 14 copies
"""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings  # noqa: E402

KEEP = 14


def main() -> None:
    target_dir = Path(sys.argv[1] if len(sys.argv) > 1 else Path(settings.db_path).parent / "backups")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"bot-{datetime.now():%Y%m%d-%H%M%S}.db"
    src = sqlite3.connect(settings.db_path)
    dst = sqlite3.connect(target)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    for old in sorted(target_dir.glob("bot-*.db"))[:-KEEP]:
        old.unlink()
    print(target)


if __name__ == "__main__":
    main()
