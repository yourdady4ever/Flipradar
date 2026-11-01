"""Generate API_DOCUMENTATION.md from the live FastAPI OpenAPI schema.

Usage (from backend/):  python scripts/gen_api_docs.py > ../API_DOCUMENTATION.md

The hand-written introduction lives in INTRO below; the endpoint reference and schema reference are
generated, so the document never drifts from the code.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

INTRO = """# FLIP RADAR API documentation

> Generated from the OpenAPI schema by `backend/scripts/gen_api_docs.py`. Do not edit the endpoint
> reference by hand; edit the code and regenerate it. Interactive docs: `http://<server>/docs`.

## Basics

* Base URL: `http://<host>:<port>`. Every endpoint except `GET /health`, `GET /` (dashboard) and
  `/static/*` lives under **`/api/v1`**.
* **Authentication:** send `Authorization: Bearer <token>`. Create tokens with
  `python -m flipradar create-token --name <device>`. A missing or invalid token returns `401`.
* **Money** is always an integer number of **centavos** (`*_cents`): `1234567` = $12,345.67 MXN.
  **Percentages** are integer **basis points** (`*_bp`): `1600` = 16 %. Datetimes are ISO-8601 UTC.
* **Errors:** `{"detail": "message"}`. Validation errors (`422`) return a list of field errors.
  `404` = unknown id, `409` = conflict (for example, selling more units than you have in stock),
  `429` = rate limited (see the `Retry-After` header).
* **Pagination:** `limit` (max 200) and `offset`. Paged responses are
  `{"items": [...], "total": N, "limit": L, "offset": O}`.

## Typical workflows

```bash
TOKEN=fr_...; API=http://127.0.0.1:8000/api/v1; H="Authorization: Bearer $TOKEN"

# 1. Collect listings now (only sources you configured are used; others are reported as skipped)
curl -s -X POST $API/collect -H "$H" -H 'Content-Type: application/json' \\
     -d '{"query": "nintendo switch oled", "limit": 50}'

# 2. Best opportunities first (suspicious listings excluded by default)
curl -s "$API/opportunities?sort=score&limit=20" -H "$H"

# 3. Full explanation, cost breakdown, comparables and risk flags
curl -s $API/opportunities/1 -H "$H"

# 4. Profit calculator: buy at $4,500, sell at $6,200 on Mercado Libre Clásica
curl -s -X POST $API/calculator/profit -H "$H" -H 'Content-Type: application/json' \\
     -d '{"buy_price_cents": 450000, "sale_price_cents": 620000, "fee_profile_key": "ml_clasica", "target_roi_bp": 2000}'

# 5. Bought it -> inventory, then record the sale
curl -s -X POST $API/opportunities/1/buy -H "$H" -H 'Content-Type: application/json' -d '{"quantity": 1}'
curl -s -X POST $API/inventory/1/sales -H "$H" -H 'Content-Type: application/json' \\
     -d '{"quantity": 1, "unit_price_cents": 620000, "fee_profile_key": "ml_clasica"}'

# 6. Analytics
curl -s "$API/analytics/summary?days=30" -H "$H"
```

## CSV import format

Upload as multipart field `file` to `POST /api/v1/import/csv` (optional query `source=<key>`).
A header row is required. Columns are case-insensitive:

| Column | Required | Notes |
|---|---|---|
| `title` | yes | Listing title as shown |
| `price` | yes | Pesos, e.g. `12999.00` or `$12,999` |
| `currency` | no | Default `MXN` |
| `url`, `condition`, `shipping`, `brand`, `model`, `gtin`, `seller`, `location` | no | `condition`: new/used/refurbished/for_parts or Spanish equivalents |
| `source` | no | Default `csv_import` |
| `external_id` | no | Default: hash of url/title |
| `sold_quantity`, `available_quantity` | no | Integers, if you observed them |
| `observed_at` | no | ISO date/datetime |

Limits: 5 MB and 20,000 rows per file. Invalid rows are rejected individually with a reason.

"""


def _type_of(schema: dict, components: dict) -> str:
    if "$ref" in schema:
        return schema["$ref"].split("/")[-1]
    if "anyOf" in schema:
        return " | ".join(_type_of(s, components) for s in schema["anyOf"])
    if schema.get("type") == "array":
        return f"list[{_type_of(schema.get('items', {}), components)}]"
    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"])
    if "const" in schema:
        return json.dumps(schema["const"])
    t = schema.get("type", "object")
    if t == "object" and "additionalProperties" in schema and isinstance(schema["additionalProperties"], dict):
        return f"dict[str, {_type_of(schema['additionalProperties'], components)}]"
    return str(t)


def main() -> None:
    tmp = tempfile.mkdtemp()
    os.environ.setdefault("FLIPRADAR_DATABASE_URL", f"sqlite:///{tmp}/docs.db")
    os.environ.setdefault("FLIPRADAR_SCHEDULER_ENABLED", "false")
    from flipradar.main import get_app

    app = get_app()
    spec = app.openapi()
    components = spec.get("components", {}).get("schemas", {})
    out = [INTRO, "## Endpoint reference\n"]
    by_tag: dict[str, list[tuple[str, str, dict]]] = {}
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            tag = (op.get("tags") or ["other"])[0]
            by_tag.setdefault(tag, []).append((method.upper(), path, op))
    for tag in sorted(by_tag):
        out.append(f"### {tag}\n")
        out.append("| Method | Path | Summary | Request body | Response |")
        out.append("|---|---|---|---|---|")
        for method, path, op in sorted(by_tag[tag], key=lambda x: (x[1], x[0])):
            body = op.get("requestBody", {}).get("content", {})
            req = ""
            if "application/json" in body:
                req = _type_of(body["application/json"]["schema"], components)
            elif "multipart/form-data" in body:
                req = "multipart/form-data"
            resp = ""
            for code in ("200", "201"):
                content = op.get("responses", {}).get(code, {}).get("content", {})
                if "application/json" in content:
                    resp = f"{code}: {_type_of(content['application/json']['schema'], components)}"
                    break
            params = [p for p in op.get("parameters", []) if p.get("in") == "query"]
            p_txt = ""
            if params:
                p_txt = "<br>query: " + ", ".join(f"`{p['name']}`" for p in params)
            summary = (op.get("summary") or "").replace("|", "/")
            out.append(f"| {method} | `{path}`{p_txt} | {summary} | {req} | {resp} |")
        out.append("")
    out.append("## Schema reference\n")
    for name in sorted(components):
        schema = components[name]
        props = schema.get("properties", {})
        if not props:
            continue
        required = set(schema.get("required", []))
        out.append(f"### {name}\n")
        out.append("| Field | Type | Required |")
        out.append("|---|---|---|")
        for field, fs in props.items():
            out.append(f"| `{field}` | {_type_of(fs, components)} | {'yes' if field in required else ''} |")
        out.append("")
    sys.stdout.write("\n".join(out))


if __name__ == "__main__":
    main()
