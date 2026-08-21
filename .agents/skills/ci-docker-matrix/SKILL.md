---
name: ci-docker-matrix
description: >-
  Run Ubuntu Hello Docker CI stages: lint, coverage, or per-DE compat matrix
  (ubuntu:26.04). Use when validating CI locally or changing Dockerfiles /
  check.yml jobs.
---

# Docker CI (Ubuntu 26.04) — lint, coverage, compat matrix

Target OS is fixed **Ubuntu 26.04 (resolute)**. Every CI Dockerfile uses `FROM ubuntu:26.04`. **Do not** use floating series tags or any `latest` alias.

## Root clean + Dockerfile location

All CI/PPA Dockerfiles live under **`docker/`** (not the repo root). See [AGENTS.md](../../../AGENTS.md) §4.7.1. When adding or relocating Docker assets: update agent MDs first, then move files, then fix script/workflow references.

## Stages

| `UH_CI_STAGE` | Dockerfile | Image | Purpose |
|---|---|---|---|
| `lint` | `docker/Dockerfile.ci.lint` | `ubuntu-hello-ci-lint:26.04` | clang-tidy + `py_compile` + `scripts/i18n-lint.py` + `scripts/no-suppressions-lint.py` + `shellcheck` |
| `coverage` | `docker/Dockerfile.ci.coverage` | `ubuntu-hello-ci-coverage:26.04` | pytest coverage floors + meson C++ tests |
| `compat` | `docker/Dockerfile.ci` / `docker/Dockerfile.ci.<de>` | `ubuntu-hello-ci-<de>:26.04` | DE compatibility build/test |

```bash
UH_CI_STAGE=lint ./scripts/ci-docker.sh
UH_CI_STAGE=coverage ./scripts/ci-docker.sh
UH_CI_STAGE=compat UH_CI_DE=kde ./scripts/ci-docker.sh
```

## Compat DE cells

`UH_CI_DE` (compat only) must be one of: `baseline`, `gnome`, `kde`, `xfce`, `cinnamon`, `mate`, `budgie`, `lxqt`.

Keep **one Dockerfile + one image per DE** (no ARG-collapsed single image).

| `UH_CI_DE` | Dockerfile | Image |
|---|---|---|
| `baseline` | `docker/Dockerfile.ci` | `ubuntu-hello-ci-baseline:26.04` |
| `gnome` | `docker/Dockerfile.ci.gnome` | `ubuntu-hello-ci-gnome:26.04` |
| `kde` | `docker/Dockerfile.ci.kde` | `ubuntu-hello-ci-kde:26.04` |
| `xfce` | `docker/Dockerfile.ci.xfce` | `ubuntu-hello-ci-xfce:26.04` |
| `cinnamon` | `docker/Dockerfile.ci.cinnamon` | `ubuntu-hello-ci-cinnamon:26.04` |
| `mate` | `docker/Dockerfile.ci.mate` | `ubuntu-hello-ci-mate:26.04` |
| `budgie` | `docker/Dockerfile.ci.budgie` | `ubuntu-hello-ci-budgie:26.04` |
| `lxqt` | `docker/Dockerfile.ci.lxqt` | `ubuntu-hello-ci-lxqt:26.04` |

## Full local gate

```bash
./scripts/ci-pipeline.sh
```

Runs **lint → coverage → compat matrix → packaging matrix** and fails fast between stages.

Compat-only parallel matrix (local):

```bash
./scripts/ci-matrix.sh
```

`ci-matrix.sh` starts every DE **compat** cell concurrently (`UH_CI_STAGE=compat`, `UH_CI_PARALLEL_BUILD=1`), each with a unique `UBUNTU_HELLO_CI_BUILD_DIR`. Parallel image builds use plain **`docker build`** (not buildx) to avoid builder deadlocks. Fails if **any** cell fails. Logs: `logs/ci-matrix/<de>.log`.

Packaging-only parallel matrix (local; same cells as GHA):

```bash
./scripts/ci-packaging-matrix.sh
# or one format:
./scripts/ci-packaging-cell.sh deb
```

