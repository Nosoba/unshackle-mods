"""Tests for the Rich gradient track listing tree."""

from __future__ import annotations

from langcodes import Language
from rich.text import Text

from unshackle.core.tracks import Audio, Tracks, Video


def make_video(**overrides: object) -> Video:
    kwargs: dict[str, object] = {
        "id_": "v1",
        "url": "https://example.test/v1.mp4",
        "language": Language.get("ja"),
        "codec": Video.Codec.AVC,
        "range_": Video.Range.SDR,
        "width": 1920,
        "height": 1080,
        "bitrate": 10_802_000,
        "fps": 29.97,
    }
    kwargs.update(overrides)
    return Video(**kwargs)


def make_audio(**overrides: object) -> Audio:
    kwargs: dict[str, object] = {
        "id_": "a1",
        "url": "https://example.test/a1.mp4",
        "language": Language.get("ja"),
        "codec": Audio.Codec.AAC,
        "channels": 2.0,
        "bitrate": 197_000,
    }
    kwargs.update(overrides)
    return Audio(**kwargs)


def test_track_tree_labels_are_gradient_text() -> None:
    tracks = Tracks(make_video(), make_audio(), manifest_url="https://example.test/mpd")
    tree, _ = tracks.tree()

    # Walk all node labels and confirm the track detail lines are gradient Text objects.
    labels = [node.label for node in tree.children]
    assert labels, "track tree should have child nodes"

    def nested_labels(nodes) -> list:
        out: list = []
        for node in nodes:
            out.append(node.label)
            out.extend(nested_labels(node.children))
        return out

    all_labels = nested_labels(tree.children)
    assert any(isinstance(label, Text) and len(label.spans) > 1 for label in all_labels)


def test_track_tree_preserves_plain_text_content() -> None:
    tracks = Tracks(make_video(), make_audio(), manifest_url="https://example.test/mpd")
    tree, _ = tracks.tree()

    def plain_labels(nodes) -> list:
        out: list[str] = []
        for node in nodes:
            label = node.label
            if isinstance(label, Text):
                out.append(label.plain)
            elif isinstance(label, str):
                out.append(label)
            out.extend(plain_labels(node.children))
        return out

    labels = plain_labels(tree.children)
    assert any(l.startswith("1 Audio") for l in labels)
    assert any(l.startswith("1 Video") for l in labels)
    assert any("| ja |" in l for l in labels)
