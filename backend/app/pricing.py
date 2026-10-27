"""Piece rates. Values live in app_settings (rate_base, rate_lazy, lazy_threshold, trainer_commission)
and are loaded here on start and on every settings change, so cards, stats and Excel agree.

«Налог на лень»: a day with fewer than lazy_threshold pieces is paid at rate_lazy instead of rate_base.
"""

# piece_cap: the most a piece may cost together with the trainer's and the instructor's share (ТЗ 27.10 §25)
DEFAULTS = {"rate_base": 120, "rate_lazy": 100, "lazy_threshold": 20, "trainer_commission": 10,
            "instructor_commission": 10, "piece_cap": 150}
_rates = dict(DEFAULTS)


def apply(values: dict) -> None:
    """Load rates from settings (keys as in DEFAULTS; others are ignored)."""
    for key in DEFAULTS:
        if key in values:
            _rates[key] = int(values[key])


def current() -> dict:
    return dict(_rates)


def calculate_rate(volume: int, rates: dict | None = None) -> int:
    r = rates or _rates
    return r["rate_lazy"] if volume < r["lazy_threshold"] else r["rate_base"]


def is_lazy(volume: int, rates: dict | None = None) -> bool:
    return volume < (rates or _rates)["lazy_threshold"]


def expected_pay(volume: int, rates: dict | None = None) -> int:
    return volume * calculate_rate(volume, rates)


def pricing_info(rates: dict | None = None) -> dict:
    """For the Mini App hints; `rates` — the person's own (rates.for_user), else the common ones."""
    r = rates or _rates
    return {"base_rate": r["rate_base"], "lazy_rate": r["rate_lazy"], "lazy_threshold": r["lazy_threshold"]}
