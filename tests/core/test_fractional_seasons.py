"""Fractional seasons retain their identity in core titles and export names."""

import pytest

from unshackle.core.config import config
from unshackle.core.export_name import export_name, season_episodes, title_label
from unshackle.core.titles.episode import Episode, Series


class StubService:
    pass


class StubMediaInfo:
    video_tracks = []
    audio_tracks = []


def episode(season, number=1):
    return Episode(
        id_=f"ep-{season}-{number}", service=StubService, title="Example Show", season=season, number=number
    )


@pytest.mark.parametrize("value", [0.5, "0.5"])
def test_fractional_season_names_and_selection(value, monkeypatch):
    monkeypatch.setattr(config, "output_template", {"series": "{title}.{season_episode}-{source}"})
    ep = episode(value)

    assert ep.season == 0.5
    assert isinstance(ep.season, float)
    assert ep.build_template_context(StubMediaInfo())["season_episode"] == "S0.5E01"
    assert ep.get_filename(StubMediaInfo()) == "Example.Show.S0.5E01-StubService"
    assert ep.folder_season() == "S0.5"
    assert str(ep) == "Example Show S0.5E01"
    assert ep.matches_wanted({"0.5x1"})
    assert not ep.matches_wanted({"0x1"})
    assert title_label(ep) == "S0.5E01"


def test_zero_and_fractional_seasons_remain_distinct():
    zero, half = episode(0), episode("0.5")
    assert zero.season == 0
    assert zero.build_template_context(StubMediaInfo())["season_episode"] == "S00E01"
    assert [ep.season for ep in Series([half, zero, episode(1)])] == [0, 0.5, 1]
    assert season_episodes([zero, half]) == {0: {1}, 0.5: {1}}
    assert export_name([zero, half], [], "EX", season_episodes([zero, half])) == "Example.Show.S00.S0.5-EX"


def test_fractional_season_export_ranges_and_whole_runs():
    half = [episode(0.5, number) for number in (1, 2, 3)]
    assert export_name(half[:2], [], "EX", season_episodes(half)) == "Example.Show.S0.5E01-E02-EX"
    assert export_name(half, [], "EX", season_episodes(half)) == "Example.Show.S0.5-EX"
    titles = [episode(season) for season in (0.5, 1.5, 2.5)]
    assert export_name(titles, [], "EX", season_episodes(titles)) == "Example.Show.S0.5.S1.5.S2.5-EX"
    assert export_name([episode(1), episode(2)], [], "EX", {1: {1}, 2: {1}}) == "Example.Show.S01-S02-EX"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), "NaN", "Infinity"])
def test_nonfinite_season_rejected(value):
    with pytest.raises((TypeError, ValueError)):
        episode(value)


def test_integral_float_normalizes_to_integer():
    assert episode(1.0).season == 1
    assert str(episode(1.0)) == "Example Show S01E01"
