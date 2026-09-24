"""Typed client for the GovFiles v2 company-registry API.

Authentication is the ``X-API-Key`` header on every call to
``https://api.govfiles.dev``. The key is read from ``GOVFILES_API_KEY``
and is never hardcoded.

Company search defaults to the California registry (``us_ca``), which is
the jurisdiction Roof Engine uses for Fresno MSA commercial prospects.
Ownership matching does not take a jurisdiction filter; GovFiles resolves
a business name and street address to a legal operator on its own.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypedDict
from urllib.parse import quote

API_BASE_URL = "https://api.govfiles.dev"
API_KEY_ENV = "GOVFILES_API_KEY"
CALIFORNIA_JURISDICTION = "us_ca"
MAX_BATCH_LOCATIONS = 500
MAX_SEARCH_LIMIT = 100

Clock = Callable[[], float]
Sleeper = Callable[[float], None]
Transport = Callable[[str, str, dict[str, str], bytes | None, float], Any]


class GovFilesError(Exception):
    """Base error for GovFiles configuration and API failures."""


class GovFilesConfigError(GovFilesError):
    """Raised when ``GOVFILES_API_KEY`` is missing."""


class GovFilesAPIError(GovFilesError):
    """Raised when the data API returns a non-success status."""

    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self.payload = payload
        super().__init__(_format_api_error(status_code, payload))


class GovFilesBatchFailed(GovFilesError):
    """Raised when an ownership batch finishes with status ``failed``."""

    def __init__(self, batch: Mapping[str, Any]):
        self.batch = dict(batch)
        batch_id = self.batch.get("batch_id", "")
        super().__init__(f"GovFiles ownership batch {batch_id} failed")


class GovFilesTimeout(GovFilesError):
    """Raised when an ownership batch is still queued or running at the deadline."""

    def __init__(self, batch_id: str, last_status: str | None):
        self.batch_id = batch_id
        self.last_status = last_status
        super().__init__(
            f"GovFiles ownership batch {batch_id} still {last_status or 'pending'} at timeout"
        )


class CreditBalance(TypedDict):
    balance: int
    overage_enabled: bool
    overage_amount: int | None
    auto_topup_enabled: bool
    auto_topup_threshold_credits: int | None
    auto_topup_target_credits: int | None


class CompanySearchRequest(TypedDict, total=False):
    q: str
    match_alternative_names: bool
    match_previous_names: bool
    jurisdictions: str
    status: str
    order_by: str
    limit: int
    page: int


class LocalBusinessLocation(TypedDict, total=False):
    customer_record_id: str
    name: str
    address: str
    phone: str
    email: str
    website: str


def api_key_from_env() -> str | None:
    """Return the GovFiles API key, or ``None`` when it is unset or blank."""

    key = os.environ.get(API_KEY_ENV, "").strip()
    return key or None


class GovFilesClient:
    """Small wrapper around GovFiles v2 search, lookup, ownership, and credits."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = API_BASE_URL,
        timeout: float = 30.0,
        transport: Transport | None = None,
    ):
        key = (api_key if api_key is not None else api_key_from_env() or "").strip()
        if not key:
            raise GovFilesConfigError(
                f"{API_KEY_ENV} is not set. Create a key at https://govfiles.dev "
                "and export it before calling the API."
            )
        self.api_key = key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport or _urllib_transport

    def search_companies(
        self,
        q: str,
        *,
        jurisdictions: str = CALIFORNIA_JURISDICTION,
        limit: int = 10,
        page: int = 1,
        status: str = "any",
        order_by: str = "relevance",
        match_alternative_names: bool = True,
        match_previous_names: bool = True,
    ) -> dict[str, Any]:
        """POST ``/v2/companies/search``.

        ``jurisdictions`` defaults to California (``us_ca``). Pass ``"all"``
        or a comma-separated list such as ``"us_ca,us_nv"`` to widen the search.
        ``order_by="relevance"`` ranks name matches for outreach; the API's
        own default is ``jurisdiction``.
        """

        query = q.strip()
        if not query:
            raise ValueError("q is required")
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
        if page < 1:
            raise ValueError("page must be >= 1")
        jurisdiction_filter = jurisdictions.strip()
        if not jurisdiction_filter:
            raise ValueError("jurisdictions is required")

        body: CompanySearchRequest = {
            "q": query,
            "jurisdictions": jurisdiction_filter,
            "limit": limit,
            "page": page,
            "status": status,
            "order_by": order_by,
            "match_alternative_names": match_alternative_names,
            "match_previous_names": match_previous_names,
        }
        return self._request("POST", "/v2/companies/search", body)

    def get_company(self, jurisdiction_code: str, entity_number: str) -> dict[str, Any]:
        """GET ``/v2/companies/{jurisdiction_code}/{entity_number}``."""

        jurisdiction = jurisdiction_code.strip().lower()
        number = entity_number.strip()
        if not jurisdiction or not number:
            raise ValueError("jurisdiction_code and entity_number are required")
        path = (
            f"/v2/companies/{quote(jurisdiction, safe='')}/{quote(number, safe='')}"
        )
        return self._request("GET", path)

    def get_credits(self) -> CreditBalance:
        """GET ``/v2/credits``. Checking the balance does not spend credits."""

        return self._request("GET", "/v2/credits")

    def create_local_business_batch(
        self, locations: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        """POST ``/v2/local-businesses/batches`` and return the accepted batch."""

        if isinstance(locations, (str, bytes)) or not isinstance(locations, Sequence):
            raise TypeError("locations must be a sequence of business records")
        if not 1 <= len(locations) <= MAX_BATCH_LOCATIONS:
            raise ValueError(
                f"locations must contain 1 to {MAX_BATCH_LOCATIONS} businesses"
            )
        body = {"locations": [_normalize_location(location) for location in locations]}
        return self._request("POST", "/v2/local-businesses/batches", body)

    def get_local_business_batch(self, batch_id: str) -> dict[str, Any]:
        """GET ``/v2/local-businesses/batches/{batch_id}``."""

        return self._request("GET", _batch_path(batch_id))

    def poll_local_business_batch(
        self,
        batch_id: str,
        *,
        interval_seconds: float = 1.0,
        timeout_seconds: float = 90.0,
        sleeper: Sleeper = time.sleep,
        clock: Clock = time.monotonic,
    ) -> dict[str, Any]:
        """Poll until the batch succeeds. Failed batches and timeouts raise."""

        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        deadline = clock() + timeout_seconds
        wait = interval_seconds
        last_status: str | None = None
        while True:
            batch = self.get_local_business_batch(batch_id)
            last_status = batch.get("status")
            if last_status == "succeeded":
                return batch
            if last_status == "failed":
                raise GovFilesBatchFailed(batch)
            if last_status not in ("queued", "running"):
                raise GovFilesAPIError(
                    200,
                    {"detail": f"unexpected batch status {last_status!r}", "batch": batch},
                )
            if clock() >= deadline:
                raise GovFilesTimeout(batch_id, last_status)
            sleeper(wait)
            wait = min(wait * 1.5, 5.0)

    def resolve_ownership(
        self,
        locations: Sequence[Mapping[str, Any]],
        **poll_kwargs: Any,
    ) -> dict[str, Any]:
        """Create an ownership batch and poll until it succeeds."""

        batch = self.create_local_business_batch(locations)
        status = batch.get("status")
        if status == "succeeded":
            return batch
        if status == "failed":
            raise GovFilesBatchFailed(batch)
        batch_id = batch.get("batch_id")
        if not batch_id:
            raise GovFilesAPIError(202, {"detail": "batch response missing batch_id", "batch": batch})
        return self.poll_local_business_batch(str(batch_id), **poll_kwargs)

    def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "X-API-Key": self.api_key,
            "User-Agent": "roofengine-govfiles/0.1",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        return self._transport(method, url, headers, payload, self.timeout)


def _normalize_location(location: Mapping[str, Any]) -> LocalBusinessLocation:
    name = str(location.get("name") or "").strip()
    address = str(location.get("address") or "").strip()
    if not name or not address:
        raise ValueError("each location requires name and address")
    if len(name) > 300:
        raise ValueError("location name must be at most 300 characters")
    if len(address) > 1000:
        raise ValueError("location address must be at most 1000 characters")

    normalized: LocalBusinessLocation = {"name": name, "address": address}
    customer_record_id = str(location.get("customer_record_id") or "").strip()
    if customer_record_id:
        normalized["customer_record_id"] = customer_record_id
    for field, limit in (("phone", 50), ("email", 320), ("website", 2048)):
        value = str(location.get(field) or "").strip()
        if not value:
            continue
        if len(value) > limit:
            raise ValueError(f"location {field} must be at most {limit} characters")
        normalized[field] = value  # type: ignore[literal-required]
    return normalized


def _batch_path(batch_id: str) -> str:
    cleaned = batch_id.strip()
    if not cleaned:
        raise ValueError("batch_id is required")
    return f"/v2/local-businesses/batches/{quote(cleaned, safe='')}"


def _format_api_error(status_code: int, payload: Any) -> str:
    if isinstance(payload, dict) and "detail" in payload:
        detail = payload["detail"]
    else:
        detail = payload
    if detail in (None, "", {}, []):
        return f"GovFiles API error {status_code}"
    return f"GovFiles API error {status_code}: {detail}"


def _urllib_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> Any:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise GovFilesAPIError(exc.code, _parse_json(raw)) from exc
    except urllib.error.URLError as exc:
        raise GovFilesError(f"GovFiles request failed: {exc.reason}") from exc
    return _parse_json(raw)


def _parse_json(raw: str) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw
