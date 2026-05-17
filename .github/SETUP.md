# GitHub Actions — Setup Guide

## Required Secrets

Go to `Settings → Secrets and variables → Actions` in your repo and add:

| Secret | Description | Required |
|---|---|---|
| `API_KEY` | Your API authentication key for the live/staging API | Yes |
| `ANTHROPIC_API_KEY` | Claude API key for LLM enrichment | Only for LLM job |

## Required Variables

Go to `Settings → Secrets and variables → Actions → Variables`:

| Variable | Value | Purpose |
|---|---|---|
| `LLM_ENRICHMENT_ENABLED` | `true` or `false` | Gates LLM enrichment job |
| `SLACK_WEBHOOK_URL` | Your Slack incoming webhook URL | Nightly failure alerts |

## Workflows

### `api-test-pipeline.yml` — Main CI (runs on every PR and push)

```
PR opened
  │
  ├─ detect-spec-changes     ← checks if specs/*.yaml changed
  │
  ├─ regenerate-tests        ← only if spec changed
  │    ├─ generate_pytest.py → tests/generated/
  │    └─ generate_postman.py → output/
  │
  ├─ pytest (parallel)       ← test_pet / test_store / test_user
  │
  ├─ schemathesis            ← property-based fuzzing
  │
  ├─ newman                  ← Postman collection via Newman
  │
  └─ report                  ← aggregates results, comments on PR
```

### `nightly.yml` — Full regression (6am AEDT daily)
- Validates spec syntax with `openapi-spec-validator`
- Runs full test suite with 200 Schemathesis examples
- Sends Slack alert on failure

### `spec-lint.yml` — Fast spec check (runs only when specs/ changes)
- Validates OpenAPI syntax
- Previews how many tests would be generated
- Runs in ~30 seconds

## Manual Trigger

You can trigger the main pipeline manually from Actions tab:

```
Actions → "API Test Suite — Full CI Pipeline" → Run workflow
  ├─ Run Schemathesis fuzzing? [true/false]
  └─ Run LLM edge case enrichment? [true/false]
```

## Environment Variables (in workflow)

| Variable | Default | Override |
|---|---|---|
| `API_BASE_URL` | `https://petstore3.swagger.io/api/v3` | Set in `env:` or as repo variable |
| `PYTHON_VERSION` | `3.11` | Change in workflow `env:` block |
| `COVERAGE_THRESHOLD` | `80` | Change in workflow `env:` block |

## Branch Setup

Workflows run on:
- Push to `main` or `develop`
- Pull requests targeting `main` or `develop`

To restrict to only `main`:
```yaml
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
```

## Cost Estimate (GitHub-hosted runners)

| Workflow | Avg duration | Free tier impact |
|---|---|---|
| spec-lint | ~1 min | Negligible |
| Main pipeline (no fuzz) | ~5 min | ~5 min/PR |
| Main pipeline (with fuzz) | ~10 min | ~10 min/PR |
| Nightly | ~12 min | ~6 hr/month |

GitHub Free plan: 2,000 min/month. This fits comfortably.
