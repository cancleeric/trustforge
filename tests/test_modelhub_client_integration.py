import os

import pytest

from trustforge.modelhub_client import ModelHubClient


pytestmark = pytest.mark.integration


def test_live_local_modelhub_health():
    if os.getenv("TRUSTFORGE_MODELHUB_INTEGRATION") != "1":
        pytest.skip("set TRUSTFORGE_MODELHUB_INTEGRATION=1 to run the local ModelHub integration test")
    client = ModelHubClient()
    # Live retrain is forbidden because there is no state-changing sandbox.
    # training-result/model-path also need dedicated req_no/product/name fixtures,
    # so those read-only contracts remain an explicit PR2 opt-in integration task.
    # Unit tests use representative ModelHub-shaped mock fixtures in the meantime.
    assert isinstance(client.list_models(), list)
