---
name: pipeline-runner
description: >-
  Run the full Ubuntu Hello local CI gate via ci-pipeline.sh (lint → coverage →
  DE compat matrix → packaging matrix), teeing output under logs/. Fix every
  failure until all stages are green — never suppress or weaken checks.
---

# Pipeline Runner

Full local quality gate matching GitHub Actions (Ubuntu **26.04**), in four fail-fast stages:

1. **lint** — `docker/Dockerfile.ci.lint` / `ubuntu-hello-ci-lint:26.04` (clang-tidy + py_compile + i18n-lint + shellcheck on packaging scripts)
2. **coverage** — `docker/Dockerfile.ci.coverage` / `ubuntu-hello-ci-coverage:26.04`
3. **compat matrix** — per-DE images under `docker/` via `scripts/ci-matrix.sh` (parallel)
4. **packaging matrix** — all 7 formats via `scripts/ci-packaging-matrix.sh` (parallel; same `scripts/ci-packaging-cell.sh` as GHA `check.yml`)

## Agent mandate (mandatory)

When this skill is used (or the user asks to run the pipeline / CI matrix):

1. **Run the gate**, then **fix every failure** and **re-run until fully green**.
2. **Do not** ignore, suppress, disable, or weaken checks to make CI pass. Forbidden examples:
   - Adding `# noqa`, `# type: ignore`, `# pyright: ignore`, or similar to silence Python linters/typecheckers — fix imports/types instead (enforced by `scripts/no-suppressions-lint.py`)
   - Adding `# shellcheck disable=…` (or `shellcheck -e`) to silence shellcheck — fix the script instead (same lint)
   - Adding `NOLINT` / `NOLINTNEXTLINE` / `NOLINTBEGIN` to silence clang-tidy (same lint)
   - Raising cognitive-complexity thresholds or removing checks from `.clang-tidy`
   - Turning off `WarningsAsErrors` or converting errors to ignored warnings
   - Lowering `--cov-fail-under` / skipping pytest, clang-tidy, or meson tests
   - Commenting out steps in `scripts/ci-*.sh` or workflows
   - Marking flakes as xfail / skip without a real root-cause fix (env gates like missing meson/`UH_REAL_GTK` are OK)
3. **Do not ignore warnings** that the gate treats as failures (clang-tidy warnings-as-errors, shellcheck findings, coverage floors, etc.). Fix the underlying code or test.
4. **Fail fast between stages and inside each cell**: `ci-pipeline.sh` stops on the first failed stage; each `ci-docker.sh` uses `set -euo pipefail` and clang-tidy `WarningsAsErrors`. Never soft-fail or continue-on-error for quality steps.
5. **Exit only when green**: lint, coverage, **all eight** DE compat cells, **and all seven** packaging format cells must pass. Partial green is not done.
6. **Scan packaging logs even when cells exit 0**: after packaging, read `logs/ci-packaging/<format>.log` for **each** format. Fix meaningful `ERROR` / `WARNING` / `!!!` / `error:` findings that imply broken install, face auth (`dlib` / models), PAM, or uninstall/keyring restore — do not treat `packaging cell OK` alone as sufficient. Re-run affected cells after fixes. See [AGENTS.md](../../../AGENTS.md) §4.8 packaging log scan.
7. Prefer iterating on the failing stage (`UH_CI_STAGE=lint` or `coverage`, one `UH_CI_DE=…` compat cell, or `./scripts/ci-packaging-cell.sh <format>`), then re-run **`./scripts/ci-pipeline.sh`** before declaring success.

## Root clean

Keep the repo root clean: CI Dockerfiles belong in **`docker/`**, not top-level. Update agent docs before relocating paths ([AGENTS.md](../../../AGENTS.md) §4.7.1).

## Full gate (preferred)

```bash
./scripts/ci-pipeline.sh 2>&1 | tee logs/ci-pipeline.log
```

CI must keep pinned deps: Actions/runners at explicit version tags (never SHAs, never a `latest` alias), pip `==` pins in Dockerfiles under `docker/`, apt via `ubuntu:26.04`. Do not “fix” CI by unpinning or floating versions.

## Individual stages (faster iteration)

```bash
set -euo pipefail
mkdir -p logs

# 1) Lint only
UH_CI_STAGE=lint ./scripts/ci-docker.sh 2>&1 | tee logs/ci-lint.log

# 2) Coverage only
UH_CI_STAGE=coverage ./scripts/ci-docker.sh 2>&1 | tee logs/ci-coverage.log

# 3) One DE compat cell
UH_CI_STAGE=compat UH_CI_DE=baseline ./scripts/ci-docker.sh 2>&1 | tee logs/ci-baseline.log

# 3b) All DE compat cells in parallel
./scripts/ci-matrix.sh 2>&1 | tee logs/ci-matrix-summary.log

# 4) One packaging format (same cell as GHA)
./scripts/ci-packaging-cell.sh deb 2>&1 | tee logs/ci-packaging-deb.log

# 4b) All packaging format cells in parallel
./scripts/ci-packaging-matrix.sh 2>&1 | tee logs/ci-packaging-summary.log
```

## What each stage includes

| Stage | Image / driver | Runs |
|---|---|---|
| `lint` | `ubuntu-hello-ci-lint:26.04` | meson/ninja, clang-tidy (PAM C++), `py_compile`, `scripts/i18n-lint.py`, `scripts/no-suppressions-lint.py`, `shellcheck` (packaging scripts) |
| `coverage` | `ubuntu-hello-ci-coverage:26.04` | meson/ninja, pytest ≥ 90%, keyring coverage 100%, `meson test pam-aes-gcm-uh1 pam-face-skip` |
| `compat` | `ubuntu-hello-ci-<de>:26.04` | meson/ninja, `py_compile`, pytest (no cov floors), Settings E2E under xvfb, `meson test pam-aes-gcm-uh1 pam-face-skip` |
| `packaging` | format release images via `ci-packaging-cell.sh` | build artifact → `packaging-smoke-verify.sh` → live E2E install/upgrade/remove/reinstall (`packaging-e2e-install.sh`; Snap E2E inside `ci-snap-build.sh`) |

Coverage DBs use `COVERAGE_FILE=${BUILD_DIR}/.coverage` (coverage stage only).

Hard rules (also in [AGENTS.md](../../../AGENTS.md) §4.7.1 / §4.8):

* Dockerfiles under `docker/` — keep the repo root clean
* `FROM ubuntu:26.04` only — never a floating/`latest` alias
* One Dockerfile/image per DE for compat — never serialize the matrix
* Packaging: GHA and local use the **same** `ci-packaging-cell.sh` — never leave packaging GHA-only
* After packaging: **scan** `logs/ci-packaging/*.log` for real ERROR/WARNING product issues and fix them (not only cell exit codes)
* Dedicated lint + coverage images — do not fold those stages back into every DE cell
* Quality bar stays strict — fix code, do not lower the bar; **never** `# shellcheck disable=…` / `NOLINT` / `# noqa` / `# type: ignore` to paper over findings

Related skills: [ci-docker-matrix](../ci-docker-matrix/SKILL.md), [test-runner](../test-runner/SKILL.md), [release-packaging](../release-packaging/SKILL.md).
