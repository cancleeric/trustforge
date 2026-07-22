import os

import pytest

from trustforge.modelhub_client import ModelHubClient


pytestmark = pytest.mark.integration


def test_live_local_modelhub_health():
    if os.getenv("TRUSTFORGE_MODELHUB_INTEGRATION") != "1":
        pytest.skip("set TRUSTFORGE_MODELHUB_INTEGRATION=1 to run the local ModelHub integration test")
    client = ModelHubClient()
    # Live retrain is intentionally excluded: it is a state-changing operation and
    # has no dedicated sandbox. Unit contract fixtures cover its request schema.
    assert isinstance(client.list_models(), list)
