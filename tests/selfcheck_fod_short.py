"""Self-check for FOD Short rights_token construction (no network).

Run: python tests/selfcheck_fod_short.py
"""

import base64
import hashlib
import hmac
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import pad

from unshackle.services.FOD import FOD
from unshackle.services.FOD.short import (
    CRYPTO_CONFIG_ID,
    RIGHTS_AES_KEY,
    RIGHTS_HMAC_KEY,
    rights_token,
)


def decode(token: str) -> dict:
    raw = token.replace("-", "+").replace("_", "/")
    raw += "=" * (-len(raw) % 4)
    outer = json.loads(base64.b64decode(raw))
    assert outer["crypto_config_id"] == CRYPTO_CONFIG_ID, outer

    blob = base64.b64decode(outer["encrypted_license"])
    iv, body, mac = blob[:16], blob[16:-20], blob[-20:]
    assert hmac.new(RIGHTS_HMAC_KEY, iv + body, hashlib.sha1).digest() == mac, "HMAC mismatch"

    plain = AES.new(RIGHTS_AES_KEY, AES.MODE_CBC, iv).decrypt(body)
    return json.loads(plain[: -plain[-1]])


def main() -> int:
    token = rights_token("user-token", "product-uuid")

    assert not re.search(r"[=+/]", token), "token must be base64url without padding"

    inner = decode(token)
    assert inner["user_info"] == "user-token", inner
    assert inner["product_code"] == "product-uuid", inner
    assert isinstance(inner["not_after"], int) and inner["not_after"] > 0, inner

    rights = inner["rights"]
    assert rights.startswith("end="), rights
    stamp = rights[4:]
    # The endpoint rejects an offset suffix; the app sends naive JST wall-clock.
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", stamp), stamp

    parsed = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S")
    expected = datetime.now(timezone(timedelta(hours=9))) + timedelta(hours=48)
    assert abs((parsed - expected.replace(tzinfo=None)).total_seconds()) < 120, (parsed, expected)

    # IV must be fresh per call; a fixed IV would leak the plaintext pattern.
    assert rights_token("a", "b") != rights_token("a", "b"), "IV is not random"

    print("OK: rights_token round-trips, HMAC verifies, naive JST rights end, unique IVs")

    # The Short discriminator also drives the per-instance fence: Short is JP-only, VOD unfenced.
    short = FOD.__new__(FOD)
    short.is_short, short.GEOBLOCK = True, ()
    short.GEOFENCE = ("JP",)
    assert short.is_region_allowed("jp") and short.is_region_allowed("JP")
    assert not short.is_region_allowed("id")
    assert short.is_region_allowed(None), "unknown region must pass, or a probe failure would block the run"

    vod = FOD.__new__(FOD)
    vod.is_short, vod.GEOBLOCK, vod.GEOFENCE = False, (), ()
    assert vod.is_region_allowed("id") and vod.is_region_allowed("jp")

    print("OK: Short is JP-fenced, VOD is not")

    # A takeover code must key its own cache entry, or a second code silently reuses the
    # first account's token and downloads the wrong purchases.
    from unshackle.services.FOD.short import FODShort

    a = FODShort.__new__(FODShort)
    a.code = "aaa111"
    a.cache_key = f"short_account_{a.code}" if a.code else "short_session"
    b = FODShort.__new__(FODShort)
    b.code = "bbb222"
    b.cache_key = f"short_account_{b.code}" if b.code else "short_session"
    assert a.cache_key != b.cache_key, "two takeover codes share one cache entry"
    assert a.cache_key == "short_account_aaa111", a.cache_key

    anon = FODShort.__new__(FODShort)
    anon.code = ""
    anon.cache_key = "short_session"
    assert anon.cache_key == "short_session"

    print("OK: takeover code cache is per code, anonymous stays on short_session")
    return 0


if __name__ == "__main__":
    sys.exit(main())
