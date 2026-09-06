"""Proxy selection for external subtitle fetching (``-dls``).

A region-locked host (TVer) must be fetched through a proxy in its country, the same way
``--proxy jp`` would, but an explicit ``--proxy`` on the run always wins so the provider is
not asked twice.
"""

from types import SimpleNamespace

import pytest

from unshackle.commands.dl import dl, region_lock_for


class DummyLog:
    def __init__(self):
        self.messages = {"debug": [], "info": [], "warning": [], "error": []}

    def debug(self, msg):
        self.messages["debug"].append(str(msg))

    def info(self, msg):
        self.messages["info"].append(str(msg))

    def warning(self, msg):
        self.messages["warning"].append(str(msg))

    def error(self, msg):
        self.messages["error"].append(str(msg))

    def all_text(self):
        return "\n".join(sum(self.messages.values(), []))


def make_dl(providers):
    """A dl instance with only the attributes external_subtitle_proxy touches."""
    command = dl.__new__(dl)
    command.log = DummyLog()
    command.proxy_providers = providers
    return command


def service_with_proxies(proxies):
    return SimpleNamespace(session=SimpleNamespace(proxies=proxies))


TVER_URL = "https://tver.jp/episodes/ep12345"


def test_region_lock_matches_the_host_not_the_whole_url():
    assert region_lock_for(TVER_URL) == "jp"
    assert region_lock_for("https://www.tver.jp/episodes/ep1") == "jp"
    # a mention of the locked host in the path or query must not route the request
    assert region_lock_for("https://evil.example/?redirect=tver.jp") is None
    assert region_lock_for("https://nottver.jp/x") is None
    assert region_lock_for("https://cdn.example.com/subs/ja.srt") is None


def test_an_explicit_run_proxy_is_reused_without_asking_a_provider():
    class Boom:
        def get_proxy(self, query):  # pragma: no cover - must not be reached
            raise AssertionError("provider was asked for a proxy despite --proxy being set")

    command = make_dl([Boom()])
    service = service_with_proxies({"all": "https://user:pass@explicit.example:8080"})

    assert command.external_subtitle_proxy(TVER_URL, service) == "https://user:pass@explicit.example:8080"
    # the credentials never reach the log
    assert "pass" not in command.log.all_text()


@pytest.mark.parametrize("key", ["all", "https", "http"])
def test_the_run_proxy_is_found_under_any_session_key(key):
    command = make_dl([])
    service = service_with_proxies({key: "socks5://run.example:1080"})

    assert command.external_subtitle_proxy(TVER_URL, service) == "socks5://run.example:1080"


def test_a_region_locked_host_resolves_a_proxy_in_its_country():
    asked = []

    class JPProvider:
        def get_proxy(self, query):
            asked.append(query)
            return "socks5://jp.example:1080"

    command = make_dl([JPProvider()])

    assert command.external_subtitle_proxy(TVER_URL, service_with_proxies({})) == "socks5://jp.example:1080"
    assert asked == ["jp"]


def test_an_unlocked_host_is_fetched_directly():
    class Boom:
        def get_proxy(self, query):  # pragma: no cover - must not be reached
            raise AssertionError("a proxy was resolved for a host that is not region locked")

    command = make_dl([Boom()])

    assert command.external_subtitle_proxy("https://cdn.example.com/subs/ja.srt", service_with_proxies({})) is None


def test_no_proxy_run_warns_and_goes_direct():
    """--no-proxy leaves proxy_providers empty; the fetch is attempted anyway."""
    command = make_dl([])

    assert command.external_subtitle_proxy(TVER_URL, service_with_proxies({})) is None
    assert any("region locked" in m for m in command.log.messages["warning"])


def test_a_provider_without_the_region_does_not_fail_the_download():
    class NoJP:
        def get_proxy(self, query):
            return None

    command = make_dl([NoJP()])

    assert command.external_subtitle_proxy(TVER_URL, service_with_proxies({})) is None
    assert any("JP proxy" in m for m in command.log.messages["error"])


def test_a_provider_that_raises_does_not_fail_the_download():
    class Broken:
        def get_proxy(self, query):
            raise RuntimeError("provider is down")

    command = make_dl([Broken()])

    assert command.external_subtitle_proxy(TVER_URL, service_with_proxies({})) is None
    assert any("provider is down" in m for m in command.log.messages["error"])


def test_a_service_without_a_session_is_tolerated():
    class JPProvider:
        def get_proxy(self, query):
            return "socks5://jp.example:1080"

    command = make_dl([JPProvider()])

    assert command.external_subtitle_proxy(TVER_URL, SimpleNamespace()) == "socks5://jp.example:1080"
