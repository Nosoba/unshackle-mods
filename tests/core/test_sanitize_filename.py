"""Tests for the mods-only unicode handling in ``utilities.sanitize_filename``.

`unicode_filenames: true` keeps the original characters of a title and swaps the
characters Windows forbids for their fullwidth twins. The ``unicode`` argument only
decides transliteration for one call, so ``export_name()`` can ask for a raw title
without enabling the swaps.
"""

from __future__ import annotations

import pytest

from unshackle.core.config import config
from unshackle.core.utilities import sanitize_filename

TITLE = 'Café: "Ünïcode"/Kanji 映画?'


def test_unicode_config_swaps_forbidden_chars_for_fullwidth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "unicode_filenames", True)
    assert sanitize_filename(TITLE) == "Café：.＂Ünïcode＂／Kanji.映画？"


def test_ascii_config_transliterates_and_strips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "unicode_filenames", False)
    assert sanitize_filename(TITLE) == "Cafe.Unicode.&.Kanji.Ying.Hua"


def test_unicode_argument_only_decides_transliteration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "unicode_filenames", False)
    assert sanitize_filename(TITLE, unicode=True) == "Café.Ünïcode.&.Kanji.映画"


def test_filename_replacements_apply_when_unicode_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "unicode_filenames", True)
    monkeypatch.setattr(config, "filename_replacements", {"~": "～"})
    assert sanitize_filename("A~B", " ") == "A～B"
