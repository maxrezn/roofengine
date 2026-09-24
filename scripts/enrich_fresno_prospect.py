#!/usr/bin/env python3
"""Match a Fresno / California commercial prospect through GovFiles.

With ``GOVFILES_API_KEY`` unset this prints the request it would send and
exits without calling the network. With the key set, the default path is
ownership matching (name + street address). ``--mode search`` runs the
verified California company search and prints ``legal_name``, not ``name``.

Get a key at https://govfiles.dev and export it as ``GOVFILES_API_KEY``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from roofengine.enrich import (  # noqa: E402
    CALIFORNIA_SEARCH_EXAMPLE,
    FRESNO_MSA_EXAMPLE,
    enrich_prospect,
    prospect_location,
    summarize_company_search,
)
from roofengine.govfiles import (  # noqa: E402
    API_BASE_URL,
    CALIFORNIA_JURISDICTION,
    GovFilesClient,
    api_key_from_env,
)


def load_local_env(path: Path) -> None:
    """Copy ``GOVFILES_API_KEY`` from a local ``.env`` when the shell has none.

    Blank values are ignored. Existing environment variables win, so a key
    already exported is never overwritten. Other variables in the file are
    left alone.
    """

    if os.environ.get("GOVFILES_API_KEY", "").strip():
        return
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != "GOVFILES_API_KEY":
            continue
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ["GOVFILES_API_KEY"] = value
        return


def build_parser() -> argparse.ArgumentParser:
    example = FRESNO_MSA_EXAMPLE
    parser = argparse.ArgumentParser(
        description=(
            "Resolve a California commercial prospect to a legal entity via GovFiles. "
            "Defaults to a Fresno MSA example."
        )
    )
    parser.add_argument(
        "--mode",
        choices=("ownership", "search"),
        default="ownership",
        help="ownership matches name + street address; search queries the California registry",
    )
    parser.add_argument(
        "--name",
        default=None,
        help=(
            "Business or company name. Ownership defaults to the Fresno example. "
            "Search defaults to the verified California query, Buzz Oates."
        ),
    )
    parser.add_argument("--street", default=example["street"], help="Street address")
    parser.add_argument("--city", default=example["city"])
    parser.add_argument("--state", default=example["state"])
    parser.add_argument("--postal-code", default=example["postal_code"])
    parser.add_argument("--customer-record-id", default=example["customer_record_id"])
    parser.add_argument("--phone", default=None)
    parser.add_argument("--email", default=None)
    parser.add_argument("--website", default=None)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Company-search page size. Defaults to 1, matching the verified California search.",
    )
    parser.add_argument(
        "--credits",
        action="store_true",
        help="Print the GovFiles credit balance and exit",
    )
    return parser


def resolve_args(args: argparse.Namespace) -> argparse.Namespace:
    """Fill the Fresno ownership example or the verified California search."""

    if args.mode == "search":
        if not args.name:
            args.name = str(CALIFORNIA_SEARCH_EXAMPLE["q"])
        if args.limit is None:
            args.limit = int(CALIFORNIA_SEARCH_EXAMPLE["limit"])
        return args
    if not args.name:
        args.name = FRESNO_MSA_EXAMPLE["name"]
    return args


def dry_run_payload(args: argparse.Namespace) -> dict:
    if args.credits:
        return {"method": "GET", "url": f"{API_BASE_URL}/v2/credits"}
    if args.mode == "search":
        return {
            "method": "POST",
            "url": f"{API_BASE_URL}/v2/companies/search",
            "body": {
                "q": args.name,
                "jurisdictions": CALIFORNIA_JURISDICTION,
                "limit": args.limit,
            },
        }
    location = prospect_location(
        name=args.name,
        street=args.street,
        city=args.city,
        state=args.state,
        postal_code=args.postal_code,
        customer_record_id=args.customer_record_id,
        phone=args.phone,
        email=args.email,
        website=args.website,
    )
    return {
        "method": "POST",
        "url": f"{API_BASE_URL}/v2/local-businesses/batches",
        "body": {"locations": [location]},
    }


def main(argv: list[str] | None = None) -> int:
    load_local_env(ROOT / ".env")
    args = resolve_args(build_parser().parse_args(argv))
    if api_key_from_env() is None:
        print("GOVFILES_API_KEY is unset; skipping live GovFiles calls.")
        print("Dry run request:")
        print(json.dumps(dry_run_payload(args), indent=2))
        print("Create a key at https://govfiles.dev and export GOVFILES_API_KEY to run this live.")
        return 0

    client = GovFilesClient()
    if args.credits:
        print(json.dumps(client.get_credits(), indent=2))
        return 0
    if args.mode == "search":
        response = client.search_companies(
            args.name,
            jurisdictions=CALIFORNIA_JURISDICTION,
            limit=args.limit,
        )
        print(json.dumps(summarize_company_search(response), indent=2))
        return 0

    summary = enrich_prospect(
        client,
        name=args.name,
        street=args.street,
        city=args.city,
        state=args.state,
        postal_code=args.postal_code,
        customer_record_id=args.customer_record_id,
        phone=args.phone,
        email=args.email,
        website=args.website,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
