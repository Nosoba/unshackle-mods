"""Selfcheck: Lemino token acquisition (manual / auto-fetch / cache) and QR-only login surface."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from unshackle.services.Lemino import Lemino

CACHE_DIR = Path(tempfile.mkdtemp())

def _make_inst(token="", use_qr=False):
    inst = Lemino.__new__(Lemino)
    inst.session = MagicMock()
    inst.log = MagicMock()
    inst.token = token
    inst._token_expiry = None
    inst.use_qr = use_qr
    inst._input_bridge = None
    inst.cache_dir = CACHE_DIR
    inst.config = {"endpoints": {"session_init": "https://if.lemino.docomo.ne.jp/v1/session/init"}}
    return inst

def _resp(headers):
    r = MagicMock()
    r.headers = headers
    return r

# 1) Happy path: token returned in header
inst = _make_inst()
inst.session.post.return_value = _resp({"X-Service-Token": "abc123fresh"})
inst.configure()
assert inst.token == "abc123fresh", f"Expected fresh token, got {inst.token}"

# 2) Second run uses cache, no HTTP call
inst = _make_inst()
inst.configure()
inst.session.post.assert_not_called()
assert inst.token == "abc123fresh", f"Expected cached token, got {inst.token}"

# 3) Missing header: stays empty
CACHE_DIR.joinpath("lemino_token.json").unlink(missing_ok=True)
inst = _make_inst()
inst.session.post.return_value = _resp({})
inst.configure()
assert inst.token == "", f"Expected empty fallback, got {inst.token}"

# 4) Network error: stays empty
inst = _make_inst()
inst.session.post.side_effect = ConnectionError("offline")
inst.configure()
assert inst.token == "", f"Expected empty after error, got {inst.token}"

# 5) --service-token provided: skip fetch, but persist to cache
CACHE_DIR.joinpath("lemino_token.json").unlink(missing_ok=True)
inst = _make_inst(token="manualtoken")
inst.configure()
inst.session.post.assert_not_called()
assert inst.token == "manualtoken", f"Expected manual token, got {inst.token}"

inst = _make_inst()
inst.configure()
inst.session.post.assert_not_called()
assert inst.token == "manualtoken", f"Expected manual token from cache, got {inst.token}"

# 6) Server update-date header drives cache expiry (epoch)
CACHE_DIR.joinpath("lemino_token.json").unlink(missing_ok=True)
inst = _make_inst()
inst.session.post.return_value = _resp({"X-Service-Token": "withttl", "X-Service-Token-UpdateDate": "9999999999"})
inst.configure()
inst = _make_inst()
inst.configure()
inst.session.post.assert_not_called()
assert inst.token == "withttl", f"Expected ttl-cached token, got {inst.token}"

# 7) Browser flags removed; only -qr remains for interactive login
names = {p.name for p in Lemino.cli.params}
assert "qr" in names, names
assert not {"browser", "browser_profile"} & names, names
assert not hasattr(Lemino, "_token_from_browser"), "browser capture should be gone"

# 8) --qr pair-code flow: init -> create loginkey -> poll -> session/update -> cached
CACHE_DIR.joinpath("lemino_token.json").unlink(missing_ok=True)
inst = _make_inst(use_qr=True)
inst.wait_for_approval = lambda msg, check, **kw: check() or check()
r_init = _resp({"X-Service-Token": "tmp_tv"})
r_create = MagicMock(ok=True, json=lambda: {"loginkey": "123456"})
r_ping_pending = MagicMock(ok=True, json=lambda: {"member": {"member_status": "0", "account_type": None}})
r_ping_ok = MagicMock(ok=True, json=lambda: {"member": {"member_status": "1", "account_type": False}})
r_upd = _resp({"X-Service-Token": "logged_in_tok", "X-Service-Token-UpdateDate": "9999999999"})
inst.session.post.side_effect = [r_init, r_create, r_ping_pending, r_ping_ok, r_upd]
inst.configure()
assert inst.token == "logged_in_tok", f"Expected QR token, got {inst.token}"

# 9) Subsequent --qr run reuses cached logged-in token after profile check (even when account_type is False)
inst2 = _make_inst(use_qr=True)
inst2.session.post.return_value = MagicMock(ok=True, json=lambda: {"member": {"member_status": "1", "account_type": False}})
inst2.configure()
assert inst2.token == "logged_in_tok"
assert inst2.session.post.call_count == 1  # only the profile check, no new loginkey

# 10) Expired cached token auto-refreshes via session/update without needing -qr
CACHE_DIR.joinpath("lemino_token.json").write_text('{"token": "old_tok", "expires_at": 1}', "utf-8")
inst3 = _make_inst()
inst3.session.post.return_value = _resp({"X-Service-Token": "refreshed_tok", "X-Service-Token-UpdateDate": "9999999999"})
inst3.configure()
assert inst3.token == "refreshed_tok", f"Expected refreshed token, got {inst3.token}"

print("ALL LEMINO TOKEN TESTS PASSED")
