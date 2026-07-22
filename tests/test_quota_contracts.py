"""Quota reservation and provider pricing contract tests (#415)."""
from __future__ import annotations

from trustforge.quota import (
    FakeQuotaReservationProvider,
    PricingRequest,
    ProviderPricing,
    QuotaRequest,
    QuotaReservationProvider,
    StaticProviderPricing,
)


def test_fake_quota_provider_is_runtime_checkable():
    provider = FakeQuotaReservationProvider(capacity_units=10)

    assert isinstance(provider, QuotaReservationProvider)


def test_reserve_commit_and_release_bound_expensive_work():
    provider = FakeQuotaReservationProvider(capacity_units=10)

    first = provider.reserve(QuotaRequest(account_id="acct", operation="llm", units=7), now=100.0)
    denied = provider.reserve(QuotaRequest(account_id="acct", operation="llm", units=4), now=101.0)

    assert first.allowed is True
    assert first.reservation is not None
    assert first.remaining_units == 3
    assert first.reservation.expires_at == 400.0
    assert denied.allowed is False
    assert denied.reason == "quota_exceeded"
    assert denied.remaining_units == 3

    provider.commit(first.reservation, actual_units=6)
    assert provider.reserved_units == 0
    assert provider.committed == [{"reservation_id": "reservation-1", "units": 6}]

    second = provider.reserve(QuotaRequest(account_id="acct", operation="llm", units=4))
    assert second.reservation is not None
    provider.release(second.reservation, reason="cancelled")
    assert provider.reserved_units == 0
    assert provider.released == [{"reservation_id": "reservation-1", "reason": "cancelled"}]


def test_quota_backend_uncertainty_denies_fail_closed():
    provider = FakeQuotaReservationProvider(capacity_units=10, fail_uncertain=True)

    result = provider.reserve(QuotaRequest(account_id="acct", operation="llm", units=1))

    assert result.allowed is False
    assert result.reservation is None
    assert result.reason == "quota_backend_uncertain"
    assert provider.reserved_units == 0


def test_invalid_units_are_denied():
    provider = FakeQuotaReservationProvider(capacity_units=10)

    result = provider.reserve(QuotaRequest(account_id="acct", operation="llm", units=0))

    assert result.allowed is False
    assert result.reason == "invalid_units"


def test_commit_and_release_are_idempotent_for_unknown_reservation():
    provider = FakeQuotaReservationProvider(capacity_units=10)
    result = provider.reserve(QuotaRequest(account_id="acct", operation="llm", units=4))
    assert result.reservation is not None

    provider.commit(result.reservation)
    provider.commit(result.reservation)
    provider.release(result.reservation, reason="late")

    assert provider.reserved_units == 0
    assert provider.committed == [{"reservation_id": "reservation-1", "units": 4}]
    assert provider.released == []


def test_static_provider_pricing_is_runtime_checkable_and_deterministic():
    pricing = StaticProviderPricing({("bedrock", "haiku"): (0.000001, 0.000005)})

    assert isinstance(pricing, ProviderPricing)
    quote = pricing.quote(
        PricingRequest(
            provider_id="bedrock",
            sku="haiku",
            input_units=1000,
            output_units=200,
        )
    )

    assert quote.provider_id == "bedrock"
    assert quote.sku == "haiku"
    assert quote.currency == "USD"
    assert quote.cost_usd == 0.002


def test_unknown_price_fails_closed_to_zero_configured_cost():
    pricing = StaticProviderPricing({})

    quote = pricing.quote(PricingRequest(provider_id="unknown", sku="missing", input_units=1))

    assert quote.cost_usd == 0.0
    assert quote.source == "configured"
