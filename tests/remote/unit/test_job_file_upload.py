"""Job-scoped host upload endpoints."""

import asyncio
from types import SimpleNamespace

import pytest
from aiohttp import web

from unshackle.core.api.handlers import api_key_authentication
from unshackle.core.api.routes import cors_middleware, setup_routes
from unshackle.core.api import download_manager as dm
from unshackle.core.api import upload
from unshackle.core.config import config

pytestmark = pytest.mark.unit


@pytest.fixture
def upload_app(tmp_path, monkeypatch):
    output = tmp_path / "output.bin"
    output.write_bytes(b"payload")
    monkeypatch.setattr(config.directories, "downloads", tmp_path)
    job = SimpleNamespace(
        status=dm.JobStatus.COMPLETED,
        output_files=[str(output)],
        owner_key="key",
    )
    monkeypatch.setattr(dm, "get_download_manager", lambda: SimpleNamespace(get_job=lambda job_id: job))
    monkeypatch.setenv("BUZZHEAVIER_ACCOUNT_ID", "off")
    monkeypatch.setattr(upload, "_upload_to_gofile", lambda *args: _uploaded(*args))
    upload.clear_upload_state()
    app = web.Application(middlewares=[cors_middleware, api_key_authentication])
    app["config"] = {"users": {"key": {}, "other": {}}}
    app["debug_api"] = False
    setup_routes(app)
    return app, output


async def _uploaded(path, name, folder_id):
    return {
        "downloadPage": "https://gofile.io/d/test",
        "parentFolder": folder_id,
        "size": path.stat().st_size,
        "name": name,
        "deleted": False,
    }


async def test_output_listing_upload_idempotency_and_explicit_delete(aiohttp_client, upload_app):
    app, output = upload_app
    client = await aiohttp_client(app)
    headers = {"X-Secret-Key": "key"}

    listed = await client.get("/api/download/jobs/job/output-files", headers=headers)
    assert listed.status == 200
    assert await listed.json() == {"files": [{"index": 0, "name": "output.bin", "size": 7}]}

    first = await client.post(
        "/api/download/jobs/job/upload-file",
        headers=headers,
        json={"index": 0, "max_bytes": 7},
    )
    second = await client.post(
        "/api/download/jobs/job/upload-file",
        headers=headers,
        json={"index": 0, "max_bytes": 7},
    )
    assert first.status == second.status == 200
    assert (await first.json())["status"] == "uploading"
    await asyncio.sleep(0)
    status = await client.get("/api/download/jobs/job/upload-file/0", headers=headers)
    assert (await status.json())["downloadPage"] == "https://gofile.io/d/test"
    assert output.exists()

    deleted = await client.request("DELETE", "/api/download/jobs/job/delete-file", headers=headers, json={"index": 0})
    assert deleted.status == 200
    assert (await deleted.json())["deleted"] is True
    assert not output.exists()


async def test_upload_requires_owner_and_quota_deletes(aiohttp_client, upload_app):
    app, output = upload_app
    client = await aiohttp_client(app)

    refused = await client.post(
        "/api/download/jobs/job/upload-file",
        headers={"X-Secret-Key": "other"},
        json={"index": 0, "max_bytes": 7},
    )
    assert refused.status == 404

    quota = await client.post(
        "/api/download/jobs/job/upload-file",
        headers={"X-Secret-Key": "key"},
        json={"index": 0, "max_bytes": 6},
    )
    assert quota.status == 200
    assert (await quota.json())["status"] == "uploading"
    for _ in range(50):
        status = await client.get("/api/download/jobs/job/upload-file/0", headers={"X-Secret-Key": "key"})
        body = await status.json()
        if body.get("status") != "uploading":
            break
        await asyncio.sleep(0.05)
    assert body == {"status": "quota_exceeded", "name": "output.bin", "size": 7, "deleted": True}
    assert not output.exists()


async def test_upload_rejects_missing_api_key(aiohttp_client, upload_app):
    app, _ = upload_app
    client = await aiohttp_client(app)
    response = await client.get("/api/download/jobs/job/output-files")
    assert response.status == 401


