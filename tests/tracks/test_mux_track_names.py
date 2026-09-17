"""Track names and the original flag written into the muxed container."""

import io
import subprocess

import pytest

from unshackle.core import binaries
from unshackle.core.config import config
from unshackle.core.tracks import Audio, Subtitle, Tracks, Video


def _audio(language: str, original: bool) -> Audio:
    return Audio(
        url="https://example.com/a.mp4",
        language=language,
        is_original_lang=original,
        codec=Audio.Codec.AAC,
        channels=2,
    )


def _subtitle(language: str, original: bool, **kwargs) -> Subtitle:
    return Subtitle(
        url="https://example.com/s.vtt",
        language=language,
        is_original_lang=original,
        codec=Subtitle.Codec.WebVTT,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("ja", "\u65e5\u672c\u8a9e [\u30aa\u30ea\u30b8\u30ca\u30eb]"),
        ("en", "English [Original]"),
        ("pt-BR", "Portugu\u00eas (Brasil) [Original]"),
        ("id", "Bahasa Indonesia [Original]"),
    ],
)
def test_original_track_gets_a_translated_suffix(language: str, expected: str) -> None:
    assert Tracks._generate_auto_name(_audio(language, True)) == expected


def test_non_original_track_gets_the_bare_autonym() -> None:
    assert Tracks._generate_auto_name(_audio("ja", False)) == "\u65e5\u672c\u8a9e"


def test_unknown_language_falls_back_to_a_plain_original_suffix() -> None:
    # Swahili has no entry in MANUAL_TRANSLATIONS, so the English word is used
    assert Tracks._generate_auto_name(_audio("sw", True)) == "Kiswahili [Original]"


def test_subtitle_flags_append_to_the_generated_name() -> None:
    track = _subtitle("en", False, sdh=True)
    track.name = Tracks._generate_auto_name(track)
    assert track.get_track_name() == "English (SDH)"


def test_descriptive_audio_flag_appends_to_the_generated_name() -> None:
    track = _audio("en", False)
    track.descriptive = True
    track.name = Tracks._generate_auto_name(track)
    assert track.get_track_name() == "English (Descriptive)"


class _FakePopen:
    """Stand in for mkvmerge so mux() can be driven without the binary."""

    def __init__(self, command, **kwargs):
        _FakePopen.command = command
        self.stdout = io.StringIO("")

    def wait(self):
        return 0


def _mux_command(monkeypatch, tmp_path, *, original_flag=None) -> list[str]:
    monkeypatch.setattr(binaries, "MKVToolNix", "mkvmerge")
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    muxing = {} if original_flag is None else {"original_flag": original_flag}
    monkeypatch.setattr(config, "muxing", muxing)

    video = Video(
        url="https://example.com/v.mp4",
        language="ja",
        is_original_lang=True,
        codec=Video.Codec.AVC,
        range_=Video.Range.SDR,
        width=1920,
        height=1080,
    )
    audio = _audio("ja", True)
    subtitle = _subtitle("en", False)

    for track, suffix in ((video, ".mp4"), (audio, ".m4a"), (subtitle, ".vtt")):
        track.path = tmp_path / f"{track.id}{suffix}"
        track.path.write_bytes(b"")

    Tracks([video, audio, subtitle]).mux("Test Title", delete=False)
    return _FakePopen.command


def test_mux_writes_generated_track_names(monkeypatch, tmp_path) -> None:
    command = _mux_command(monkeypatch, tmp_path)

    assert "0:\u65e5\u672c\u8a9e [\u30aa\u30ea\u30b8\u30ca\u30eb]" in command
    assert "0:English" in command


def test_original_flag_is_set_by_default(monkeypatch, tmp_path) -> None:
    command = _mux_command(monkeypatch, tmp_path)

    flags = [command[i + 1] for i, arg in enumerate(command) if arg == "--original-flag"]
    # video and audio are the original language, the English subtitle is not
    assert flags == ["0:True", "0:True", "0:False"]


def test_original_flag_can_be_turned_off(monkeypatch, tmp_path) -> None:
    command = _mux_command(monkeypatch, tmp_path, original_flag=False)

    flags = [command[i + 1] for i, arg in enumerate(command) if arg == "--original-flag"]
    assert flags == ["0:False", "0:False", "0:False"]


def test_turning_off_the_original_flag_keeps_the_track_names(monkeypatch, tmp_path) -> None:
    command = _mux_command(monkeypatch, tmp_path, original_flag=False)

    assert "0:\u65e5\u672c\u8a9e [\u30aa\u30ea\u30b8\u30ca\u30eb]" in command
