"""Provider-neutral quota reservation and pricing contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


QuotaDecision = Literal["reserved", "denied"]


@dataclass(frozen=True)
class QuotaRequest:
    """Generic quota request without TrustForge route or model-specific fields."""

    account_id: str
    units: float
    operation: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QuotaReservation:
    """Opaque reservation handle returned only for approved work."""

    reservation_id: str
    account_id: str
    units: float
    operation: str
    expires_at: float | None = None


@dataclass(frozen=True)
class QuotaReservationResult:
    """Reservation result with fail-closed denial reason."""

    decision: QuotaDecision
    reservation: QuotaReservation | None = None
    reason: str = ""
    remaining_units: float | None = None

    @property
    def allowed(self) -> bool:
        return self.decision == "reserved" and self.reservation is not None


@dataclass(frozen=True)
class PricingRequest:
    """Provider-neutral usage pricing request."""

    provider_id: str
    sku: str
    input_units: float = 0.0
    output_units: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PricingQuote:
    """A deterministic provider pricing quote."""

    provider_id: str
    sku: str
    cost_usd: float
    currency: str = "USD"
    source: str = "configured"


@runtime_checkable
class QuotaReservationProvider(Protocol):
    """Generic quota reservation lifecycle.

    Implementations must deny when backend state is uncertain so expensive work
    remains bounded instead of creating a thundering herd.
    """

    provider_id: str

    def reserve(self, request: QuotaRequest, *, now: float | None = None) -> QuotaReservationResult:
        """Reserve quota before doing work."""
        ...

    def commit(self, reservation: QuotaReservation, *, actual_units: float | None = None) -> None:
        """Commit the reservation after successful work."""
        ...

    def release(self, reservation: QuotaReservation, *, reason: str = "") -> None:
        """Release unused quota after failed or cancelled work."""
        ...


@runtime_checkable
class ProviderPricing(Protocol):
    """Generic provider pricing contract."""

    provider_id: str

    def quote(self, request: PricingRequest) -> PricingQuote:
        """Return deterministic cost for the request."""
        ...


class FakeQuotaReservationProvider:
    """Test/local quota provider with bounded in-memory reservations."""

    provider_id = "fake-quota"

    def __init__(self, capacity_units: float, *, fail_uncertain: bool = False) -> None:
        self.capacity_units = capacity_units
        self.fail_uncertain = fail_uncertain
        self.reserved_units = 0.0
        self.reservations: dict[str, QuotaReservation] = {}
        self.committed: list[dict[str, Any]] = []
        self.released: list[dict[str, Any]] = []

    def reserve(self, request: QuotaRequest, *, now: float | None = None) -> QuotaReservationResult:
        if self.fail_uncertain:
            return QuotaReservationResult(decision="denied", reason="quota_backend_uncertain")
        if request.units <= 0:
            return QuotaReservationResult(decision="denied", reason="invalid_units")
        remaining = self.capacity_units - self.reserved_units
        if request.units > remaining:
            return QuotaReservationResult(
                decision="denied",
                reason="quota_exceeded",
                remaining_units=max(0.0, remaining),
            )
        reservation = QuotaReservation(
            reservation_id=f"reservation-{len(self.reservations) + 1}",
            account_id=request.account_id,
            units=request.units,
            operation=request.operation,
            expires_at=None if now is None else now + 300,
        )
        self.reservations[reservation.reservation_id] = reservation
        self.reserved_units += reservation.units
        return QuotaReservationResult(
            decision="reserved",
            reservation=reservation,
            remaining_units=self.capacity_units - self.reserved_units,
        )

    def commit(self, reservation: QuotaReservation, *, actual_units: float | None = None) -> None:
        if self.reservations.pop(reservation.reservation_id, None) is None:
            return
        units = reservation.units if actual_units is None else actual_units
        self.reserved_units = max(0.0, self.reserved_units - reservation.units)
        self.committed.append({"reservation_id": reservation.reservation_id, "units": units})

    def release(self, reservation: QuotaReservation, *, reason: str = "") -> None:
        if self.reservations.pop(reservation.reservation_id, None) is None:
            return
        self.reserved_units = max(0.0, self.reserved_units - reservation.units)
        self.released.append({"reservation_id": reservation.reservation_id, "reason": reason})


class StaticProviderPricing:
    """Simple deterministic pricing table adapter."""

    provider_id = "static-pricing"

    def __init__(self, prices: dict[tuple[str, str], tuple[float, float]]) -> None:
        self.prices = dict(prices)

    def quote(self, request: PricingRequest) -> PricingQuote:
        input_price, output_price = self.prices.get((request.provider_id, request.sku), (0.0, 0.0))
        cost = request.input_units * input_price + request.output_units * output_price
        return PricingQuote(
            provider_id=request.provider_id,
            sku=request.sku,
            cost_usd=round(cost, 6),
        )
