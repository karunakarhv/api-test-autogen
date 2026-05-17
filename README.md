# API Test Auto-Generation Demo
### Test Automation Summit 2026 Sydney — 22 May 2026

Talk: **"API Test Automation at Scale: Auto-Generating Test Suites from OpenAPI Specs"**

---

## Repo Structure

```
demo/
├── specs/
│   └── petstore.yaml          # OpenAPI 3.0 spec (the demo input)
├── src/
│   ├── spec_parser.py         # STEP 1: Parses spec → TestCase objects
│   ├── generate_pytest.py     # STEP 2: TestCases → pytest test files
│   ├── generate_postman.py    # STEP 3: TestCases → Postman collection
│   └── llm_enricher.py        # STEP 4: LLM smart data + edge cases
├── tests/
│   └── generated/             # ← auto-generated, do not edit
├── output/                    # ← Postman collection + environment JSON
├── run_all.py                 # 🎤 THE LIVE DEMO SCRIPT
└── requirements.txt
```

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the full demo pipeline
python run_all.py

# 3. Execute the generated tests (optional — needs live Petstore API)
pytest tests/generated/ -v

# 4. Run via Newman (optional — needs npm + newman)
newman run output/petstore_collection.json \
  --environment output/petstore_environment.json \
  --reporters cli,junit \
  --reporter-junit-export output/newman_results.xml
```

---

## Run Each Step Individually

```bash
# Step 1 — Parse spec and show test matrix summary
python src/spec_parser.py specs/petstore.yaml

# Step 2 — Generate pytest suite
python src/generate_pytest.py specs/petstore.yaml tests/generated

# Step 3 — Generate Postman collection
python src/generate_postman.py specs/petstore.yaml output

# Step 4 — LLM enrichment (set API key first)
export ANTHROPIC_API_KEY=sk-ant-...
python src/llm_enricher.py specs/petstore.yaml
```

---

## What Gets Generated

From the Petstore spec (13 endpoints):

| Scenario          | Tests |
|-------------------|-------|
| happy_path        |  13   |
| no_auth           |   9   |
| missing_required  |   6   |
| wrong_type        |   8   |
| invalid_enum      |   7   |
| **Total**         | **43+** |

Broken into:
- `tests/generated/test_pet.py`
- `tests/generated/test_store.py`
- `tests/generated/test_user.py`
- `tests/generated/conftest.py`

---

## Stage Notes (for presenter)

### Terminal setup
- Font size 18+, dark theme
- Split terminal: left = editor showing spec, right = terminal

### Recommended live sequence
```bash
# Window 1: show the spec
cat specs/petstore.yaml | head -60

# Window 2: run step by step for audience
python src/spec_parser.py specs/petstore.yaml
python src/generate_pytest.py specs/petstore.yaml
cat tests/generated/test_pet.py | head -80
python src/generate_postman.py specs/petstore.yaml
python src/llm_enricher.py specs/petstore.yaml   # if API key set
```

### Key talking points per step
1. **spec_parser.py** — "13 endpoints → 43 test cases in under a second. The spec already had all this information — we just hadn't read it programmatically."
2. **generate_pytest.py** — "It writes real pytest functions: happy path, missing fields, wrong types, bad enums, stripped auth. Each one is immediately runnable."
3. **generate_postman.py** — "Same data, different output format. The Postman collection drops straight into Newman for CI."
4. **llm_enricher.py** — "Now watch what happens when we add an LLM. It doesn't replace the generator — it makes the inputs smarter and the names readable."

---

## Using Your Own Spec

```bash
python run_all.py path/to/your_api.yaml
```

Works with any OpenAPI 3.x YAML or JSON spec.

---

## Talk Repo
github.com/karun/api-test-autogen
