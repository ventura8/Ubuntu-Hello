"""Tests for scripts/uh-apt-deps.sh shared apt dependency lists."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPS_SCRIPT = ROOT / "scripts" / "uh-apt-deps.sh"

# Fixed harness: $1 = deps script path; $2 = snippet to eval.
_BASH_HARNESS = 'set -euo pipefail; source "$1"; eval "$2"'


def _bash_eval(snippet: str) -> str:
    out = subprocess.check_output(
        ["bash", "-c", _BASH_HARNESS, "_", str(DEPS_SCRIPT), snippet],
        text=True,
    )
    return out


def test_runtime_deps_include_gtk_babel_and_de_tools():
    pkgs = set(_bash_eval("uh_apt_unique_packages").split())
    for required in (
        "python3-babel",
        "python3-cryptography",
        "python3-gi",
        "gir1.2-gtk-4.0",
        "dconf-cli",
        "libglib2.0-bin",
        "xfconf",
        "libpam-gnome-keyring",
        "libpam-kwallet5",
        "pkexec",
        "polkitd",
        "tpm2-tools",
        "v4l-utils",
        "meson",
        "ninja-build",
    ):
        assert required in pkgs, f"missing {required}"
    # KF6 where apt offers it, KF5 on Ubuntu 22.04 (uh_apt_resolve_package): exactly one.
    kde_config = pkgs & {"libkf6config-bin", "libkf5config-bin"}
    assert len(kde_config) == 1, kde_config


def test_never_remove_includes_python3():
    names = _bash_eval('printf "%s\\n" "${UH_APT_NEVER_REMOVE[@]}"').split()
    assert "python3" in names


def test_marker_path_default():
    path = _bash_eval('printf "%s" "$UH_APT_MARKER"').strip()
    assert path == "/var/lib/ubuntu-hello/apt-packages-added.list"


def test_remove_exact_allows_auto_transitive_deps(tmp_path):
    """Auto-installed Remv packages (e.g. libxfconf-0-3) must not block uninstall."""
    snippet = r"""
uh_apt_planned_removals() { printf '%s\n' xfconf libxfconf-0-3; }
uh_apt_is_auto_installed() { [ "$1" = "libxfconf-0-3" ]; }
apt-get() {
  if [ "${1:-}" = "remove" ]; then
    return 0
  fi
  if [ "${1:-}" = "autoremove" ]; then
    return 0
  fi
  return 0
}
uh_apt_remove_exact xfconf
printf 'rc=%s\n' "$?"
"""
    out = _bash_eval(snippet)
    assert "rc=0" in out


def test_remove_exact_refuses_untracked_manual(tmp_path):
    snippet = r"""
uh_apt_planned_removals() { printf '%s\n' xfconf some-user-app; }
uh_apt_is_auto_installed() { return 1; }
apt-get() { return 0; }
set +e
uh_apt_remove_exact xfconf 2>&1
printf 'rc=%s\n' "$?"
"""
    out = _bash_eval(snippet)
    assert "untracked manual package 'some-user-app'" in out
    assert "rc=1" in out


def test_is_auto_installed_ignores_while_read_stdin():
    """apt-mark must not inherit while-read stdin (false negatives / SIGPIPE)."""
    snippet = r"""
apt-mark() {
  # Without </dev/null, leftover Remv lines from the while-read are readable.
  local line
  if IFS= read -r line && [ -n "$line" ]; then
    printf 'STDIN_LEAK:%s\n' "$line"
    return 1
  fi
  printf '%s\n' libxfconf-0-3
}
planned=$'libpam-kwallet5\nlibxfconf-0-3\nxfconf'
while IFS= read -r p || [ -n "$p" ]; do
  [ -z "$p" ] && continue
  if uh_apt_is_auto_installed "$p"; then
    printf 'auto:%s\n' "$p"
  else
    printf 'manual:%s\n' "$p"
  fi
done <<<"$planned"
"""
    out = _bash_eval(snippet)
    assert "STDIN_LEAK" not in out
    assert "auto:libxfconf-0-3" in out
    assert "manual:libpam-kwallet5" in out
    assert "manual:xfconf" in out


def _fake_apt_cache(available: tuple[str, ...]) -> str:
    """Bash function standing in for apt-cache policy with *available* packages."""
    lines = [
        "apt-cache() {",
        '  echo "$2:"',
        '  case "$2" in',
    ]
    for name in available:
        lines.append(f'    {name}) echo "  Candidate: 1.0" ;;')
    lines.append('    *) echo "  Candidate: (none)" ;;')
    lines += ["  esac", "}"]
    return "\n".join(lines)


def _resolve(package: str, available: tuple[str, ...]) -> str:
    return _bash_eval(_fake_apt_cache(available) + f"\nuh_apt_resolve_package {package}").strip()


def test_kreadconfig_falls_back_to_kf5_on_ubuntu_22_04():
    """Jammy ships KF5 only: kreadconfig5 (theme_detect tries kreadconfig6, then 5)."""
    assert _resolve("libkf6config-bin", ("libkf5config-bin",)) == "libkf5config-bin"
    pkgs = _bash_eval(_fake_apt_cache(("libkf5config-bin",)) + "\nuh_apt_unique_packages").split()
    assert "libkf5config-bin" in pkgs and "libkf6config-bin" not in pkgs


def test_kreadconfig_stays_kf6_when_available_or_lists_are_missing():
    assert _resolve("libkf6config-bin", ("libkf6config-bin", "libkf5config-bin")) == "libkf6config-bin"
    # No apt lists at all (fresh container): keep the default rather than guess.
    assert _resolve("libkf6config-bin", ()) == "libkf6config-bin"
    assert _resolve("xfconf", ()) == "xfconf"
