import ast
import json
from pathlib import Path
from unittest.mock import MagicMock

# Import SearchResult to assert actual returned objects
from unshackle.core.search_result import SearchResult
from unshackle.services.AMZN import AMZN
from unshackle.services.DMM import DMM
from unshackle.services.DSNP import DSNP

def test_ast_integrity():
    for name in ("AMZN", "DSNP", "DMM"):
        p = Path(f"unshackle/services/{name}/__init__.py")
        tree = ast.parse(p.read_text(encoding="utf-8"))
        search_node = next(
            (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "search"),
            None,
        )
        assert search_node is not None, f"{name}.search function missing"
        source = ast.unparse(search_node)
        assert "yield SearchResult(" in source, f"{name}.search must yield SearchResult"

def test_amzn_search_mock():
    # Mock instance of AMZN
    instance = MagicMock(spec=AMZN)
    instance.title = "test anime"
    instance.region = {"base": "www.amazon.co.jp"}

    # Mock response for /s HTML scrape
    html_sample = (
        '<html><article class="s-result-item">'
        '<a href="/dp/B08XYZ1234">link</a>'
        '<span data-card-title="Sample Anime Title"></span>'
        '</article></html>'
    )
    scrape_resp = MagicMock()
    scrape_resp.ok = True
    scrape_resp.text = html_sample

    # Mock response for enrichItemMetadata
    enrich_data = {
        "enrichments": {
            "B08XYZ1234": {
                "displayTitle": "Sample Anime Title (Enriched)",
                "releaseYear": 2024,
                "synopsis": "A sample description of anime.",
            }
        }
    }
    enrich_resp = MagicMock()
    enrich_resp.ok = True
    enrich_resp.json.return_value = enrich_data

    instance.session = MagicMock()
    instance.session.get.return_value = scrape_resp
    instance.session.post.return_value = enrich_resp

    results = list(AMZN.search(instance))
    assert len(results) == 1, "Expected 1 search result"
    res = results[0]
    assert isinstance(res, SearchResult), "Must return SearchResult instance"
    assert res.id == "B08XYZ1234", f"Expected ASIN, got {res.id}"
    assert res.title == "Sample Anime Title (Enriched)"
    assert res.label == "2024"
    assert res.description == "A sample description of anime."
    assert res.url == "https://www.amazon.co.jp/dp/B08XYZ1234"

def test_dsnp_search_mock():
    instance = MagicMock(spec=DSNP)
    instance.title = "star wars"

    # Mock _get_search response
    data = {
        "page": {
            "containers": [
                {
                    "items": [
                        {
                            "id": "1234-abcd-5678",
                            "visuals": {
                                "title": "Star Wars Show",
                                "description": {"brief": "A galaxy far away."},
                                "metastringParts": {
                                    "releaseYearRange": {"startYear": 2023}
                                },
                            },
                        }
                    ]
                }
            ]
        }
    }
    instance._get_search = MagicMock()
    instance._get_search.return_value = data

    results = list(DSNP.search(instance))
    assert len(results) == 1, "Expected 1 search result"
    res = results[0]
    assert isinstance(res, SearchResult), "Must return SearchResult instance"
    assert res.id == "entity-1234-abcd-5678"
    assert res.title == "Star Wars Show"
    assert res.label == "2023"
    assert res.description == "A galaxy far away."
    assert res.url == "https://disneyplus.com/browse/entity-1234-abcd-5678"

def test_dmm_search_mock():
    instance = MagicMock(spec=DMM)
    instance.title = "sample dmm title"
    instance.profiles_id = "prof123"
    instance.config = {"endpoints": {"graphql": "https://api.tv.dmm.com/graphql"}}

    # Mock response of FetchSearchVideos GraphQL
    gql_data = {
        "data": {
            "searchVideos": {
                "edges": [
                    {
                        "node": {
                            "id": "season999",
                            "titleName": "Sample Series",
                            "seasonName": "Season 1",
                            "description": "DMM series synopsis here.",
                            "seasonType": "MULTI_EPISODES",
                        }
                    }
                ]
            }
        }
    }
    gql_resp = MagicMock()
    gql_resp.json.return_value = gql_data
    instance.session = MagicMock()
    instance.session.post.return_value = gql_resp

    results = list(DMM.search(instance))
    assert len(results) == 1, "Expected 1 search result"
    res = results[0]
    assert isinstance(res, SearchResult), "Must return SearchResult instance"
    assert res.id == "season999"
    assert res.title == "Sample Series Season 1"
    assert res.description == "DMM series synopsis here."
    assert res.label == "MULTI_EPISODES"
    assert res.url == "https://tv.dmm.com/vod?season=season999"

if __name__ == "__main__":
    test_ast_integrity()
    test_amzn_search_mock()
    test_dsnp_search_mock()
    test_dmm_search_mock()
    print("ALL SEARCH SELFCHECKS PASSED: AMZN, DSNP, DMM")
