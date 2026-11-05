"""Offline selfcheck for the FANZA service: title-ID parsing and signed Digital API envelope."""
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from unshackle.services.FANZA import FANZA


def _inst(title="abc123"):
    inst = FANZA.__new__(FANZA)
    inst.title_input = title
    inst.log = MagicMock()
    inst.session = MagicMock()
    inst.user_id = "u1"
    inst.license_uid = "lic1"
    inst.part = 1
    inst.secret_key = "hp2Y944L"
    inst.content = {"product_id": "p1", "mylibrary_id": 42, "shop_name": "digital"}
    inst.config = {"endpoints": {"graphql": "https://api.tv.dmm.com/graphql", "license": "l"}}
    return inst


# --- title id parsing (the __init__ logic, mirrored) ---
def parse(title):
    import re
    from urllib.parse import parse_qs, urlparse

    m = re.search(r"parent_product_id=([^/&]+)", title)
    if m:
        return m.group(1)
    if m := re.search(r"[?&]cid=([^/&]+)", title):
        return m.group(1)
    if "tv.dmm.com/vod" in title or "video.dmm.co.jp" in title:
        qs = parse_qs(urlparse(title).query)
        return (qs.get("season") or qs.get("content") or [title])[0]
    return title


assert parse("https://www.dmm.co.jp/x?parent_product_id=h_1234abc/") == "h_1234abc"
assert parse("https://www.dmm.co.jp/x&cid=zz99") == "zz99"
assert parse("https://tv.dmm.com/vod/detail/?season=s_42") == "s_42"
assert parse("https://video.dmm.co.jp/av/content/?id=m_7") == "https://video.dmm.co.jp/av/content/?id=m_7"
assert parse("plainid") == "plainid"

# --- Digital API envelope is signed over the exact JSON that is sent ---
inst = _inst()
inst.session.post.side_effect = [
    SimpleNamespace(json=lambda: {"data": {"product_id": "p1"}}),
    SimpleNamespace(json=lambda: {"data": {"redirect": "https://x/manifest.m3u8"}}),
]
url = inst._get_playback_url()
assert url == "https://x/manifest.m3u8", url

sent = inst.session.post.call_args.kwargs["data"]
assert sent["appid"] == "android_movieplayer_app"
expected = hmac.new(inst.secret_key.encode(), sent["params"].encode(), hashlib.sha256).hexdigest()
assert sent["authkey"] == expected, "authkey must HMAC the exact params string"

params = json.loads(sent["params"])
assert params["parent_product_id"] == "p1"
assert params["licenseUID"] == "lic1"
assert params["part"] == 1  # first candidate carries the CLI part number
assert params["exploit_id"] == "uid:u1"

# --- errno on the first candidate falls through to the next one ---
inst = _inst()
inst.session.post.side_effect = [
    SimpleNamespace(json=lambda: {"data": {"product_id": "p1"}}),
    SimpleNamespace(json=lambda: {"data": {"errno": 1}}),
    SimpleNamespace(json=lambda: {"data": {"redirect": "https://y/m.m3u8"}}),
]
assert inst._get_playback_url() == "https://y/m.m3u8"

print("ALL FANZA SELFCHECKS PASSED")
