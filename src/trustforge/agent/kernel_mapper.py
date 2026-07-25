"""Application-to-core contract normalization.

This is intentionally an application adapter: it may know the current
TrustForge ``Claim``/``Document`` shapes, while ``trustforge_core`` may not.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from trustforge_core import JsonValue, KernelClaim, KernelDocument, KernelInput

from ..trust.scoring import Claim


def _freeze_json(value: Any) -> JsonValue:
    """Convert JSON-compatible application metadata to immutable values."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        return tuple(
            (str(key), _freeze_json(item))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_json(item) for item in value)
    return str(value)


def to_kernel_claim(claim: Claim) -> KernelClaim:
    """Normalize one application claim into the independent core contract."""
    document = claim.doc
    metadata = _freeze_json(document.meta)
    if not isinstance(metadata, tuple):
        metadata = (("value", metadata),)
    return KernelClaim(
        id=claim.id,
        text=claim.text,
        claim_type=claim.claim_type,
        direction=claim.direction,
        document=KernelDocument(
            id=document.id,
            kind=document.kind,
            source=document.source,
            text=document.text,
            timestamp=float(document.ts),
            url=document.url,
            metadata=metadata,
        ),
    )


def to_kernel_input(
    claims: Sequence[Claim], *, pit_epoch: float, coin: str, query: str
) -> KernelInput:
    """Build an immutable kernel request at the application boundary."""
    return KernelInput(
        claims=tuple(to_kernel_claim(claim) for claim in claims),
        pit_epoch=float(pit_epoch),
        coin=coin,
        query=query,
    )
