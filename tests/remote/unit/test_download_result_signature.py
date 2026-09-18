"""The API's dl.result() call must satisfy that function's signature.

A regression guard for a real break: season_override, episode_override, set_year
and dl_sub are required parameters of dl.result(), the CLI fills them from click,
and the API call site did not pass them. Every API download died with
"dl.result() missing 4 required positional arguments".

The check is static on purpose. Calling perform_download() for real needs a
service, credentials and a network, none of which belong in a unit test, so the
call site is read from source and matched against the live signature instead.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from unshackle.commands.dl import dl
from unshackle.core.api import download_manager

pytestmark = pytest.mark.unit


def _result_call() -> ast.Call:
    """The single dl_instance.result(...) call inside download_manager."""
    source = Path(inspect.getfile(download_manager)).read_text("utf8")
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "result"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "dl_instance"
    ]
    assert len(calls) == 1, f"expected one dl_instance.result() call, found {len(calls)}"
    return calls[0]


def _supplied() -> dict[str, ast.expr]:
    call = _result_call()
    assert not call.args, "dl.result() takes keywords only; a positional would silently shift"
    # A **kwargs splat would hide what is passed from this test, and would also let a
    # missing argument through to runtime, which is the bug being pinned here.
    assert all(kw.arg for kw in call.keywords), "no **kwargs splat in the dl.result() call"
    return {kw.arg: kw.value for kw in call.keywords}


def _params() -> tuple[set[str], set[str]]:
    """(required, known) parameter names of dl.result(), ignoring self and *args/**kwargs."""
    required: set[str] = set()
    known: set[str] = set()
    for name, param in inspect.signature(dl.result).parameters.items():
        if name == "self" or param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        known.add(name)
        if param.default is inspect.Parameter.empty:
            required.add(name)
    return required, known


def test_every_required_parameter_is_supplied() -> None:
    required, _ = _params()
    missing = required - set(_supplied())
    assert not missing, (
        f"download_manager does not pass {sorted(missing)} to dl.result(). Every API download fails with a TypeError."
    )


def test_the_four_parameters_that_broke_the_api_are_covered() -> None:
    """Names the actual regression, so a re-break points straight at this history."""
    supplied = set(_supplied())
    for name in ("season_override", "episode_override", "set_year", "dl_sub"):
        assert name in supplied, f"{name} is missing again"


def test_no_unknown_keyword_is_passed() -> None:
    """dl.result() ends in **__, which would swallow a misspelled keyword in silence."""
    _, known = _params()
    unknown = set(_supplied()) - known
    assert not unknown, f"dl.result() does not accept {sorted(unknown)}; **__ would hide the mistake"


def test_dl_sub_is_hardcoded_to_none() -> None:
    """dl_sub copies an arbitrary server path into the output.

    process_external_subtitle() hands the value to shutil.copy2(), so taking it from
    request params would give any API client a read primitive for the whole
    filesystem. It must be pinned, not plumbed.
    """
    value = _supplied()["dl_sub"]
    assert isinstance(value, ast.Constant) and value.value is None, (
        "dl_sub must be the literal None at the API call site, never taken from params"
    )
