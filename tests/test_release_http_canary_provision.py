from __future__ import annotations

import os
import socket
import stat
import sys
from types import SimpleNamespace

import pytest

from scripts import provision_release_http_canary_allowlist as provision
from trustforge.release_http_canary import ReleaseHTTPCanaryPolicy


SNIPPET_PATH = provision.Path("/etc/nginx/snippets/trustforge-release-router.conf")
SNIPPET = """location ~ ^/(healthz|api/) {
  if ($remote_user = "") { return 401; }
  proxy_pass http://unix:/run/trustforge/release-router.sock:;
  proxy_set_header X-TrustForge-Stable-Subject "";
  proxy_set_header X-TrustForge-Trusted-Subject "";
  proxy_set_header X-TrustForge-Trusted-Identity $remote_user;
}
"""
NGINX = f"""# configuration file /etc/nginx/nginx.conf:
user www-data;
http {{ include /etc/nginx/sites-enabled/release; }}
# configuration file /etc/nginx/sites-enabled/release:
server {{
  auth_basic "release";
  include {SNIPPET_PATH};
}}
# configuration file {SNIPPET_PATH}:
{SNIPPET}"""


def test_exact_authenticated_nginx_topology_derives_nonroot_worker_uid(monkeypatch):
    monkeypatch.setattr(
        provision.pwd,
        "getpwnam",
        lambda name: SimpleNamespace(pw_uid=33) if name == "www-data" else None,
    )
    assert provision._nginx_worker(
        NGINX, snippet_path=SNIPPET_PATH, expected_snippet=SNIPPET.encode()
    ) == ("www-data", 33)


@pytest.mark.parametrize(
    "config",
    [
        NGINX.replace('if ($remote_user = "") { return 401; }', ""),
        NGINX.replace(
            'proxy_set_header X-TrustForge-Stable-Subject "";',
            "proxy_set_header X-TrustForge-Stable-Subject $http_x_trustforge_stable_subject;",
        ),
        NGINX + NGINX,
        "user www-data;\nhttp {}\n",
        NGINX.replace(
            'proxy_set_header X-TrustForge-Stable-Subject "";',
            '# proxy_set_header X-TrustForge-Stable-Subject "";\n'
            "  proxy_set_header X-TrustForge-Stable-Subject "
            "$http_x_trustforge_stable_subject;",
        ),
        NGINX.replace('auth_basic "release";', '# auth_basic "release";'),
    ],
)
def test_unsafe_or_missing_nginx_topology_is_release_block(monkeypatch, config):
    monkeypatch.setattr(
        provision.pwd, "getpwnam", lambda _name: SimpleNamespace(pw_uid=33)
    )
    with pytest.raises(SystemExit, match="release evidence BLOCK"):
        provision._nginx_worker(
            config, snippet_path=SNIPPET_PATH, expected_snippet=SNIPPET.encode()
        )


def test_nginx_comments_cannot_satisfy_required_header_strip(monkeypatch):
    bad = SNIPPET.replace(
        'proxy_set_header X-TrustForge-Stable-Subject "";',
        '# proxy_set_header X-TrustForge-Stable-Subject "";\n'
        "  proxy_set_header X-TrustForge-Stable-Subject "
        "$http_x_trustforge_stable_subject;",
    )
    config = NGINX.replace(SNIPPET, bad)
    monkeypatch.setattr(
        provision.pwd, "getpwnam", lambda _name: SimpleNamespace(pw_uid=33)
    )
    with pytest.raises(SystemExit, match="release evidence BLOCK"):
        provision._nginx_worker(
            config, snippet_path=SNIPPET_PATH, expected_snippet=bad.encode()
        )


def test_duplicate_same_nginx_worker_directive_is_ambiguous(monkeypatch):
    config = NGINX.replace("user www-data;", "user www-data;\nuser www-data;")
    monkeypatch.setattr(
        provision.pwd, "getpwnam", lambda _name: SimpleNamespace(pw_uid=33)
    )
    with pytest.raises(SystemExit, match="worker user is ambiguous"):
        provision._nginx_worker(
            config, snippet_path=SNIPPET_PATH, expected_snippet=SNIPPET.encode()
        )


