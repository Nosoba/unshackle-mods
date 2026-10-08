"""Stateless auth must fail fast; remote sessions and download workers still relay input."""

import asyncio
import builtins
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import unshackle.core.console as console_module
import unshackle.core.service as service_module
from unshackle.core.api import download_worker, handlers
from unshackle.core.api.errors import APIError, APIErrorCode, build_error_response
from unshackle.core.api.input_bridge import AuthStatus, InputBridge, StatelessInputBridge
from unshackle.core.service import Service


PROMPT = "Enter a OTP code (Check email): "
RESULT = {"id": "t", "title": "Title", "description": "Description", "label": "Movie", "url": "https://example.com/t"}


class FakeService:
    # Exercise the real dispatch without Service.__init__ doing any external setup.
    request_input = Service.request_input
    prompt_on_auth = True

    def __init__(self):
        self._input_bridge = None
        self.login_material = None
        self.authenticated = False

    def authenticate(self, cookies, credential):
        self.login_material = (cookies, credential)
        if self.prompt_on_auth:
            self.request_input(PROMPT)
        self.authenticated = True

    def search(self):
        assert self.authenticated
        yield SimpleNamespace(**RESULT)

    def get_titles(self):
        assert self.authenticated
        return []


@pytest.fixture(autouse=True)
def no_terminal(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Stateless authentication must not wait or read from the API terminal")

    monkeypatch.setattr(builtins, "input", forbidden)
    monkeypatch.setattr(service_module, "prompt_user", forbidden)
    monkeypatch.setattr(console_module.console, "input", forbidden)
    return forbidden


@pytest.fixture(params=["list", "search"])
def operation(request):
    return request.param


@pytest.fixture
def services(monkeypatch):
    instances = []

    def instantiate(*args, **kwargs):
        service = FakeService()
        instances.append(service)
        return service

    monkeypatch.setattr(handlers, "validate_service", lambda *args: "FAKE")
    monkeypatch.setattr(handlers, "load_service_yaml", lambda *args: {})
    monkeypatch.setattr(handlers, "resolve_handler_proxy", lambda *args: (None, []))
    monkeypatch.setattr(handlers, "resolve_server_account", lambda *args: None)
    monkeypatch.setattr(handlers, "load_full_cdm", lambda *args: None)
    monkeypatch.setattr(handlers, "server_login_material", lambda *args: ("cookies", "credential"))
    monkeypatch.setattr(handlers, "build_parent_ctx", lambda *args, **kwargs: None)
    monkeypatch.setattr(handlers.Services, "load", staticmethod(lambda *args: None))
    monkeypatch.setattr(handlers, "instantiate_service", instantiate)
    monkeypatch.setattr(handlers.RequestCache, "attach", lambda *args: None)
    return instances


def run_stateless(operation):
    if operation == "list":
        return handlers.setup_list_service({}, "FAKE", None, "t")
    return handlers.run_service_search({}, "FAKE", "query")


def test_guard_rejects_without_waiting_or_exposing_prompt(monkeypatch, no_terminal):
    bridge = StatelessInputBridge()
    monkeypatch.setattr(InputBridge, "request_input", no_terminal)
    monkeypatch.setattr(bridge._response_ready, "wait", no_terminal)

    with pytest.raises(APIError, match="Start a download job or remote session") as raised:
        bridge.request_input("Private account information", timeout=600)

    error = raised.value
    assert error.error_code is APIErrorCode.AUTH_FAILED
    assert error.http_status == 401
    assert error.details == {"reason": "interactive_auth_required"}
    assert not error.retryable
    assert "Private account information" not in json.dumps(json.loads(build_error_response(error).body))
    assert bridge.status is AuthStatus.FAILED
    assert bridge.get_pending_prompt() is None


def test_stateless_attaches_guard_before_authentication(operation, services):
    with pytest.raises(APIError) as raised:
        run_stateless(operation)

    assert raised.value.error_code is APIErrorCode.AUTH_FAILED
    service = services[0]
    assert isinstance(service._input_bridge, StatelessInputBridge)
    assert service.login_material == ("cookies", "credential")
    assert not service.authenticated


def test_no_prompt_paths_succeed_with_per_instance_guards(operation, services, monkeypatch):
    monkeypatch.setattr(FakeService, "prompt_on_auth", False)
    for _ in range(2):
        result = run_stateless(operation)
        service = services[-1]
        assert service.authenticated
        assert service.login_material == ("cookies", "credential")
        assert isinstance(service._input_bridge, StatelessInputBridge)
        if operation == "list":
            assert result is service
        else:
            assert result == [RESULT]
    assert services[0]._input_bridge is not services[1]._input_bridge


@pytest.mark.asyncio
async def test_stateless_handlers_propagate_auth_failed_and_clean_up(operation, services, monkeypatch):
    cleanup = Mock()
    monkeypatch.setattr(handlers.RequestCache, "cleanup", cleanup)
    handler = handlers.list_titles_handler if operation == "list" else handlers.search_handler
    with pytest.raises(APIError) as raised:
        await handler({"service": "FAKE", "title_id": "t", "query": "query"})

    assert raised.value.error_code is APIErrorCode.AUTH_FAILED
    assert json.loads(build_error_response(raised.value).body)["error_code"] == "AUTH_FAILED"
    cleanup.assert_called_once_with()


@pytest.mark.asyncio
async def test_remote_bridge_still_relays_service_input():
    service = FakeService()
    bridge = service._input_bridge = InputBridge()
    with ThreadPoolExecutor(max_workers=1) as pool:
        answer = pool.submit(service.request_input, PROMPT)
        try:
            for _ in range(200):
                if bridge.get_pending_prompt() is not None:
                    break
                await asyncio.sleep(0.01)
            assert bridge.get_pending_prompt() == PROMPT
            assert bridge.status is AuthStatus.PENDING_INPUT
            assert bridge.submit_response("123456")
            assert answer.result(timeout=2) == "123456"
            assert bridge.status is AuthStatus.AUTHENTICATING
        finally:
            bridge.cancel()


def test_download_worker_still_relays_service_input(monkeypatch):
    monkeypatch.setattr(service_module, "prompt_user", console_module.prompt_user)
    monkeypatch.setattr(download_worker, "AUTH_INPUT_TIMEOUT", 2)
    read_fd, write_fd = os.pipe()
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(fileno=lambda: read_fd))
    updates = []

    def answer_on_prompt(update):
        updates.append(update)
        if update["input_prompt"]:
            os.write(write_fd, b'"123456"\n')

    previous_handler = console_module._prompt_handler
    try:
        download_worker.relay_prompts(answer_on_prompt)
        service = FakeService()
        assert service._input_bridge is None
        assert service.request_input(PROMPT) == "123456"
    finally:
        console_module.set_prompt_handler(previous_handler)
        os.close(write_fd)
        os.close(read_fd)
    assert updates == [{"input_prompt": PROMPT}, {"input_prompt": None}]
