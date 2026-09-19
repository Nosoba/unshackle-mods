"""Resolution probing for HLS variants that omit RESOLUTION.

NHKOne's master playlist lists only BANDWIDTH and CODECS, so unshackle probes the first segment to
learn each variant's resolution. The segments are CENC-encrypted fMP4, and sniffing an SPS out of
encrypted bytes yields garbage: a start code turns up by chance and the "SPS" after it decodes to
something like 16x48. That is worse than knowing nothing, because Tracks.sort_videos orders on
height first, so the junk track outranks the real 1080p one and gets picked as "best".

Two defences, both covered here:
  1. parse_mp4_init_info reads the resolution straight out of the init segment's moov, which stays
     unencrypted under CENC (the sample entry becomes `encv`, wrapping a `frma` that names the real
     codec). This is the accurate path.
  2. is_plausible_resolution rejects nonsense from the SPS fallback, so a track keeps height None
     and sorts on bitrate rather than on a fabricated resolution.
"""

from __future__ import annotations

import base64
import struct

import pytest

from unshackle.core.manifests.hls import HLS
from unshackle.core.tracks import Tracks, Video

pytestmark = pytest.mark.unit

# A real fMP4 init segment: ffmpeg -f lavfi -i testsrc=size=1920x1080 -c:v libx264 -f hls
# -hls_segment_type fmp4. Unencrypted, avc1 sample entry.
INIT_1080P_AVC = base64.b64decode(
    "AAAAHGZ0eXBpc281AAACAGlzbzVpc282bXA0MQAAAyBtb292AAAAbG12aGQAAAAAAAAAAAAAAAAAAAPoAAAAAAABAAABAAAA"
    "AAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAC"
    "AAACI3RyYWsAAABcdGtoZAAAAAMAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAA"
    "AAEAAAAAAAAAAAAAAAAAAEAAAAAHgAAABDgAAAAAADBlZHRzAAAAKGVsc3QAAAAAAAAAAgAAAEL/////AAEAAAAAAAAAAAQA"
    "AAEAAAAAAY9tZGlhAAAAIG1kaGQAAAAAAAAAAAAAAAAAADwAAAAAAFXEAAAAAAAtaGRscgAAAAAAAAAAdmlkZQAAAAAAAAAA"
    "AAAAAFZpZGVvSGFuZGxlcgAAAAE6bWluZgAAABR2bWhkAAAAAQAAAAAAAAAAAAAAJGRpbmYAAAAcZHJlZgAAAAAAAAABAAAA"
    "DHVybCAAAAABAAAA+nN0YmwAAACuc3RzZAAAAAAAAAABAAAAnmF2YzEAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAHgAQ4AEgA"
    "AABIAAAAAAAAAAEVTGF2YzYxLjEwLjEwMCBsaWJ4MjY0AAAAAAAAAAAAAAAY//8AAAA4YXZjQwFkACj/4QAbZ2QAKKzZQHgC"
    "J+XARAAAAwAEAAADAPA8YMZYAQAGaOvjyyLA/fj4AAAAABBwYXNwAAAAAQAAAAEAAAAQc3R0cwAAAAAAAAAAAAAAEHN0c2MA"
    "AAAAAAAAAAAAABRzdHN6AAAAAAAAAAAAAAAAAAAAEHN0Y28AAAAAAAAAAAAAAChtdmV4AAAAIHRyZXgAAAAAAAAAAQAAAAEA"
    "AAAAAAAAAAAAAAAAAABhdWR0YQAAAFltZXRhAAAAAAAAACFoZGxyAAAAAAAAAABtZGlyYXBwbAAAAAAAAAAAAAAAACxpbHN0"
    "AAAAJKl0b28AAAAcZGF0YQAAAAEAAAAATGF2ZjYxLjUuMTAx"
)

# A real H.264 SPS for 1920x1080 (High profile), prefixed with a 4-byte Annex-B start code.
SPS_1080P = bytes.fromhex("0000000167640028acd940780227e58400000300040000030ca3c60c65")


def _box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + box_type + payload


def _visual_sample_entry(fourcc: bytes, width: int, height: int, extra: bytes = b"") -> bytes:
    body = b"\x00" * 6 + b"\x00\x01"  # reserved + data_reference_index
    body += b"\x00" * 16  # pre_defined + reserved
    body += struct.pack(">HH", width, height)
    body += struct.pack(">II", 0x00480000, 0x00480000)  # horiz/vert resolution
    body += b"\x00" * 4  # reserved
    body += struct.pack(">H", 1)  # frame_count
    body += b"\x00" * 32  # compressorname
    body += struct.pack(">H", 24)  # depth
    body += struct.pack(">h", -1)  # pre_defined
    return _box(fourcc, body + extra)