@pytest.mark.parametrize(
    "replacement",
    [
        'location /nested { auth_basic "release"; }',
        'location /sibling { auth_request /verify; }',
        "auth_basic off;",
    ],
)
def test_nested_sibling_or_off_auth_cannot_authorize_canary_include(
    monkeypatch, replacement
):
    config = NGINX.replace('auth_basic "release";', replacement)
    monkeypatch.setattr(
        provision.pwd, "getpwnam", lambda _name: SimpleNamespace(pw_uid=33)
    )
    with pytest.raises(SystemExit, match="not included by an auth server"):
        provision._nginx_worker(
            config, snippet_path=SNIPPET_PATH, expected_snippet=SNIPPET.encode()
        )


def test_canary_location_cannot_disable_inherited_auth(monkeypatch):
    bad = SNIPPET.replace(
        "location ~ ^/(healthz|api/) {",
        "location ~ ^/(healthz|api/) {\n  auth_basic off;",
    )
    config = NGINX.replace(SNIPPET, bad)
    monkeypatch.setattr(
        provision.pwd, "getpwnam", lambda _name: SimpleNamespace(pw_uid=33)
    )
    with pytest.raises(SystemExit, match="disables authentication"):
        provision._nginx_worker(
            config, snippet_path=SNIPPET_PATH, expected_snippet=bad.encode()
        )


def test_production_cli_has_no_synthetic_nginx_config_escape_hatch(monkeypatch):
    source = provision.Path(provision.__file__).read_text()
    assert "--nginx-config" not in source
    monkeypatch.setattr(provision.shutil, "which", lambda _name: None)
    with pytest.raises(SystemExit, match="release evidence BLOCK"):
        provision._real_nginx_config()


@pytest.mark.skipif(
    not hasattr(socket, "SO_PEERCRED"),
    reason="Linux SO_PEERCRED unavailable: release evidence remains BLOCK",
)
def test_real_linux_af_unix_direct_identity_spoof_is_a_only():
    client, server = socket.socketpair(socket.AF_UNIX)
    try:
        policy = ReleaseHTTPCanaryPolicy(
            (),
            trusted_proxy_uid=os.getuid() + 1,
            control_ledger_head="sha256:" + "a" * 64,
        )
        assert policy.authenticated_identity(server, "spoofed@example.test") is None
    finally:
        client.close()
        server.close()


def test_unsupported_path_is_a_only_even_for_enabled_policy():
    policy = ReleaseHTTPCanaryPolicy(
        (),
        trusted_proxy_uid=33,
        control_ledger_head="sha256:" + "a" * 64,
    )
    snapshot = SimpleNamespace(control_event_head="sha256:" + "a" * 64)
    assert policy.routing_subject(
        trusted_identity="operator@example.test",
        path="/api/admin/config",
        snapshot=snapshot,
    ) == (None, None)


def _valid_payload():
    budget = {
        "deployment_ledger_id": "ledger-1",
        "canary_epoch": "sha256:" + "a" * 64,
        "active_artifact_digest": "sha256:" + "b" * 64,
        "candidate_artifact_digest": "sha256:" + "c" * 64,
        "ramp_id": "ramp-1",
        "routing_policy_digest": "sha256:" + "d" * 64,
        "ramp_budget_id": "sha256:" + "e" * 64,
        "request_binding_digest": "sha256:" + "f" * 64,
        "model_call_cap": 2,
        "monetary_cap_microusd": 100,
        "per_request_model_calls": 1,
        "per_request_cost_microusd": 50,
        "issued_at": "2026-07-30T00:00:00+00:00",
        "expires_at": "2026-07-30T01:00:00+00:00",
        "nonce": "fixture-budget",
        "key_id": "cost-budget-1",
        "signature": "00" * 64,
        "receipt_version": "trustforge.canary-cost-budget/v1",
    }
    return {
        "schema": provision.ALLOWLIST_SCHEMA,
        "activation_contract": provision.ACTIVATION_CONTRACT,
        "trusted_proxy_uid": 33,
        "control_ledger_head": "sha256:" + "a" * 64,
        "entries": [
            {
                "trusted_identity": "operator@example.test",
                "endpoint": "analyze",
                "assets": ["BTC"],
                "query_digest": "sha256:" + "1" * 64,
                "question_type": "multi_source",
                "live_mode": False,
                "sample_mode": False,
                "data_mode": "live",
                "llm_mode": "off",
                "online_stance_mode": True,
                "active_release_digest": "sha256:" + "b" * 64,
                "candidate_release_digest": "sha256:" + "c" * 64,
                "ramp_id": "ramp-1",
                "control_ledger_id": "ledger-1",
                "policy_digest": "sha256:" + "d" * 64,
                "cost_budget": budget,
            }
        ],
    }


