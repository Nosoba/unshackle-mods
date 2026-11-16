import pytest

from unshackle.core.api import handlers
from unshackle.core.api.errors import APIError


@pytest.fixture
def accounts(monkeypatch):
    monkeypatch.setattr(
        handlers.config, "credentials", {"EX": {"ca_main": "a:b", "eu_box": "c:d", "shared": "e:f"}, "SINGLE": "u:p"}
    )
    monkeypatch.setattr(
        handlers.config,
        "serve",
        {
            "server_accounts": {
                "EX": {"ca_main": "ca", "eu_box": ["gb", "fr", "de"], "shared": "global"},
                "SINGLE": True,
            }
        },
    )
    monkeypatch.setattr(handlers.Services, "get_tag", staticmethod(lambda s: s))
    handlers._account_counters.clear()


def test_pool_filters_by_region(accounts):
    assert handlers.server_account_pool("EX", "gb") == ["eu_box", "shared"]
    assert handlers.server_account_pool("EX", "CA") == ["ca_main", "shared"]
    assert handlers.server_account_pool("EX", "jp") == ["shared"]
    assert handlers.server_account_pool("SINGLE", None) == [None]
    assert handlers.server_account_pool("OTHER", "gb") is None


def test_round_robin_and_empty_pool(accounts):
    assert [handlers.next_server_profile("EX", "gb") for _ in range(3)] == ["eu_box", "shared", "eu_box"]
    handlers.config.serve["server_accounts"]["EX"].pop("shared")
    with pytest.raises(APIError):
        handlers.next_server_profile("EX", "jp")


def test_advertised_regions(accounts):
    assert handlers.server_account_regions("EX") == {"regions": ["ca", "de", "fr", "gb"], "global": True}
    assert handlers.server_account_regions("SINGLE") == {"regions": [], "global": True}
    assert handlers.server_account_regions("OTHER") is None


def test_falsy_or_malformed_spec_is_off(accounts):
    handlers.config.serve["server_accounts"]["EX"] = False
    assert handlers.server_account_spec("EX") is None
    handlers.config.serve["server_accounts"]["EX"] = "ca"
    assert handlers.server_account_spec("EX") is None
    assert handlers.server_account_regions("EX") is None
    with pytest.raises(ValueError):
        handlers.validate_server_accounts()


def test_validate_rejects_unknown_profile(accounts, tmp_path, monkeypatch):
    monkeypatch.setattr(handlers.config.directories, "cookies", tmp_path)
    handlers.config.serve["server_accounts"]["EX"]["typo"] = "ca"
    with pytest.raises(ValueError, match="typo"):
        handlers.validate_server_accounts()
    (tmp_path / "EX").mkdir()
    (tmp_path / "EX" / "typo.txt").write_text("# Netscape HTTP Cookie File\n")
    handlers.validate_server_accounts()


def test_single_account_always_same(accounts):
    handlers.config.serve["server_accounts"]["EX"].pop("shared")
    assert [handlers.next_server_profile("EX", "ca") for _ in range(3)] == ["ca_main"] * 3
    assert handlers.next_server_profile("SINGLE", None) is None


def test_client_managed_gets_nothing(monkeypatch, accounts):
    monkeypatch.setattr(handlers, "load_service_yaml", lambda s: {})
    monkeypatch.setattr(handlers, "load_full_cdm", lambda *a: None)
    monkeypatch.setattr(handlers.Services, "load", staticmethod(lambda s: object()))
    monkeypatch.setattr(handlers, "instantiate_service", lambda *a: "svc")
    svc, cookies, cred = handlers.create_service_instance("OTHER", "t", {}, None, [], None)
    assert (svc, cookies, cred) == ("svc", None, None)


def test_per_key_gate(accounts, monkeypatch):
    class Req:
        def __init__(self, key):
            self.headers = {"X-Secret-Key": key}

    monkeypatch.setattr(handlers, "request_secret_key", lambda r: r.headers["X-Secret-Key"])
    handlers.config.serve["users"] = {"k1": {}, "k2": {"server_accounts": True}, "k3": {"server_accounts": ["EX"]}}
    assert handlers.server_accounts_allowed(Req("k1"), "EX") is False
    assert handlers.server_accounts_allowed(Req("k2"), "EX") is True
    assert handlers.server_accounts_allowed(Req("k3"), "EX") is True
    assert handlers.server_accounts_allowed(Req("k3"), "SINGLE") is False
    assert handlers.server_accounts_allowed(Req("admin"), "EX") is True
    assert handlers.server_account_for(Req("k1"), "EX") is False
    assert handlers.server_account_for(Req("k2"), "OTHER") is False


