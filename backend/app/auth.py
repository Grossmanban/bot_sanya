"""Telegram Mini App initData validation.

https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class InitDataError(Exception):
    pass


def validate_init_data(init_data: str, bot_token: str, max_age: int, now: float | None = None) -> dict:
    """Return the Telegram `user` object if the signature is valid."""
    if not init_data or not bot_token:
        raise InitDataError("missing init data")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise InitDataError("missing hash")
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise InitDataError("bad signature")
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError as exc:
        raise InitDataError("bad auth_date") from exc
    if (now or time.time()) - auth_date > max_age:
        raise InitDataError("init data expired")
    try:
        user = json.loads(pairs.get("user", ""))
    except json.JSONDecodeError as exc:
        raise InitDataError("bad user") from exc
    if not isinstance(user, dict) or "id" not in user:
        raise InitDataError("bad user")
    return user


def sign_init_data(user: dict, bot_token: str, auth_date: int | None = None) -> str:
    """Build a signed initData string. Used by tests and local previews."""
    from urllib.parse import urlencode

    pairs = {"auth_date": str(auth_date or int(time.time())), "user": json.dumps(user, ensure_ascii=False)}
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)
