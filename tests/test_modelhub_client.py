import io
import json
import math
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
    assert request.full_url == "http://127.0.0.1:8950/v1/models"
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


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_default_opener_never_follows_redirect_or_forwards_key(status):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("X-API-Key")))
            if self.path == "/v1/models":
                self.send_response(status)
                self.send_header("Location", "/second-hop")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = ModelHubClient(base_url=f"http://127.0.0.1:{server.server_port}", api_key="redirect-secret")
        with pytest.raises(ModelHubHTTPError) as caught:
            client.list_models()
        assert caught.value.status == status
        assert seen == [("/v1/models", "redirect-secret")]
        assert "redirect-secret" not in str(caught.value)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_default_opener_ignores_environment_proxy_and_key_never_reaches_it(monkeypatch):
    target_seen = []
    proxy_seen = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            target_seen.append((self.path, self.headers.get("X-API-Key")))
            body = b'{"models":[]}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    class Proxy(BaseHTTPRequestHandler):
        def do_GET(self):
            proxy_seen.append((self.path, self.headers.get("X-API-Key")))
            self.send_response(502)
            self.end_headers()

        def log_message(self, *_args):
            pass

    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (target, proxy)]
    for thread in threads:
        thread.start()
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{proxy.server_port}")
    monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{proxy.server_port}")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    try:
        client = ModelHubClient(base_url=f"http://localhost:{target.server_port}", api_key="proxy-secret")
        assert client.list_models() == []
        assert target_seen == [("/v1/models", "proxy-secret")]
        assert proxy_seen == []
        assert client.base_url == f"http://127.0.0.1:{target.server_port}"
    finally:
        for server in (target, proxy):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join()


@pytest.mark.parametrize("url", [
    "https://localhost:8950", "http://example.com:8950", "http://user@localhost:8950",
    "http://127.0.0.2:8950", "http://localhost:8950?x=1", "http://localhost:",
])
def test_rejects_non_loopback_or_unsafe_base_urls(url):
    with pytest.raises(ModelHubConfigurationError):
        ModelHubClient(base_url=url)


def test_accepts_ipv4_and_ipv6_loopback():
    assert ModelHubClient(base_url="http://127.0.0.1:8950").base_url == "http://127.0.0.1:8950"
    assert ModelHubClient(base_url="http://[::1]:8950").base_url == "http://[::1]:8950"


def test_localhost_is_normalized_without_dns_lookup():
    assert ModelHubClient(base_url="http://localhost:8950").base_url == "http://127.0.0.1:8950"


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


@pytest.mark.parametrize("failure", [URLError("unknown outcome"), http_error(429), http_error(500)])
def test_non_idempotent_retrain_is_never_retried(failure):
    opener = Opener(failure, {"status": "duplicate"})
    client = ModelHubClient(opener=opener, sleep=lambda _: None)
    with pytest.raises((ModelHubTransportError, ModelHubHTTPError)):
        client.trigger_retrain("REQ-1", {"dataset": "abc"})
    assert len(opener.calls) == 1


def test_4xx_fails_fast():
    opener = Opener(http_error(400), {"models": []})
    with pytest.raises(ModelHubHTTPError) as caught:
        ModelHubClient(opener=opener).list_models()
    assert caught.value.status == 400
    assert len(opener.calls) == 1


def test_health_check_gracefully_returns_false():
    opener = Opener(URLError("down"), URLError("down"), URLError("down"))
    assert ModelHubClient(opener=opener, sleep=lambda _: None).health_check() is False


def test_header_send_value_error_is_redacted_and_health_is_graceful():
    secret = "do-not-leak"
    opener = Opener(ValueError(f"bad header {secret}"), ValueError(f"bad header {secret}"))
    client = ModelHubClient(api_key=secret, opener=opener)
    with pytest.raises(ModelHubConfigurationError) as caught:
        client.list_models()
    assert secret not in str(caught.value)
    assert client.health_check() is False


@pytest.mark.parametrize(
    "key",
    ["secret\rInjected: yes", "secret\nmore", "secret\0tail", "secret\x1ftail", "secret\x85tail", 123],
)
def test_api_key_rejects_control_characters_without_echo(key):
    with pytest.raises(ModelHubConfigurationError) as caught:
        ModelHubClient(api_key=key)
    assert str(key) not in str(caught.value)


@pytest.mark.parametrize("field,value", [
    ("req_no", ""), ("req_no", " R1"), ("req_no", "R1\n"), ("req_no", 1),
    ("product", ""), ("product", " trustforge"), ("name", "model\0bad"), ("name", None),
])
def test_path_identifiers_fail_before_request(field, value):
    opener = Opener({"status": "should-not-be-used"})
    client = ModelHubClient(opener=opener)
    with pytest.raises(ModelHubConfigurationError):
        if field == "req_no":
            client.trigger_retrain(value, {})
        else:
            kwargs = {"product": "trustforge", "name": "calibrator"}
            kwargs[field] = value
            client.get_model_path(**kwargs)
    assert opener.calls == []


