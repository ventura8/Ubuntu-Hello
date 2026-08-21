# Gemini CLI — Ubuntu Hello

Canonical agent rules for this repository: **[AGENTS.md](AGENTS.md)**.

This file is a thin project entrypoint for Gemini CLI context. Prefer `AGENTS.md` over duplicating rules here.

Also load as needed:

- [docs/INSTRUCTIONS.md](docs/INSTRUCTIONS.md)
- [docs/architecture/README.md](docs/architecture/README.md)
- [docs/SECURITY.md](docs/SECURITY.md)
- [`.agents/skills/`](.agents/skills/)

Optional import (if using Gemini `@` imports in your local setup):

```text
@./AGENTS.md
```

Progress logs: `logs/`. Lint and test new or changed code per `AGENTS.md` §4.5. Sync agent docs with code per `AGENTS.md` §4.7. Baseline OS for CI/docs: Ubuntu **26.04**.