def test_request_v2_accepts_only_path_and_presigned_budget(monkeypatch):
    budget = _valid_payload()["entries"][0]["cost_budget"]
    payload = {
        "schema": provision.REQUEST_SCHEMA,
        "entries": [
            {
                "trusted_identity": "operator@example.test",
                "path": "/api/analyze?coin=BTC",
                "live_token_digest": "",
                "cost_budget": budget,
            }
        ],
    }
    raw = provision.canonical_json(payload) + b"\n"
    monkeypatch.setattr(
        provision,
        "read_regular_file",
        lambda *_args, **_kwargs: (
            raw,
            SimpleNamespace(st_uid=0, st_mode=0o100600, st_nlink=1),
        ),
    )
    assert provision._request(provision.Path("/root/request.json")) == payload[
        "entries"
    ]


def test_main_runs_authenticated_payload_and_atomic_publish_pipeline(
    monkeypatch, capsys
):
    snippet = b"reviewed-nginx-snippet"
    records = [
        {
            "ledger_id": "ledger-1",
            "event_hash": "sha256:" + "a" * 64,
            "event": {"kind": "deployment_initialized"},
        }
    ]

    class Lock:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Ledger:
        def coordination_lock(self):
            return Lock()

        def read(self):
            return records

    observed = {}
    monkeypatch.setattr(provision.os, "geteuid", lambda: 0)
    monkeypatch.setattr(provision, "_real_nginx_config", lambda: "nginx")
    monkeypatch.setattr(provision, "_json_file", lambda _path: {})
    monkeypatch.setattr(
        provision,
        "read_regular_file",
        lambda *_args, **_kwargs: (
            snippet,
            SimpleNamespace(st_uid=0, st_mode=0o100644, st_nlink=1),
        ),
    )
    monkeypatch.setattr(provision, "_control_ledger", lambda *_args, **_kwargs: Ledger())
    monkeypatch.setattr(
        provision,
        "_payload",
        lambda args, nginx, history, expected: (
            observed.update(
                args=args,
                nginx=nginx,
                history=history,
                expected=expected,
            )
            or _valid_payload()
        ),
    )
    monkeypatch.setattr(
        provision,
        "_publish",
        lambda output, payload: (
            observed.update(output=output, payload=payload)
            or "published-digest"
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "provision_release_http_canary_allowlist.py",
            "--request",
            "/root/request.json",
            "--runtime",
            "/etc/runtime.json",
            "--keys",
            "/etc/keys.json",
            "--control-bootstrap",
            "/ledger/bootstrap.json",
            "--control-events",
            "/ledger/events.jsonl",
            "--control-head",
            "/ledger/head.json",
            "--output",
            "/etc/allowlist.json",
            "--nginx-snippet",
            "/etc/nginx/snippet",
            "--expected-nginx-snippet-sha256",
            provision.hashlib.sha256(snippet).hexdigest(),
        ],
    )
    assert provision.main() == 0
    assert observed["nginx"] == "nginx"
    assert observed["history"] == records
    assert observed["expected"] == snippet
    assert observed["payload"]["schema"] == provision.ALLOWLIST_SCHEMA
    assert capsys.readouterr().out.strip() == "published-digest"


