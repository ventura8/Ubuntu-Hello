# Agent progress logs

This directory holds **agent progress** for active workstreams and **local CI tee output**.

## Convention

- Agents append timestamped status lines to files under `logs/` as they complete significant steps.
- **Preferred:** also echo the same lines to the terminal (`tee -a` or equivalent) so progress is visible in the session and on disk.
- Primary log for the AES keyring workstream: `aes-keyring-progress.log`
- Related workstreams may add sibling `*.log` files here.

## CI pipeline logs

When running the Docker CI gate (`scripts/ci-pipeline.sh` / `ci-docker.sh` / `ci-matrix.sh` / `ci-packaging-matrix.sh`), tee output here:

| Log | Source |
|---|---|
| `ci-pipeline.log` | Full `./scripts/ci-pipeline.sh` tee (optional outer wrap) |
| `ci-lint.log` | Lint stage (`UH_CI_STAGE=lint`) |
| `ci-coverage.log` | Coverage stage (`UH_CI_STAGE=coverage`) |
| `ci-matrix-summary.log` | Compat matrix launcher summary |
| `ci-matrix/<de>.log` | Per-DE compat cell (`baseline`, `gnome`, …) |
| `ci-packaging-summary.log` | Packaging matrix launcher summary |
| `ci-packaging/<format>.log` | Per-format packaging cell (`deb`, `snap`, …) |

After packaging, agents must **scan every** `ci-packaging/<format>.log` for meaningful ERROR/WARNING product issues (not only cell exit codes) — see [AGENTS.md](../AGENTS.md) §4.8 packaging log scan and `.agents/skills/pipeline-runner/SKILL.md`.

See [AGENTS.md](../AGENTS.md) §4.7.1 (root clean / `docker/` Dockerfiles), §4.8, and `.agents/skills/pipeline-runner/SKILL.md`.

## Git

`*.log` files under `logs/` are gitignored. This README may be committed so the folder purpose stays documented.
