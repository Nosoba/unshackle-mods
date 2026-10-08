"""Offline coverage of the local DSNP OTP send method, without importing services.

Custom services are ignored and may not exist on CI. Extract only this method's
AST; importing DSNP would load deployment configuration and optional dependencies.
"""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from unshackle.core.api.errors import APIError
from unshackle.core.api.input_bridge import InputBridge, StatelessInputBridge
from unshackle.core.service import Service


@pytest.fixture
def request_otp():
    path = Path(__file__).resolve().parents[3] / "unshackle" / "services" / "DSNP" / "__init__.py"
    if not path.is_file():
        pytest.skip("local DSNP service is not installed")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DSNP")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_request_otp")
    scope = {"queries": SimpleNamespace(REQUESET_OTP="test-query")}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), scope)
    return scope["_request_otp"]


@pytest.mark.parametrize("mode", ["stateless", "cli", "worker", "remote"])
def test_dsnp_preflight_runs_before_request_otp_http(request_otp, mode):
    service = SimpleNamespace(
        _input_bridge={"stateless": StatelessInputBridge(), "remote": InputBridge()}.get(mode),
        prod_config={"services": {"orchestration": {"client": {"endpoints": {"query": {"href": "https://invalid"}}}}}},
        config={"device": {"platform_id": "fake-platform"}},
        _request=Mock(return_value={"data": {"requestOtp": {"accepted": True}}}),
    )
    service.ensure_input_supported = lambda: Service.ensure_input_supported(service)
    if mode == "stateless":
        for _ in range(3):
            with pytest.raises(APIError) as raised:
                request_otp(service, "test@example.invalid", "fake-token")
            assert raised.value.details == {"reason": "interactive_auth_required"}
        service._request.assert_not_called()
    else:
        request_otp(service, "test@example.invalid", "fake-token")
        service._request.assert_called_once()
        assert service._request.call_args.kwargs["payload"]["operationName"] == "requestOtp"
        if service._input_bridge is not None:
            assert service._input_bridge.get_pending_prompt() is None
            assert not service._input_bridge.answered
