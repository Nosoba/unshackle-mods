"""The {source} file-name variable resolves to a Service's first alias."""

from unittest.mock import MagicMock

import pytest

from unshackle.core.titles import Episode, Movie
from unshackle.core.titles.title import source_tag


def _media_info() -> MagicMock:
    media_info = MagicMock()
    media_info.video_tracks = []
    media_info.audio_tracks = []
    return media_info


def _service(name: str, aliases=None) -> type:
    return type(name, (), {} if aliases is None else {"ALIASES": aliases})


def test_first_alias_wins_over_the_class_name() -> None:
    assert source_tag(_service("NHKOne", ["NHKO", "NHKONE", "NHK One"])) == "NHKO"


def test_tuple_aliases_work_the_same_as_lists() -> None:
    assert source_tag(_service("NHKOnDemand", ("NOD", "NHK", "NHKOD"))) == "NOD"


def test_class_name_is_used_when_no_aliases_are_declared() -> None:
    assert source_tag(_service("YTBE")) == "YTBE"


@pytest.mark.parametrize("aliases", [(), [], None])
def test_class_name_is_used_when_aliases_are_empty(aliases) -> None:
    assert source_tag(_service("YTBE", aliases)) == "YTBE"


def test_alias_case_is_preserved() -> None:
    assert source_tag(_service("FRIDAY", ("friDay", "FRIDAY"))) == "friDay"


def test_blank_aliases_are_skipped() -> None:
    assert source_tag(_service("TELASA", ("", "  ", "TLSA"))) == "TLSA"


def test_an_all_blank_alias_list_falls_back_to_the_class_name() -> None:
    assert source_tag(_service("TELASA", ("", "  "))) == "TELASA"


def test_episode_context_uses_the_alias() -> None:
    episode = Episode(
        id_="nhkone-episode-0001",
        service=_service("NHKOne", ["NHKO", "NHKONE"]),
        title="Some Show",
        season=1,
        number=1,
        year=2024,
    )
    assert episode.build_template_context(_media_info())["source"] == "NHKO"


def test_movie_context_uses_the_alias() -> None:
    movie = Movie(
        id_="telasa-movie-0001",
        service=_service("TELASA", ("TLSA", "telasa")),
        name="Some Movie",
        year=2024,
    )
    assert movie.build_template_context(_media_info())["source"] == "TLSA"


def test_hiding_the_service_still_blanks_the_source() -> None:
    episode = Episode(
        id_="nhkone-episode-0002",
        service=_service("NHKOne", ["NHKO"]),
        title="Some Show",
        season=1,
        number=1,
        year=2024,
    )
    assert episode.build_template_context(_media_info(), show_service=False)["source"] == ""
