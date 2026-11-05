"""Offline assert selfcheck for Lemino, NHKOnDemand, and WOWOW search implementations."""
import base64
import json
from unittest.mock import MagicMock
from unshackle.core.search_result import SearchResult

# --- LEMINO CHECK ---
from unshackle.services.Lemino import Lemino

lemino_inst = Lemino.__new__(Lemino)
lemino_inst.title = "test"
lemino_inst.session = MagicMock()

fake_lemino_resp = MagicMock()
fake_lemino_resp.ok = True
fake_lemino_resp.json.return_value = {
    "items": [
        {
            "crid": "crid://plala.iptvf.jp/vod/0000000000_fake123",
            "title": "Lemino Title 1",
            "synopsis": "Some outline description",
            "display_start": "2026-01-01T00:00:00",
        }
    ]
}
lemino_inst.session.get.return_value = fake_lemino_resp

lemino_results = list(lemino_inst.search())
assert len(lemino_results) == 1, f"Expected 1 result, got {len(lemino_results)}"
res = lemino_results[0]
assert isinstance(res, SearchResult)
assert res.title == "Lemino Title 1"
# Verify ID is base64 encoded crid as expected by get_titles
decoded_crid = base64.b64decode(res.id).decode("utf-8")
assert decoded_crid == "crid://plala.iptvf.jp/vod/0000000000_fake123"
assert res.label == "2026"

# --- NHK ON DEMAND CHECK ---
from unshackle.services.NHKOnDemand import NHKOnDemand

nhk_inst = NHKOnDemand.__new__(NHKOnDemand)
nhk_inst.title = "nhk test"
nhk_inst.session = MagicMock()

fake_nhk_resp = MagicMock()
fake_nhk_resp.ok = True
fake_nhk_resp.json.return_value = {
    "result": {
        "items": [
            {
                "program_id": "P202600123400000",
                "title": "NHK Show 1",
                "synopsis": "Program synopsis",
                "broadcast_gen": "2026年",
            }
        ]
    }
}
nhk_inst.session.get.return_value = fake_nhk_resp

nhk_results = list(nhk_inst.search())
assert len(nhk_results) == 1
nhk_res = nhk_results[0]
assert isinstance(nhk_res, SearchResult)
assert nhk_res.title == "NHK Show 1"
assert nhk_res.id == "https://www.nhk-ondemand.jp/program/P202600123400000/"

# --- WOWOW CHECK ---
from unshackle.services.WOWOW import WOWOW

wowow_inst = WOWOW.__new__(WOWOW)
wowow_inst.title = "wowow test"
wowow_inst.session = MagicMock()

fake_wowow_resp = MagicMock()
fake_wowow_resp.ok = True
fake_wowow_resp.json.return_value = {
    "jsonGraph": {
        "search": {
            "byTerm": {
                "|wowow test": {
                    "a": {
                        "p:a|target:titles": {
                            "0": {
                                "$type": "ref",
                                "value": ["meta", "meta_123"]
                            }
                        }
                    }
                }
            }
        },
        "meta": {
            "meta_123": {
                "refId": {"value": "999888"},
                "type": {"value": "group"},
                "name": {"value": "WOWOW Series 1"},
                "cardInfo": {"value": {"description": "WOWOW synopsis", "productionYear": 2026}},
            }
        }
    }
}
wowow_inst.session.get.return_value = fake_wowow_resp

wowow_results = list(wowow_inst.search())
assert len(wowow_results) == 1
wowow_res = wowow_results[0]
assert isinstance(wowow_res, SearchResult)
assert wowow_res.title == "WOWOW Series 1"
assert wowow_res.id == "https://wod.wowow.co.jp/program/999888"
assert wowow_res.label == "2026"

print("ALL LEMINO, NHK, WOWOW TESTS PASSED")
