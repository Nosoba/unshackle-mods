"""Safe, job-scoped uploads of completed download files."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import aiohttp

from unshackle.core.api.errors import APIError, APIErrorCode
from unshackle.core.config import config

log = logging.getLogger("unshackle.core.api.upload")

GOFILE_UPLOAD_URL = "https://upload.gofile.io/uploadfile"
BUZZ_API_URL = "https://buzzheavier.com/api"
BUZZ_UPLOAD_URL = "https://w.buzzheavier.com"
BUZZ_NOTE_B64 = base64.b64encode(b"uploaded by NSBC").decode("ascii")
DEFAULT_BUZZ_ACCOUNT_ID = "JWHZ3YDGCAXOU9QTXPXO"
DEFAULT_BUZZ_LOCATION_ID = "g65gmc5rfv2c"


def _buzz_token() -> str:
    raw = os.environ.get("BUZZHEAVIER_ACCOUNT_ID", "").strip()
    return "" if raw.lower() in ("off", "disabled", "false", "0") else (raw or DEFAULT_BUZZ_ACCOUNT_ID)


@dataclass
class UploadRecord:
    result: dict[str, Any]
    path: Path
    size: int


_records: dict[tuple[str, int], UploadRecord] = {}
_locks: dict[tuple[str, int], asyncio.Lock] = {}
_buzz_folders: dict[str, str] = {}
_buzz_locks: dict[str, asyncio.Lock] = {}
# ponytail: process-local tasks are lost on API restart; files remain for a fresh retry.
_tasks: dict[tuple[str, int], asyncio.Task[dict[str, Any]]] = {}


def _lock_for(job_id: str, index: int) -> asyncio.Lock:
    return _locks.setdefault((job_id, index), asyncio.Lock())


def _invalid_file(message: str) -> APIError:
    return APIError(APIErrorCode.INVALID_INPUT, message)


def resolve_job_file(job: Any, index: int) -> tuple[Path, str, int]:
    """Resolve one server-produced job output without accepting a new filesystem path."""
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise _invalid_file("index must be a non-negative integer")
    files = getattr(job, "output_files", None)
    if not isinstance(files, list) or index >= len(files):
        raise _invalid_file("index does not identify a job output file")

    raw = files[index]
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise _invalid_file("job output file is not a usable path")

    root = Path(config.directories.downloads).resolve()
    lexical = Path(raw)
    if not lexical.is_absolute():
        lexical = root / lexical

    # Check the lexical path before resolve(): resolve() would hide a symlink by replacing it
    # with its target. The resolved path is checked separately for traversal.
    current = lexical
    while current != current.parent:
        if current.is_symlink():
            raise _invalid_file("job output file cannot be a symlink")
        current = current.parent

    try:
        path = lexical.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _invalid_file("job output file is unavailable") from exc
    if not path.is_relative_to(root):
        raise _invalid_file("job output file is outside the downloads directory")
    if path.is_symlink() or not path.is_file():
        raise _invalid_file("job output file must be a regular file")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise _invalid_file("job output file cannot be read") from exc
    return path, path.name, size


def _gofile_error(message: str, *, retryable: bool = False) -> APIError:
    return APIError(APIErrorCode.NETWORK_ERROR, message, retryable=retryable)


def _buzz_id(payload: Any) -> str:
    data = payload.get("data") if isinstance(payload, dict) else None
    value = data.get("id") if isinstance(data, dict) else None
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise _gofile_error("Buzzheavier returned no usable ID")
    return value


async def _upload_to_buzz(path: Path, name: str, job_id: str) -> dict[str, Any]:
    token = _buzz_token()
    if not token:
        raise APIError(APIErrorCode.SERVICE_UNAVAILABLE, "BUZZHEAVIER_ACCOUNT_ID is not configured")
    headers = {"Authorization": f"Bearer {token}"}
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=900)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if job_id not in _buzz_folders:
                async with _buzz_locks.setdefault(job_id, asyncio.Lock()):
                    if job_id not in _buzz_folders:
                        log.debug("Buzzheavier: requesting root directory for job %s...", job_id)
                        async with session.get(f"{BUZZ_API_URL}/fs", headers=headers) as response:
                            if response.status != 200:
                                try:
                                    snippet = (await response.text())[:300]
                                except Exception:
                                    snippet = ""
                                log.warning("Buzzheavier root lookup failed HTTP %s: %s", response.status, snippet)
                                raise _gofile_error(f"Buzzheavier root: HTTP {response.status} ({snippet})")
                            root = _buzz_id(await response.json(content_type=None))
                        log.debug("Buzzheavier root ID %s; creating subfolder for job %s...", root, job_id)
                        async with session.post(
                            f"{BUZZ_API_URL}/fs/{quote(root, safe='')}",
                            json={"name": f"unshackle {job_id}"}, headers=headers,
                        ) as response:
                            if response.status not in (200, 201):
                                try:
                                    snippet = (await response.text())[:300]
                                except Exception:
                                    snippet = ""
                                log.warning("Buzzheavier folder creation failed HTTP %s: %s", response.status, snippet)
                                raise _gofile_error(f"Buzzheavier folder: HTTP {response.status} ({snippet})")
                            _buzz_folders[job_id] = _buzz_id(await response.json(content_type=None))
                            log.info("Buzzheavier subfolder created: %s for job %s", _buzz_folders[job_id], job_id)
            folder = _buzz_folders[job_id]
            safe_name = "".join("_" if c in "#;|\\/" or ord(c) < 32 else c for c in name)
            loc_id = os.environ.get("BUZZHEAVIER_LOCATION_ID", "").strip() or DEFAULT_BUZZ_LOCATION_ID
            params = {"note": BUZZ_NOTE_B64}
            if loc_id:
                params["locationId"] = loc_id
            query = urlencode(params)
            log.info("Uploading %s (%s bytes) to Buzzheavier folder %s (location=%s)...", safe_name, path.stat().st_size, folder, loc_id)
            with closing(path.open("rb")) as file:
                async with session.put(
                    f"{BUZZ_UPLOAD_URL}/{quote(folder, safe='')}/{quote(safe_name, safe='')}?{query}",
                    data=file, headers={**headers, "Content-Length": str(path.stat().st_size)},
                ) as response:
                    if response.status not in (200, 201):
                        try:
                            snippet = (await response.text())[:300]
                        except Exception:
                            snippet = ""
                        log.warning("Buzzheavier file upload failed HTTP %s: %s", response.status, snippet)
                        raise _gofile_error(f"Buzzheavier upload: HTTP {response.status} ({snippet})")
                    data = _buzz_id(await response.json(content_type=None))
                    log.info("Buzzheavier file upload finished for %s, ID: %s", safe_name, data)
    except asyncio.CancelledError:
        raise
    except APIError:
        raise
    except (OSError, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        log.warning("Buzzheavier upload unavailable: %s", exc)
        raise _gofile_error(f"Buzzheavier upload unavailable: {exc}", retryable=True) from exc
    return {"downloadPage": f"https://buzzheavier.com/{folder}", "parentFolder": folder,
            "size": path.stat().st_size, "name": safe_name, "deleted": False}


async def _upload_to_gofile(path: Path, name: str, folder_id: str | None) -> dict[str, Any]:
    token = os.environ.get("GOFILE_TOKEN")
    if not token:
        raise APIError(APIErrorCode.SERVICE_UNAVAILABLE, "GOFILE_TOKEN is not configured")

    try:
        with closing(path.open("rb")) as file:
            form = aiohttp.FormData(quote_fields=False)
            form.add_field("file", file, filename=name, content_type="application/octet-stream")
            if folder_id:
                form.add_field("folderId", folder_id)
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=900)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    GOFILE_UPLOAD_URL, data=form, headers={"Authorization": f"Bearer {token}"}
                ) as response:
                    try:
                        payload = await response.json(content_type=None)
                    except (ValueError, aiohttp.ClientError) as exc:
                        raise _gofile_error("Gofile returned an invalid response", retryable=True) from exc
                    if (
                        not isinstance(payload, dict)
                        or response.status < 200
                        or response.status >= 300
                        or payload.get("status") != "ok"
                    ):
                        raise _gofile_error("Gofile rejected the upload", retryable=response.status >= 500)
    except asyncio.CancelledError:
        raise
    except APIError:
        raise
    except (OSError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise _gofile_error("Gofile upload failed", retryable=True) from exc

    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("downloadPage"), str):
        raise _gofile_error("Gofile returned no download page", retryable=True)
    return {
        "downloadPage": data["downloadPage"],
        "parentFolder": data.get("parentFolder"),
        "size": path.stat().st_size,
        "name": name,
        "deleted": False,
    }


async def upload_job_file(
    job_id: str, index: int, path: Path, name: str, size: int, max_bytes: int, folder_id: str | None
) -> dict[str, Any]:
    """Upload once, cache a successful response, and delete quota-exceeding files immediately."""
    key = (job_id, index)
    async with _lock_for(job_id, index):
        record = _records.get(key)
        if record:
            if record.path != path or record.size != size:
                raise APIError(APIErrorCode.CONFLICT, "Job output changed since upload")
            if size > max_bytes:
                raise APIError(
                    APIErrorCode.CONFLICT, "File was already uploaded; use delete-file after posting its link"
                )
            return dict(record.result)

        if size > max_bytes:
            deleted = await _delete_path(path)
            return {"status": "quota_exceeded", "name": name, "size": size, "deleted": deleted}

        buzz_token = _buzz_token()
        if buzz_token:
            try:
                log.info("Job %s: uploading %s to Buzzheavier...", job_id, name)
                result = await _upload_to_buzz(path, name, job_id)
                log.info("Job %s: Buzzheavier upload complete for %s: %s", job_id, name, result.get("downloadPage"))
            except Exception as exc:
                log.warning("Job %s: Buzzheavier upload failed for %s (%s); falling back to Gofile", job_id, name, exc)
                if not os.environ.get("GOFILE_TOKEN"):
                    raise
                log.info("Job %s: uploading %s to fallback Gofile...", job_id, name)
                result = await _upload_to_gofile(path, name, folder_id)
                log.info("Job %s: fallback Gofile upload complete for %s: %s", job_id, name, result.get("downloadPage"))
        else:
            log.info("Job %s: Buzzheavier disabled, uploading %s to Gofile...", job_id, name)
            result = await _upload_to_gofile(path, name, folder_id)
            log.info("Job %s: Gofile upload complete for %s: %s", job_id, name, result.get("downloadPage"))
        _records[key] = UploadRecord(result, path, size)
        return dict(result)


def upload_status(job_id: str, index: int) -> dict[str, Any]:
    task = _tasks.get((job_id, index))
    if task is None:
        record = _records.get((job_id, index))
        if record:
            return dict(record.result)
        raise APIError(APIErrorCode.NOT_FOUND, "No active upload; retry the upload request")
    if not task.done():
        return {"status": "uploading"}
    try:
        return task.result()
    except APIError:
        raise
    except (Exception, asyncio.CancelledError) as exc:
        raise _gofile_error("Upload failed; local file retained", retryable=True) from exc


def start_upload(
    job_id: str, index: int, path: Path, name: str, size: int, max_bytes: int, folder_id: str | None
) -> dict[str, Any]:
    key = (job_id, index)
    task = _tasks.get(key)
    if task is not None and not task.done():
        return {"status": "uploading"}
    if task is not None:
        try:
            result = task.result()
            if result.get("status") != "quota_exceeded" and size > max_bytes:
                raise APIError(
                    APIErrorCode.CONFLICT, "File was already uploaded; use delete-file after posting its link"
                )
            return result
        except APIError as exc:
            if exc.error_code is APIErrorCode.CONFLICT:
                raise
        except (Exception, asyncio.CancelledError):
            pass  # A failed attempt is retryable; the output is still on disk.
    _tasks[key] = asyncio.create_task(upload_job_file(job_id, index, path, name, size, max_bytes, folder_id))
    return {"status": "uploading"}


async def delete_uploaded_job_file(job_id: str, index: int, path: Path) -> dict[str, Any]:
    key = (job_id, index)
    async with _lock_for(job_id, index):
        record = _records.get(key)
        if not record or record.path != path:
            raise APIError(APIErrorCode.CONFLICT, "File has not been uploaded")
        if not record.result["deleted"]:
            record.result["deleted"] = await _delete_path(path)
        return dict(record.result)


async def _delete_path(path: Path) -> bool:
    try:
        if path.is_symlink():
            return False
        if not path.exists():
            return True
        if not path.is_file():
            return False
        await asyncio.to_thread(path.unlink)
        return True
    except OSError:
        return False


def clear_upload_state() -> None:
    """Test/support hook; successful upload state is process-local by design."""
    _records.clear()
    _locks.clear()
    _tasks.clear()
    _buzz_folders.clear()
    _buzz_locks.clear()
