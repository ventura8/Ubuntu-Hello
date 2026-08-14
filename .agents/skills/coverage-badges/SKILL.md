---
name: coverage-badges
description: >-
  Regenerate local coverage and linting SVG badges with generate_badges.py.
  Use when refreshing docs/badges after test or lint changes.
---

# Coverage & Linting Badges

```bash
python3 generate_badges.py
```

This utility:

1. Runs pytest with coverage for fresh statistics
2. Verifies Python syntax across the codebase
3. Updates `docs/badges/coverage.svg` and `docs/badges/linting.svg`

CI enforces coverage via `scripts/ci-docker.sh` (project ≥ 90%, keyring modules 100%). Badges are a local/docs convenience, not a separate gate.
