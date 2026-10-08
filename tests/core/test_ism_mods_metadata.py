"""Offline regressions for upstream ISM metadata alongside mods duration handling."""

import pytest

from unshackle.core.manifests.ism import ISM
from unshackle.core.tracks import Subtitle, Track

URL = "https://cdn.example.test/title.ism/manifest"
MANIFEST = """<SmoothStreamingMedia MajorVersion="2" MinorVersion="0" Duration="62500000" TimeScale="10000000">
  <StreamIndex Type="video" Name="video" Language="en" Url="video/{start_time}">
    <QualityLevel Index="0" Bitrate="1000000" FourCC="H264" MaxWidth="1280" MaxHeight="720"/>
    <c t="0" d="62500000"/>
  </StreamIndex>
  <StreamIndex Type="audio" Name="audio" Language="en" Url="audio/{start_time}">
    <QualityLevel Index="0" Bitrate="384000" FourCC="EC-3" Channels="6" HasAtmos="TrUe"/>
    <c t="0" d="62500000"/>
  </StreamIndex>
  <StreamIndex Type="text" Name="text" Language="en" Url="text/{start_time}">
    <QualityLevel Index="0" Bitrate="1000" FourCC="TTML"/>
    <c t="0" d="62500000"/>
  </StreamIndex>
</SmoothStreamingMedia>
"""


@pytest.mark.parametrize(
    ("language_attr", "fallback", "expected_language", "original"),
    [
        ('Language="en"', "en", "en", True),
        ('Language="fr"', "en", "fr", False),
        ("", "en", "en", False),
        ('Language="und"', "en", "en", False),
        ('Language="en"', None, "en", False),
    ],
)
def test_language_original_and_duration(language_attr, fallback, expected_language, original) -> None:
    manifest = MANIFEST.replace('Language="en"', language_attr)
    tracks = ISM.from_text(manifest, URL).to_tracks(language=fallback)

    assert (len(tracks.videos), len(tracks.audio), len(tracks.subtitles)) == (1, 1, 1)
    for track in tracks:
        assert str(track.language) == expected_language
        assert track.is_original_lang is original
        assert track.duration == pytest.approx(6.25)
        assert track.descriptor == Track.Descriptor.ISM
    assert tracks.subtitles[0].codec == Subtitle.Codec.fTTML
    assert tracks.audio[0].extra["atmos"] is True
    assert tracks.audio[0].atmos is True


@pytest.mark.parametrize(
    ("has_atmos", "expected"),
    [('HasAtmos="true"', True), ('HasAtmos="TRUE"', True), ('HasAtmos="false"', False), ("", False)],
)
def test_audio_has_atmos_and_duration(has_atmos, expected) -> None:
    manifest = MANIFEST.replace('HasAtmos="TrUe"', has_atmos)
    audio = ISM.from_text(manifest, URL).to_tracks(language="en").audio[0]

    assert audio.extra == ({"atmos": True} if expected else {})
    assert audio.atmos is expected
    assert audio.duration == pytest.approx(6.25)


@pytest.mark.parametrize(
    ("root_timescale", "stream_timescale", "duration", "expected"),
    [
        ("", "", "62500000", 6.25),  # Default 10 MHz timescale.
        ('TimeScale="1000"', "", "6250", 6.25),  # Manifest timescale.
        ('TimeScale="1000"', 'TimeScale="2000"', "6250", 3.125),  # Mods stream override.
        ('TimeScale="10000000"', "", "0", None),
    ],
)
def test_duration_timescales_for_all_track_types(root_timescale, stream_timescale, duration, expected) -> None:
    manifest = MANIFEST.replace('TimeScale="10000000"', root_timescale)
    manifest = manifest.replace('Duration="62500000"', f'Duration="{duration}"')
    manifest = manifest.replace("<StreamIndex ", f"<StreamIndex {stream_timescale} ")
    tracks = ISM.from_text(manifest, URL).to_tracks(language="en")

    assert (len(tracks.videos), len(tracks.audio), len(tracks.subtitles)) == (1, 1, 1)
    for track in tracks:
        if expected is None:
            assert track.duration is None
        else:
            assert track.duration == pytest.approx(expected)