def _init_segment(entry: bytes) -> bytes:
    stsd = _box(b"stsd", b"\x00\x00\x00\x00" + struct.pack(">I", 1) + entry)
    return _box(b"moov", _box(b"trak", _box(b"mdia", _box(b"minf", _box(b"stbl", stsd)))))


def _cenc_entry(original_codec: bytes, width: int, height: int) -> bytes:
    """A CENC-protected sample entry: `encv` wrapping a sinf whose frma names the real codec."""
    schm = _box(b"schm", b"\x00\x00\x00\x00" + b"cenc" + struct.pack(">I", 0x00010000))
    sinf = _box(b"sinf", _box(b"frma", original_codec) + schm)
    return _visual_sample_entry(b"encv", width, height, sinf)


class TestInitSegmentProbe:
    def test_reads_resolution_from_a_real_init_segment(self) -> None:
        assert HLS.parse_mp4_init_info(INIT_1080P_AVC) == (1920, 1080, Video.Codec.AVC)

    @pytest.mark.parametrize(
        "fourcc, codec",
        [
            (b"avc1", Video.Codec.AVC),
            (b"avc3", Video.Codec.AVC),
            (b"hvc1", Video.Codec.HEVC),
            (b"hev1", Video.Codec.HEVC),
            (b"vp09", Video.Codec.VP9),
            (b"av01", Video.Codec.AV1),
        ],
    )
    def test_recognises_sample_entry_codecs(self, fourcc: bytes, codec: Video.Codec) -> None:
        init = _init_segment(_visual_sample_entry(fourcc, 1920, 1080))
        assert HLS.parse_mp4_init_info(init) == (1920, 1080, codec)

    @pytest.mark.parametrize("original, codec", [(b"avc1", Video.Codec.AVC), (b"hvc1", Video.Codec.HEVC)])
    def test_reads_through_cenc_encryption(self, original: bytes, codec: Video.Codec) -> None:
        """The NHKOne case: encrypted media, but the init segment's moov is still readable."""
        init = _init_segment(_cenc_entry(original, 1280, 720))
        assert HLS.parse_mp4_init_info(init) == (1280, 720, codec)

    @pytest.mark.parametrize(
        "data",
        [
            b"",
            b"\x00\x00\x00\x08moov",
            INIT_1080P_AVC[:40],  # truncated mid-box
            bytes((i * 91 + 7) % 256 for i in range(4096)),  # noise
        ],
        ids=["empty", "bare-moov", "truncated", "noise"],
    )
    def test_malformed_input_returns_none(self, data: bytes) -> None:
        assert HLS.parse_mp4_init_info(data) is None

    def test_implausible_dimensions_are_refused(self) -> None:
        init = _init_segment(_visual_sample_entry(b"avc1", 16, 48))
        assert HLS.parse_mp4_init_info(init) is None


class TestSpsFallback:
    def test_real_sps_still_parses(self) -> None:
        assert HLS.parse_ts_video_info(SPS_1080P) == (1920, 1080, Video.Codec.AVC)

    @pytest.mark.parametrize(
        "width, height",
        [(16, 48), (16, 32), (0, 0), (-16, 48), (99999, 8), (1920, 1)],
    )
    def test_implausible_resolutions_are_rejected(self, width: int, height: int) -> None:
        assert not HLS.is_plausible_resolution(width, height)

    @pytest.mark.parametrize("width, height", [(1920, 1080), (3840, 2160), (640, 360), (128, 128)])
    def test_real_resolutions_are_accepted(self, width: int, height: int) -> None:
        assert HLS.is_plausible_resolution(width, height)

    def test_garbage_payload_yields_no_resolution(self) -> None:
        """Encrypted-looking bytes must produce None rather than a fabricated resolution."""
        blob = bytes((i * 37 + 11) % 256 for i in range(8192))
        assert HLS.parse_ts_video_info(blob) is None


def test_unset_heights_fall_back_to_bitrate_ordering() -> None:
    """With no bogus height to skew it, sort_videos ranks the NHKOne ladder by bitrate."""

    def video(index: int, bitrate: int) -> Video:
        return Video(
            id_=f"v{index}-{bitrate}",
            url="https://example.invalid/v.m3u8",
            language="ja",
            codec=Video.Codec.AVC,
            range_=Video.Range.SDR,
            bitrate=bitrate * 1000,
        )

    # Bitrates as NHKOne lists them, each rung duplicated across the two audio groups.
    tracks = Tracks([video(i, b) for i, b in enumerate((1692, 1692, 6192, 6192, 3192, 3192, 832))])
    assert all(v.height is None for v in tracks.videos)

    tracks.sort_videos()

    assert tracks.videos[0].bitrate == 6192 * 1000
    assert tracks.videos[-1].bitrate == 832 * 1000
