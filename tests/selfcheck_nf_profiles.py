"""Local self-check for the gitignored NF service's own content-profile codec mapping.

Run: uv run --no-sync python -m pytest tests/selfcheck_nf_profiles.py -q
"""

import pytest

from unshackle.core.tracks.audio import Audio
from unshackle.core.tracks.subtitle import Subtitle
from unshackle.core.tracks.video import Video
from unshackle.services.NF import NF


@pytest.mark.parametrize(
    ("profile", "codec"),
    [
        ("playready-h264mpl40-dash", Video.Codec.AVC),
        (" H264HPL40-DASH ", Video.Codec.AVC),
        ("hevc-dv5-main10-L41-dash-cenc-prk", Video.Codec.HEVC),
        ("vp9-profile0-L40-dash-cenc", Video.Codec.VP9),
        ("av1-main-L40-dash-cbcs-prk", Video.Codec.AV1),
        ("heaac-2-dash", Audio.Codec.AAC),
        ("xheaac-dash", Audio.Codec.AAC),
        ("dd-5.1-dash", Audio.Codec.AC3),
        ("ddplus-atmos-dash", Audio.Codec.EC3),
        ("ac4-dash", Audio.Codec.AC4),
        ("playready-oggvorbis-2-dash", Audio.Codec.OGG),
        ("webvtt-lssdh-ios8", Subtitle.Codec.WebVTT),
        ("dfxp-ls-sdh", Subtitle.Codec.TimedTextMarkupLang),
    ],
)
def test_profile_maps_to_codec(profile: str, codec) -> None:
    assert NF.profile_codec(profile, type(codec)) is codec


@pytest.mark.parametrize(
    ("profile", "codec_type"),
    [("unknown-profile", Video.Codec), (None, Audio.Codec), ("hevc-main10-L41-dash-cenc", Audio.Codec)],
)
def test_unsupported_profile_raises(profile, codec_type) -> None:
    with pytest.raises(ValueError):
        NF.profile_codec(profile, codec_type)
