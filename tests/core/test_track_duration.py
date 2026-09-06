"""Tests for shared track duration and size metadata."""

from __future__ import annotations

import math
from typing import Any

import pytest

from unshackle.core.api.handlers import serialize_audio_track
from unshackle.core.tracks import Audio
from unshackle.core.tracks.track import Track


def make_audio(**overrides: Any) -> Audio:
    kwargs: dict[str, Any] = {
        "id_": "audio-duration",
        "url": "https://example.test/audio.mp4",
        "language": "en",
        "codec": Audio.Codec.AAC,
        "bitrate": 8_000_000,
    }
    kwargs.update(overrides)
    return Audio(**kwargs)


def test_duration_is_formatted_and_estimates_size() -> None:
    track = make_audio(duration=3661.9)

    assert track.duration == 3661.9
    assert track.formatted_duration == "01:01:01"
    assert track.estimated_size_bytes == 3_661_900_000
    assert track.estimated_size == "3.41GiB"


def test_zero_duration_is_known_but_has_zero_size() -> None:
    track = make_audio(duration=0)

    assert track.formatted_duration == "00:00:00"
    assert track.estimated_size_bytes == 0
    assert track.estimated_size == "0.00B"


def test_missing_duration_or_bitrate_has_no_estimate() -> None:
    assert make_audio().formatted_duration is None
    assert make_audio().estimated_size is None
    assert make_audio(duration=60, bitrate=None).estimated_size is None


@pytest.mark.parametrize("duration", [True, -1, math.inf, math.nan, "60"])
def test_duration_rejects_invalid_values(duration: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        make_audio(duration=duration)


def test_duration_roundtrips_through_export_data() -> None:
    original = make_audio(duration=60)

    rebuilt = Track.from_dict(original.to_dict())

    assert rebuilt.duration == 60.0
    assert rebuilt.formatted_duration == "00:01:00"


def test_api_exposes_duration_and_size_metadata() -> None:
    data = serialize_audio_track(make_audio(duration=60))

    assert data["duration"] == 60.0
    assert data["formatted_duration"] == "00:01:00"
    assert data["estimated_size_bytes"] == 60_000_000
    assert data["estimated_size"] == "57.22MiB"
