"""Report screenshots.

ТЗ 1.2: media is stored by Telegram itself — we keep only `file_id`. A photo uploaded from the
Mini App is sent to the employee's own chat as a receipt, and the file_id of that message is kept.
Without a running bot (local development) files go to MEDIA_DIR and get a `local:` id.

The Mini App shows photos through short-lived signed links, because <img> can't send initData.
"""

import hashlib
import hmac
import time
from pathlib import Path

from .config import settings

MAX_PHOTO = 10 * 1024 * 1024
LINK_TTL = 3600
TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def sniff(data: bytes) -> str | None:
    """jpg / png / webp by magic bytes, None for anything else."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def save_local(data: bytes, ext: str) -> str:
    folder = Path(settings.media_dir)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{hashlib.sha256(data).hexdigest()[:32]}.{ext}"
    (folder / name).write_bytes(data)
    return f"local:{name}"


def local_path(file_id: str) -> Path | None:
    name = file_id.removeprefix("local:")
    path = (Path(settings.media_dir) / name).resolve()
    return path if path.parent == Path(settings.media_dir).resolve() and path.is_file() else None


def _sig(report_id: int, exp: int) -> str:
    return hmac.new(settings.bot_token.encode(), f"report:{report_id}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]


def photo_url(report_id: int, now: float | None = None) -> str:
    # rounded expiry keeps the URL stable for a while, so the browser can cache the image
    exp = (int((now or time.time()) // 600) * 600) + LINK_TTL
    return f"/api/media/report/{report_id}?exp={exp}&sig={_sig(report_id, exp)}"


def verify(report_id: int, exp: int, sig: str) -> bool:
    return exp > time.time() and hmac.compare_digest(_sig(report_id, exp), sig)
