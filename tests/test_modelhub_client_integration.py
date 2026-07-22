import os

import pytest

from trustforge.modelhub_client import ModelHubClient


pytestmark = pytest.mark.integration


def test_live_local_modelhub_health():
    if os.getenv("TRUSTFORGE_MODELHUB_INTEGRATION") != "1":
        pytest.skip("set TRUSTFORGE_MODELHUB_INTEGRATION=1 to run the local ModelHub integration test")
    client = ModelHubClient()
    assert client.health_check()
    assert isinstance(client.list_models(), list)
