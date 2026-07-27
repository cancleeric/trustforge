"""Ed25519-authenticated, append-only event ledger with signer capabilities."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from trustforge.authenticated_ledger import (
    GENESIS_HASH,
    LedgerError,
    LedgerLimitError,
    NonceAlreadyConsumed,
)

SCHEMA = "trustforge.signed-event-ledger/v2"
HEAD_SCHEMA = "trustforge.signed-event-ledger-head/v2"
BOOTSTRAP_SCHEMA = "trustforge.signed-ledger-bootstrap/v1"
BOOTSTRAP_FILENAME = "bootstrap.json"
EPOCH_STOP_PREFIX = "epoch-stop-"
SECURITY_LEDGER_ROOT = Path("/var/lib/trustforge/security-ledger")


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise LedgerError("event is not canonical JSON") from exc


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise LedgerError("partial ledger write")
        view = view[written:]


class SignedEventLedger:
    """A bounded ledger whose reader needs public keys only.

    ``event_permissions`` is part of verification, not merely an append-time
    convenience. A valid router signature therefore cannot authenticate a
    control-plane event.
    """

    def __init__(
        self,
        *,
        directory: str | os.PathLike[str],
        verification_keys: Mapping[str, bytes],
        event_permissions: Mapping[str, frozenset[str]],
        domain_keys: Mapping[str, frozenset[str]],
        signing_key_id: str | None = None,
        signing_private_key: bytes | None = None,
        signing_domain: str | None = None,
        ledger_role: str,
        bootstrap: bool = False,
        coordination_root: str | os.PathLike[str] | None = None,
        max_file_bytes: int = 8 * 1024 * 1024,
        max_events: int = 10_000,
        max_event_bytes: int = 32 * 1024,
    ) -> None:
        if not verification_keys or any(
            not isinstance(key_id, str)
            or not key_id
            or not isinstance(key, bytes)
            or len(key) != 32
            for key_id, key in verification_keys.items()
        ):
            raise LedgerError("Ed25519 verification keys must be named 32-byte keys")
        if not event_permissions or any(
            not domain or not kinds or any(not kind for kind in kinds)
            for domain, kinds in event_permissions.items()
        ):
            raise LedgerError("signer event permissions are required")
        if set(domain_keys) != set(event_permissions) or any(
            not key_ids or not key_ids.issubset(verification_keys)
            for key_ids in domain_keys.values()
        ):
            raise LedgerError("each signer domain requires explicit verification keys")
        signing_values = (signing_key_id, signing_private_key, signing_domain)
        if any(value is not None for value in signing_values) and not all(
            value is not None for value in signing_values
        ):
            raise LedgerError("complete signing identity is required")
        if signing_private_key is not None:
            if (
                len(signing_private_key) != 32
                or signing_key_id not in verification_keys
            ):
                raise LedgerError("Ed25519 signing key is invalid")
            derived = Ed25519PrivateKey.from_private_bytes(signing_private_key)
            if (
                derived.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
                != verification_keys[signing_key_id]
            ):
                raise LedgerError("signing private key does not match verification key")
            if signing_domain not in event_permissions:
                raise LedgerError("signing domain has no event permissions")
            if signing_key_id not in domain_keys[signing_domain]:
                raise LedgerError("signing key is not authorized for signing domain")
            self._private_key = derived
        else:
            self._private_key = None
        if min(max_file_bytes, max_events, max_event_bytes) <= 0:
            raise LedgerError("ledger bounds must be positive")
        self.directory = Path(directory).absolute()
        self.ledger_role = ledger_role
        self.coordination_root = Path(
            coordination_root or self.directory.parent
        ).absolute()
        if self.directory.parent != self.coordination_root:
            raise LedgerError("ledger directory must be a direct child of pinned root")
        self._verification_keys = dict(verification_keys)
        self._permissions = dict(event_permissions)
        self._domain_keys = dict(domain_keys)
        self._signing_key_id = signing_key_id
        self._signing_domain = signing_domain
        self._max_file_bytes = max_file_bytes
        self._max_events = max_events
        self._max_event_bytes = max_event_bytes
        root_existed = self.coordination_root.exists()
        if not root_existed and not bootstrap:
            raise LedgerError("ledger root requires explicit secure bootstrap")
        self.coordination_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root_info = os.lstat(self.coordination_root)
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or stat.S_ISLNK(root_info.st_mode)
            or root_info.st_uid != os.geteuid()
            or (root_existed and stat.S_IMODE(root_info.st_mode) != 0o700)
        ):
            raise LedgerError("ledger coordination root metadata is unsafe")
        if not root_existed:
            os.chmod(self.coordination_root, 0o700)
        existed = self.directory.exists()
        legacy_events = self.coordination_root / "events.jsonl"
        legacy_head = self.coordination_root / "head.json"
        if os.path.lexists(legacy_events) or os.path.lexists(legacy_head):
            raise LedgerError("legacy root ledger requires audited offline migration")
        if not existed and not bootstrap:
            raise LedgerError("ledger requires explicit secure bootstrap")
        if bootstrap and self._private_key is None:
            raise LedgerError("bootstrap requires a signing identity")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = os.lstat(self.directory)
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or (existed and stat.S_IMODE(info.st_mode) != 0o700)
        ):
            raise LedgerError("ledger directory metadata is unsafe")
        if not existed:
            os.chmod(self.directory, 0o700)
        if bootstrap and not self._bootstrap_path().exists():
            if (self.directory / "events.jsonl").exists() or (
                self.directory / "head.json"
            ).exists():
                raise LedgerError("cannot bootstrap a non-empty ledger")
            self._write_bootstrap()
        self._verify_bootstrap()

    @contextmanager
    def coordination_lock(self):
        """Serialize cross-ledger control transitions and route reservations."""
        fd = os.open(
            self.coordination_root / "coordination.lock",
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise LedgerError("coordination lock metadata is unsafe")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _bootstrap_path(self) -> Path:
        return self.directory / BOOTSTRAP_FILENAME

    def _write_bootstrap(self) -> None:
        assert self._private_key is not None
        unsigned = {
            "schema": BOOTSTRAP_SCHEMA,
            "ledger_role": self.ledger_role,
            "key_id": self._signing_key_id,
            "signer_domain": self._signing_domain,
        }
        payload = {
            **unsigned,
            "signature": self._private_key.sign(
                b"trustforge.signed-ledger-bootstrap.v1\x00" + _canonical(unsigned)
            ).hex(),
        }
        fd = os.open(
            self._bootstrap_path(),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            _write_all(fd, _canonical(payload) + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        directory_fd = self._open_directory()
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _verify_bootstrap(self) -> None:
        path = self._bootstrap_path()
        try:
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                info = os.fstat(fd)
                raw = os.read(fd, self._max_event_bytes + 1)
            finally:
                os.close(fd)
            payload = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LedgerError("ledger bootstrap record is missing or corrupt") from exc
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > self._max_event_bytes
            or not isinstance(payload, dict)
            or _canonical(payload) + b"\n" != raw
            or set(payload)
            != {"schema", "ledger_role", "key_id", "signer_domain", "signature"}
            or payload["schema"] != BOOTSTRAP_SCHEMA
            or payload["ledger_role"] != self.ledger_role
            or payload["key_id"]
            not in self._domain_keys.get(payload["signer_domain"], frozenset())
        ):
            raise LedgerError("ledger bootstrap record is invalid")
        key = self._verification_keys.get(payload["key_id"])
        unsigned = {name: payload[name] for name in payload if name != "signature"}
        try:
            if key is None:
                raise InvalidSignature
            Ed25519PublicKey.from_public_bytes(key).verify(
                bytes.fromhex(payload["signature"]),
                b"trustforge.signed-ledger-bootstrap.v1\x00" + _canonical(unsigned),
            )
        except (InvalidSignature, ValueError) as exc:
            raise LedgerError("ledger bootstrap signature is invalid") from exc

    def trip_epoch_stop(self, *, ledger_id: str, canary_epoch: str) -> None:
        """Create a signed, one-way stop latch for one canary epoch."""
        if (
            self._private_key is None
            or self._signing_key_id is None
            or self._signing_domain is None
            or len(ledger_id) != 32
            or len(canary_epoch) != 64
            or any(character not in "0123456789abcdef" for character in canary_epoch)
        ):
            raise LedgerError("epoch stop latch identity is invalid")
        unsigned = {
            "schema": "trustforge.epoch-stop/v1",
            "ledger_id": ledger_id,
            "canary_epoch": canary_epoch,
            "key_id": self._signing_key_id,
            "signer_domain": self._signing_domain,
        }
        payload = {
            **unsigned,
            "signature": self._private_key.sign(
                b"trustforge.epoch-stop.v1\x00" + _canonical(unsigned)
            ).hex(),
        }
        filename = EPOCH_STOP_PREFIX + canary_epoch + ".json"
        directory_fd = self._open_directory()
        try:
            try:
                fd = os.open(
                    filename,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory_fd,
                )
            except FileExistsError:
                if not self.epoch_stopped(
                    ledger_id=ledger_id, canary_epoch=canary_epoch
                ):
                    raise LedgerError("existing epoch stop latch is invalid")
                return
            try:
                _write_all(fd, _canonical(payload) + b"\n")
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def epoch_stopped(self, *, ledger_id: str, canary_epoch: str) -> bool:
        """Read and authenticate the immutable stop latch when present."""
        if (
            len(ledger_id) != 32
            or len(canary_epoch) != 64
            or any(character not in "0123456789abcdef" for character in canary_epoch)
        ):
            raise LedgerError("epoch stop latch identity is invalid")
        filename = EPOCH_STOP_PREFIX + canary_epoch + ".json"
        directory_fd = self._open_directory()
        try:
            try:
                fd = os.open(
                    filename,
                    os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                return False
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid not in {0, os.geteuid()}
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_size > self._max_event_bytes
                ):
                    raise LedgerError("epoch stop latch metadata is unsafe")
                raw = os.read(fd, self._max_event_bytes + 1)
            finally:
                os.close(fd)
        finally:
            os.close(directory_fd)
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LedgerError("epoch stop latch is corrupt") from exc
        required = {
            "schema",
            "ledger_id",
            "canary_epoch",
            "key_id",
            "signer_domain",
            "signature",
        }
        if (
            not isinstance(payload, dict)
            or set(payload) != required
            or _canonical(payload) + b"\n" != raw
        ):
            raise LedgerError("epoch stop latch schema is invalid")
        unsigned = {key: payload[key] for key in required if key != "signature"}
        key_id = payload["key_id"]
        domain = payload["signer_domain"]
        key = self._verification_keys.get(key_id)
        try:
            if (
                payload["schema"] != "trustforge.epoch-stop/v1"
                or payload["ledger_id"] != ledger_id
                or payload["canary_epoch"] != canary_epoch
                or key_id not in self._domain_keys.get(domain, frozenset())
                or key is None
            ):
                raise InvalidSignature
            Ed25519PublicKey.from_public_bytes(key).verify(
                bytes.fromhex(str(payload["signature"])),
                b"trustforge.epoch-stop.v1\x00" + _canonical(unsigned),
            )
        except (InvalidSignature, ValueError) as exc:
            raise LedgerError("epoch stop latch authentication failed") from exc
        return True

    def _open_directory(self) -> int:
        fd = os.open(
            self.directory, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            os.close(fd)
            raise LedgerError("ledger directory permissions changed")
        return fd

    @staticmethod
    def _open_file(directory_fd: int, name: str, *, create: bool) -> int:
        flags = (
            ((os.O_RDWR | os.O_APPEND) if create else os.O_RDONLY)
            | os.O_CLOEXEC
            | os.O_NOFOLLOW
        )
        try:
            fd = os.open(name, flags, dir_fd=directory_fd)
        except FileNotFoundError:
            if not create:
                raise
            try:
                fd = os.open(
                    name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory_fd
                )
                os.fsync(directory_fd)
            except FileExistsError:
                fd = os.open(name, flags, dir_fd=directory_fd)
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            os.close(fd)
            raise LedgerError("ledger file metadata is unsafe")
        return fd

    def _verify_signature(self, record: Mapping[str, Any]) -> None:
        key = self._verification_keys.get(str(record.get("key_id", "")))
        try:
            if key is None:
                raise InvalidSignature
            Ed25519PublicKey.from_public_bytes(key).verify(
                bytes.fromhex(str(record.get("signature", ""))),
                b"trustforge.signed-event-ledger.v2\x00"
                + _canonical({k: v for k, v in record.items() if k != "signature"}),
            )
        except (InvalidSignature, ValueError) as exc:
            raise LedgerError("ledger signature is invalid") from exc

    def _decode(self, fd: int) -> list[dict[str, Any]]:
        size = os.fstat(fd).st_size
        if size > self._max_file_bytes:
            raise LedgerLimitError("ledger file exceeds size bound")
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, self._max_file_bytes + 1)
        if len(raw) != size or (raw and not raw.endswith(b"\n")):
            raise LedgerError("ledger is truncated or changed during read")
        records: list[dict[str, Any]] = []
        previous = GENESIS_HASH
        ledger_id: str | None = None
        nonces: set[str] = set()
        required = {
            "schema",
            "sequence",
            "previous_hash",
            "event_hash",
            "key_id",
            "signer_domain",
            "ledger_id",
            "event",
            "signature",
        }
        for sequence, line in enumerate(raw.splitlines(), 1):
            if sequence > self._max_events or len(line) > self._max_event_bytes:
                raise LedgerLimitError("ledger bound exceeded")
            try:
                record = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise LedgerError("ledger contains corrupt JSON") from exc
            # This explicitly rejects the legacy HMAC v1 schema.
            if (
                not isinstance(record, dict)
                or set(record) != required
                or record.get("schema") != SCHEMA
                or _canonical(record) != line
            ):
                raise LedgerError("legacy or invalid ledger schema")
            domain = record.get("signer_domain")
            event = record.get("event")
            if (
                record.get("sequence") != sequence
                or record.get("previous_hash") != previous
                or not isinstance(event, dict)
                or event.get("kind")
                not in self._permissions.get(str(domain), frozenset())
                or record.get("key_id")
                not in self._domain_keys.get(str(domain), frozenset())
            ):
                raise LedgerError("ledger chain or signer capability is invalid")
            current_id = record.get("ledger_id")
            if (
                not isinstance(current_id, str)
                or len(current_id) != 32
                or (ledger_id is not None and current_id != ledger_id)
            ):
                raise LedgerError("ledger identity is invalid")
            core = {
                name: record[name]
                for name in (
                    "schema",
                    "sequence",
                    "previous_hash",
                    "key_id",
                    "signer_domain",
                    "ledger_id",
                    "event",
                )
            }
            event_hash = hashlib.sha256(_canonical(core)).hexdigest()
            if record.get("event_hash") != event_hash:
                raise LedgerError("ledger event hash is invalid")
            self._verify_signature(record)
            nonce = event.get("nonce")
            if nonce is not None:
                if not isinstance(nonce, str) or not nonce or nonce in nonces:
                    raise LedgerError("ledger nonce is invalid or repeated")
                nonces.add(nonce)
            ledger_id = current_id
            previous = event_hash
            records.append(record)
        return records

    def _head_path(self) -> Path:
        return self.directory / "head.json"

    def _verify_head(self, records: list[dict[str, Any]]) -> str | None:
        path = self._head_path()
        if not path.exists():
            if records:
                raise LedgerError("ledger head is missing")
            return None
        info = os.lstat(path)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > self._max_event_bytes
        ):
            raise LedgerError("ledger head metadata is unsafe")
        raw = path.read_bytes()
        try:
            head = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LedgerError("ledger head is corrupt") from exc
        if not isinstance(head, dict) or _canonical(head) + b"\n" != raw:
            raise LedgerError("ledger head is invalid")
        if (
            set(head)
            != {
                "schema",
                "count",
                "event_hash",
                "ledger_id",
                "key_id",
                "signer_domain",
                "signature",
            }
            or head.get("schema") != HEAD_SCHEMA
        ):
            raise LedgerError("legacy or invalid ledger head schema")
        key = self._verification_keys.get(str(head.get("key_id", "")))
        unsigned = {key_: value for key_, value in head.items() if key_ != "signature"}
        try:
            if key is None:
                raise InvalidSignature
            if head.get("key_id") not in self._domain_keys.get(
                str(head.get("signer_domain", "")), frozenset()
            ):
                raise InvalidSignature
            Ed25519PublicKey.from_public_bytes(key).verify(
                bytes.fromhex(str(head.get("signature", ""))),
                b"trustforge.signed-event-ledger-head.v2\x00" + _canonical(unsigned),
            )
        except (InvalidSignature, ValueError) as exc:
            raise LedgerError("ledger head signature is invalid") from exc
        expected_hash = records[-1]["event_hash"] if records else GENESIS_HASH
        if (
            head["count"] != len(records)
            or head["event_hash"] != expected_hash
            or (records and head["ledger_id"] != records[0]["ledger_id"])
        ):
            raise LedgerError("ledger head does not match authenticated tail")
        return str(head["ledger_id"])

    def _write_head(self, count: int, event_hash: str, ledger_id: str) -> None:
        if self._private_key is None:
            raise LedgerError("ledger is projection-only")
        unsigned = {
            "schema": HEAD_SCHEMA,
            "count": count,
            "event_hash": event_hash,
            "ledger_id": ledger_id,
            "key_id": self._signing_key_id,
            "signer_domain": self._signing_domain,
        }
        head = {
            **unsigned,
            "signature": self._private_key.sign(
                b"trustforge.signed-event-ledger-head.v2\x00" + _canonical(unsigned)
            ).hex(),
        }
        temporary = self.directory / f".head-{os.getpid()}-{id(self)}"
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600
        )
        try:
            _write_all(fd, _canonical(head) + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, self._head_path())

    def read(self) -> list[dict[str, Any]]:
        directory_fd = self._open_directory()
        try:
            fd = self._open_file(directory_fd, "events.jsonl", create=False)
        except FileNotFoundError:
            os.close(directory_fd)
            if self._head_path().exists():
                raise LedgerError("ledger events are missing")
            return []
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            records = self._decode(fd)
            self._verify_head(records)
            return records
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            os.close(directory_fd)

    def append(
        self, event: Mapping[str, Any], *, expected_head: str | None = None
    ) -> dict[str, Any]:
        if self._private_key is None:
            raise LedgerError("ledger is projection-only")
        event_copy = dict(event)
        if event_copy.get("kind") not in self._permissions[str(self._signing_domain)]:
            raise LedgerError("signer is not authorized for event kind")
        if len(_canonical(event_copy)) > self._max_event_bytes:
            raise LedgerLimitError("event exceeds size bound")
        directory_fd = self._open_directory()
        fd = self._open_file(directory_fd, "events.jsonl", create=True)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            records = self._decode(fd)
            actual_head = records[-1]["event_hash"] if records else GENESIS_HASH
            if expected_head is not None and expected_head != actual_head:
                raise LedgerError("ledger head changed before conditional append")
            ledger_id = self._verify_head(records) or secrets.token_hex(16)
            nonce = event_copy.get("nonce")
            if nonce is not None and any(
                record["event"].get("nonce") == nonce for record in records
            ):
                raise NonceAlreadyConsumed("nonce was already consumed")
            core = {
                "schema": SCHEMA,
                "sequence": len(records) + 1,
                "previous_hash": actual_head,
                "key_id": self._signing_key_id,
                "signer_domain": self._signing_domain,
                "ledger_id": ledger_id,
                "event": event_copy,
            }
            event_hash = hashlib.sha256(_canonical(core)).hexdigest()
            unsigned = {**core, "event_hash": event_hash}
            record = {
                **unsigned,
                "signature": self._private_key.sign(
                    b"trustforge.signed-event-ledger.v2\x00" + _canonical(unsigned)
                ).hex(),
            }
            encoded = _canonical(record) + b"\n"
            if os.fstat(fd).st_size + len(encoded) > self._max_file_bytes:
                raise LedgerLimitError("ledger file size bound reached")
            _write_all(fd, encoded)
            os.fsync(fd)
            self._write_head(len(records) + 1, event_hash, ledger_id)
            os.fsync(directory_fd)
            return record
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            os.close(directory_fd)