@pytest.mark.parametrize("timeout", [True, "30", None, math.nan, math.inf, -math.inf, 0, -1])
def test_timeout_must_be_positive_finite_real(timeout):
    with pytest.raises(ModelHubConfigurationError):
        ModelHubClient(timeout=timeout)


@pytest.mark.parametrize("retries", [True, 1.0, "1", -1, 3])
def test_retries_must_be_bounded_real_int(retries):
    with pytest.raises(ModelHubConfigurationError):
        ModelHubClient(retries=retries)


@pytest.mark.parametrize("limit", [True, 4.0, "4", 0, -1])
def test_response_limit_must_be_positive_real_int(limit):
    with pytest.raises(ModelHubConfigurationError):
        ModelHubClient(max_response_bytes=limit)


@pytest.mark.parametrize("body", [b"not-json", b'"wrong-shape"'])
def test_malformed_or_wrong_schema_response(body):
    with pytest.raises(ModelHubResponseError):
        ModelHubClient(opener=Opener(body)).list_models()


@pytest.mark.parametrize("body", [b"1" + b"0" * 5000, b"[" * 2000 + b"]" * 2000])
def test_pathological_json_is_a_redacted_response_error(body):
    with pytest.raises(ModelHubResponseError) as caught:
        ModelHubClient(api_key="json-secret", opener=Opener(body)).list_models()
    assert "json-secret" not in str(caught.value)


def test_response_read_value_error_is_not_misclassified_as_header_error():
    class BrokenResponse(Response):
        def read(self, amount):
            raise ValueError("read failed with body-secret")

    opener = Opener({"models": []})
    opener.results = [BrokenResponse({"models": []})]

    def return_response(request, timeout):
        opener.calls.append((request, timeout))
        return opener.results.pop(0)

    with pytest.raises(ModelHubResponseError) as caught:
        ModelHubClient(opener=return_response).list_models()
    assert not isinstance(caught.value, ModelHubConfigurationError)
    assert "body-secret" not in str(caught.value)


def test_oversized_response_is_rejected():
    with pytest.raises(ModelHubResponseError, match="size limit"):
        ModelHubClient(opener=Opener(b"12345"), max_response_bytes=4).list_models()


def test_poll_returns_success_and_failure_terminal_statuses():
    opener = Opener({"status": "running"}, {"status": "COMPLETED"})
    client = ModelHubClient(opener=opener, sleep=lambda _: None, monotonic=lambda: 0)
    assert client.poll_training_result("R", interval=0.05)["status"] == "COMPLETED"

    failed = ModelHubClient(opener=Opener({"status": "failed"}))
    assert failed.poll_training_result("R")["status"] == "failed"


def test_poll_uses_monotonic_deadline_and_bounded_sleep():
    now = iter([10.0, 12.0, 12.0, 12.0, 12.0, 12.0, 15.0])
    sleeps = []
    client = ModelHubClient(
        opener=Opener({"status": "queued"}, {"status": "running"}),
        monotonic=lambda: next(now),
        sleep=sleeps.append,
    )
    with pytest.raises(ModelHubPollTimeout):
        client.poll_training_result("R", max_wait=5, interval=4)
    assert sleeps == [3.0]


def test_poll_rejects_terminal_response_that_arrives_after_deadline():
    now = iter([0.0, 0.0, 0.0, 0.0, 6.0])
    client = ModelHubClient(opener=Opener({"status": "completed"}), monotonic=lambda: next(now))
    with pytest.raises(ModelHubPollTimeout):
        client.poll_training_result("R", max_wait=5)


def test_chunked_body_read_stops_as_soon_as_deadline_expires():
    class SlowChunkResponse(Response):
        def __init__(self):
            self.calls = 0

        def read1(self, amount):
            self.calls += 1
            return b'{"status":"completed"}' if self.calls == 1 else b""

    response = SlowChunkResponse()

    def opener(_request, timeout):
        return response

    now = iter([0.0, 0.0, 0.0, 0.0, 6.0])
    client = ModelHubClient(opener=opener, monotonic=lambda: next(now))
    with pytest.raises(ModelHubPollTimeout):
        client.poll_training_result("R", max_wait=5)
    assert response.calls == 1


@pytest.mark.parametrize("max_wait", [True, "1", None, math.nan, math.inf, 0, -1, 300.01])
def test_poll_max_wait_must_be_finite_and_bounded(max_wait):
    with pytest.raises(ModelHubConfigurationError):
        ModelHubClient(opener=Opener({"status": "completed"})).poll_training_result("R", max_wait=max_wait)


@pytest.mark.parametrize("interval", [True, "1", None, math.nan, math.inf, 0, -1, 0.049])
def test_poll_interval_must_be_finite_and_reasonable(interval):
    with pytest.raises(ModelHubConfigurationError):
        ModelHubClient(opener=Opener({"status": "completed"})).poll_training_result("R", interval=interval)


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
