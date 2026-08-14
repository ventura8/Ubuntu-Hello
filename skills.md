# Skills (moved)

Ubuntu Hello agent skills live under **[`.agents/skills/`](.agents/skills/)** — one directory per skill with a `SKILL.md` runbook.

Canonical agent rules: [AGENTS.md](AGENTS.md). Setup guide: [docs/INSTRUCTIONS.md](docs/INSTRUCTIONS.md).

| Skill | Purpose |
|-------|---------|
| [meson-build](.agents/skills/meson-build/SKILL.md) | Meson setup / compile / install / uninstall |
| [face-cli](.agents/skills/face-cli/SKILL.md) | Face profile CLI |
| [diagnostics](.agents/skills/diagnostics/SKILL.md) | compare.py dry-run / `ubuntu-hello test` |
| [troubleshoot](.agents/skills/troubleshoot/SKILL.md) | Auth logs, debug knobs, PAM lockout |
| [gtk-ui](.agents/skills/gtk-ui/SKILL.md) | Glade / GTK / theme_detect |
| [keyring-wallet](.agents/skills/keyring-wallet/SKILL.md) | PAM_AUTHTOK / keyring / KWallet |
| [rubberstamp](.agents/skills/rubberstamp/SKILL.md) | Liveness plugins |
| [coverage-badges](.agents/skills/coverage-badges/SKILL.md) | Badge generator |
| [ci-docker-matrix](.agents/skills/ci-docker-matrix/SKILL.md) | Lint / coverage / per-DE compat Docker CI (`docker/` Dockerfiles) |
| [test-runner](.agents/skills/test-runner/SKILL.md) | pytest + PAM C++ tests |
| [pipeline-runner](.agents/skills/pipeline-runner/SKILL.md) | Full gate: lint → coverage → compat matrix; fix until green |
| [release](.agents/skills/release/SKILL.md) | Release notes from **all** branch changes; amend commit title/body |
| [installer-tester](.agents/skills/installer-tester/SKILL.md) | install.sh / uninstall.sh tests |
| [pam-verifier](.agents/skills/pam-verifier/SKILL.md) | PAM lifecycle / face-skip / UH1 |
| [resolve-pr-comments](.agents/skills/resolve-pr-comments/SKILL.md) | Resolve PR review threads |
| [review-with-coderabbit](.agents/skills/review-with-coderabbit/SKILL.md) | CodeRabbit review (user-gated) |
