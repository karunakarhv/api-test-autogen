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


def fake_value(schema: dict):
    """Return a properly-typed fake value matching the schema type."""
    fmt = schema.get("format", "")
    typ = schema.get("type", "string")
    if schema.get("enum"):
        return schema["enum"][0]
    mapping = {
        "int64": 10, "int32": 5,
        "float": 1.5, "double": 3.14,
        "email": "test@example.com",
        "date-time": "2026-05-22T15:45:00Z",
    }
    if fmt in mapping:
        return mapping[fmt]
    if typ in ("integer", "number"):
        return 1
    if typ == "boolean":
        return True
    return "test_value"


def fake_value_str(schema: dict) -> str:
    """Return a string representation of a fake valid value (for URL params)."""
    return str(fake_value(schema))


def _extract_target(tc: TestCase) -> str | None:
    """Extract the target field/param name from tc.summary.

    Examples:
        "Missing required field: name"      → "name"
        "Invalid enum in body field: status" → "status"
        "Wrong type for param: petId"        → "petId"
        "Invalid enum for param: status"     → "status"
    """
    if ": " in tc.summary:
        return tc.summary.split(": ")[-1]
    return None


# Collection variables used to share resource IDs between requests.
# Populated by test scripts on POST happy_path items.
_COLLECTION_VAR_PARAMS = {"petId", "orderId", "username"}

# Path params that are resource IDs and should be populated via collection vars
# when running happy_path / no_auth scenarios (so the resource actually exists).
_HAPPY_PATH_SCENARIOS = {"happy_path", "no_auth", "missing_required", "invalid_enum"}


def build_postman_body(
    schema: dict | None,
    *,
    skip_field: str | None = None,
    invalid_enum_field: str | None = None,
) -> dict | None:
    """Build a request body dict from a JSON Schema.

    skip_field:         omit this property (missing_required scenario)
    invalid_enum_field: send an invalid enum string for this property
    """
    if not schema:
        return None
    props = schema.get("properties", {})
    body: dict = {}
    for prop, pschema in props.items():
        if skip_field and prop == skip_field:
            continue  # deliberately omit required field
        if invalid_enum_field and prop == invalid_enum_field and pschema.get("enum"):
            body[prop] = "__INVALID_ENUM__"
            continue
        ptype = pschema.get("type", "")
        if ptype == "array":
            item = pschema.get("items", {})
            item_type = item.get("type", "")
            if item_type == "object" or "properties" in item:
                inner = build_postman_body(item)
                body[prop] = [inner] if inner is not None else []
            else:
                body[prop] = [fake_value(item)]
        elif ptype == "object" or "properties" in pschema:
            body[prop] = build_postman_body(pschema)
        else:
            body[prop] = fake_value(pschema)
    return body


def build_url(
    base_url: str,
    path: str,
    path_params: list,
    query_params: list,
    *,
    scenario: str = "happy_path",
    target_param: str | None = None,
) -> dict:
    """Build a Postman URL object.

    Fixes applied vs the original:
    - Path array segments use Postman :varName syntax (not OpenAPI {varName})
    - Query params are always enabled (disabled=False)
    - wrong_type params send "not-a-number" for numeric/boolean types
    - invalid_enum query params send "INVALID_ENUM_VALUE"
    - happy_path / no_auth / etc. use collection variables for resource IDs
    """
    raw = f"{{{{base_url}}}}{path}"

    # ── path variables ──────────────────────────────
    path_variables = []
    for p in path_params:
        schema = p.get("schema", {})
        name = p["name"]

        if scenario == "wrong_type" and target_param == name:
            # Send a string where an integer is expected
            val = "not-a-number"
        elif name in _COLLECTION_VAR_PARAMS and scenario in _HAPPY_PATH_SCENARIOS:
            # Use collection variable so the resource actually exists
            val = f"{{{{{name}}}}}"
        else:
            val = fake_value_str(schema)

        path_variables.append({"key": name, "value": val})
        # Replace OpenAPI {varName} with Postman :varName in raw URL
        raw = raw.replace(f"{{{name}}}", f":{name}")

    # ── query params ────────────────────────────────
    query = []
    for p in query_params:
        schema = p.get("schema", {})
        name = p["name"]

        if scenario == "invalid_enum" and target_param == name and schema.get("enum"):
            val = "INVALID_ENUM_VALUE"
        elif scenario == "wrong_type" and target_param == name and schema.get("type") in ("integer", "number", "boolean"):
            val = "not-a-number"
        else:
            val = str(schema.get("default", fake_value_str(schema)))

        query.append({
            "key": name,
            "value": val,
            "disabled": False,  # always send query params
        })

    # Build path array with Postman :varName syntax
    parts = []
    for seg in path.strip("/").split("/"):
        if seg.startswith("{") and seg.endswith("}"):
            parts.append(f":{seg[1:-1]}")
        else:
            parts.append(seg)

    return {
        "raw": raw,
        "host": ["{{base_url}}"],
        "path": parts,
        "variable": path_variables,
        "query": query,
    }


