# GitHub Copilot — Ubuntu Hello

Canonical agent rules live in **[AGENTS.md](../AGENTS.md)** at the repository root. Follow that document for architecture, coding standards, CI/Docker DE matrix rules, exit codes, and documentation sync.

Additional context (do not fork rules into this file):

- [docs/INSTRUCTIONS.md](../docs/INSTRUCTIONS.md) — setup and contribution
- [docs/architecture/README.md](../docs/architecture/README.md) — system design
- [docs/SECURITY.md](../docs/SECURITY.md) — security practices
- [`.agents/skills/`](../.agents/skills/) — task-specific runbooks

When changing behavior, update `AGENTS.md` and affected skills in the same change set. Lint and test new or changed code per `AGENTS.md` §4.5. Use `logs/` for agent progress output.