def test_publish_atomically_rereads_and_validates_root_only_output(
    monkeypatch, tmp_path
):
    output = tmp_path / "allowlist.json"
    payload = _valid_payload()
    real_write = provision.write_atomic_at
    writes = []

    def tracked_write(parent_fd, name, data, *, immutable):
        writes.append((name, data, immutable))
        return real_write(parent_fd, name, data, immutable=immutable)

    monkeypatch.setattr(provision, "write_atomic_at", tracked_write)
    digest = provision._publish(output, payload, owner_uid=os.geteuid())

    assert len(writes) == 1
    assert writes[0][0] == output.name
    assert writes[0][2] is False
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert digest == provision.hashlib.sha256(output.read_bytes()).hexdigest()


def test_publish_restores_inode_bound_prior_after_postpublish_failure(
    monkeypatch, tmp_path
):
    output = tmp_path / "allowlist.json"
    prior = b"prior-version\n"
    output.write_bytes(prior)
    output.chmod(0o600)
    calls = 0
    real_validate = provision.ReleaseHTTPCanaryPolicy.from_payload

    def fail_postpublish(cls, payload):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("postpublish validation failed")
        return real_validate(payload)

    monkeypatch.setattr(
        provision.ReleaseHTTPCanaryPolicy,
        "from_payload",
        classmethod(fail_postpublish),
    )
    with pytest.raises(RuntimeError, match="postpublish"):
        provision._publish(output, _valid_payload(), owner_uid=os.geteuid())
    assert output.read_bytes() == prior
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_publish_removes_new_file_after_postpublish_failure(monkeypatch, tmp_path):
    output = tmp_path / "allowlist.json"
    calls = 0
    real_validate = provision.ReleaseHTTPCanaryPolicy.from_payload

    def fail_postpublish(cls, payload):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("postpublish validation failed")
        return real_validate(payload)

    monkeypatch.setattr(
        provision.ReleaseHTTPCanaryPolicy,
        "from_payload",
        classmethod(fail_postpublish),
    )
    with pytest.raises(RuntimeError, match="postpublish"):
        provision._publish(output, _valid_payload(), owner_uid=os.geteuid())
    assert not output.exists()


def test_publish_rejects_symlink_destination(tmp_path):
    target = tmp_path / "target"
    target.write_text("untouched")
    output = tmp_path / "allowlist.json"
    output.symlink_to(target)
    with pytest.raises(OSError):
        provision._publish(output, _valid_payload(), owner_uid=os.geteuid())
    assert target.read_text() == "untouched"


def test_publish_uses_pinned_parent_across_path_swap(monkeypatch, tmp_path):
    parent = tmp_path / "config"
    moved = tmp_path / "config-pinned"
    parent.mkdir()
    output = parent / "allowlist.json"
    real_write = provision.write_atomic_at

    def swap_then_write(parent_fd, name, data, *, immutable):
        parent.rename(moved)
        parent.mkdir()
        return real_write(parent_fd, name, data, immutable=immutable)

    monkeypatch.setattr(provision, "write_atomic_at", swap_then_write)
    provision._publish(output, _valid_payload(), owner_uid=os.geteuid())
    assert (moved / output.name).is_file()
    assert not output.exists()


def test_publish_short_write_keeps_prior(monkeypatch, tmp_path):
    output = tmp_path / "allowlist.json"
    prior = b"prior-version\n"
    output.write_bytes(prior)
    output.chmod(0o600)
    real_write = provision.os.write
    first = True

    def zero_once(fd, data):
        nonlocal first
        if first:
            first = False
            return 0
        return real_write(fd, data)

    monkeypatch.setattr(provision.os, "write", zero_once)
    with pytest.raises(OSError, match="short write"):
        provision._publish(output, _valid_payload(), owner_uid=os.geteuid())
    assert output.read_bytes() == prior
