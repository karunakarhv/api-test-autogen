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
import sys
from pathlib import Path

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

    def __init__(self, model: str = "claude-sonnet-4-20250514"):
        if not _ANTHROPIC_AVAILABLE:
            raise ImportError(
                "anthropic package not installed.\n"
                "Run: pip install anthropic"
            )
        self.client = anthropic.Anthropic(
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
    spec = sys.argv[1] if len(sys.argv) > 1 else "specs/petstore.yaml"

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("\n  ⚠  ANTHROPIC_API_KEY not set.")
        print("     Set it to run live LLM enrichment:")
        print("     export ANTHROPIC_API_KEY=sk-ant-...\n")
        print("  Running in DRY-RUN mode (no API calls).\n")

        # Dry-run: show what the prompts look like
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
        sys.exit(0)

    demo(spec)
