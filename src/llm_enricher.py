"""
llm_enricher.py
---------------
Optional layer: uses an LLM to enrich the generated test suite with:

  1. Smart test data    — realistic values based on field names/formats
  2. Edge case ideas    — boundary conditions the spec doesn't capture
  3. Human-readable names — rewrites ugly auto-gen test names

This is the "Where LLMs Fit In" slide made real.

Usage:
    python src/llm_enricher.py specs/petstore.yaml

Requires:
    pip install anthropic

Set env var:
    export ANTHROPIC_API_KEY=sk-ant-...
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from anthropic import Anthropic

sys.path.insert(0, str(Path(__file__).parent))
from spec_parser import SpecParser, TestCase

try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False


# ──────────────────────────────────────────────
# LLM prompt templates
# ──────────────────────────────────────────────

SMART_DATA_PROMPT = """\
You are a QA engineer generating realistic test data for an API.

Given this JSON Schema for a request body:
{schema}

Generate a JSON object with realistic, domain-appropriate values.
Rules:
- Use plausible real-world values (not "string", "test", or "foo")
- Respect types, formats, min/max constraints
- For name fields: use real-sounding names
- For email fields: use realistic email addresses
- For enum fields: pick a valid enum value
- For date-time: use ISO 8601 format

Return ONLY valid JSON. No explanation, no markdown.
"""

EDGE_CASES_PROMPT = """\
You are a senior QA automation engineer.

Given this API endpoint:
  Method : {method}
  Path   : {path}
  Summary: {summary}
  Params : {params}
  Body schema: {body_schema}

List 5 edge cases / boundary conditions that a happy-path test would miss.
Focus on: numeric boundaries, string length limits, special characters,
null vs missing fields, concurrent requests, and auth edge cases.

Return a JSON array of objects with keys:
  "name"       : short test name (snake_case, under 60 chars)
  "description": one sentence explanation
  "input_hint" : what value or condition to test

Return ONLY valid JSON array. No explanation, no markdown.
"""

RENAME_PROMPT = """\
You are a QA engineer. Rename these auto-generated test function names
to be clear, human-readable pytest function names.

Rules:
- Start with test_
- Use snake_case
- Be specific: include the resource, action, and expected outcome
- Under 80 characters
- No generic words like "valid", "invalid" alone — be specific

Auto-generated names:
{names}

Return a JSON object mapping old_name -> new_name.
Return ONLY valid JSON. No explanation, no markdown.
"""


# ──────────────────────────────────────────────
# Enricher class
# ──────────────────────────────────────────────

class LLMEnricher:
    """
    Uses Claude to enrich OpenAPI-derived test cases with
    smart data, edge cases, and readable names.
    """

    def __init__(self, model: str = "claude-haiku-4-5"):
        if not _ANTHROPIC_AVAILABLE:
            raise ImportError(
                "anthropic package not installed.\n"
                "Run: pip install anthropic"
            )
        self.client = Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY")
        )
        self.model = model

    def _call(self, prompt: str) -> str:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()

    def _parse_json(self, text: str) -> dict | list:
        # strip markdown fences if present
        clean = text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean)

    # ── Public methods ─────────────────────────

    def smart_test_data(self, schema: dict) -> dict:
        """Return a dict of realistic field values for a body schema."""
        if not schema or not schema.get("properties"):
            return {}
        prompt = SMART_DATA_PROMPT.format(
            schema=json.dumps(schema, indent=2)
        )
        try:
            raw = self._call(prompt)
            return self._parse_json(raw)
        except Exception as e:
            print(f"  [LLM] smart_test_data failed: {e}")
            return {}

    def edge_cases(self, tc: TestCase) -> list[dict]:
        """Return a list of edge case dicts for a TestCase."""
        prompt = EDGE_CASES_PROMPT.format(
            method=tc.method,
            path=tc.path,
            summary=tc.summary,
            params=json.dumps(
                tc.path_params + tc.query_params, indent=2
            ),
            body_schema=json.dumps(
                tc.request_body_schema or {}, indent=2
            ),
        )
        try:
            raw = self._call(prompt)
            return self._parse_json(raw)
        except Exception as e:
            print(f"  [LLM] edge_cases failed: {e}")
            return []

    def rename_tests(self, names: list[str]) -> dict[str, str]:
        """Return a mapping of old name → improved name."""
        if not names:
            return {}
        prompt = RENAME_PROMPT.format(names=json.dumps(names, indent=2))
        try:
            raw = self._call(prompt)
            return self._parse_json(raw)
        except Exception as e:
            print(f"  [LLM] rename_tests failed: {e}")
            return {}


# ──────────────────────────────────────────────
# File-generation helpers
# ──────────────────────────────────────────────

_CONFTEST = '''\
"""
conftest.py  —  shared pytest fixtures
Auto-generated by llm_enricher.py
"""
import pytest
import os

BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8080/api/v3/")
API_KEY  = os.environ.get("API_KEY", "special-key")


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def auth_headers():
    return {"api_key": API_KEY}
'''


def _safe_name(name: str) -> str:
    """Return a valid pytest function name ≤ 80 chars."""
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name).strip("_")
    if not name.startswith("test_"):
        name = f"test_{name}"
    return name[:80]


def _build_path(path: str, path_params: list[dict]) -> str:
    """Substitute {param} placeholders with simple test values."""
    result = path
    for p in path_params:
        typ = p.get("schema", {}).get("type", "string")
        val = "10" if typ in ("integer", "number") else "test_value"
        result = result.replace(f"{{{p['name']}}}", val)
    return result


def _render_smart_data_test(tc: TestCase, smart_data: dict) -> str:
    """Pytest function for a happy-path test using LLM-generated realistic data."""
    name = _safe_name(f"test_{tc.method.lower()}_{tc.tag}_with_realistic_data")
    url_path = _build_path(tc.path, tc.path_params)
    method = tc.method.lower()
    expected = tc.expected_status

    lines = [
        f"def {name}(base_url, auth_headers):",
        f'    """',
        f'    [{tc.tag.upper()}] {tc.summary}',
        f'    Scenario : happy_path — LLM-generated realistic data',
        f'    Expected : HTTP {expected}',
        f'    """',
        f'    url = f"{{base_url}}{url_path}"',
    ]

    if smart_data:
        payload_json = json.dumps(smart_data)
        lines.append(f"    payload = json.loads('{payload_json}')")

    call_args = ["url", "headers=auth_headers"]
    if smart_data:
        call_args.append("json=payload")

    lines += [
        f"    response = requests.{method}(",
        *[f"        {a}{',' if i < len(call_args) - 1 else ''}"
          for i, a in enumerate(call_args)],
        f"    )",
        f"",
        f"    assert response.status_code == {expected}, (",
        f'        f"Expected {expected}, got {{response.status_code}}\\n"',
        f'        f"Body: {{response.text[:200]}}"',
        f"    )",
    ]

    indent = "    "
    return "\n".join(indent + l if i > 0 else l for i, l in enumerate(lines))


