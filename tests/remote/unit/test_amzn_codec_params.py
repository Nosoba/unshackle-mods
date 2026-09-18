from __future__ import annotations

import pytest

pytest.importorskip(
    "unshackle.services.AMZN",
    reason="services/ is machine-local (gitignored); this check runs where the service is deployed",
)


def test_amzn_pick_codec_accepts_api_cli_and_legacy_shapes() -> None:
    """AMZN crashed with 'list' has no attribute 'upper' on API downloads: it called
    .upper() on the vcodec list the API passes via ctx.parent.params. All shapes must
    now reduce to a single codec (or the service default when unset).
    """
    from unshackle.core.tracks import Audio, Video
    from unshackle.services.AMZN import _pick_codec

    # The shape the API hands over: a list of codec enums, empty when unset.
    assert _pick_codec([Video.Codec.HEVC], Video.Codec.AVC) is Video.Codec.HEVC
    assert _pick_codec([Audio.Codec.AAC], Audio.Codec.EC3) is Audio.Codec.AAC
    assert _pick_codec([], Video.Codec.AVC) is Video.Codec.AVC
    assert _pick_codec(None, Audio.Codec.EC3) is Audio.Codec.EC3

    # Legacy bare-string shape keeps its case-normalising behaviour.
    assert _pick_codec("h.264", Video.Codec.AVC) == Video.Codec.AVC

    # A bare codec member passes through as an equal codec (str-Enum members hit the
    # string branch, which stringifies them, so compare by equality).
    assert _pick_codec(Video.Codec.AV1, Video.Codec.AVC) == Video.Codec.AV1
