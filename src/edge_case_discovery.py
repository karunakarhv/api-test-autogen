"""
edge_case_discovery.py
----------------------
Demonstrates how to feed an OpenAPI endpoint to an LLM and get back
boundary conditions a developer would miss.

This is the "Edge Cases" section of the LLM enrichment layer.

Usage:
    python src/edge_case_discovery.py
    python src/edge_case_discovery.py specs/petstore.yaml POST /pet
    python src/edge_case_discovery.py specs/petstore.yaml GET /pet/{petId}

Output:
    - Prints discovered edge cases to terminal
    - Writes output/edge_cases_<operationId>.json
    - Writes tests/generated/test_edge_<operationId>.py (runnable pytest file)
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from spec_parser import SpecParser, TestCase

import anthropic


# ─────────────────────────────────────────────────────────────────
# The prompt — this is the core of the technique
# ─────────────────────────────────────────────────────────────────

EDGE_CASE_PROMPT = """\
You are a senior QA automation engineer with deep experience in API security and reliability testing.

I have an API endpoint I need comprehensive edge case tests for.

## Endpoint Details
Method  : {method}
Path    : {path}
Summary : {summary}
Tag     : {tag}

## Path Parameters
{path_params}

## Query Parameters
{query_params}

## Request Body Schema
{body_schema}

## Documented Response Codes
{responses}

## Security Requirements
{security}

---

Your job: identify edge cases and boundary conditions that a developer writing happy-path tests would miss.

Think across these categories:
1. **Numeric boundaries** — min/max values, zero, negative, overflow (int64 max)
2. **String edge cases** — empty string, whitespace-only, max length + 1, special chars, unicode, SQL injection, XSS payload, null bytes
3. **Missing vs null** — field absent from JSON entirely vs field present with null value (these are different!)
4. **Array edge cases** — empty array, single item, very large array, duplicate items
5. **Type coercion** — string "1" where int expected, boolean as string "true", float where int expected
6. **Auth edge cases** — expired token, malformed token, token for wrong scope, no auth header vs empty auth header
7. **Concurrent/ordering** — what if this endpoint is called twice simultaneously with the same data?
8. **Business logic gaps** — conditions the spec doesn't document but the API should handle

Return a JSON array. Each object must have exactly these keys:
- "name"         : snake_case test function name, starting with test_, under 80 chars, specific about what breaks and why
- "category"     : one of [numeric_boundary, string_edge, missing_vs_null, array_edge, type_coercion, auth_edge, concurrent, business_logic]
- "description"  : one sentence — what this test checks and why it matters
- "input"        : the specific value or condition to send (be concrete — actual values, not "a very long string")
- "expected"     : what the API should return (status code + brief reason)
- "severity"     : high | medium | low