`ci-packaging-matrix.sh` starts every format cell concurrently with `UH_PACKAGING_ARTIFACTS_ISOLATE=1` (per-format `artifacts/ci-packaging/<format>/`). Logs: `logs/ci-packaging/<format>.log`.

## Dependency pins

* GHA runners: `runs-on: ubuntu-26.04`
* GHA actions: explicit version tags only (e.g. `@v7.0.1`, `@v4.2.0`) — never commit SHAs, never a `latest` alias
* Docker: `FROM ubuntu:26.04`; `# syntax=docker/dockerfile:1.26.0`
* Pip in CI images: exact pins (`pytest==9.1.1`, `pytest-cov==7.1.0`, `coverage==7.15.4`, `keyboard==0.13.5`)
* Apt: distro-locked by `FROM ubuntu:26.04` (document; do not add unpinned URL installers)

## Caching

* Dockerfiles: `# syntax=docker/dockerfile:1.26.0` + BuildKit apt/pip cache mounts
* `scripts/ci-docker.sh`: `DOCKER_BUILDKIT=1`; `UH_CI_DOCKER_CACHE=local|gha|none` (default `local`)
* Local: `.cache/docker-ci/<scope>` + skip rebuild when image label `ubuntu-hello.ci.dockerfile-digest` matches Dockerfile sha256 (`UH_CI_FORCE_BUILD=1` to rebuild). On local `buildx` failure, continue only if the loaded image’s digest label matches the current Dockerfile digest (never a stale pre-existing tag); otherwise retry without cache export.
* GHA: `docker/setup-buildx-action@v4.2.0` + `UH_CI_DOCKER_CACHE=gha` (`cache-from/to: type=gha`, scope per stage/DE)
* Bind-mount `/src` runs are unchanged — cache is image-layer only

## What each stage runs

* **lint**: meson/ninja (g++), clang-tidy on PAM `.cc` (+ UH1 test), `py_compile`, `scripts/i18n-lint.py` (JSON + `.po`), `scripts/no-suppressions-lint.py`, `shellcheck` on packaging scripts
* **coverage**: meson/ninja, pytest ≥ 90%, keyring coverage 100%, `meson test pam-aes-gcm-uh1 pam-face-skip` (`COVERAGE_FILE=${BUILD_DIR}/.coverage`)
* **compat**: meson/ninja, `py_compile`, pytest **without** coverage floors, Settings E2E under xvfb, `meson test pam-aes-gcm-uh1 pam-face-skip`

## GitHub Actions

`.github/workflows/check.yml` (OSS **20-runner** concurrency):

* **17 runners** start **immediately** in parallel — no `needs` gates: `lint`, `coverage`, 8× `compat` matrix (`max-parallel: 20`, `fail-fast: false`), and 7× `packaging` format matrix (`deb`, `rpm-fedora`, `rpm-opensuse`, `arch`, `snap`, `appimage`, `flatpak`; same concurrency knobs; skip fork PRs). Packaging cells call **`scripts/ci-packaging-cell.sh`** (build + smoke + live E2E; Snap E2E inside `ci-snap-build.sh`)
* **`concurrency.cancel-in-progress: true`** — a new push to the same PR or branch cancels the previous workflow run
* Each job uses its own runner; matrix jobs use `strategy.max-parallel: 20` to saturate OSS workers
* Local `./scripts/ci-pipeline.sh` fail-fast **lint → coverage → compat → packaging** (same packaging cell script via `ci-packaging-matrix.sh`)
* Each compat matrix job: `UH_CI_STAGE=compat UH_CI_DE=… ./scripts/ci-docker.sh`
* **Never** turn DE compat into a sequential loop in one job
* **Never** re-run full clang-tidy/coverage floors inside every DE cell
* **Never** leave packaging smoke/E2E GHA-only — local gate must fail when packaging fails
`docker/Dockerfile.ppa` remains `ubuntu:26.04` only (no DE packaging matrix). When changing CI/Docker/DE support, update [AGENTS.md](../../../AGENTS.md) §4.7.1 / §4.8 and this skill in the same change.

For the **full gate + fix-until-green** agent loop (no NOLINT / no `# shellcheck disable` / no `# noqa` / no `# type: ignore` / no weakened checks), use [pipeline-runner](../pipeline-runner/SKILL.md).
