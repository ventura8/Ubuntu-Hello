## What and why

<!-- What changes, and the problem it solves. Link an issue if there is one. -->

## How it was verified

<!-- Commands you ran and what they showed. "CI is green" alone is not enough
     for changes to PAM, the keyring, or the installer. -->

## Checklist

See [AGENTS.md](../AGENTS.md) §4.5–4.8 for what each item means.

- [ ] Touched code passes the lint and tests for its type — C++: meson build + clang-tidy; Python: `py_compile` + pytest; shell: `shellcheck`
- [ ] No suppressions (`NOLINT`, `# noqa`, `# shellcheck disable`, `NOSONAR`, `xfail`/`skip`) — `scripts/no-suppressions-lint.py`
- [ ] Changed a user-visible string? Ran `./scripts/i18n-update.sh`, filled every language, `scripts/i18n-lint.py` clean
- [ ] Agent docs updated where behaviour, paths or workflows changed (`AGENTS.md`, `.agents/skills/`, `docs/`)
- [ ] Touches PAM, the keyring/TPM path, or packaging? The VM tier ran: it runs on `feature/v*` PRs, or trigger it with `gh workflow run check.yml --ref <branch>`
