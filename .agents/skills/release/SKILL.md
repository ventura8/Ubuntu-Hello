---
name: release
description: >-
  Write Ubuntu Hello release docs for the version on the current branch by
  reviewing all changes (committed, staged, and unstaged vs the merge base),
  then amend the current commit with a release title and description. Use when
  the user asks for a release, release notes, changelog, GitHub release
  description, or to document the current version from the branch name.
---

# Release Docs

Produce release documentation for the **current branch version**, covering
**all** changes for that release, then **amend** HEAD with a matching title and
body.

## Agent mandate (mandatory)

1. **Version from the current branch only** for release *docs naming* — parse
   `git branch --show-current`. Then write that semver into **`VERSION`**
   (single source of truth). Do **not** invent a version from Meson literals,
   tags, or prior docs, and do **not** leave shipping consumers with a
   hardcoded older pin (e.g. `1.0.4`) when releasing `v1.1.0`.
2. **Look at ALL changes** — every file and theme that lands in this release,
   not a subset. Include commits since the merge base **and** uncommitted /
   staged work still on the tree.
3. Write **both** release files under `docs/releases/`.
4. **Amend** the current commit so its subject and body match the release
   title and description (user is requesting amend by invoking this skill).
5. Update agent indexes (`skills.md`, and `AGENTS.md` if paths/workflow change)
   in the **same** change set.

## Version from branch

```bash
branch="$(git branch --show-current)"
# Accept: feature/v1.1.0 | release/v1.1.0 | v1.1.0 | 1.1.0
version="$(printf '%s' "$branch" | sed -E 's#.*/##; s/^v//')"
# Canonical forms:
#   VER=1.1.0
#   TAG=v1.1.0
```

Abort if `$version` is not `N.N.N` (semver digits). Do **not** fall back to
Meson / debian / tags.

Previous tag for compare links: latest `v*` tag on the default branch older than
this release (usually `v` + prior patch), e.g. `v1.0.4` → `v1.1.0`.

## Bump project version (mandatory — single source of truth)

**Canonical version file:** repo-root [`VERSION`](../../../VERSION) (one line, e.g. `1.1.0`).

After resolving `$version` from the branch:

1. Write `$version` into **`VERSION`** (overwrite the file; first line only).
2. Do **not** hand-edit version strings in Meson / PKGBUILD / i18n / conftest — they **read** `VERSION` via `scripts/read-version.py` (or open `VERSION` directly).
3. Add a **new top** `debian/changelog` entry `ubuntu-hello (${version}-1ppa1) …` summarizing the release (historical older entries stay, including prior `1.0.4`).

Consumers of `VERSION` (do not duplicate the number elsewhere for shipping pins):

| Path | How it uses VERSION |
|------|---------------------|
| `VERSION` | **Source of truth** |
| `meson.build` (+ subproject meson files) | `run_command(…/scripts/read-version.py)` |
| `ubuntu-hello/archlinux/ubuntu-hello/PKGBUILD` | `pkgver=$(python3 …/scripts/read-version.py)` |
| `scripts/i18n-update.sh` / `scripts/i18n-fill-translations.py` | `--package-version` / `Project-Id-Version` from VERSION |
| `tests/conftest.py` | `mock_paths.version = f"{VERSION}-dev"` |
| CLI/GTK fallbacks (`cli.py`, `window.py`, `onboarding.py`) | Read `VERSION` when `paths.version` unavailable |
| GTK UI (`version_display.py`) | Settings / setup wizard label — `v`+VERSION, optional `-dev`; never older `git describe` tags in parentheses |

Do **not** leave a stale shipping pin (e.g. hardcoded `1.0.4` in Meson/PKGBUILD) when releasing `v1.1.0`. Historical `docs/releases/v1.0.4*.md` and older `debian/changelog` entries are archives — leave them. Mention the version bump in both release docs’ changelogs.

## Gather ALL changes (do not skip)

Run these and read the outputs fully before writing prose:

```bash
base="$(git merge-base HEAD main 2>/dev/null || git merge-base HEAD master)"
git log --oneline "$base"..HEAD
git diff --stat "$base"...HEAD
git diff --name-status "$base"...HEAD
git status --porcelain=v1
git diff --stat HEAD
git diff --cached --stat
# For every major area touched, also read the actual diff / file summaries:
# PAM, compare, GTK, keyring, CI/docker, tests, docs, packaging
git diff "$base"...HEAD -- <touched paths>
git diff HEAD -- <touched uncommitted paths>
git diff --cached -- <touched staged paths>
```

**Completeness rules:**

* Cover **product** changes (PAM, compare, GTK, CLI, crypto, config).
* Cover **CI / Docker / scripts / workflows**.
* Cover **tests**, **docs**, **agent skills**, **packaging** when present.
* Do **not** omit “boring” moves (e.g. `Dockerfile.ci` → `docker/`) or
  agent-doc reorganizations — mention them briefly in changelog.
* Group by theme; do not paste raw file lists as the release narrative.
* **Always spell out ALL desktop / greeter / wallet / CI compatibility** when
  the release touches multi-DE support, theme detection, wallets, or the
  compat matrix. Name every target explicitly — do **not** summarize as
  “multi-DE” alone:
  * Desktops: GNOME, KDE Plasma, XFCE, Cinnamon, MATE, Budgie, LXQt
  * Greeters/DMs when relevant: GDM, SDDM, LightDM (and face-skip service
    patterns)
  * Wallets: GNOME Keyring (`pam_gnome_keyring`) **and** KWallet
    (`pam_kwallet5`)
  * CI compat cells: `baseline`, `gnome`, `kde`, `xfce`, `cinnamon`,
    `mate`, `budgie`, `lxqt`
  Prefer a dedicated compatibility section or table in both release files.

## Output files

| File | Purpose |
|------|---------|
| `docs/releases/vX.Y.Z.md` | Full release page (install + changelog) |
| `docs/releases/vX.Y.Z_github_description.md` | GitHub Release body (title line = H1) |

Mirror the tone and section shape of the latest prior files in `docs/releases/`
(e.g. `v1.0.4.md` / `v1.0.4_github_description.md`): welcome blurb, key
enhancement sections with bullets, install methods, short changelog.

GitHub description H1 pattern:

```markdown
# Ubuntu Hello vX.Y.Z - <Short Theme Title>
```

End the GitHub description with:

```markdown
**Full Changelog**: [vPREV...vX.Y.Z](https://github.com/ventura8/ubuntu-hello/compare/vPREV...vX.Y.Z)
```

## Amend current commit (mandatory)

After the release docs (and skill index updates) are written:

1. Confirm amend is safe enough for this workflow:
   * Branch has **no upstream**, or status shows **not** diverged in a way that
     requires force-push of others' work.
   * Prefer amending only when HEAD is this release branch tip and the user
     invoked this skill (explicit amend request).
2. Stage **everything** that belongs in the release tip (docs, skill, indexes,
   and any still-uncommitted release work):

```bash
git add -A
# Review: git status && git diff --cached --stat
```

3. Amend with title + description (HEREDOC). Subject follows repo style:

```text
release: vX.Y.Z - <Short Theme Title>
```

Body = short multi-paragraph / bullet summary aligned with the GitHub
description (why + highlights), not a dump of `git status`.

```bash
git commit --amend -m "$(cat <<'EOF'
release: vX.Y.Z - <Short Theme Title>

<1–3 short paragraphs or bullets covering ALL major themes.>

EOF
)"
```

4. Verify: `git log -1 --format='%s%n%n%b'` and `git status -sb`.

**Do not** push unless the user explicitly asks. **Do not** use `--no-verify`.

## Checklist

```
Release progress:
- [ ] Version parsed from current branch only
- [ ] `VERSION` file set to that version (single source of truth)
- [ ] debian/changelog top entry added for that version
- [ ] ALL diffs vs merge-base + working tree reviewed
- [ ] docs/releases/vX.Y.Z.md written
- [ ] docs/releases/vX.Y.Z_github_description.md written
- [ ] skills.md (+ AGENTS.md if needed) updated
- [ ] Staged + amended HEAD with release title and description
- [ ] git log -1 / git status verified
```
