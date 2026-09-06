"""Temp file cleanup for external subtitles (``-dls``).

Every branch of ``process_external_subtitle`` stages files in the temp dir. Those files have to
be tracked so they can be removed once the title is done, including the extras yt-dlp writes
alongside the subtitle it was asked for.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from unshackle.commands.dl import dl
from unshackle.core.config import config
from unshackle.core.temp import remove_task_dir, task_temp_dir


class DummyLog:
    def __init__(self):
        self.messages = {"debug": [], "info": [], "warning": [], "error": []}

    def debug(self, msg):
        self.messages["debug"].append(str(msg))

    def info(self, msg):
        self.messages["info"].append(str(msg))

    def warning(self, msg):
        self.messages["warning"].append(str(msg))

    def error(self, msg):
        self.messages["error"].append(str(msg))

    def all_text(self):
        return "\n".join(sum(self.messages.values(), []))


class DummyTracks(list):
    def add(self, track, warn_only=False):
        self.append(track)


def make_dl():
    command = dl.__new__(dl)
    command.log = DummyLog()
    command.proxy_providers = []
    return command


def make_title():
    return SimpleNamespace(tracks=DummyTracks())


def service_without_proxies():
    return SimpleNamespace(session=SimpleNamespace(proxies={}))


@pytest.fixture
def temp_dir(tmp_path, monkeypatch):
    target = tmp_path / "temp"
    target.mkdir()
    monkeypatch.setattr(config.directories, "temp", target)
    return target


def test_a_local_subtitle_copy_is_tracked_and_then_removed(tmp_path, temp_dir):
    source = tmp_path / "movie.ja.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf8")

    command = make_dl()
    title = make_title()
    temp_files = []
    command.process_external_subtitle(title, str(source), service_without_proxies(), temp_files_list=temp_files)

    assert temp_files, "the staged copy must be tracked for cleanup"
    assert all(p.exists() for p in temp_files)

    command.cleanup_temp_files(temp_files)

    assert not list(temp_dir.iterdir())
    assert source.exists(), "the user's own file must never be deleted"


def test_a_downloaded_subtitle_is_tracked_and_then_removed(temp_dir, monkeypatch):
    monkeypatch.setattr(
        "unshackle.commands.dl.requests.get",
        lambda *a, **k: SimpleNamespace(content=b"WEBVTT\n", raise_for_status=lambda: None),
    )

    command = make_dl()
    temp_files = []
    command.process_external_subtitle(
        make_title(), "https://cdn.example.com/subs/ja.vtt", service_without_proxies(), temp_files_list=temp_files
    )

    assert temp_files
    command.cleanup_temp_files(temp_files)
    assert not list(temp_dir.iterdir())


def test_every_file_yt_dlp_writes_is_tracked_not_just_the_usable_subtitles(temp_dir, monkeypatch):
    monkeypatch.setattr("unshackle.commands.dl.shutil.which", lambda name: "yt-dlp")
    monkeypatch.setattr("unshackle.commands.dl.binaries.FFMPEG", None)

    def fake_run(cmd, **kwargs):
        out_tmpl = Path(cmd[cmd.index("--output") + 1])
        # yt-dlp writes the subtitle plus leftovers we never turn into tracks
        out_tmpl.with_suffix(".ja.vtt").write_text("WEBVTT\n", encoding="utf8")
        out_tmpl.with_suffix(".info.json").write_text("{}", encoding="utf8")
        out_tmpl.with_suffix(".ja.vtt.part").write_text("", encoding="utf8")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    command = make_dl()
    title = make_title()
    temp_files = []
    command.process_external_subtitle(
        title, "https://tver.jp/episodes/ep12345", service_without_proxies(), temp_files_list=temp_files
    )

    assert len(title.tracks) == 1, "only the .vtt becomes a track"
    assert len(temp_files) == 3, "but all three files must be tracked for cleanup"

    command.cleanup_temp_files(temp_files)
    assert not list(temp_dir.iterdir())


def test_cleanup_tolerates_files_that_are_already_gone(temp_dir):
    command = make_dl()
    missing = temp_dir / "external_sub_gone.srt"
    present = temp_dir / "external_sub_here.srt"
    present.write_text("x", encoding="utf8")

    command.cleanup_temp_files([missing, present])

    assert not present.exists()
    assert not command.log.messages["warning"]


def test_a_locked_file_warns_instead_of_crashing_the_run(temp_dir, monkeypatch):
    command = make_dl()
    locked = temp_dir / "external_sub_locked.srt"
    locked.write_text("x", encoding="utf8")

    def deny(self, missing_ok=False):
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(Path, "unlink", deny)

    command.cleanup_temp_files([locked])

    assert "in use?" in command.log.all_text()


def test_cleanup_is_a_no_op_without_tracked_files():
    command = make_dl()
    command.cleanup_temp_files(None)
    command.cleanup_temp_files([])


def test_the_task_temp_dir_is_removed_with_leftover_subtitle_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config.directories, "temp", tmp_path)

    with task_temp_dir("abc123") as task_dir:
        (task_dir / "external_sub_leftover.srt").write_text("x", encoding="utf8")

    assert not task_dir.exists()
    assert config.directories.temp == tmp_path, "the temp dir must be restored on exit"


def test_a_temp_dir_that_cannot_be_cleared_reports_the_files_holding_it(tmp_path, monkeypatch, caplog):
    task_dir = tmp_path / "task_stuck"
    task_dir.mkdir()
    (task_dir / "tver_sub_abc.ja.vtt").write_text("WEBVTT\n", encoding="utf8")

    monkeypatch.setattr("unshackle.core.temp.shutil.rmtree", lambda *a, **k: None)
    monkeypatch.setattr("unshackle.core.temp.time.sleep", lambda *a: None)

    with caplog.at_level("WARNING"):
        assert remove_task_dir(task_dir) is False

    assert "tver_sub_abc.ja.vtt" in caplog.text
