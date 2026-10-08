"""Exercise the merged download loop with local tracks and no media tools/services.

Keep selection, renumbering, post-script bookkeeping, file placement and external
subtitle staging real. Stub only service/UI and download/mux/tagging boundaries.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import socket
import subprocess
from contextlib import nullcontext

from types import SimpleNamespace

import pytest

from unshackle.commands.dl import dl, post_script_group, title_wanted
from unshackle.core.config import config
from unshackle.core.events import Events
from unshackle.core.titles import Episode, Series
from unshackle.core.tracks import Audio, Subtitle, Tracks

dl_module = importlib.import_module("unshackle.commands.dl")


class Harness(dl):
    def __getattr__(self, name):
        return None  # optional command attributes, as in test_kind_only_selection


class FakeService:
    def __init__(self, episodes, kind, before_tracks):
        self.titles = Series(
            Episode(id_=f"show-s{s}-e{n}", service=FakeService, title="Show", season=s, number=n, language="en")
            for s, n, _ in episodes
        )
        self.available = {f"show-s{s}-e{n}": available for s, n, available in episodes}
        self.kind = kind
        self.before_tracks = before_tracks
        self.seen = []
        self.session = SimpleNamespace(proxies={}, cookies={})

    def authenticate(self, cookies=None, credential=None):
        assert cookies is None and credential is None

    def get_titles_cached(self):
        return self.titles

    def get_tracks(self, title):
        self.before_tracks()
        self.seen.append(title.id)
        available = self.available[title.id]
        if self.kind == "audio_description_only":
            return Tracks(
                Audio(
                    id_=title.id,
                    url="https://offline.invalid/audio",
                    language="en",
                    codec=Audio.Codec.AAC,
                    bitrate=128_000,
                    descriptive=available,
                )
            )
        return Tracks(
            Subtitle(
                id_=title.id,
                url="https://offline.invalid/subtitle",
                language="en",
                codec=Subtitle.Codec.SubRip,
                forced=available,
            )
        )

    def get_chapters(self, title):
        return []

    def close(self):
        pass

    def __getattr__(self, name):
        if name.startswith("on_"):
            return lambda *args, **kwargs: None
        raise AttributeError(name)


@pytest.fixture
def offline_run(monkeypatch, tmp_path):
    temp = tmp_path / "temp"
    temp.mkdir()
    output = tmp_path / "output"
    monkeypatch.setattr(config.directories, "temp", temp)
    monkeypatch.setattr(config.directories, "downloads", output)
    monkeypatch.setattr(config, "audio", {})
    monkeypatch.setattr(config, "subtitle", {"output_mode": "mux", "strip_sdh": False})
    monkeypatch.setattr(config, "muxing", {"merge_audio": True, "merge_video": False, "concurrency": 1})
    monkeypatch.setattr(config, "decryption", "shaka")
    monkeypatch.setattr(config, "decryption_map", {})
    monkeypatch.setattr(dl_module, "events", Events())
    monkeypatch.setattr(dl_module, "DOWNLOAD_ALL_DRM", SimpleNamespace(clear=lambda: None))
    monkeypatch.setattr(dl_module, "set_speed_limit", lambda *args: None)
    monkeypatch.setattr(dl_module, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(dl_module, "SyncLive", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(dl_module.console, "status", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(dl_module.console, "print", lambda *args, **kwargs: None)
    monkeypatch.setattr(dl_module.providers, "resolve_by_ids", lambda *args, **kwargs: None)
    monkeypatch.setattr(dl_module, "grow_session_pool", lambda *args: None)
    monkeypatch.setattr(dl_module.MediaInfo, "parse", lambda *args: SimpleNamespace(video_tracks=[], audio_tracks=[]))
    monkeypatch.setattr(dl_module.tags, "tag_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(dl_module.tags, "apply_container_metadata", lambda *args, **kwargs: None)

    def forbidden(*args, **kwargs):
        pytest.fail("Offline regression attempted network access or an external process")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(dl_module.requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)

    def filename(self, media_info, folder=False, show_service=True):
        return f"Show.S{self.season:02}" + ("" if folder else f"E{self.number:02}")

    monkeypatch.setattr(Episode, "get_filename", filename)

    def download(tracks, *args, **kwargs):
        for track in tracks:
            track.path = temp / f"{track.id}.track"
            track.path.write_bytes(b"offline track")

    def mux(self, *args, output_path, **kwargs):
        output_path.write_bytes(b"offline mux")
        return output_path, 0, []

    monkeypatch.setattr(dl_module, "download_tracks_in_passes", download)
    monkeypatch.setattr(Tracks, "mux", mux)

    def run(episodes, kind="audio_description_only", source=None, **flags):
        command = Harness.__new__(Harness)
        command.log = logging.getLogger("test_merge_post_scripts")
        command.service = "FAKE"
        command.completed_files = []
        command.skipped_subtitles = []
        command.proxy_providers = []
        command.get_cookie_jar = lambda *args: None
        command.get_credentials = lambda *args: None
        command.apply_romaji = lambda *args: None
        command.wait_vault_writes = lambda: None
        command.attach_subtitle_fonts = lambda *args: (0, [])
        staged = []
        process_external_subtitle = command.process_external_subtitle

        def stage(*args, temp_files_list, **kwargs):
            process_external_subtitle(*args, temp_files_list=temp_files_list, **kwargs)
            assert temp_files_list and all(path.exists() for path in temp_files_list)
            staged.extend(temp_files_list)

        command.process_external_subtitle = stage

        def before_tracks():
            assert all(not path.exists() for path in staged), "previous title left staged subtitles behind"

        service = FakeService(episodes, kind, before_tracks)
        calls = []

        def dispatch(event, mode, context, override):
            calls.append((event, mode, dict(context), tuple(service.seen)))

        monkeypatch.setattr(dl_module, "dispatch", dispatch)
        kwargs = {
            name: None if param.default is inspect.Parameter.empty else param.default
            for name, param in inspect.signature(dl.result).parameters.items()
            if name not in ("self", "service")
            and param.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        }
        for name in (
            "quality",
            "vcodec",
            "acodec",
            "range_",
            "wanted",
            "lang",
            "v_lang",
            "a_lang",
            "s_lang",
            "require_audio",
            "require_video",
            "require_subs",
            "forced_s_lang",
        ):
            kwargs[name] = []
        kwargs.update(
            downloads=1,
            workers=1,
            sub_format="original",
            folder=False,
            no_mux=False,
            audio_only=kind == "audio_description_only",
            subs_only=kind == "forced_subs_only",
            dl_sub=str(source) if source else None,
        )
        kwargs[kind] = True
        kwargs.update(flags)
        exit_code = None
        try:
            # Bypass the task-temp decorator so its final sweep cannot hide missing per-title cleanup.
            inspect.unwrap(dl.result)(command, service, **kwargs)
        except SystemExit as exc:
            exit_code = exc.code
        assert not command.download_failed
        assert all(event == "success" for event, *_ in calls)
        return SimpleNamespace(
            command=command, service=service, calls=calls, staged=staged, exit_code=exit_code, output=output
        )

    return run


@pytest.mark.parametrize("override", [None, 0, 7])
def test_effective_group_does_not_mutate_wanted_selection(override):
    title = Episode(id_="show-s1-e2", service=FakeService, title="Show", season=1, number=2)
    key = post_script_group(title, season_override=override)
    assert title.season == 1
    assert title_wanted(title, ["1x2"])
    assert not title_wanted(title, ["7x2"])
    if override is not None:
        title.season = override
    assert post_script_group(title, season_override=override) == key


@pytest.mark.parametrize("override", [None, 0, 7])
@pytest.mark.parametrize("last_available", [False, True])
@pytest.mark.parametrize("kind", ["audio_description_only", "forced_subs_only"])
def test_season_waits_for_last_selected_title_even_when_skipped(offline_run, override, last_available, kind):
    result = offline_run(
        [(1, 1, True), (1, 2, last_available), (1, 3, True)],
        kind=kind,
        season_override=override,
        wanted=["1x1", "1x2"],
    )
    assert result.exit_code is None
    expected_modes = ["file", "file", "season", "run"] if last_available else ["file", "season", "run"]
    assert [mode for _, mode, _, _ in result.calls] == expected_modes
    assert result.service.seen == ["show-s1-e1", "show-s1-e2"]
    season_call = next(call for call in result.calls if call[1] == "season")
    assert season_call[2]["season"] == str(1 if override is None else override)
    assert season_call[2]["episode"] == season_call[2]["filepath"] == ""
    assert season_call[3] == tuple(result.service.seen), "season hook fired before the last title settled"
    assert all(path.parent == result.output and path.exists() for path in result.command.completed_files)


@pytest.mark.parametrize("folder", [False, True])
@pytest.mark.parametrize("override", [None, 0, 7])
def test_original_seasons_can_collapse_without_changing_folder_opt_in(offline_run, folder, override):
    result = offline_run([(1, 1, True), (2, 2, True), (3, 3, True)], season_override=override, folder=folder)
    assert result.exit_code is None
    seasons = [call for call in result.calls if call[1] == "season"]
    assert len(seasons) == (3 if override is None else 1)
    if override is not None:
        assert seasons[0][3] == tuple(result.service.seen)
    assert len(result.command.completed_files) == 3
    for number, path in enumerate(result.command.completed_files, 1):
        season = number if override is None else override
        assert path.parent == (result.output / f"Show.S{season:02}" if folder else result.output)
        assert path.exists()


@pytest.mark.parametrize("kind", ["audio_description_only", "forced_subs_only"])
@pytest.mark.parametrize("single_selected", [False, True])
def test_kind_only_skips_clean_staged_subtitles_before_continue_or_exit(offline_run, tmp_path, kind, single_selected):
    source = tmp_path / "external.en.srt"
    content = "1\n00:00:00,000 --> 00:00:01,000\nhello\n"
    source.write_text(content, encoding="utf8")
    result = offline_run(
        [(1, 1, False), (1, 2, False)],
        kind=kind,
        source=source,
        season_override=7,
        wanted=["1x1"] if single_selected else [],
    )
    assert result.exit_code == (1 if single_selected else None)
    assert len(result.staged) == (1 if single_selected else 2)
    assert all(not path.exists() for path in result.staged)
    assert source.read_text(encoding="utf8") == content
    assert result.calls == [], "all-skipped runs must not emit successful file/season/run hooks"
    assert result.command.completed_files == []


@pytest.mark.parametrize("kind", ["audio_description_only", "forced_subs_only"])
def test_skipped_first_title_still_allows_the_last_success_to_settle(offline_run, kind):
    result = offline_run([(1, 1, False), (1, 2, True)], kind=kind, season_override=7)
    assert result.exit_code is None
    assert [mode for _, mode, _, _ in result.calls] == ["file", "season", "run"]
    assert len(result.command.completed_files) == 1


@pytest.mark.parametrize("kind", ["audio_description_only", "forced_subs_only"])
def test_no_mux_does_not_start_dispatching_post_scripts(offline_run, kind):
    result = offline_run([(1, 1, True), (1, 2, False)], kind=kind, season_override=7, no_mux=True)
    assert result.exit_code is None
    assert len(result.command.completed_files) == 1
    assert result.calls == []
