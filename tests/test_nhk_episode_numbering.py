import pytest
from unittest.mock import Mock
from unshackle.services.NHKOnDemand import NHKOnDemand

def test_nhk_himawari_episode_numbering(mocker):
    svc = NHKOnDemand.__new__(NHKOnDemand)
    svc.content_id = 'P202200307600000'
    svc.session = Mock()
    svc.log = Mock()
    
    # Mock zeta cx response
    # Only need 2 items to prove ascending order and correct numbering
    mock_data = {
        "result": {
            "items": [
                {
                    "item_id": "N202212300800000",
                    "title": "ひまわり",
                    "title_alt": "ひまわり　１６２",
                    "sub_title": "（１６２）「第十四章 実るほど頭の下がる稲穂かな？」",
                    "sub_title_alt": "だい１４しょう みのるほどこうべのさがるいなほかな",
                    "broadcast_date": "1996-10-05T08:15:00"
                },
                {
                    "item_id": "N202212300700000",
                    "title": "ひまわり",
                    "title_alt": "ひまわり　１６１",
                    "sub_title": "（１６１）「第十四章 実るほど頭の下がる稲穂かな？」",
                    "sub_title_alt": "だい１４しょう みのるほどこうべのさがるいなほかな",
                    "broadcast_date": "1996-10-04T08:15:00"
                }
            ]
        }
    }
    svc.session.get.return_value.json.return_value = mock_data
    
    eps = svc._get_program_metadata()
    
    # Sort order should be ascending based on correct number
    assert eps[0].number == 161
    assert eps[1].number == 162

