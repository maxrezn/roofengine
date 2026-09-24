"""Resolve a commercial prospect to a legal entity and published principals.

Roof Engine uses this for foam-coating and restoration outreach. The
default geography is the Fresno MSA in California: company search is
filtered to ``us_ca``, and the sample location is a Fresno street address.
"""

from __future__ import annotations

from typing import Any, Mapping

from roofengine.govfiles import CALIFORNIA_JURISDICTION, GovFilesClient

# Fictional prospect used by the dry-run example. A live run sends this
# name and address only when the caller does not pass their own.
FRESNO_MSA_EXAMPLE: dict[str, str] = {
    "customer_record_id": "fresno-msa-example",
    "name": "Fresno Metal Warehouse",
    "street": "4500 N Blackstone Ave",
    "city": "Fresno",
    "state": "CA",
    "postal_code": "93726",
}


def format_us_address(
    street: str,
    city: str = "",
    state: str = "",
    postal_code: str = "",
) -> str:
    """Build the single address string GovFiles ownership matching expects."""

    street_line = street.strip()
    if not street_line:
        raise ValueError("street is required")
    region = " ".join(part for part in (state.strip(), postal_code.strip()) if part)
    locality = ", ".join(part for part in (city.strip(), region) if part)
    if locality:
        return f"{street_line}, {locality}"
    return street_line


def prospect_location(
    *,
    name: str,
    street: str,
    city: str = "Fresno",
    state: str = "CA",
    postal_code: str = "",
    customer_record_id: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    website: str | None = None,
) -> dict[str, str]:
    """Map a Roof Engine prospect onto a GovFiles local-business location."""

    location: dict[str, str] = {
        "name": name.strip(),
        "address": format_us_address(street, city, state, postal_code),
    }
    if customer_record_id and customer_record_id.strip():
        location["customer_record_id"] = customer_record_id.strip()
    for field, value in (("phone", phone), ("email", email), ("website", website)):
        if value and value.strip():
            location[field] = value.strip()
    return location


def enrich_prospect(
    client: GovFilesClient,
    *,
    name: str,
    street: str,
    city: str = "Fresno",
    state: str = "CA",
    postal_code: str = "",
    customer_record_id: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    website: str | None = None,
    **poll_kwargs: Any,
) -> dict[str, Any]:
    """Match one commercial building to its operator and principals.

    Submits a one-location ownership batch and polls until GovFiles finishes.
    A location that returns at least one person costs 15 credits. Unmatched
    locations and polling are free.
    """

    location = prospect_location(
        name=name,
        street=street,
        city=city,
        state=state,
        postal_code=postal_code,
        customer_record_id=customer_record_id,
        phone=phone,
        email=email,
        website=website,
    )
    batch = client.resolve_ownership([location], **poll_kwargs)
    return summarize_ownership(batch)


def search_california_companies(
    client: GovFilesClient,
    q: str,
    *,
    limit: int = 10,
    status: str = "active",
) -> dict[str, Any]:
    """Search the California registry for a commercial prospect name."""

    return client.search_companies(
        q,
        jurisdictions=CALIFORNIA_JURISDICTION,
        limit=limit,
        status=status,
        order_by="relevance",
    )


def summarize_ownership(batch: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten a succeeded ownership batch into outreach-oriented rows."""

    if batch.get("status") != "succeeded":
        raise ValueError("ownership batch has not succeeded")
    document = batch.get("result") or {}
    matches = [_summarize_result(item) for item in document.get("results") or []]
    return {
        "batch_id": batch.get("batch_id"),
        "credits_charged": document.get("credits_charged"),
        "summary": document.get("summary"),
        "matches": matches,
    }


def _summarize_result(item: Mapping[str, Any]) -> dict[str, Any]:
    match = item.get("match") or {}
    restaurant = item.get("restaurant") or {}
    operator = item.get("operator") or {}
    registration = operator.get("business_registration") or {}
    address = restaurant.get("address") or {}
    submitted_address = _format_submitted_address(address)
    people = [
        {
            "name": person.get("name"),
            "title": person.get("title"),
            "role": person.get("role"),
        }
        for person in item.get("people") or []
    ]
    return {
        "customer_record_id": item.get("customer_record_id"),
        "match_status": match.get("status"),
        "relationship_status": match.get("relationship_status"),
        "submitted_name": restaurant.get("name"),
        "submitted_address": submitted_address,
        "legal_name": operator.get("legal_name"),
        "entity_type": operator.get("entity_type"),
        "jurisdiction": registration.get("jurisdiction"),
        "registration_number": registration.get("registration_number"),
        "registry_status": registration.get("status"),
        "incorporated_on": registration.get("incorporated_on"),
        "people": people,
    }


def _format_submitted_address(address: Mapping[str, Any]) -> str | None:
    if not address:
        return None
    region = " ".join(
        part for part in (address.get("state") or "", address.get("postal_code") or "") if part
    )
    locality = ", ".join(part for part in (address.get("city") or "", region) if part)
    line = ", ".join(part for part in (address.get("line1") or "", locality) if part)
    return line or None
