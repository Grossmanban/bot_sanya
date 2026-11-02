import pytest
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[4] / "backend"))
from tests.conftest import *  # noqa: F401,F403  (env vars, conn, make_user, schedule_noon)


@pytest.fixture(autouse=True)
def reset_rates():
    """F12 changes the process-wide rates (pricing._rates): put the defaults back for the other tests."""
    from app import pricing
    pricing.apply(pricing.DEFAULTS)
    yield
    pricing.apply(pricing.DEFAULTS)
