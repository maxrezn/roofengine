"""Offline tests for the GovFiles client and Fresno prospect example."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
import urllib.error
from contextlib import redirect_stdout
from io import BytesIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from roofengine.enrich import (  # noqa: E402
    CALIFORNIA_SEARCH_EXAMPLE,
    FRESNO_MSA_EXAMPLE,
    enrich_prospect,
    format_us_address,
    summarize_company_search,
    summarize_ownership,
)
from roofengine.govfiles import (  # noqa: E402
    API_KEY_ENV,
    CALIFORNIA_JURISDICTION,
    GovFilesAPIError,
    GovFilesBatchFailed,
    GovFilesClient,
    GovFilesConfigError,
    GovFilesTimeout,
)
from scripts.enrich_fresno_prospect import main  # noqa: E402


class RecordingTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "body": json.loads(body.decode("utf-8")) if body else None,
                "timeout": timeout,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def client_with(responses, api_key="test-key"):
    transport = RecordingTransport(responses)
    client = GovFilesClient(api_key=api_key, transport=transport)
    return client, transport


class GovFilesClientTests(unittest.TestCase):
    def test_missing_api_key_raises_before_any_request(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(GovFilesConfigError):
                GovFilesClient()

    def test_blank_api_key_raises(self):
        with self.assertRaises(GovFilesConfigError):
            GovFilesClient(api_key="   ")

    def test_search_companies_defaults_to_california(self):
        client, transport = client_with([{"results": [], "summary": {"returned": 0}}])
        client.search_companies("metal warehouse", limit=5)

        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], "https://api.govfiles.dev/v2/companies/search")
        self.assertEqual(call["headers"]["X-API-Key"], "test-key")
        self.assertEqual(call["body"]["q"], "metal warehouse")
        self.assertEqual(call["body"]["jurisdictions"], CALIFORNIA_JURISDICTION)
        self.assertEqual(call["body"]["limit"], 5)
        self.assertEqual(set(call["body"]), {"q", "jurisdictions", "limit"})
        self.assertNotIn("test-key", call["url"])
        self.assertNotIn("test-key", json.dumps(call["body"]))

    def test_search_companies_sends_optional_filters_when_set(self):
        client, transport = client_with([{"results": []}])
        client.search_companies(
            "Buzz Oates",
            jurisdictions="us_ca",
            limit=1,
            status="active",
            order_by="relevance",
        )
        self.assertEqual(
            transport.calls[0]["body"],
            {
                "q": "Buzz Oates",
                "jurisdictions": "us_ca",
                "limit": 1,
                "status": "active",
                "order_by": "relevance",
            },
        )

    def test_get_company_encodes_path_segments(self):
        client, transport = client_with([{"legal_name": "ACME"}])
        client.get_company(" US_CA ", "C123/456")
        self.assertEqual(
            transport.calls[0]["url"],
            "https://api.govfiles.dev/v2/companies/us_ca/C123%2F456",
        )
        self.assertEqual(transport.calls[0]["method"], "GET")

    def test_get_credits(self):
        client, transport = client_with([{"balance": 1000, "overage_enabled": False}])
        balance = client.get_credits()
        self.assertEqual(balance["balance"], 1000)
        self.assertEqual(transport.calls[0]["url"], "https://api.govfiles.dev/v2/credits")

    def test_create_batch_sends_only_known_location_fields(self):
        client, transport = client_with([{"batch_id": "batch_abc", "status": "queued"}])
        client.create_local_business_batch(
            [
                {
                    "name": " Fresno Metal Warehouse ",
                    "address": " 4500 N Blackstone Ave, Fresno, CA 93726 ",
                    "customer_record_id": "fresno-1",
                    "ignored": "nope",
                }
            ]
        )
        body = transport.calls[0]["body"]
        self.assertEqual(
            body,
            {
                "locations": [
                    {
                        "name": "Fresno Metal Warehouse",
                        "address": "4500 N Blackstone Ave, Fresno, CA 93726",
                        "customer_record_id": "fresno-1",
                    }
                ]
            },
        )
        self.assertEqual(
            transport.calls[0]["url"],
            "https://api.govfiles.dev/v2/local-businesses/batches",
        )

    def test_resolve_ownership_polls_until_succeeded(self):
        succeeded = {
            "batch_id": "batch_abc",
            "status": "succeeded",
            "result": {"credits_charged": 0, "summary": {}, "results": []},
        }
        client, transport = client_with(
            [
                {"batch_id": "batch_abc", "status": "queued"},
                {"batch_id": "batch_abc", "status": "queued"},
                {"batch_id": "batch_abc", "status": "running"},
                succeeded,
            ]
        )
        sleeps = []
        result = client.resolve_ownership(
            [{"name": "Fresno Metal Warehouse", "address": "4500 N Blackstone Ave"}],
            interval_seconds=0.25,
            timeout_seconds=10,
            sleeper=sleeps.append,
            clock=lambda: 0,
        )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(sleeps, [0.25, 0.375])
        self.assertEqual(transport.calls[1]["method"], "GET")
        self.assertTrue(transport.calls[1]["url"].endswith("/batch_abc"))

    def test_poll_raises_when_batch_fails(self):
        client, _transport = client_with(
            [{"batch_id": "batch_abc", "status": "failed", "completed_at": "2026-09-24T00:00:00Z"}]
        )
        with self.assertRaises(GovFilesBatchFailed):
            client.poll_local_business_batch("batch_abc", sleeper=lambda _seconds: None, clock=lambda: 0)

    def test_poll_times_out(self):
        client, _transport = client_with([{"batch_id": "batch_abc", "status": "queued"}])
        clocks = iter((0, 50))
        with self.assertRaises(GovFilesTimeout):
            client.poll_local_business_batch(
                "batch_abc",
                timeout_seconds=10,
                sleeper=lambda _seconds: None,
                clock=lambda: next(clocks),
            )

    def test_http_error_becomes_api_error_without_leaking_the_key(self):
        error = urllib.error.HTTPError(
            url="https://api.govfiles.dev/v2/credits",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=BytesIO(b'{"detail":"missing or invalid api key"}'),
        )
        with mock.patch("roofengine.govfiles.urllib.request.urlopen", side_effect=error):
            client = GovFilesClient(api_key="super-secret-key")
            with self.assertRaises(GovFilesAPIError) as caught:
                client.get_credits()
        self.assertEqual(caught.exception.status_code, 401)
        self.assertNotIn("super-secret-key", str(caught.exception))


class EnrichmentTests(unittest.TestCase):
    def test_fresno_address_format(self):
        example = FRESNO_MSA_EXAMPLE
        self.assertEqual(
            format_us_address(example["street"], example["city"], example["state"], example["postal_code"]),
            "4500 N Blackstone Ave, Fresno, CA 93726",
        )

    def test_enrich_prospect_returns_outreach_summary(self):
        batch = {
            "batch_id": "batch_fresno",
            "status": "succeeded",
            "result": {
                "credits_charged": 15,
                "summary": {"submitted": 1, "matched": 1, "unmatched": 0},
                "results": [
                    {
                        "customer_record_id": "fresno-msa-example",
                        "match": {"status": "matched", "relationship_status": "current"},
                        "restaurant": {
                            "name": "Fresno Metal Warehouse",
                            "address": {
                                "line1": "4500 N Blackstone Ave",
                                "city": "Fresno",
                                "state": "CA",
                                "postal_code": "93726",
                            },
                        },
                        "operator": {
                            "legal_name": "FRESNO METAL WAREHOUSE LLC",
                            "entity_type": "llc",
                            "business_registration": {
                                "jurisdiction": "us_ca",
                                "registration_number": "202412345678",
                                "status": "active",
                                "incorporated_on": "2024-01-15",
                            },
                        },
                        "people": [
                            {"name": "ADA LOVELACE", "title": "Manager", "role": "manager"}
                        ],
                    }
                ],
            },
        }
        client, transport = client_with([batch])
        summary = enrich_prospect(
            client,
            name="Fresno Metal Warehouse",
            street="4500 N Blackstone Ave",
            city="Fresno",
            state="CA",
            postal_code="93726",
            customer_record_id="fresno-msa-example",
        )
        self.assertEqual(summary["batch_id"], "batch_fresno")
        self.assertEqual(summary["credits_charged"], 15)
        match = summary["matches"][0]
        self.assertEqual(match["legal_name"], "FRESNO METAL WAREHOUSE LLC")
        self.assertEqual(match["jurisdiction"], "us_ca")
        self.assertEqual(match["registration_number"], "202412345678")
        self.assertEqual(match["people"][0]["name"], "ADA LOVELACE")
        self.assertEqual(transport.calls[0]["body"]["locations"][0]["address"], "4500 N Blackstone Ave, Fresno, CA 93726")

    def test_summarize_unmatched_location(self):
        summary = summarize_ownership(
            {
                "batch_id": "batch_none",
                "status": "succeeded",
                "result": {
                    "credits_charged": 0,
                    "summary": {"submitted": 1, "matched": 0, "unmatched": 1},
                    "results": [
                        {
                            "customer_record_id": "fresno-msa-example",
                            "match": {"status": "unmatched"},
                            "restaurant": {"name": "Fresno Metal Warehouse"},
                            "operator": None,
                            "people": [],
                        }
                    ],
                },
            }
        )
        self.assertIsNone(summary["matches"][0]["legal_name"])
        self.assertEqual(summary["matches"][0]["people"], [])

    def test_dry_run_script_skips_live_calls_when_key_is_unset(self):
        env = os.environ.copy()
        env.pop(API_KEY_ENV, None)
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "enrich_fresno_prospect.py")],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("skipping live GovFiles calls", completed.stdout)
        self.assertIn("4500 N Blackstone Ave, Fresno, CA 93726", completed.stdout)
        self.assertIn("/v2/local-businesses/batches", completed.stdout)
        self.assertNotIn("gf_" + "live_", completed.stdout)

    def test_company_search_summary_uses_legal_name(self):
        example = CALIFORNIA_SEARCH_EXAMPLE
        summary = summarize_company_search(
            {
                "request": {"query": example["q"], "jurisdictions": "us_ca"},
                "summary": {"returned": 1, "total_matches": 1, "next_page": None},
                "results": [
                    {
                        "match": {
                            "matched_field": "name",
                            "matched_value": example["legal_name"],
                        },
                        "company": {
                            "legal_name": example["legal_name"],
                            "name": "not the company field",
                            "jurisdiction_code": example["jurisdiction_code"],
                            "entity_number": example["entity_number"],
                            "status": example["status"],
                            "addresses": {"headquarters": {"locality": "STOCKTON", "region": "CA"}},
                            "parties": [{"type": "person", "name": "EXAMPLE PRINCIPAL", "roles": []}],
                        },
                    }
                ],
            }
        )
        company = summary["companies"][0]
        self.assertEqual(company["legal_name"], "BUZZ OATES DEVELOPMENT, L.P.")
        self.assertEqual(company["jurisdiction_code"], "us_ca")
        self.assertEqual(company["entity_number"], "200105100029")
        self.assertEqual(company["status"], "active")
        self.assertEqual(company["addresses"]["headquarters"]["region"], "CA")
        self.assertEqual(company["parties"][0]["name"], "EXAMPLE PRINCIPAL")
        self.assertNotEqual(company["legal_name"], "not the company field")

    def test_dry_run_search_matches_verified_california_request(self):
        with mock.patch.dict(os.environ, {API_KEY_ENV: ""}, clear=False):
            os.environ.pop(API_KEY_ENV, None)
            with redirect_stdout(io.StringIO()) as stdout:
                code = main(["--mode", "search"])
        self.assertEqual(code, 0)
        self.assertIn('"q": "Buzz Oates"', stdout.getvalue())
        payload = json.loads(stdout.getvalue().split("Dry run request:\n", 1)[1].split("\nCreate a key", 1)[0])
        self.assertEqual(
            payload["body"],
            {"q": "Buzz Oates", "jurisdictions": "us_ca", "limit": 1},
        )
        self.assertEqual(payload["url"], "https://api.govfiles.dev/v2/companies/search")

    def test_repository_does_not_contain_a_live_key(self):
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if any(part in {".git", "__pycache__"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            self.assertNotIn("gf_" + "live_", text, str(path))


if __name__ == "__main__":
    unittest.main()
