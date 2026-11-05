import ast
import json
import re
import unittest.mock as mock
from pathlib import Path
from typing import Any

from click import Command, Context
from unshackle.core.search_result import SearchResult
from unshackle.services.FOD import FOD
from unshackle.services.HULUJP import HULUJP
from unshackle.services.TELASA import TELASA


def _make_context(cmd_name: str | None, service_name: str) -> Context:
    parent = Context(Command(cmd_name), info_name=cmd_name) if cmd_name else None
    ctx = Context(Command(service_name), parent=parent, info_name=service_name)
    ctx.parent = parent
    return ctx


def _extract_method_ast(source_path: Path, class_name: str, method_name: str) -> ast.FunctionDef:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return item
    raise AssertionError(f"{class_name}.{method_name} not found in {source_path}")


def test_fod_search_contract() -> None:
    ctx = _make_context("search", "FOD")
    service = object.__new__(FOD)
    service.ctx = ctx
    service.title_input = "test query"
    service.title = "test"
    service.WEB_USER_AGENT = "TestAgent/1.0"
    service.session = mock.MagicMock()
    service.session.headers = {"X-Authorization": "Bearer leaked"}

    mock_resp = mock.MagicMock()
    mock_resp.json.return_value = {
        "search_result": [
            {
                "lu_id": "8k42",
                "title": "Sample Drama",
                "text": "FOD synopsis",
                "product_year": 2024,
            },
            {
                "lu_id": "../invalid/path",
                "title": "Ignored",
                "text": "",
                "product_year": None,
            },
        ]
    }
    service.session.get.return_value = mock_resp

    results = list(FOD.search(service))

    assert len(results) == 1
    res = results[0]
    assert isinstance(res, SearchResult)
    assert res.id == "8k42"
    assert res.title == "Sample Drama"
    assert res.description == "FOD synopsis"
    assert res.label == "2024"
    assert res.url == "https://fod.fujitv.co.jp/title/8k42"
    assert re.search(FOD.TITLE_RE, res.url).group("id") == res.id
    assert re.search(FOD.TITLE_RE, res.id).group("id") == res.id

    assert service.session.headers.get("X-Authorization") == "Bearer leaked"
    called_url = service.session.get.call_args.kwargs["url"]
    called_headers = service.session.get.call_args.kwargs["headers"]
    called_params = service.session.get.call_args.kwargs["params"]
    assert called_url == "https://search-fujitv.dga.jp/api/search_result_api.php"
    assert "X-Authorization" not in called_headers
    assert called_params["keyword"] == "test query"
    assert called_params["site"] == "fod"


def test_hulu_jp_search_contract() -> None:
    ctx = _make_context("search", "HULUJP")
    service = object.__new__(HULUJP)
    service.ctx = ctx
    service.title_input = "test query"
    service.title = "test"
    service.config = {"user_agent": "HuluAgent/1.0"}
    service.session = mock.MagicMock()
    service.session.headers = {}

    page_data = {
        "video": {
            "total_count": 2,
            "data": [
                {
                    "slug": "sample-series",
                    "title": "Sample Series",
                    "description": "Hulu synopsis",
                    "additionalInfo": {"card_info": {"premiere_year": 2023}},
                },
                {
                    "slug": "store/rental-movie",
                    "title": "Rental Movie",
                    "description": "Store rental",
                    "bottomMetas": ["2022年", "120分"],
                },
            ],
        }
    }
    encoded_state = json.dumps(f'1:null,{json.dumps(page_data)}')
    mock_resp = mock.MagicMock()
    mock_resp.text = f'self.__next_f.push([1,{encoded_state}])'
    service.session.get.return_value = mock_resp

    results = list(HULUJP.search(service))

    assert len(results) == 2
    assert [r.id for r in results] == ["sample-series", "store/rental-movie"]
    assert [r.label for r in results] == ["2023", "2022"]
    assert [r.url for r in results] == [
        "https://www.hulu.jp/sample-series",
        "https://www.hulu.jp/store/rental-movie",
    ]

    hulu_regex = r"^(?:https?://(?:www\.)?hulu\.jp/)(?P<id>(?:store/)?[a-z0-9-]+)"
    for r in results:
        assert isinstance(r, SearchResult)
        assert re.search(hulu_regex, r.url).group("id") == r.id

    called_url = service.session.get.call_args.args[0]
    called_params = service.session.get.call_args.kwargs["params"]
    assert called_url == "https://www.hulu.jp/search"
    assert called_params == {"q": "test query"}


