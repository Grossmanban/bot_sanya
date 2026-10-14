"""Rates. Hardcoded in v1; swap calculate_rate for a rules-table lookup later."""

BASE_RATE = 120
LAZY_RATE = 100
LAZY_THRESHOLD = 20


def calculate_rate(volume: int) -> int:
    return LAZY_RATE if volume < LAZY_THRESHOLD else BASE_RATE


def is_lazy(volume: int) -> bool:
    return volume < LAZY_THRESHOLD


def expected_pay(volume: int) -> int:
    return volume * calculate_rate(volume)


def pricing_info() -> dict:
    return {"base_rate": BASE_RATE, "lazy_rate": LAZY_RATE, "lazy_threshold": LAZY_THRESHOLD}
