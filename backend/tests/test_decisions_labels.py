"""§27 «Решения»: the Mini App list (frontend/src/lib/pending.ts) shows the same keys, words and order as the
bot's /pending (app.pending.KEYS / LABELS) — a new count added on the server must not silently miss the app."""

import re
from pathlib import Path

from app import pending

PENDING_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "pending.ts"


def test_decisions_list_matches_server_counts():
    src = PENDING_TS.read_text(encoding="utf-8")
    block = src.split("PENDING_LABELS", 1)[1].split("];", 1)[0]
    pairs = re.findall(r'\["(\w+)", "([^"]+)"\]', block)
    assert [k for k, _ in pairs] == list(pending.KEYS)
    assert {k: v for k, v in pairs} == {k: pending.LABELS[k] for k in pending.KEYS}


def test_every_count_has_a_section():
    src = PENDING_TS.read_text(encoding="utf-8")
    block = src.split("SECTION_COUNTS", 1)[1].split("};", 1)[0]
    mapped = set(re.findall(r'"(\w+)"', block.split("=", 1)[1]))
    assert set(pending.KEYS) <= mapped