def _render_edge_case_test(tc: TestCase, edge: dict, base_payload: dict) -> str:
    """Pytest function skeleton for an LLM-discovered edge case."""
    name = _safe_name(edge.get("name", f"test_edge_{tc.method.lower()}_{tc.tag}"))
    url_path = _build_path(tc.path, tc.path_params)
    method = tc.method.lower()
    description = edge.get("description", "")
    input_hint = edge.get("input_hint", "")

    lines = [
        f"def {name}(base_url, auth_headers):",
        f'    """',
        f'    [{tc.tag.upper()}] {tc.summary}',
        f'    Edge case : {description}',
        f'    Input hint: {input_hint}',
        f'    """',
        f"    # TODO: adjust payload/params per input hint above",
        f'    url = f"{{base_url}}{url_path}"',
    ]

    if base_payload:
        payload_json = json.dumps(base_payload)
        lines.append(f"    payload = json.loads('{payload_json}')")

    call_args = ["url", "headers=auth_headers"]
    if base_payload:
        call_args.append("json=payload")

    lines += [
        f"    response = requests.{method}(",
        *[f"        {a}{',' if i < len(call_args) - 1 else ''}"
          for i, a in enumerate(call_args)],
        f"    )",
        f"",
        f"    assert response.status_code in [400, 404, 422], (",
        f'        f"Expected a validation error, got {{response.status_code}}\\n"',
        f'        f"Body: {{response.text[:200]}}"',
        f"    )",
    ]

    indent = "    "
    return "\n".join(indent + l if i > 0 else l for i, l in enumerate(lines))


# ──────────────────────────────────────────────
# File generator
# ──────────────────────────────────────────────

