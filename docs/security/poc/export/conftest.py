import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[4] / "backend"))
from tests.conftest import *  # noqa: F401,F403  (env vars, conn, make_user, schedule_noon)
