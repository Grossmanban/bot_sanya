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


# broadcasts: a bot may upload up to 50 MB; MP4 and iPhone's MOV share the ISO «ftyp» box
MAX_VIDEO = 50 * 1024 * 1024


def sniff_video(data: bytes) -> str | None:
    """mp4 / mov by the «ftyp» box, None for anything else."""
    if data[4:8] != b"ftyp":
        return None
    return "mov" if data[8:10] == b"qt" else "mp4"


def save_local(data: bytes, ext: str) -> str:
    folder = Path(settings.media_dir)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{hashlib.sha256(data).hexdigest()[:32]}.{ext}"
    (folder / name).write_bytes(data)
    return f"local:{name}"


VIDEO_TYPES = {"mp4": "video/mp4", "mov": "video/quicktime"}


def _kept(file_id: str) -> Path:
    return Path(settings.media_dir) / f"v_{hashlib.sha256(file_id.encode()).hexdigest()[:32]}"


def keep_video(file_id: str, data: bytes, ext: str) -> None:
    """A copy of a video sent through Telegram: the Bot API won't hand back files over 20 MB, and the Mini App
    shows sent videos (a broadcast, a prize)."""
    path = _kept(file_id).with_suffix(f".{ext}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def kept_video(file_id: str) -> Path | None:
    if file_id.startswith("local:"):
        return local_path(file_id)
    for ext in VIDEO_TYPES:
        path = _kept(file_id).with_suffix(f".{ext}")
        if path.is_file():
            return path
    return None


def local_path(file_id: str) -> Path | None:
    name = file_id.removeprefix("local:")
    path = (Path(settings.media_dir) / name).resolve()
    return path if path.parent == Path(settings.media_dir).resolve() and path.is_file() else None


def _sig(object_id: int, exp: int, kind: str) -> str:
    return hmac.new(settings.bot_token.encode(), f"{kind}:{object_id}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]


def photo_url(object_id: int, now: float | None = None, kind: str = "report") -> str:
    """kind: report (shift report), event (live event entry), tx (proof of a fine/bonus/deduction) or
    violation<N> (N-th proof of a schedule violation decision) — each signed separately."""
    # rounded expiry keeps the URL stable for a while, so the browser can cache the image
    exp = (int((now or time.time()) // 600) * 600) + LINK_TTL
    # alice — a photo stage of a «Яндекс Алиса» execution, aliceqr — a QR code's picture
    path = f"violation/{object_id}/{kind[9:]}" if kind.startswith("violation") else f"{kind}/{object_id}"
    return f"/api/media/{path}?exp={exp}&sig={_sig(object_id, exp, kind)}"


def verify(object_id: int, exp: int, sig: str, kind: str = "report") -> bool:
    return exp > time.time() and hmac.compare_digest(_sig(object_id, exp, kind), sig)