def generate_to_files(spec_path: str, output_dir: str = "tests/llm_generated") -> None:
    """
    Run all three enrichment features against every endpoint in the spec
    and write pytest test files to *output_dir*.

    For each API tag the following files are written:
      tests/llm_generated/
        conftest.py
        __init__.py
        test_llm_<tag>.py   ← smart-data happy-path + edge-case tests

    Raw edge-case JSON is also saved to output/edge_cases_<tag>.json.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    parser = SpecParser(spec_path)
    # Only one test case per endpoint (happy_path) to avoid duplicating LLM calls
    happy_cases = [tc for tc in parser.test_cases() if tc.scenario == "happy_path"]

    enricher = LLMEnricher()

    # Collect (tc, smart_data, edges) grouped by tag
    by_tag: dict[str, list[tuple]] = defaultdict(list)
    for tc in happy_cases:
        print(f"  → {tc.method} {tc.path} …", flush=True)
        smart_data = enricher.smart_test_data(tc.request_body_schema or {})
        edges = enricher.edge_cases(tc)
        by_tag[tc.tag].append((tc, smart_data, edges))

    # Write shared fixtures
    (out / "conftest.py").write_text(_CONFTEST)
    (out / "__init__.py").write_text("")

    json_out = Path("output")
    json_out.mkdir(exist_ok=True)

    total_tests = 0
    for tag, items in sorted(by_tag.items()):
        functions: list[str] = []
        edge_cases_json: list[dict] = []

        for tc, smart_data, edges in items:
            if smart_data:
                functions.append(_render_smart_data_test(tc, smart_data))
                functions.append("")
                total_tests += 1

            for edge in edges:
                functions.append(_render_edge_case_test(tc, edge, smart_data))
                functions.append("")
                total_tests += 1
                edge_cases_json.append({"endpoint": f"{tc.method} {tc.path}", **edge})

        header = (
            f'"""\n'
            f"test_llm_{tag}.py\n"
            f"Auto-generated by llm_enricher.py\n"
            f"Source spec : {spec_path}\n"
            f"Tag         : {tag}\n"
            f"Tests       : {total_tests}\n\n"
            f"DO NOT EDIT MANUALLY — re-run llm_enricher.py to regenerate.\n"
            f'"""\n'
            f"import json\n"
            f"import requests\n"
            f"import pytest\n\n\n"
        )
        (out / f"test_llm_{tag}.py").write_text(header + "\n".join(functions))

        (json_out / f"edge_cases_{tag}.json").write_text(
            json.dumps(edge_cases_json, indent=2)
        )

    print("\n" + "═" * 60)
    print("  LLM ENRICHER — Files Written")
    print("═" * 60)
    print(f"  Spec        : {spec_path}")
    print(f"  Output dir  : {output_dir}/")
    print(f"  Total tests : {total_tests}")
    print()
    for tag, items in sorted(by_tag.items()):
        n = sum(
            (1 if sd else 0) + len(edges)
            for _, sd, edges in items
        )
        print(f"  test_llm_{tag}.py  →  {n} tests")
    print()
    print("  Edge-case JSON saved to output/edge_cases_<tag>.json")
    print()
    print("  Run with:")
    print(f"    pytest {output_dir}/ -v")
    print("═" * 60 + "\n")


# ──────────────────────────────────────────────
# Demo runner
# ──────────────────────────────────────────────

def demo(spec_path: str) -> None:
    """
    Showcase all three LLM enrichment features against
    the first POST endpoint in the spec.
    """
    parser = SpecParser(spec_path)
    all_cases = parser.test_cases()

    # Find a POST with a body
    target = next(
        (tc for tc in all_cases
         if tc.method == "POST" and tc.request_body_schema),
        all_cases[0]
    )

    print("\n" + "═" * 60)
    print("  LLM ENRICHER — Demo Output")
    print("═" * 60)
    print(f"  Target: {target.method} {target.path}")
    print()

    enricher = LLMEnricher()

    # ── 1. Smart test data
    print("  [1] Smart Test Data")
    print("      Asking LLM for realistic field values...")
    smart_data = enricher.smart_test_data(target.request_body_schema or {})
    print(json.dumps(smart_data, indent=6))
    print()

    # ── 2. Edge cases
    print("  [2] Edge Cases")
    print("      Asking LLM for boundary conditions...")
    edges = enricher.edge_cases(target)
    for i, e in enumerate(edges, 1):
        print(f"      {i}. {e.get('name')}")
        print(f"         → {e.get('description')}")
        print(f"         Input hint: {e.get('input_hint')}")
    print()

    # ── 3. Rename test functions
    print("  [3] Human-Readable Test Names")
    ugly_names = [tc.test_name for tc in all_cases[:6]]
    print("      Auto-generated:")
    for n in ugly_names:
        print(f"        {n}")
    rename_map = enricher.rename_tests(ugly_names)
    print("      After LLM rename:")
    for old, new in rename_map.items():
        print(f"        {old}")
        print(f"        → {new}")
        print()

    print("═" * 60 + "\n")


if __name__ == "__main__":
    spec   = sys.argv[1] if len(sys.argv) > 1 else "specs/petstore.yaml"
    outdir = sys.argv[2] if len(sys.argv) > 2 else "tests/llm_generated"

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("\n  ⚠  ANTHROPIC_API_KEY not set.")
        print("     Set it to run live LLM enrichment:")
        print("     export ANTHROPIC_API_KEY=sk-ant-...\n")
        print("  Running in DRY-RUN mode (no API calls).\n")

        # Dry-run: show what the prompts would look like
        parser = SpecParser(spec)
        cases = parser.test_cases()
        target = next(
            (tc for tc in cases if tc.method == "POST" and tc.request_body_schema),
            cases[0]
        )
        print("  Would send this to the LLM for smart test data:")
        print()
        prompt = SMART_DATA_PROMPT.format(
            schema=json.dumps(target.request_body_schema or {}, indent=2)
        )
        for line in prompt.strip().split("\n"):
            print(f"    {line}")
        print()
        print("  Would send this for edge cases:")
        print()
        prompt2 = EDGE_CASES_PROMPT.format(
            method=target.method,
            path=target.path,
            summary=target.summary,
            params=json.dumps(target.path_params + target.query_params),
            body_schema=json.dumps(target.request_body_schema or {}, indent=2),
        )
        for line in prompt2.strip().split("\n"):
            print(f"    {line}")
        print(f"\n  Output would be written to: {outdir}/")
        sys.exit(0)

    generate_to_files(spec, outdir)
