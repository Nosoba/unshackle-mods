"""Tests for optional title replacement configuration."""

from pathlib import Path

from unshackle.commands.dl import dl
from unshackle.core.config import Config, config
from unshackle.core.titles.episode import Episode, Series
from unshackle.core.titles.movie import Movie


class DummyService:
    pass


def test_romaji_map_is_empty_when_file_is_missing(tmp_path: Path) -> None:
    config = Config()
    config.directories.user_configs = tmp_path

    # Loading is normally performed during construction, so explicitly reload after
    # redirecting the user config directory for this isolated test.
    config.romaji_map = {}
    config._load_romaji_map()

    assert config.romaji_map == {}


def test_romaji_map_parses_pipe_separated_titles(tmp_path: Path) -> None:
    (tmp_path / "config_romaji.txt").write_text(
        "\ufeff日本語タイトル | Nihongo Title\n"
        "\n"
        "invalid line without separator\n"
        "English Title | English Replacement | ignored\n"
        " | missing source\n"
        "missing replacement | \n",
        encoding="utf-8",
    )

    config = Config()
    config.directories.user_configs = tmp_path
    config.romaji_map = {}
    config._load_romaji_map()

    assert config.romaji_map == {
        "日本語タイトル": "Nihongo Title",
        "English Title": "English Replacement | ignored",
    }


def test_apply_romaji_renames_series_and_episode_titles(monkeypatch) -> None:
    """apply_romaji rewrites the Series headline and each episode's title/name."""
    episodes = [
        Episode(
            id_="ep-0001",
            service=DummyService,
            title="Your Sky ハレのち恋",
            season=1,
            number=1,
            name="第1話 危険な先輩",
            year=2026,
        ),
        Episode(
            id_="ep-0002",
            service=DummyService,
            title="Your Sky ハレのち恋",
            season=1,
            number=2,
            name="第2話 そらごと",
            year=2026,
        ),
    ]
    series = Series(episodes)

    monkeypatch.setattr(
        config,
        "romaji_map",
        {
            "Your Sky ハレのち恋": "Your Sky Hare no Chi Koi",
            "第1話 危険な先輩": "Dai 1-wa: Kiken na Senpai",
        },
    )

    dl.__new__(dl).apply_romaji(series)

    assert str(series) == "Your Sky Hare no Chi Koi (2026)"
    assert series[0].title == "Your Sky Hare no Chi Koi"
    assert series[0].name == "Dai 1-wa: Kiken na Senpai"
    # An episode with no matching map entry is left untouched
    assert series[1].title == "Your Sky Hare no Chi Koi"
    assert series[1].name == "第2話 そらごと"


def test_apply_romaji_renames_movie_name(monkeypatch) -> None:
    """apply_romaji rewrites a standalone Movie's name."""
    movie = Movie(id_="movie-0001", service=DummyService, name="ハレのち恋", year=2026)

    monkeypatch.setattr(config, "romaji_map", {"ハレのち恋": "Hare no Chi Koi"})

    dl.__new__(dl).apply_romaji(movie)

    assert movie.name == "Hare no Chi Koi"
    assert str(movie) == "Hare no Chi Koi (2026)"


def test_apply_romaji_noop_without_map() -> None:
    """With an empty romaji_map, apply_romaji leaves titles untouched."""
    movie = Movie(id_="movie-0002", service=DummyService, name="Original", year=2025)

    dl.__new__(dl).apply_romaji(movie)

    assert movie.name == "Original"


def test_apply_romaji_accepts_empty_iterable() -> None:
    """An empty Series must not raise even though it has no __iter__ element to rename."""
    series = Series()

    dl.__new__(dl).apply_romaji(series)

    assert len(series) == 0
