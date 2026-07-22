"""Small, defensive client for the loopback-only ModelHub API."""
from __future__ import annotations

import json
from http.client import HTTPException
import math
import numbers
import os
import socket
import time
import unicodedata
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

DEFAULT_BASE_URL = "http://localhost:8950"
DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024
MIN_POLL_INTERVAL = 0.05
_TRANSPORT_ERRORS = (URLError, TimeoutError, socket.timeout, ConnectionError, OSError, HTTPException)


class ModelHubError(Exception):
    """Base class for errors safe to display (API keys are never included)."""


class ModelHubConfigurationError(ModelHubError):
    pass


class ModelHubTransportError(ModelHubError):
    pass


class ModelHubHTTPError(ModelHubError):
    def __init__(self, status: int, message: str = "ModelHub request failed") -> None:
        self.status = status
        super().__init__(f"{message} (HTTP {status})")


class ModelHubResponseError(ModelHubError):
    pass


class ModelHubPollTimeout(ModelHubError):
    pass


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


_DEFAULT_OPENER = build_opener(ProxyHandler({}), _NoRedirectHandler())


def _open_no_redirect(request: Request, *, timeout: float) -> Any:
    return _DEFAULT_OPENER.open(request, timeout=timeout)


def _has_control(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _validated_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or _has_control(value):
        raise ModelHubConfigurationError(
            f"ModelHub {label} must be a non-empty, unpadded string without control characters"
        )
    return value


def _finite_real(value: Any, label: str, *, minimum: float, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(value):
        raise ModelHubConfigurationError(f"ModelHub {label} must be a finite real number")
    converted = float(value)
    if converted < minimum or (maximum is not None and converted > maximum):
        raise ModelHubConfigurationError(f"ModelHub {label} is outside the allowed range")
    return converted


def _validated_base_url(value: str) -> str:
    if not isinstance(value, str):
        raise ModelHubConfigurationError("ModelHub base URL must be a string")
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ModelHubConfigurationError("ModelHub base URL must use HTTP on a loopback host")
    if parsed.username is not None or parsed.password is not None:
        raise ModelHubConfigurationError("ModelHub base URL must not contain user information")
    if parsed.query or parsed.fragment:
        raise ModelHubConfigurationError("ModelHub base URL must not contain a query or fragment")
    if parsed.netloc.endswith(":"):
        raise ModelHubConfigurationError("ModelHub base URL has an empty port")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ModelHubConfigurationError("ModelHub base URL has an invalid port") from exc
    netloc = "127.0.0.1" if parsed.hostname == "localhost" else (parsed.hostname or "")
    if ":" in netloc:
        netloc = f"[{netloc}]"
    if port is not None:
        netloc += f":{port}"
    return urlunsplit(("http", netloc, parsed.path.rstrip("/"), "", ""))


class ModelHubClient:
    """ModelHub REST client using only the Python standard library.

    Poll deadlines bound socket timeouts and response-body reads. The stdlib
    opener/header phase itself cannot be asynchronously interrupted; this
    client deliberately does not create background threads to simulate that.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 30.0,
        *,
        api_key: str | None = None,
        retries: int = 2,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        opener: Callable[..., Any] = _open_no_redirect,
    ) -> None:
        validated_timeout = _finite_real(timeout, "timeout", minimum=math.nextafter(0.0, 1.0))
        if type(retries) is not int or retries < 0 or retries > 2:
            raise ModelHubConfigurationError("Invalid timeout, retry count, or response limit")
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ModelHubConfigurationError("Invalid timeout, retry count, or response limit")
        selected_base_url = base_url if base_url is not None else os.getenv("MODELHUB_BASE_URL", DEFAULT_BASE_URL)
        self.base_url = _validated_base_url(selected_base_url)
        self.timeout = validated_timeout
        self.retries = retries
        self.max_response_bytes = max_response_bytes
        selected_api_key = api_key if api_key is not None else os.getenv("MODELHUB_API_KEY")
        if selected_api_key is not None and (not isinstance(selected_api_key, str) or _has_control(selected_api_key)):
            raise ModelHubConfigurationError("ModelHub API key contains invalid characters")
        self._api_key = selected_api_key
        self._sleep = sleep
        self._monotonic = monotonic
        self._opener = opener

    def __repr__(self) -> str:
        return f"ModelHubClient(base_url={self.base_url!r}, timeout={self.timeout!r}, api_key=<redacted>)"

    def _url(self, *segments: str) -> str:
        path = "/".join(quote(str(segment), safe="") for segment in segments)
        return f"{self.base_url}/{path}"

    def _check_deadline(self, deadline: float | None) -> None:
        if deadline is not None and deadline - self._monotonic() <= 0:
            raise ModelHubPollTimeout("ModelHub training result polling timed out")

    def _decode_response(self, response: Any, deadline: float | None = None) -> Any:
        self._check_deadline(deadline)
        try:
            read1 = getattr(response, "read1", None)
            if callable(read1):
                chunks: list[bytes] = []
                total = 0
                while total <= self.max_response_bytes:
                    self._check_deadline(deadline)
                    chunk = read1(min(64 * 1024, self.max_response_bytes + 1 - total))
                    self._check_deadline(deadline)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                raw = b"".join(chunks)
            else:
                raw = response.read(self.max_response_bytes + 1)
                self._check_deadline(deadline)
        except ModelHubPollTimeout:
            raise
        except (ValueError, RecursionError):
            raise ModelHubResponseError("ModelHub response body could not be read") from None
        if len(raw) > self.max_response_bytes:
            raise ModelHubResponseError("ModelHub response exceeded the configured size limit")
        try:
            return json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            raise ModelHubResponseError("ModelHub returned malformed JSON") from None

    def _request(
        self,
        method: str,
        segments: tuple[str, ...],
        payload: dict[str, Any] | None = None,
        *,
        deadline: float | None = None,
    ) -> Any:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        data = None
        if payload is not None:
            try:
                data = json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError, RecursionError):
                raise ModelHubResponseError("ModelHub request payload is not JSON serializable") from None
            headers["Content-Type"] = "application/json"

        try:
            request = Request(self._url(*segments), data=data, headers=headers, method=method)
        except ValueError:
            raise ModelHubConfigurationError("ModelHub request headers are invalid") from None
        last_transport: BaseException | None = None
        retry_count = self.retries if method == "GET" else 0
        for attempt in range(retry_count + 1):
            request_timeout = self.timeout
            if deadline is not None:
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    raise ModelHubPollTimeout("ModelHub training result polling timed out")
                request_timeout = min(request_timeout, remaining)
            try:
                response = self._opener(request, timeout=request_timeout)
            except ValueError:
                raise ModelHubConfigurationError(
                    "ModelHub request could not be sent because its headers are invalid"
                ) from None
            except HTTPError as exc:
                if exc.code != 429 and exc.code < 500:
                    raise ModelHubHTTPError(exc.code) from None
                if attempt == retry_count:
                    raise ModelHubHTTPError(exc.code, "ModelHub retry limit exhausted") from None
            except _TRANSPORT_ERRORS as exc:
                last_transport = exc
                if attempt == retry_count:
                    raise ModelHubTransportError("ModelHub is unavailable after retry limit") from None
            else:
                try:
                    with response as opened_response:
                        decoded = self._decode_response(opened_response, deadline)
                    if deadline is not None and deadline - self._monotonic() <= 0:
                        raise ModelHubPollTimeout("ModelHub training result polling timed out")
                    return decoded
                except ModelHubError:
                    raise
                except _TRANSPORT_ERRORS as exc:
                    last_transport = exc
                    if attempt == retry_count:
                        raise ModelHubTransportError("ModelHub is unavailable after retry limit") from None
            if attempt < retry_count:
                delay = min(0.1 * (2**attempt), 1.0)
                if deadline is not None:
                    remaining = deadline - self._monotonic()
                    if remaining <= 0:
                        raise ModelHubPollTimeout("ModelHub training result polling timed out")
                    delay = min(delay, remaining)
                self._sleep(delay)
        raise ModelHubTransportError("ModelHub is unavailable") from last_transport

    @staticmethod
    def _require_dict(value: Any, operation: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ModelHubResponseError(f"ModelHub {operation} response must be an object")
        return value

    def health_check(self) -> bool:
        try:
            self.list_models()
            return True
        except ModelHubError:
            return False

    def list_models(self) -> list[dict[str, Any]]:
        value = self._request("GET", ("v1", "models"))
        models = value.get("models") if isinstance(value, dict) else value
        if not isinstance(models, list) or not all(isinstance(model, dict) for model in models):
            raise ModelHubResponseError("ModelHub models response has an invalid schema")
        return models

    def trigger_retrain(self, req_no: str, payload: dict[str, Any]) -> dict[str, Any]:
        req_no = _validated_identifier(req_no, "request number")
        if not isinstance(payload, dict):
            raise ModelHubResponseError("ModelHub retrain payload must be an object")
        return self._require_dict(
            self._request("POST", ("api", "submissions", req_no, "retrain-lightning"), payload),
            "retrain",
        )

    def poll_training_result(self, req_no: str, *, max_wait: float = 300.0, interval: float = 1.0) -> dict[str, Any]:
        req_no = _validated_identifier(req_no, "request number")
        max_wait = _finite_real(max_wait, "maximum poll wait", minimum=math.nextafter(0.0, 1.0), maximum=300.0)
        interval = _finite_real(interval, "poll interval", minimum=MIN_POLL_INTERVAL)
        deadline = self._monotonic() + max_wait
        terminal = {"completed", "complete", "succeeded", "success", "failed", "error", "cancelled", "canceled"}
        while True:
            result = self._require_dict(
                self._request("GET", ("api", "submissions", req_no, "training-result"), deadline=deadline),
                "training result",
            )
            status = result.get("status")
            if not isinstance(status, str):
                raise ModelHubResponseError("ModelHub training result is missing a string status")
            if status.lower() in terminal:
                return result
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise ModelHubPollTimeout("ModelHub training result polling timed out")
            self._sleep(min(interval, remaining))

    def get_model_path(self, product: str, name: str) -> str:
        """Return an untrusted artifact path string; callers must not open it without validation."""
        product = _validated_identifier(product, "product")
        name = _validated_identifier(name, "model name")
        result = self._require_dict(
            self._request("GET", ("api", "external-models", product, name, "path")),
            "model path",
        )
        path = result.get("path")
        if not isinstance(path, str) or not path:
            raise ModelHubResponseError("ModelHub model path response has an invalid schema")
        return path
