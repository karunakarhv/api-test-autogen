"""
generate_postman.py
-------------------
Reads an OpenAPI spec via SpecParser and writes a Postman
Collection v2.1 JSON file ready to run with Newman.

Run:
    python src/generate_postman.py specs/petstore.yaml

Output:
    output/petstore_collection.json
    output/petstore_environment.json
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from spec_parser import SpecParser, TestCase

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def uid() -> str:
    return str(uuid.uuid4())


def fake_value_str(schema: dict) -> str:
    """Return a string representation of a fake valid value."""
    fmt = schema.get("format", "")
    typ = schema.get("type", "string")
    if schema.get("enum"):
        return str(schema["enum"][0])
    mapping = {
        "int64": "10", "int32": "5",
        "float": "1.5", "double": "3.14",
        "email": "test@example.com",
        "date-time": "2026-05-22T15:45:00Z",
    }
    if fmt in mapping:
        return mapping[fmt]
    if typ in ("integer", "number"):
        return "1"
    if typ == "boolean":
        return "true"
    return "test_value"


def build_postman_body(schema: dict | None) -> dict | None:
    if not schema:
        return None
    props = schema.get("properties", {})
    body: dict = {}
    for prop, pschema in props.items():
        if pschema.get("type") == "array":
            item = pschema.get("items", {})
            body[prop] = [fake_value_str(item)] if item.get("type") != "object" else []
        else:
            body[prop] = fake_value_str(pschema)
    return body


def build_url(base_url: str, path: str, path_params: list, query_params: list) -> dict:
    """Build a Postman URL object."""
    raw = f"{{{{base_url}}}}{path}"

    # path variables
    path_variables = []
    for p in path_params:
        path_variables.append({
            "key": p["name"],
            "value": fake_value_str(p.get("schema", {})),
        })
        raw = raw.replace(f"{{{p['name']}}}", f":{p['name']}")

    # query params (only on happy_path)
    query = []
    for p in query_params:
        schema = p.get("schema", {})
        val = str(schema.get("default", fake_value_str(schema)))
        query.append({
            "key": p["name"],
            "value": val,
            "disabled": not p.get("required", False),
        })

    parts = path.strip("/").split("/")
    return {
        "raw": raw,
        "host": ["{{base_url}}"],
        "path": parts,
        "variable": path_variables,
        "query": query,
    }


def build_postman_request(tc: TestCase, base_url: str) -> dict:
    """Convert one TestCase into a Postman request object."""
    headers = []

    # Auth header
    if tc.security:
        headers.append({
            "key": "api_key",
            "value": "{{api_key}}",
            "type": "text",
        })

    # Content-Type if body
    if tc.request_body_schema:
        headers.append({
            "key": "Content-Type",
            "value": "application/json",
            "type": "text",
        })

    # Body
    body_obj = build_postman_body(tc.request_body_schema)
    body = None
    if body_obj:
        body = {
            "mode": "raw",
            "raw": json.dumps(body_obj, indent=2),
            "options": {"raw": {"language": "json"}},
        }

    url = build_url(base_url, tc.path, tc.path_params, tc.query_params)

    return {
        "method": tc.method,
        "header": headers,
        "body": body or {},
        "url": url,
    }


def build_postman_tests(tc: TestCase) -> str:
    """Generate Postman test script (pm.test assertions)."""
    expected = tc.expected_status
    lines = [
        f'pm.test("Status code is {expected}", function () {{',
        f'    pm.response.to.have.status({expected});',
        f'}});',
        '',
        'pm.test("Response time < 3000ms", function () {',
        '    pm.expect(pm.response.responseTime).to.be.below(3000);',
        '});',
    ]
    # Schema validation on 2xx
    if tc.scenario == "happy_path" and tc.request_body_schema:
        lines += [
            '',
            'pm.test("Response is valid JSON", function () {',
            '    pm.response.to.be.json;',
            '});',
        ]
    return "\n".join(lines)


def build_item(tc: TestCase, base_url: str) -> dict:
    """Build one Postman collection item."""
    return {
        "id": uid(),
        "name": f"[{tc.scenario}] {tc.method} {tc.path} — {tc.summary}",
        "request": build_postman_request(tc, base_url),
        "response": [],
        "event": [
            {
                "listen": "test",
                "script": {
                    "id": uid(),
                    "type": "text/javascript",
                    "exec": build_postman_tests(tc).split("\n"),
                },
            }
        ],
    }


# ──────────────────────────────────────────────
# Main collection builder
# ──────────────────────────────────────────────

def build_collection(spec_path: str) -> tuple[dict, dict]:
    """Return (collection_dict, environment_dict)."""
    parser = SpecParser(spec_path)
    all_cases = parser.test_cases()

    # Read spec metadata
    info = parser.spec.get("info", {})
    servers = parser.spec.get("servers", [{}])
    base_url = servers[0].get("url", "https://api.example.com")

    # Group items by tag
    folders: dict[str, list] = {}
    for tc in all_cases:
        tag = tc.tag
        if tag not in folders:
            folders[tag] = []
        folders[tag].append(build_item(tc, base_url))

    collection = {
        "info": {
            "_postman_id": uid(),
            "name": f"{info.get('title', 'API')} — Auto-Generated",
            "description": (
                f"Auto-generated from OpenAPI spec by generate_postman.py\n"
                f"Version: {info.get('version','1.0')}\n"
                f"Total requests: {len(all_cases)}"
            ),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "auth": {
            "type": "apikey",
            "apikey": [
                {"key": "key",   "value": "api_key",   "type": "string"},
                {"key": "value", "value": "{{api_key}}", "type": "string"},
                {"key": "in",    "value": "header",     "type": "string"},
            ],
        },
        "item": [
            {
                "id": uid(),
                "name": tag.upper(),
                "item": items,
            }
            for tag, items in sorted(folders.items())
        ],
        "variable": [],
    }

    environment = {
        "id": uid(),
        "name": f"{info.get('title', 'API')} — Environment",
        "values": [
            {"key": "base_url", "value": base_url, "enabled": True, "type": "default"},
            {"key": "api_key",  "value": "special-key", "enabled": True, "type": "secret"},
        ],
    }

    return collection, environment


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def main(spec_path: str, output_dir: str = "output") -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    collection, environment = build_collection(spec_path)

    stem = Path(spec_path).stem
    col_path = out / f"{stem}_collection.json"
    env_path = out / f"{stem}_environment.json"

    col_path.write_text(json.dumps(collection, indent=2))
    env_path.write_text(json.dumps(environment, indent=2))

    total = sum(len(f["item"]) for f in collection["item"])

    print("\n" + "═" * 60)
    print("  POSTMAN GENERATOR — Output Summary")
    print("═" * 60)
    print(f"  Spec            : {spec_path}")
    print(f"  Collection      : {col_path}")
    print(f"  Environment     : {env_path}")
    print(f"  Total requests  : {total}")
    print()
    for folder in collection["item"]:
        print(f"  [{folder['name']}]  {len(folder['item'])} requests")
    print("═" * 60 + "\n")
    print("  Run with Newman:")
    print(f"    newman run {col_path} \\")
    print(f"      --environment {env_path} \\")
    print(f"      --reporters cli,junit \\")
    print(f"      --reporter-junit-export output/newman_results.xml")
    print()


if __name__ == "__main__":
    spec  = sys.argv[1] if len(sys.argv) > 1 else "specs/petstore.yaml"
    outd  = sys.argv[2] if len(sys.argv) > 2 else "output"
    main(spec, outd)
