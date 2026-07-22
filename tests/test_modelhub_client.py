import io
import json
import socket
from urllib.error import HTTPError, URLError

import pytest

from trustforge.modelhub_client import (
    ModelHubClient,
    ModelHubConfigurationError,
    ModelHubHTTPError,
    ModelHubPollTimeout,
    ModelHubResponseError,
    ModelHubTransportError,
)


class Response:
    def __init__(self, value):
        self.raw = value if isinstance(value, bytes) else json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, amount):
        return self.raw[:amount]


class Opener:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return Response(result)


def http_error(status):
    return HTTPError("http://localhost", status, "failure", {}, io.BytesIO(b'{}'))


def test_list_models_builds_url_timeout_and_redacted_header():
    opener = Opener({"models": [{"slug": "safe"}]})
    client = ModelHubClient(api_key="top-secret", opener=opener, sleep=lambda _: None)
    assert client.list_models() == [{"slug": "safe"}]
    request, timeout = opener.calls[0]
    assert request.full_url == "http://localhost:8950/v1/models"
    assert request.get_header("X-api-key") == "top-secret"
    assert request.get_method() == "GET"
    assert timeout == 30
    assert "top-secret" not in repr(client)


def test_trigger_retrain_quotes_path_and_sends_json():
    opener = Opener({"status": "accepted"})
    client = ModelHubClient(opener=opener)
    assert client.trigger_retrain("REQ /?#", {"dataset": "abc"})["status"] == "accepted"
    request = opener.calls[0][0]
    assert request.full_url.endswith("/api/submissions/REQ%20%2F%3F%23/retrain-lightning")
    assert json.loads(request.data) == {"dataset": "abc"}
    assert request.get_header("Content-type") == "application/json"


@pytest.mark.parametrize("url", [
    "https://localhost:8950", "http://example.com:8950", "http://user@localhost:8950",
    "http://127.0.0.2:8950", "http://localhost:8950?x=1",
])
def test_rejects_non_loopback_or_unsafe_base_urls(url):
    with pytest.raises(ModelHubConfigurationError):
        ModelHubClient(base_url=url)


def test_accepts_ipv4_and_ipv6_loopback():
    assert ModelHubClient(base_url="http://127.0.0.1:8950").base_url == "http://127.0.0.1:8950"
    assert ModelHubClient(base_url="http://[::1]:8950").base_url == "http://[::1]:8950"


@pytest.mark.parametrize("failure", [URLError("down secret-value"), TimeoutError("secret-value"), socket.timeout()])
def test_transport_retries_exactly_twice_and_redacts_error(failure):
    opener = Opener(failure, failure, failure)
    client = ModelHubClient(api_key="secret-value", opener=opener, sleep=lambda _: None)
    with pytest.raises(ModelHubTransportError) as caught:
        client.list_models()
    assert len(opener.calls) == 3
    assert "secret-value" not in str(caught.value)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_recoverable_http_status_retries(status):
    opener = Opener(http_error(status), http_error(status), {"models": []})
    assert ModelHubClient(opener=opener, sleep=lambda _: None).list_models() == []
    assert len(opener.calls) == 3


def test_4xx_fails_fast():
    opener = Opener(http_error(400), {"models": []})
    with pytest.raises(ModelHubHTTPError) as caught:
        ModelHubClient(opener=opener).list_models()
    assert caught.value.status == 400
    assert len(opener.calls) == 1


def test_health_check_gracefully_returns_false():
    opener = Opener(URLError("down"), URLError("down"), URLError("down"))
    assert ModelHubClient(opener=opener, sleep=lambda _: None).health_check() is False


@pytest.mark.parametrize("body", [b"not-json", b'"wrong-shape"'])
def test_malformed_or_wrong_schema_response(body):
    with pytest.raises(ModelHubResponseError):
        ModelHubClient(opener=Opener(body)).list_models()


def test_oversized_response_is_rejected():
    with pytest.raises(ModelHubResponseError, match="size limit"):
        ModelHubClient(opener=Opener(b"12345"), max_response_bytes=4).list_models()


def test_poll_returns_success_and_failure_terminal_statuses():
    ticks = iter([0, 0, 1, 1])
    opener = Opener({"status": "running"}, {"status": "COMPLETED"})
    client = ModelHubClient(opener=opener, sleep=lambda _: None, monotonic=lambda: next(ticks))
    assert client.poll_training_result("R", interval=0)["status"] == "COMPLETED"

    failed = ModelHubClient(opener=Opener({"status": "failed"}))
    assert failed.poll_training_result("R")["status"] == "failed"


def test_poll_uses_monotonic_deadline_and_bounded_sleep():
    now = iter([10.0, 12.0, 12.0, 15.0])
    sleeps = []
    client = ModelHubClient(
        opener=Opener({"status": "queued"}, {"status": "running"}),
        monotonic=lambda: next(now),
        sleep=sleeps.append,
    )
    with pytest.raises(ModelHubPollTimeout):
        client.poll_training_result("R", max_wait=5, interval=4)
    assert sleeps == [3.0]


def test_poll_requires_status_schema():
    with pytest.raises(ModelHubResponseError, match="status"):
        ModelHubClient(opener=Opener({"result": "unknown"})).poll_training_result("R")


@pytest.mark.parametrize("response", [{}, {"path": ""}, {"path": 3}])
def test_get_model_path_validates_schema(response):
    with pytest.raises(ModelHubResponseError, match="path"):
        ModelHubClient(opener=Opener(response)).get_model_path("trust forge", "calibrator/v1")


def test_get_model_path_quotes_segments():
    opener = Opener({"path": "/models/calibrator.pkl"})
    client = ModelHubClient(opener=opener)
    assert client.get_model_path("trust forge", "calibrator/v1") == "/models/calibrator.pkl"
    assert opener.calls[0][0].full_url.endswith("/api/external-models/trust%20forge/calibrator%2Fv1/path")
