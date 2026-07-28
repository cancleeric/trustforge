from scripts.agentcore_event_analysis import _checkpoint


def test_event_checkpoint_advances_only_successful_coins():
    receipt = {
        "snapshot": {"BTC": 20.0, "ETH": 30.0},
        "results": [
            {"coin": "BTC", "result": {"status": "succeeded"}},
            {"coin": "ETH", "result": {"status": "failed"}},
        ],
    }

    assert _checkpoint({"BTC": 10.0, "ETH": 11.0}, receipt) == {
        "BTC": 20.0,
        "ETH": 11.0,
    }