Return ONLY a valid JSON array. No markdown, no explanation, no preamble.
"""


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def format_params(params: list[dict]) -> str:
    if not params:
        return "None"
    lines = []
    for p in params:
        schema = p.get("schema", {})
        constraints = []
        if "minimum" in schema: constraints.append(f"min={schema['minimum']}")
        if "maximum" in schema: constraints.append(f"max={schema['maximum']}")
        if "minLength" in schema: constraints.append(f"minLength={schema['minLength']}")
        if "maxLength" in schema: constraints.append(f"maxLength={schema['maxLength']}")
        if "enum" in schema: constraints.append(f"enum={schema['enum']}")
        if "format" in schema: constraints.append(f"format={schema['format']}")
        c = f" [{', '.join(constraints)}]" if constraints else ""
        req = " (required)" if p.get("required") else " (optional)"
        lines.append(f"  - {p['name']}: {schema.get('type','any')}{c}{req}")
    return "\n".join(lines)


def format_schema(schema: dict | None) -> str:
    if not schema:
        return "No request body"
    return json.dumps(schema, indent=2)


def format_responses(responses: dict) -> str:
    lines = []
    for code, resp in responses.items():
        lines.append(f"  {code}: {resp.get('description', '')}")
    return "\n".join(lines) or "None documented"


def call_llm(prompt: str) -> list[dict]:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    # strip markdown fences if model adds them
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(raw)


# ─────────────────────────────────────────────────────────────────
# Pytest generator for edge cases
# ─────────────────────────────────────────────────────────────────

SEVERITY_MARK = {
    "high":   "@pytest.mark.high_severity",
    "medium": "@pytest.mark.medium_severity",
    "low":    "@pytest.mark.low_severity",
}

CATEGORY_COMMENTS = {
    "numeric_boundary": "Numeric boundary — tests min/max/overflow",
    "string_edge":      "String edge case — special chars, injection, length",
    "missing_vs_null":  "Missing vs null — absent field vs explicit null",
    "array_edge":       "Array edge case — empty/large/duplicate",
    "type_coercion":    "Type coercion — wrong type that might be accepted",
    "auth_edge":        "Auth edge case — malformed/missing/wrong-scope token",
    "concurrent":       "Concurrent — race condition or duplicate request",
    "business_logic":   "Business logic — undocumented behaviour",
}


def render_pytest_file(tc: TestCase, edge_cases: list[dict]) -> str:
    lines = [
        f'"""',
        f'test_edge_{tc.operation_id}.py',
        f'LLM-generated edge case tests for {tc.method} {tc.path}',
        f'Auto-generated by edge_case_discovery.py — review before running in production',
        f'"""',
        f'import pytest',
        f'import requests',
        f'',
        f'',
        f'BASE_URL = "https://petstore3.swagger.io/api/v3"',
        f'AUTH_HEADERS = {{"api_key": "special-key"}}',
        f'',
        f'',
    ]

    for ec in edge_cases:
        name     = ec.get("name", "test_unnamed")
        category = ec.get("category", "unknown")
        desc     = ec.get("description", "")
        inp      = ec.get("input", "")
        expected = ec.get("expected", "")
        severity = ec.get("severity", "medium")

        # try to extract expected status code
        try:
            exp_status = int(str(expected).split()[0])
        except (ValueError, IndexError):
            exp_status = 400

        comment = CATEGORY_COMMENTS.get(category, category)
        mark = SEVERITY_MARK.get(severity, "")

        lines += [
            f"# {comment}",
            f"{mark}" if mark else "",
            f"def {name}():",
            f'    """',
            f'    {desc}',
            f'    Input    : {inp}',
            f'    Expected : {expected}',
            f'    Severity : {severity}',
            f'    """',
            f"    url = f\"{BASE_URL}{tc.path.replace('{', '{{'+ '').replace('}', '}}')}\"",
        ]

        # Build a payload hint based on category
        if category == "missing_vs_null":
            lines += [
                f"    # Test: field explicitly set to null",
                f'    payload = {{"name": None, "photoUrls": ["https://example.com/photo.jpg"]}}',
            ]
        elif category == "string_edge":
            lines += [
                f"    # Test: adversarial string input",
                f"    payload = {{",
                f'        "name": {json.dumps(str(inp)[:80])},',
                f'        "photoUrls": ["https://example.com/photo.jpg"]',
                f"    }}",
            ]
        elif category == "numeric_boundary":
            lines += [
                f"    # Test: boundary numeric value",
                f"    payload = {{",
                f'        "name": "test_pet",',
                f'        "photoUrls": ["https://example.com/photo.jpg"],',
                f'        "id": {json.dumps(inp) if isinstance(inp, (int, float)) else 9999999999999}',
                f"    }}",
            ]
        elif category == "auth_edge":
            lines += [
                f"    # Test: auth edge case — modified headers",
                f'    payload = {{"name": "test_pet", "photoUrls": ["https://example.com/photo.jpg"]}}',
            ]
        else:
            lines += [
                f"    payload = {{",
                f'        "name": "test_pet",',
                f'        "photoUrls": ["https://example.com/photo.jpg"]',
                f"    }}",
            ]

        # Auth for the request
        if category == "auth_edge":
            lines.append(f'    headers = {{"api_key": "invalid-or-expired-token"}}')
        else:
            lines.append(f"    headers = AUTH_HEADERS")

        lines += [
            f"    response = requests.post(url, json=payload, headers=headers)",
            f"",
            f"    assert response.status_code == {exp_status}, (",
            f'        f"Expected {exp_status} for edge case: {desc[:60]!r}\\n"',
            f'        f"Got: {{response.status_code}} — {{response.text[:200]}}"',
            f"    )",
            f"",
            f"",
        ]

    # remove blank mark lines
    return "\n".join(l for l in lines if l != "")


# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────

def discover(spec_path: str, method: str = "POST", path: str = "/pet") -> None:
    # ── 1. Load spec and find endpoint
    parser = SpecParser(spec_path)
    cases  = parser.test_cases()
    tc = next(
        (c for c in cases if c.method == method.upper() and c.path == path),
        None
    )
    if tc is None:
        print(f"\n  ✗ Endpoint {method.upper()} {path} not found in spec.")
        print(f"    Available: {[f'{c.method} {c.path}' for c in cases[:8]]}")
        return

    # ── 2. Build the prompt
    prompt = EDGE_CASE_PROMPT.format(
        method      = tc.method,
        path        = tc.path,
        summary     = tc.summary,
        tag         = tc.tag,
        path_params = format_params(tc.path_params),
        query_params= format_params(tc.query_params),
        body_schema = format_schema(tc.request_body_schema),
        responses   = format_responses(tc.responses),
        security    = ", ".join(tc.security) or "None",
    )

    # ── 3. Print what we're sending
    print("\n" + "═" * 64)
    print("  EDGE CASE DISCOVERY")
    print("═" * 64)
    print(f"  Endpoint : {tc.method} {tc.path}")
    print(f"  Summary  : {tc.summary}")
    print(f"\n  Prompt sent to Claude:")
    print("  ┌" + "─" * 60 + "┐")
    for line in prompt.strip().split("\n")[:20]:
        print(f"  │  {line:<58}│")
    print(f"  │  ... ({len(prompt.split(chr(10)))} lines total){' '*(44 - len(str(len(prompt.split(chr(10))))))}│")
    print("  └" + "─" * 60 + "┘")
    print(f"\n  Calling Claude ({os.environ.get('ANTHROPIC_MODEL','claude-sonnet-4-20250514')})...")

    # ── 4. Call the LLM
    edge_cases = call_llm(prompt)

    # ── 5. Print results
    print(f"\n  ✓ {len(edge_cases)} edge cases discovered\n")

    SEV_COLOUR = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    CAT_WIDTH = max(len(ec.get("category","")) for ec in edge_cases)

    for i, ec in enumerate(edge_cases, 1):
        sev  = ec.get("severity", "medium")
        cat  = ec.get("category", "").ljust(CAT_WIDTH)
        name = ec.get("name", "unnamed")
        desc = ec.get("description", "")
        inp  = str(ec.get("input", ""))
        exp  = str(ec.get("expected", ""))

        print(f"  {i:02d}. {SEV_COLOUR.get(sev,'○')} [{cat}]")
        print(f"       {name}")
        print(f"       → {desc}")
        print(f"       Input    : {inp[:80]}")
        print(f"       Expected : {exp}")
        print()

    # ── 6. Save JSON
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    json_path = out_dir / f"edge_cases_{tc.operation_id}.json"
    json_path.write_text(json.dumps(edge_cases, indent=2))
    print(f"  JSON saved  → {json_path}")

    # ── 7. Generate pytest file
    test_dir = Path("tests/generated")
    test_dir.mkdir(parents=True, exist_ok=True)
    test_path = test_dir / f"test_edge_{tc.operation_id}.py"
    test_path.write_text(render_pytest_file(tc, edge_cases))
    print(f"  pytest file → {test_path}")

    # ── Summary by category
    from collections import Counter
    cats = Counter(ec.get("category","") for ec in edge_cases)
    sevs = Counter(ec.get("severity","") for ec in edge_cases)
    print(f"\n  By category:")
    for cat, n in sorted(cats.items()):
        print(f"    {cat:<22} {n}")
    print(f"\n  By severity:")
    print(f"    🔴 high   {sevs.get('high',0)}")
    print(f"    🟡 medium {sevs.get('medium',0)}")
    print(f"    🟢 low    {sevs.get('low',0)}")
    print("═" * 64 + "\n")


if __name__ == "__main__":
    spec   = sys.argv[1] if len(sys.argv) > 1 else "specs/petstore.yaml"
    method = sys.argv[2] if len(sys.argv) > 2 else "POST"
    path   = sys.argv[3] if len(sys.argv) > 3 else "/pet"

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("\n  ✗  ANTHROPIC_API_KEY not set.")
        print("     export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    discover(spec, method, path)
