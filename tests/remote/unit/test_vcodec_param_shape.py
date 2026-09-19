"""Services that want a single video codec must not consume the raw param.

``ctx.parent.params["vcodec"]`` is the CLI's multiple-choice value, so it is a list
of ``Video.Codec`` members (the API coerces its strings to members but keeps the
list). A service that treats it as a scalar fails on a bot/API job: AMZN died on
``'list' object has no attribute 'upper'``, HULUJP and FRIDAY on
``cannot use 'list' as a dict key``. NF's shape - unwrap the list, then fall back to
the default - is the one to copy.

A source check on purpose: building one of these services needs live config, a CDM
and a hand-built click context, and ``services/`` is machine-local (gitignored), so
it is absent on CI and only deployed copies are inspected.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parents[3] / "unshackle" / "services"

# self.vcodec[: T] = ctx.parent.params.get("vcodec") with no unwrap in between.
RAW_PARAM = re.compile(r"self\.vcodec(?::[^=\n]+)?\s*=\s*ctx\.parent\.params\.get\([\"']vcodec[\"']\)")


def test_no_service_uses_the_raw_vcodec_param_as_a_scalar() -> None:
    if not SERVICES.is_dir():
        pytest.skip("services/ is machine-local (gitignored); this check runs where it is deployed")

    offenders = [
        f"{path.parent.name}:{number}: {line.strip()}"
        for path in sorted(SERVICES.glob("*/__init__.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if RAW_PARAM.search(line)
    ]
    assert not offenders, "unwrap ctx.parent.params['vcodec'] before using it:\n" + "\n".join(offenders)
