# roofengine

Sell More Coatings static site lives in this repo.

## GovFiles entity enrichment

Roof Engine uses [GovFiles](https://govfiles.dev) to turn a commercial prospect — the business name and street address on a metal-roof or foam-coating lead — into the legal entity, registry status, and published principals behind it. Company search defaults to California (`us_ca`), which covers the Fresno MSA and the rest of the state. Ownership matching sends the name and street address and lets GovFiles resolve the operator; that endpoint does not take a jurisdiction filter.

### Get an API key

1. Open https://govfiles.dev and choose **Get API key**.
2. Create a key in the dashboard at https://govfiles.dev/dashboard/. The secret is shown once.
3. Export it as `GOVFILES_API_KEY`, or copy `.env.example` to `.env` and set it there.

The client reads the key only from `GOVFILES_API_KEY`. Do not hardcode it and do not commit `.env`.

New accounts include 1,000 free credits. A company search or direct entity lookup costs 1 credit per returned row. An ownership match costs 15 credits when a location comes back with at least one person. Unmatched locations, status polling, and the credit-balance check are free.

### Fresno / California example

Dry run (no key, no network). Prints the ownership-batch request for the built-in Fresno prospect:

```bash
python3 scripts/enrich_fresno_prospect.py
```

Live ownership match. Pass the company name and street address from a coating prospect:

```bash
export GOVFILES_API_KEY=your_key_here
python3 scripts/enrich_fresno_prospect.py \
  --name "Fresno Metal Warehouse" \
  --street "4500 N Blackstone Ave" \
  --city Fresno \
  --state CA \
  --postal-code 93726
```

The script prints one JSON object per run: batch id, credits charged, and a match row with legal name, jurisdiction, registration number, registry status, and people (name, title, role).

California registry search (active entities, filtered to `us_ca`):

```bash
python3 scripts/enrich_fresno_prospect.py --mode search --name "Fresno Metal Warehouse"
```

Credit balance:

```bash
python3 scripts/enrich_fresno_prospect.py --credits
```

### Library

```python
from roofengine.enrich import enrich_prospect
from roofengine.govfiles import GovFilesClient

client = GovFilesClient()  # reads GOVFILES_API_KEY
summary = enrich_prospect(
    client,
    name="Fresno Metal Warehouse",
    street="4500 N Blackstone Ave",
    city="Fresno",
    state="CA",
    postal_code="93726",
)
```

`GovFilesClient` calls `https://api.govfiles.dev` with `X-API-Key`:

| Method | Endpoint |
| --- | --- |
| `search_companies` | `POST /v2/companies/search` (default `jurisdictions="us_ca"`) |
| `get_company` | `GET /v2/companies/{jurisdiction_code}/{entity_number}` |
| `create_local_business_batch` | `POST /v2/local-businesses/batches` |
| `get_local_business_batch` / `poll_local_business_batch` | `GET /v2/local-businesses/batches/{batch_id}` |
| `resolve_ownership` | create a batch, then poll until `succeeded` |
| `get_credits` | `GET /v2/credits` |

### Tests

```bash
python3 -m unittest discover -s tests
```

The tests mock HTTP and never call GovFiles. `scripts/enrich_fresno_prospect.py` skips live calls when `GOVFILES_API_KEY` is unset.