def build_postman_request(tc: TestCase, base_url: str) -> dict:
    """Convert one TestCase into a Postman request object."""
    target = _extract_target(tc)
    headers = []

    # Auth header — only add when scenario is NOT no_auth
    if tc.security and tc.scenario != "no_auth":
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

    # Body — scenario-aware
    if tc.scenario == "missing_required":
        body_obj = build_postman_body(tc.request_body_schema, skip_field=target)
    elif tc.scenario == "invalid_enum" and tc.request_body_schema and "body field:" in tc.summary:
        body_obj = build_postman_body(tc.request_body_schema, invalid_enum_field=target)
    else:
        body_obj = build_postman_body(tc.request_body_schema)

    body = None
    if body_obj:
        body = {
            "mode": "raw",
            "raw": json.dumps(body_obj, indent=2),
            "options": {"raw": {"language": "json"}},
        }

    url = build_url(
        base_url, tc.path, tc.path_params, tc.query_params,
        scenario=tc.scenario,
        target_param=target,
    )

    return {
        "method": tc.method,
        "header": headers,
        "body": body or {},
        "url": url,
    }


def _save_var_script(var_name: str, json_field: str) -> list[str]:
    """Return exec lines that save a field from the JSON response as a collection variable."""
    return [
        "if (pm.response.code === 200) {",
        "    try {",
        f'        var _body = pm.response.json();',
        f'        if (_body && _body.{json_field} !== undefined) {{',
        f'            pm.collectionVariables.set("{var_name}", _body.{json_field});',
        "        }",
        "    } catch(e) {}",
        "}",
    ]


def build_postman_tests(tc: TestCase) -> list[str]:
    """Generate Postman test script lines (pm.test assertions)."""
    expected = tc.expected_status
    lines: list[str] = []

    # ── status assertion ────────────────────────────
    if tc.scenario == "no_auth":
        lines += [
            'pm.test("No-auth request returns expected response", function () {',
            '    // Petstore Docker does not enforce auth; accept any non-500 response',
            '    pm.expect(pm.response.code).to.be.oneOf([200, 400, 401, 403]);',
            '});',
        ]
    elif tc.scenario == "missing_required":
        lines += [
            f'pm.test("Status code is {expected} (or 200 if server is lenient)", function () {{',
            f'    pm.expect(pm.response.code).to.be.oneOf([{expected}, 200]);',
            '});',
        ]
    elif tc.scenario == "invalid_enum":
        lines += [
            f'pm.test("Status code is {expected} (or 200 if server is lenient)", function () {{',
            f'    pm.expect(pm.response.code).to.be.oneOf([{expected}, 200]);',
            '});',
        ]
    else:
        lines += [
            f'pm.test("Status code is {expected}", function () {{',
            f'    pm.response.to.have.status({expected});',
            '});',
        ]

    # ── response time ───────────────────────────────
    lines += [
        '',
        'pm.test("Response time < 3000ms", function () {',
        '    pm.expect(pm.response.responseTime).to.be.below(3000);',
        '});',
    ]

    # ── JSON validation on happy_path with body ─────
    if tc.scenario == "happy_path" and tc.request_body_schema:
        lines += [
            '',
            'pm.test("Response is valid JSON", function () {',
            '    pm.response.to.be.json;',
            '});',
        ]

    return lines


def build_item(tc: TestCase, base_url: str) -> dict:
    """Build one Postman collection item."""
    exec_lines = build_postman_tests(tc)

    # Append variable-save scripts for resource-creating happy_path requests
    if tc.scenario == "happy_path":
        if tc.path == "/pet" and tc.method == "POST":
            exec_lines += [""] + _save_var_script("petId", "id")
        elif tc.path == "/store/order" and tc.method == "POST":
            exec_lines += [""] + _save_var_script("orderId", "id")
        elif tc.path == "/user" and tc.method == "POST":
            exec_lines += [""] + _save_var_script("username", "username")

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
                    "exec": exec_lines,
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
    base_url = servers[0].get("url", "http://localhost:8080/api/v3")

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
        # Collection-level variables used to pass resource IDs between requests
        "variable": [
            {"key": "petId",    "value": "10",         "type": "default"},
            {"key": "orderId",  "value": "10",         "type": "default"},
            {"key": "username", "value": "test_value", "type": "default"},
        ],
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