def test_rotation_per_region_pool(accounts):
    picks = [handlers.next_server_profile("EX", r) for r in ["ca", "gb", "ca", "gb"]]
    assert picks == ["ca_main", "eu_box", "shared", "shared"]


def test_server_account_ignores_client_credentials(monkeypatch, accounts):
    monkeypatch.setattr(handlers, "load_service_yaml", lambda s: {})
    monkeypatch.setattr(handlers, "load_full_cdm", lambda *a: None)
    monkeypatch.setattr(handlers.Services, "load", staticmethod(lambda s: object()))
    monkeypatch.setattr(handlers, "instantiate_service", lambda *a: "svc")
    monkeypatch.setattr(handlers, "server_account_cookies", lambda s, p: "server-jar")
    data = {"credentials": {"username": "old", "password": "client"}, "cookies": "AAAA", "cache": {"x": "y"}}
    svc, cookies, cred = handlers.create_service_instance("EX", "t", data, None, [], "ca_main", server_account=True)
    assert cookies == "server-jar"
    assert cred.username == "a" and cred.password == "b"


def test_validate_rejects_bad_region_values(accounts):
    handlers.config.serve["server_accounts"]["EX"]["ca_main"] = ["gb", False]
    with pytest.raises(ValueError, match="quote"):
        handlers.validate_server_accounts()
    handlers.config.serve["server_accounts"]["EX"]["ca_main"] = "canada"
    with pytest.raises(ValueError):
        handlers.validate_server_accounts()


def test_server_cdm_ignores_client_cdm_type(monkeypatch):
    """A server_cdm tier licenses with the server's own device, so the client's CDM type must
    not swap it for a stub of the other DRM system."""
    seen = []
    monkeypatch.setattr(handlers, "load_service_yaml", lambda s: {})
    monkeypatch.setattr(handlers, "load_full_cdm", lambda s, p, t=None: seen.append(t))
    monkeypatch.setattr(handlers.Services, "load", staticmethod(lambda s: object()))
    monkeypatch.setattr(handlers, "instantiate_service", lambda *a: "svc")
    data = {"cdm_type": "playready"}
    handlers.create_service_instance("OTHER", "t", data, None, [], None, server_cdm=True)
    handlers.create_service_instance("OTHER", "t", data, None, [], None)
    assert seen == [None, "playready"]


class _LangOptionService:
    def __init__(self, ctx, title, lang):
        self.parent_params = ctx.parent.params
        self.lang = lang


def _lang_option_module():
    import click

    @click.command()
    @click.argument("title")
    @click.option("--lang", type=int, default=None)
    @click.pass_context
    def cli(ctx, **kwargs):
        return _LangOptionService(ctx, **kwargs)

    return type("Module", (), {"cli": cli})


def _create_with_real_ctx(monkeypatch, data):
    monkeypatch.setattr(handlers, "load_service_yaml", lambda s: {})
    monkeypatch.setattr(handlers, "load_full_cdm", lambda *a: None)
    monkeypatch.setattr(handlers.Services, "load", staticmethod(lambda s: _lang_option_module()))
    svc, _, _ = handlers.create_service_instance("OTHER", "t", data, None, [], None)
    return svc


DL_DEFAULTS = {
    "lang": ["best"],
    "v_lang": [],
    "a_lang": [],
    "s_lang": ["all"],
    "acodec": [],
    "forced_subs": False,
    "exact_lang": False,
}


