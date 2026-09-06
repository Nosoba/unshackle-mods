"""Tests for metadata.xml release tags and muxer branding."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from unshackle.core import binaries
from unshackle.core.config import config
from unshackle.core.utils import tags as tags_mod

METADATA_XML = """<?xml version="1.0"?>
<Tags>
  <Tag>
    <Targets>
      <TargetTypeValue>50</TargetTypeValue>
    </Targets>
    <Simple>
      <Name>ENCODED_BY</Name>
      <String>Crash | NSBC</String>
    </Simple>
    <Simple>
      <Name>DISCORD</Name>
      <String>@nsbc_crash</String>
    </Simple>
    <Simple>
      <Name>Blank</Name>
      <String></String>
    </Simple>
  </Tag>
</Tags>
"""


@pytest.fixture
def user_configs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config.directories, "user_configs", tmp_path)
    return tmp_path


def test_metadata_xml_tags_are_loaded(user_configs: Path) -> None:
    (user_configs / "metadata.xml").write_text(METADATA_XML, encoding="utf-8")

    assert tags_mod.load_metadata_xml_tags() == {
        "ENCODED_BY": "Crash | NSBC",
        "DISCORD": "@nsbc_crash",
    }


def test_missing_metadata_xml_is_not_an_error(user_configs: Path) -> None:
    assert tags_mod.load_metadata_xml_tags() == {}


def test_malformed_metadata_xml_is_ignored(user_configs: Path) -> None:
    (user_configs / "metadata.xml").write_text("<Tags><Tag>", encoding="utf-8")

    assert tags_mod.load_metadata_xml_tags() == {}


def test_branding_overwrites_writing_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(binaries, "Mkvpropedit", Path("mkvpropedit"))
    monkeypatch.setattr(tags_mod.binaries, "Mkvpropedit", Path("mkvpropedit"))

    captured: dict[str, Any] = {}

    def fake_run(cl: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        captured["cl"] = cl
        return subprocess.CompletedProcess(cl, 0, "", "")

    monkeypatch.setattr(tags_mod.subprocess, "run", fake_run)

    target = tmp_path / "Show.S01E01.mkv"
    target.touch()
    tags_mod.apply_container_metadata(target, title_name=target.stem)

    cl = captured["cl"]
    assert "--edit" in cl and "info" in cl
    assert f"muxing-application={tags_mod.MUXER_BRANDING}" in cl
    assert f"writing-application={tags_mod.MUXER_BRANDING}" in cl
    assert f"title={target.stem}" in cl
    assert "--add-track-statistics-tags" in cl
    assert tags_mod.MUXER_BRANDING == "NSBC Muxer v16.78 ( \u300cCRASH\u300d )"


def test_branding_skips_non_matroska_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("mkvpropedit must not run on non-Matroska output")

    monkeypatch.setattr(tags_mod.subprocess, "run", fail_run)

    target = tmp_path / "song.m4a"
    target.touch()
    tags_mod.apply_container_metadata(target, title_name=target.stem)


def test_branding_is_a_noop_without_mkvpropedit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tags_mod.binaries, "Mkvpropedit", None)

    def fail_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("mkvpropedit is unavailable and must not be invoked")

    monkeypatch.setattr(tags_mod.subprocess, "run", fail_run)

    target = tmp_path / "Show.S01E01.mkv"
    target.touch()
    tags_mod.apply_container_metadata(target, title_name=target.stem)
