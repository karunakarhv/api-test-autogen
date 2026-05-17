"""
run_all.py  —  LIVE DEMO SCRIPT
================================
This is what you run on stage. It:

  Step 1  Parse the spec  →  show the test matrix summary
  Step 2  Generate pytest suite
  Step 3  Generate Postman collection
  Step 4  Show LLM enrichment (dry-run if no API key, live if set)

Usage:
    python run_all.py                         # uses petstore.yaml
    python run_all.py specs/petstore.yaml     # explicit spec path

Stage tip: run with  python -u run_all.py  to see output as it prints.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

# ──────────────────────────────────────────────────────────────────
# Pretty print helpers
# ──────────────────────────────────────────────────────────────────

def banner(title: str) -> None:
    width = 62
    print()
    print("╔" + "═" * width + "╗")
    pad = (width - len(title)) // 2
    print("║" + " " * pad + title + " " * (width - pad - len(title)) + "║")
    print("╚" + "═" * width + "╝")


def step(n: int, title: str) -> None:
    print(f"\n  ── STEP {n}: {title} {'─' * (45 - len(title))}")


def done(msg: str = "") -> None:
    print(f"  ✓  {msg}")


def pause(seconds: float = 0.6) -> None:
    """Small pause for demo effect."""
    time.sleep(seconds)


# ──────────────────────────────────────────────────────────────────
# Main demo
# ──────────────────────────────────────────────────────────────────

def main():
    spec_path = sys.argv[1] if len(sys.argv) > 1 else "specs/petstore.yaml"

    banner("API TEST AUTO-GENERATION — LIVE DEMO")
    print(f"\n  Spec: {spec_path}")
    print(f"  Test Automation Summit 2026 Sydney  ·  22 May 2026")

    # ────────────────────────────────────────────────
    # STEP 1: Parse the spec
    # ────────────────────────────────────────────────
    step(1, "Parse the OpenAPI spec")
    from spec_parser import SpecParser

    print(f"  Loading {spec_path} ...")
    pause()
    parser = SpecParser(spec_path)
    s = parser.summary()

    print(f"\n  {'Endpoints found:':<28} {s['endpoints']}")
    print(f"  {'Test cases to generate:':<28} {s['total']}")
    print()
    print(f"  Breakdown by scenario:")
    for scenario, count in sorted(s["by_scenario"].items()):
        bar = "▓" * (count // 2)
        print(f"    {scenario:<22} {count:>3}  {bar}")
    print()
    print(f"  Breakdown by tag:")
    for tag, count in sorted(s["by_tag"].items()):
        print(f"    {tag:<22} {count:>3}")

    done(f"Spec parsed — {s['total']} test cases ready to generate")
    pause()

    # ────────────────────────────────────────────────
    # STEP 2: Generate pytest suite
    # ────────────────────────────────────────────────
    step(2, "Generate pytest test suite")
    from generate_pytest import main as gen_pytest

    output_dir = "tests/generated"
    gen_pytest(spec_path, output_dir)

    # Show a sample of generated test names
    from spec_parser import SpecParser as SP2
    p2 = SP2(spec_path)
    cases = p2.test_cases()
    print("  Sample test names generated:")
    for tc in cases[:8]:
        print(f"    def {tc.test_name}(base_url, auth_headers):")
    print(f"    ... and {len(cases) - 8} more\n")
    done(f"pytest suite written to {output_dir}/")
    pause()

    # ────────────────────────────────────────────────
    # STEP 3: Generate Postman collection
    # ────────────────────────────────────────────────
    step(3, "Generate Postman collection")
    from generate_postman import main as gen_postman

    gen_postman(spec_path, "output")
    done("Postman collection written to output/")
    pause()

    # ────────────────────────────────────────────────
    # STEP 4: LLM enrichment
    # ────────────────────────────────────────────────
    step(4, "LLM enrichment layer")
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        print("  ANTHROPIC_API_KEY found — running live LLM enrichment")
        from llm_enricher import demo as llm_demo
        llm_demo(spec_path)
    else:
        print("  (No ANTHROPIC_API_KEY set — showing prompt preview)\n")
        from llm_enricher import SMART_DATA_PROMPT, EDGE_CASES_PROMPT
        import json

        target = next(
            (tc for tc in cases if tc.method == "POST" and tc.request_body_schema),
            cases[0]
        )
        print(f"  Endpoint: {target.method} {target.path}")
        print()
        print("  Smart data prompt sent to Claude:")
        print("  ┌" + "─" * 56 + "┐")
        schema_str = json.dumps(target.request_body_schema or {}, indent=2)
        prompt = SMART_DATA_PROMPT.format(schema=schema_str)
        for line in prompt.strip().split("\n")[:12]:
            print(f"  │  {line:<54}│")
        print("  └" + "─" * 56 + "┘")
        print()
        print("  Edge cases prompt summary:")
        print(f"    → What boundaries should I test for {target.method} {target.path}?")
        print(f"    → Numeric limits, null handling, auth bypass, injection...")
        print()
        print("  To run with live LLM:")
        print("    export ANTHROPIC_API_KEY=sk-ant-...")
        print("    python run_all.py")

    # ────────────────────────────────────────────────
    # Final summary
    # ────────────────────────────────────────────────
    banner("DEMO COMPLETE")
    print()
    print(f"  {'Spec parsed:':<30} {spec_path}")
    print(f"  {'pytest tests generated:':<30} {len(cases)}")
    print(f"  {'Output dir:':<30} tests/generated/ + output/")
    print()
    print("  Next steps:")
    print("    pytest tests/generated/ -v")
    print("    newman run output/petstore_collection.json \\")
    print("      --environment output/petstore_environment.json \\")
    print("      --reporters cli,junit")
    print()


if __name__ == "__main__":
    main()