def test_nested_dl_params_reach_the_parent_ctx(monkeypatch):
    from unshackle.core.tracks import Audio

    data = {
        "dl_params": {
            "lang": ["es-419", "en"],
            "v_lang": ["ja"],
            "a_lang": ["es-419"],
            "s_lang": ["ko"],
            "acodec": ["EC3", "NOPE", "AAC"],
            "forced_subs": True,
            "exact_lang": True,
        }
    }
    svc = _create_with_real_ctx(monkeypatch, data)
    assert svc.parent_params["lang"] == ["es-419", "en"]
    assert svc.parent_params["v_lang"] == ["ja"]
    assert svc.parent_params["a_lang"] == ["es-419"]
    assert svc.parent_params["s_lang"] == ["ko"]
    assert svc.parent_params["acodec"] == [Audio.Codec.EC3, Audio.Codec.AAC]
    assert svc.parent_params["forced_subs"] is True
    assert svc.parent_params["exact_lang"] is True


def test_absent_dl_params_fall_back_to_the_dl_defaults(monkeypatch):
    svc = _create_with_real_ctx(monkeypatch, {})
    selection = {k: svc.parent_params[k] for k in (*DL_DEFAULTS,)}
    assert selection == DL_DEFAULTS


def test_an_empty_lang_list_is_kept(monkeypatch):
    svc = _create_with_real_ctx(monkeypatch, {"dl_params": {"lang": [], "a_lang": []}})
    assert svc.parent_params["lang"] == []


def test_a_service_lang_option_never_gets_the_dl_lang(monkeypatch):
    svc = _create_with_real_ctx(monkeypatch, {"dl_params": {"lang": ["en"]}})
    assert svc.lang is None
    assert svc.parent_params["lang"] == ["en"]
    assert "dl_params" in handlers.SESSION_TRANSPORT_KEYS


@pytest.mark.parametrize(
    "dl_params",
    [
        "en",
        {"lang": "en", "v_lang": "ja", "a_lang": {"x": 1}, "acodec": "EC3", "forced_subs": "yes", "exact_lang": "yes"},
        {"lang": [1], "v_lang": [None], "a_lang": ["en", None], "acodec": [3], "forced_subs": 1, "exact_lang": 1},
    ],
)
def test_malformed_dl_params_are_ignored(monkeypatch, dl_params):
    svc = _create_with_real_ctx(monkeypatch, {"dl_params": dl_params})
    selection = {k: svc.parent_params[k] for k in (*DL_DEFAULTS,)}
    assert selection == DL_DEFAULTS


def test_profile_reaches_the_parent_ctx_params(monkeypatch):
    monkeypatch.setattr(handlers, "load_service_yaml", lambda s: {})
    monkeypatch.setattr(handlers, "load_full_cdm", lambda *a: None)
    monkeypatch.setattr(handlers.Services, "load", staticmethod(lambda s: _lang_option_module()))
    svc, _, _ = handlers.create_service_instance("OTHER", "t", {}, None, [], "work")
    assert svc.parent_params["profile"] == "work"


def test_stateless_server_login_rejects_approval_before_polling(monkeypatch):
    import logging
    from types import SimpleNamespace

    from unshackle.core import console

    from unshackle.core.api.errors import APIErrorCode
    from unshackle.core.api.input_bridge import AuthStatus, StatelessInputBridge
    from unshackle.core.service import Service

    monkeypatch.setattr(console, "_notice_handler", None)
    bridge = StatelessInputBridge()
    service = SimpleNamespace(log=logging.getLogger("test-approval"), _input_bridge=bridge)
    checks = []
    with pytest.raises(APIError) as exc_info:
        Service.wait_for_approval(service, "Approve login", lambda: checks.append(True), timeout=0)
    assert exc_info.value.error_code is APIErrorCode.AUTH_FAILED
    assert exc_info.value.details == {"reason": "interactive_auth_required"}
    assert bridge.status is AuthStatus.FAILED
    assert not bridge.answered and not checks


@pytest.mark.parametrize("cdm_call", [None, {"method": "get_license_challenge"}])
def test_login_bridge_counts_only_user_answers(cdm_call):
    from unshackle.core.api.input_bridge import AuthStatus, InputBridge

    bridge = InputBridge()
    assert not bridge.answered
    assert bridge.submit_response("unused") is False
    bridge.status = AuthStatus.PENDING_INPUT
    bridge._cdm_call = cdm_call
    assert bridge.submit_response("answer") is True
    assert bridge.answered is (cdm_call is None)
