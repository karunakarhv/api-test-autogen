"""
spec_parser.py
--------------
Heart of the demo: reads an OpenAPI 3.x YAML spec and extracts
every endpoint + method into a TestCase dataclass.

Used by:
  - generate_pytest.py   → writes pytest test files
  - generate_postman.py  → writes a Postman collection JSON
"""

from __future__ import annotations

import yaml
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ──────────────────────────────────────────────
# Data model
# ──────────────────────────────────────────────

@dataclass
class TestCase:
    """One test scenario extracted from the spec."""
    path: str                          # e.g.  /pet/{petId}
    method: str                        # GET | POST | PUT | DELETE …
    operation_id: str                  # e.g.  getPetById
    tag: str                           # e.g.  pet
    summary: str                       # human-readable description
    path_params: list[dict]            # [{name, required, schema}]
    query_params: list[dict]
    header_params: list[dict]
    request_body_schema: dict | None   # JSON Schema of the request body
    responses: dict                    # {status_code: {description, schema}}
    security: list[str]               # ["api_key", "petstore_auth", …]
    required_fields: list[str]         # top-level required fields in body
    scenario: str = "happy_path"       # happy_path | missing_required |
                                       # wrong_type | invalid_enum |
                                       # no_auth | boundary

    @property
    def test_name(self) -> str:
        """
        Generates a human-readable pytest function name.
        e.g.  test_get_pet_petid__happy_path
        """
        path_slug = (
            self.path
            .replace("/", "_")
            .replace("{", "")
            .replace("}", "")
            .strip("_")
        )
        return f"test_{self.method.lower()}_{path_slug}__{self.scenario}"

    @property
    def expected_status(self) -> int:
        """Return the primary expected HTTP status for this scenario."""
        scenario_map = {
            "happy_path":       self._first_success_code(),
            "missing_required": 422,
            "wrong_type":       400,
            "invalid_enum":     400,
            "no_auth":          401,
            "boundary":         self._first_success_code(),
        }
        return scenario_map.get(self.scenario, 200)

    def _first_success_code(self) -> int:
        for code in self.responses:
            try:
                if 200 <= int(code) < 300:
                    return int(code)
            except (ValueError, TypeError):
                pass
        return 200


# ──────────────────────────────────────────────
# Parser
# ──────────────────────────────────────────────