def test_telasa_search_contract() -> None:
    ctx = _make_context("search", "TELASA")
    service = object.__new__(TELASA)
    service.ctx = ctx
    service.title_input = "test query"
    service.title_id = "test"
    service.session = mock.MagicMock()
    service.session.headers = {
        "User-Agent": "TelasaAgent/1.0",
        "Origin": "https://www.telasa.jp",
        "Referer": "https://www.telasa.jp/"
    }

    mock_resp = mock.MagicMock()
    mock_resp.json.return_value = {
        "data": {
            "result_items": [
                {
                    "data": {
                        "id": 12345,
                        "name": "Sample Tokusatsu",
                        "synopses": {"long": "Hero story"},
                    }
                },
                {
                    "data": {
                        "id": "not-numeric",
                        "name": "Skipped",
                    }
                },
            ]
        }
    }
    service.session.post.return_value = mock_resp

    results = list(TELASA.search(service))

    assert len(results) == 1
    res = results[0]
    assert isinstance(res, SearchResult)
    assert res.id == "https://www.telasa.jp/series/12345"
    assert res.title == "Sample Tokusatsu"
    assert res.description == "Hero story"
    assert res.url == "https://www.telasa.jp/series/12345"

    m = re.search(TELASA.TITLE_RE, res.url)
    assert m and m.group("type") == "series" and m.group("id") == "12345"

    called_url = service.session.post.call_args.kwargs["url"]
    called_params = service.session.post.call_args.kwargs["params"]
    called_json = service.session.post.call_args.kwargs["json"]
    called_headers = service.session.post.call_args.kwargs["headers"]
    assert called_url == "https://api-videopass-anon.kddi-video.com/v2/search/videos"
    assert called_params == {"page": 1, "per_page": 20}
    assert called_json == {"query": "test query", "video_type": "unlimited"}
    assert called_headers["Origin"] == "https://www.telasa.jp"
    assert called_headers["Referer"] == "https://www.telasa.jp/"
    assert called_headers["Content-Type"] == "application/json"
    assert re.fullmatch(r"[0-9a-fA-F-]{36}", called_headers["X-Device-Id"])


def test_auth_rejection_boundaries() -> None:
    services_dir = Path(__file__).resolve().parent.parent / "unshackle" / "services"

    for service_name, cls, source_file in (
        ("FOD", FOD, services_dir / "FOD" / "__init__.py"),
        ("HULUJP", HULUJP, services_dir / "HULUJP" / "__init__.py"),
        ("TELASA", TELASA, services_dir / "TELASA" / "__init__.py"),
    ):
        fn_ast = _extract_method_ast(source_file, service_name, "authenticate")
        ast_src = ast.unparse(fn_ast)
        assert "info_name" in ast_src, f"{service_name}.authenticate must check ctx info_name"

        # 1. CLI search: unauthenticated call must succeed without exception or exit
        search_ctx = _make_context("search", service_name)
        svc_search = object.__new__(cls)
        svc_search.ctx = search_ctx
        svc_search.session = mock.MagicMock()
        svc_search.session.cookies = []
        svc_search.credentials = None
        svc_search.auth_method = "credentials"
        svc_search.log = mock.MagicMock()
        cls.authenticate(svc_search, None, None)

        # 2. DL context (no search in command name): unauthenticated call must still be rejected
        dl_ctx = _make_context("dl", service_name)
        svc_dl = object.__new__(cls)
        svc_dl.ctx = dl_ctx
        svc_dl.session = mock.MagicMock()
        svc_dl.session.cookies = []
        svc_dl.credentials = None
        svc_dl.log = mock.MagicMock()
        svc_dl.cache = mock.MagicMock()
        svc_dl.cache_key = "dummy"
        svc_dl.config = {"endpoints": {"auth": "https://dummy"}}
        svc_dl.auth_method = "credentials"

        rejected = False
        try:
            with mock.patch("sys.exit", side_effect=SystemExit(1)):
                cls.authenticate(svc_dl, None, None)
        except (ValueError, SystemExit):
            rejected = True
        assert rejected, f"{service_name}.authenticate must reject unauthenticated downloads"


if __name__ == "__main__":
    test_fod_search_contract()
    test_hulu_jp_search_contract()
    test_telasa_search_contract()
    test_auth_rejection_boundaries()
    print("ALL TESTS PASSED")
