"""The API's language defaults must match the CLI's.

A regression guard for a real break: dl's --lang defaults to "best", but the API
hardcoded ["orig"] in DEFAULT_DOWNLOAD_PARAMS and again at the dl.result() call
site. A title whose service reports no original language cannot resolve "orig",
so the request collapsed to nothing and every such API download died with
"Original language not available for title, skipping 'orig' selection" followed
by "There's no orig Audio Track, cannot continue". The same download from the
CLI worked, because the CLI never defaulted to "orig".

The check casts defaults without running perform_download(), which needs a
service, credentials and a network.
"""

from __future__ import annotations

from typing import Any

import pytest

from unshackle.commands.dl import dl
from unshackle.core.api.handlers import DEFAULT_DOWNLOAD_PARAMS, cast_dl_params

pytestmark = pytest.mark.unit

# Params the API is expected to mirror from the CLI. a_lang is deliberately not
# here, see test_a_lang_stays_empty_on_purpose.
MIRRORED = ("lang", "v_lang", "s_lang")


def cli_default(name: str) -> list[Any]:
    """dl's click default for ``name``, normalised to a list.

    click stores a scalar default as-is (--lang is the string "best"), while the
    param itself is a LANGUAGE_RANGE that yields a list. The API always stores the
    list form, so the scalar is wrapped before comparing.
    """
    for param in dl.cli.params:
        if getattr(param, "name", None) == name:
            value = param.default
            return list(value) if isinstance(value, (list, tuple)) else [value]
    raise AssertionError(f"dl has no --{name.replace('_', '-')} option; the API default has nothing to mirror")


@pytest.mark.parametrize("name", MIRRORED)
def test_api_default_matches_the_cli(name: str) -> None:
    assert DEFAULT_DOWNLOAD_PARAMS[name] == cli_default(name), (
        f"the API's {name} default disagrees with dl's --{name.replace('_', '-')}: "
        f"{DEFAULT_DOWNLOAD_PARAMS[name]} vs {cli_default(name)}"
    )


@pytest.mark.parametrize("name", MIRRORED)
def test_cast_preserves_the_declared_default(name: str) -> None:
    """The cast feeds ctx.params, then the dl.result() keyword splat."""
    assert cast_dl_params(dl.cli, {})[name] == DEFAULT_DOWNLOAD_PARAMS[name]


def test_default_lang_needs_no_title_language() -> None:
    """The whole point: the default must not be able to collapse to nothing.

    "orig" is the one token that cannot resolve on a title whose service reports
    no language, and when it was the default every such download failed outright.
    """
    default = DEFAULT_DOWNLOAD_PARAMS["lang"]
    assert any(token in default for token in ("best", "all")), (
        f"the API's lang default {default} filters by language, so a title without a "
        "reported original language still cannot be downloaded without arguments"
    )


def test_a_lang_stays_empty_on_purpose() -> None:
    """Empty is what lets an API caller's ``lang`` reach the audio tracks.

    dl computes ``audio_languages = a_lang or lang``, so an empty a_lang cascades to
    lang. The CLI's --a-lang defaults to "best" instead, but copying that here would
    make a_lang always truthy and silently stop ``lang`` from ever filtering audio.
    """
    assert DEFAULT_DOWNLOAD_PARAMS["a_lang"] == []
    assert cli_default("a_lang") == ["best"], "if this changed, revisit the cascade reasoning above"
