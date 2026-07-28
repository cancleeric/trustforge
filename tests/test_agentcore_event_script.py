from scripts.agentcore_event_analysis import _successful


def test_event_state_advances_only_when_every_invocation_succeeds():
    assert _successful({"results": []}) is True
    assert (
        _successful(
            {
                "results": [
                    {"result": {"status": "succeeded"}},
                    {"result": {"status": "succeeded"}},
                ]
            }
        )
        is True
    )
    assert (
        _successful(
            {
                "results": [
                    {"result": {"status": "succeeded"}},
                    {"result": {"status": "failed"}},
                ]
            }
        )
        is False
    )