class SpecParser:
    """
    Parses an OpenAPI 3.x spec file and emits TestCase objects.

    Usage:
        parser = SpecParser("specs/petstore.yaml")
        for tc in parser.test_cases():
            print(tc.test_name)
    """

    def __init__(self, spec_path: str | Path):
        self.spec_path = Path(spec_path)
        with open(self.spec_path) as f:
            self.spec: dict = yaml.safe_load(f)
        self._schemas: dict = (
            self.spec.get("components", {}).get("schemas", {})
        )

    # ── public API ────────────────────────────

    def test_cases(self) -> list[TestCase]:
        """Return ALL test cases across every scenario type."""
        base = self._extract_base_cases()
        expanded: list[TestCase] = []
        for tc in base:
            expanded.append(tc)                          # happy_path
            expanded.extend(self._missing_required(tc))  # missing body fields
            expanded.extend(self._wrong_type(tc))        # wrong param type
            expanded.extend(self._invalid_enum(tc))      # bad enum values
            expanded.extend(self._no_auth(tc))           # auth stripped
        return expanded

    def summary(self) -> dict:
        cases = self.test_cases()
        by_scenario: dict[str, int] = {}
        by_tag: dict[str, int] = {}
        for tc in cases:
            by_scenario[tc.scenario] = by_scenario.get(tc.scenario, 0) + 1
            by_tag[tc.tag] = by_tag.get(tc.tag, 0) + 1
        return {
            "total": len(cases),
            "endpoints": len(self._extract_base_cases()),
            "by_scenario": by_scenario,
            "by_tag": by_tag,
        }

    # ── internal extraction ───────────────────

    def _extract_base_cases(self) -> list[TestCase]:
        cases: list[TestCase] = []
        for path, path_item in self.spec.get("paths", {}).items():
            for method, operation in path_item.items():
                if method not in {"get","post","put","patch","delete","options"}:
                    continue
                cases.append(self._build_case(path, method, operation))
        return cases

    def _build_case(self, path: str, method: str, op: dict) -> TestCase:
        params = op.get("parameters", [])
        path_p   = [p for p in params if p.get("in") == "path"]
        query_p  = [p for p in params if p.get("in") == "query"]
        header_p = [p for p in params if p.get("in") == "header"]

        body_schema = self._resolve_body_schema(op)
        required    = body_schema.get("required", []) if body_schema else []

        # collect security scheme names
        sec_schemes = []
        for sec_req in op.get("security", []):
            sec_schemes.extend(sec_req.keys())

        return TestCase(
            path=path,
            method=method.upper(),
            operation_id=op.get("operationId", f"{method}_{path}"),
            tag=(op.get("tags", ["general"])[0]),
            summary=op.get("summary", ""),
            path_params=path_p,
            query_params=query_p,
            header_params=header_p,
            request_body_schema=body_schema,
            responses=op.get("responses", {}),
            security=sec_schemes,
            required_fields=required,
        )

    def _resolve_body_schema(self, op: dict) -> dict | None:
        """Walk requestBody → content → application/json → schema,
        resolving $ref if needed."""
        body = op.get("requestBody", {})
        content = body.get("content", {})
        json_content = content.get("application/json", {})
        schema = json_content.get("schema", None)
        if schema is None:
            return None
        return self._resolve_ref(schema)

    def _resolve_ref(self, schema: dict, _visited: frozenset = frozenset()) -> dict:
        """Recursively dereference all $refs in a schema, including nested ones."""
        if "$ref" in schema:
            ref_path = schema["$ref"]          # e.g. #/components/schemas/Pet
            if ref_path in _visited:
                return {}  # circular ref guard
            name = ref_path.split("/")[-1]
            resolved = self._schemas.get(name, {})
            return self._resolve_ref(resolved, _visited | {ref_path})

        result = dict(schema)
        if "properties" in schema:
            result["properties"] = {
                k: self._resolve_ref(v, _visited)
                for k, v in schema["properties"].items()
            }
        if "items" in schema:
            result["items"] = self._resolve_ref(schema["items"], _visited)
        return result

    # ── scenario generators ───────────────────

    def _missing_required(self, base: TestCase) -> list[TestCase]:
        """One test per required body field — omit that field."""
        out = []
        for field_name in base.required_fields:
            tc = TestCase(**base.__dict__.copy())
            tc.scenario = "missing_required"
            tc.summary  = f"Missing required field: {field_name}"
            out.append(tc)
        return out

    def _wrong_type(self, base: TestCase) -> list[TestCase]:
        """One test sending wrong type for each path/query param."""
        out = []
        for param in base.path_params + base.query_params:
            schema = param.get("schema", {})
            if schema.get("type") in ("integer", "number", "boolean"):
                tc = TestCase(**base.__dict__.copy())
                tc.scenario = "wrong_type"
                tc.summary  = f"Wrong type for param: {param['name']}"
                out.append(tc)
        return out

    def _invalid_enum(self, base: TestCase) -> list[TestCase]:
        """One test sending an out-of-range enum value."""
        out = []
        for param in base.query_params:
            if param.get("schema", {}).get("enum"):
                tc = TestCase(**base.__dict__.copy())
                tc.scenario = "invalid_enum"
                tc.summary  = f"Invalid enum for param: {param['name']}"
                out.append(tc)
        # also check body schema properties
        if base.request_body_schema:
            for prop, pschema in base.request_body_schema.get("properties", {}).items():
                if pschema.get("enum"):
                    tc = TestCase(**base.__dict__.copy())
                    tc.scenario = "invalid_enum"
                    tc.summary  = f"Invalid enum in body field: {prop}"
                    out.append(tc)
        return out

    def _no_auth(self, base: TestCase) -> list[TestCase]:
        """One test per secured endpoint with auth header stripped."""
        if not base.security:
            return []
        tc = TestCase(**base.__dict__.copy())
        tc.scenario = "no_auth"
        tc.summary  = "Request without authentication"
        return [tc]


# ──────────────────────────────────────────────
# CLI preview  (python src/spec_parser.py)
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    spec_file = sys.argv[1] if len(sys.argv) > 1 else "specs/petstore.yaml"
    parser = SpecParser(spec_file)

    print("\n" + "═" * 60)
    print("  SPEC PARSER — Test Case Extraction Summary")
    print("═" * 60)
    s = parser.summary()
    print(f"  Endpoints parsed : {s['endpoints']}")
    print(f"  Test cases total : {s['total']}")
    print()
    print("  By scenario:")
    for scenario, count in sorted(s["by_scenario"].items()):
        print(f"    {scenario:<20} {count:>3}")
    print()
    print("  By tag:")
    for tag, count in sorted(s["by_tag"].items()):
        print(f"    {tag:<20} {count:>3}")
    print("═" * 60 + "\n")

    print("  First 10 test names:")
    for tc in parser.test_cases()[:10]:
        print(f"    {tc.test_name}")
    print()