def test_resolve_job_file_rejects_path_escape(tmp_path, monkeypatch):
    outside = tmp_path.parent / "outside.bin"
    outside.write_bytes(b"secret")
    monkeypatch.setattr(config.directories, "downloads", tmp_path)
    job = SimpleNamespace(output_files=[str(outside)])

    with pytest.raises(Exception, match="outside the downloads directory"):
        upload.resolve_job_file(job, 0)


async def test_buzz_primary_and_gofile_fallback(upload_app, monkeypatch):
    _app, output = upload_app
    called = []

    async def buzz(path, name, job_id):
        called.append(("buzz", job_id))
        return {"downloadPage": "https://buzzheavier.com/folder", "parentFolder": "folder",
                "size": path.stat().st_size, "name": name, "deleted": False}

    async def gofile(path, name, folder_id):
        called.append(("gofile", folder_id))
        return await _uploaded(path, name, folder_id)

    monkeypatch.setenv("BUZZHEAVIER_ACCOUNT_ID", "test-id")
    monkeypatch.setenv("GOFILE_TOKEN", "test-token")
    monkeypatch.setattr(upload, "_upload_to_buzz", buzz)
    monkeypatch.setattr(upload, "_upload_to_gofile", gofile)
    result = await upload.upload_job_file("job", 0, output, output.name, 7, 7, None)
    assert result["downloadPage"] == "https://buzzheavier.com/folder"
    assert called == [("buzz", "job")]
    upload.clear_upload_state()

    async def fail(*_args):
        raise upload._gofile_error("Buzzheavier down")

    monkeypatch.setattr(upload, "_upload_to_buzz", fail)
    result = await upload.upload_job_file("job", 0, output, output.name, 7, 7, None)
    assert result["downloadPage"] == "https://gofile.io/d/test"
    assert called[-1] == ("gofile", None)
    assert output.exists()


async def test_buzz_default_account_id(upload_app, monkeypatch):
    _app, output = upload_app
    called = []

    async def buzz(path, name, job_id):
        called.append(("buzz", job_id, upload._buzz_token()))
        return {"downloadPage": "https://buzzheavier.com/folder", "parentFolder": "folder",
                "size": path.stat().st_size, "name": name, "deleted": False}

    monkeypatch.delenv("BUZZHEAVIER_ACCOUNT_ID", raising=False)
    monkeypatch.setattr(upload, "_upload_to_buzz", buzz)
    result = await upload.upload_job_file("job", 0, output, output.name, 7, 7, None)
    assert result["downloadPage"] == "https://buzzheavier.com/folder"
    assert called == [("buzz", "job", upload.DEFAULT_BUZZ_ACCOUNT_ID)]
    upload.clear_upload_state()


async def test_upstream_failure_keeps_file_and_does_not_cache(aiohttp_client, upload_app, monkeypatch):
    app, output = upload_app
    client = await aiohttp_client(app)

    async def fail(*_args):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(upload, "_upload_to_gofile", fail)
    response = await client.post(
        "/api/download/jobs/job/upload-file",
        headers={"X-Secret-Key": "key"},
        json={"index": 0, "max_bytes": 7},
    )
    assert response.status == 200
    await asyncio.sleep(0)
    status = await client.get("/api/download/jobs/job/upload-file/0", headers={"X-Secret-Key": "key"})
    assert status.status == 424
    assert output.exists()


def test_buzz_default_location_id():
    assert upload.DEFAULT_BUZZ_LOCATION_ID == "g65gmc5rfv2c"


async def test_buzz_upload_uses_location_param(tmp_path, monkeypatch):
    file = tmp_path / "test.bin"
    file.write_bytes(b"data")
    put_urls = []

    class DummyResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def json(self, **kwargs):
            return {"data": {"id": "dummy-id"}}

    class DummySession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def get(self, url, **kwargs):
            return DummyResponse()

        def post(self, url, **kwargs):
            return DummyResponse()

        def put(self, url, **kwargs):
            put_urls.append(url)
            return DummyResponse()

    monkeypatch.setattr(upload.aiohttp, "ClientSession", DummySession)
    monkeypatch.setattr(upload, "_buzz_folders", {"job": "folder123"})
    result = await upload._upload_to_buzz(file, "test.bin", "job")
    assert result["downloadPage"] == "https://buzzheavier.com/folder123"
    assert len(put_urls) == 1
    assert "locationId=g65gmc5rfv2c" in put_urls[0]
    assert "note=" in put_urls[0]
