"""Static checks for multi-format release packaging metadata."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

PACKAGING_PATHS = [
    "packaging/arch/ubuntu-hello/PKGBUILD",
    "packaging/rpm/fedora/ubuntu-hello.spec",
    "packaging/rpm/opensuse/ubuntu-hello.spec",
    "packaging/snap/snapcraft.yaml",
    "packaging/snap/SNAPCRAFT_REVISION",
    "packaging/snap/install-wrapper.sh",
    "packaging/snap/hooks/configure",
    "packaging/appimage/build-appimage.sh",
    "packaging/flatpak/com.github.ventura8.UbuntuHello.yml",
    "packaging/flatpak/install-host.sh",
    "packaging/flatpak/data/com.github.ventura8.UbuntuHello.install.policy",
    "packaging/flatpak/data/com.github.ventura8.UbuntuHello.host.policy",
    "packaging/file-lists/ubuntu-hello.install",
    "packaging/file-lists/ubuntu-hello-gtk.install",
]

RELEASE_SCRIPTS = [
    "scripts/release-common.sh",
    "scripts/release-deb.sh",
    "scripts/release-rpm.sh",
    "scripts/release-arch.sh",
    "scripts/release-portable.sh",
    "scripts/packaging-smoke-verify.sh",
    "scripts/packaging-e2e-install.sh",
    "scripts/ci-snap-build.sh",
    "scripts/ci-packaging-cell.sh",
    "scripts/ci-packaging-matrix.sh",
    "scripts/release-verify-tag-version.sh",
    "scripts/package-configure.sh",
    "scripts/package-gtk-onboard.sh",
    "scripts/package-prerm.sh",
]

DOCKER_RELEASE_IMAGES = [
    "docker/Dockerfile.ppa",
    "docker/Dockerfile.rpm.fedora",
    "docker/Dockerfile.rpm.opensuse",
    "docker/Dockerfile.arch",
    "docker/Dockerfile.release",
]

CI_PACKAGING_FORMATS = [
    "deb",
    "rpm-fedora",
    "rpm-opensuse",
    "arch",
    "snap",
    "appimage",
    "flatpak",
]

SHELLCHECK_PATHS = [
    "scripts/package-configure.sh",
    "scripts/package-gtk-onboard.sh",
    "scripts/package-prerm.sh",
    "scripts/release-common.sh",
    "scripts/release-deb.sh",
    "scripts/release-rpm.sh",
    "scripts/release-arch.sh",
    "scripts/release-portable.sh",
    "scripts/packaging-smoke-verify.sh",
    "scripts/packaging-e2e-install.sh",
    "scripts/ci-snap-build.sh",
    "scripts/ci-packaging-cell.sh",
    "scripts/ci-packaging-matrix.sh",
    "scripts/ci-pipeline.sh",
    "scripts/release-verify-tag-version.sh",
    "scripts/test-split-install-adapter.sh",
    "scripts/test-packaging-installers.sh",
    "scripts/ci-matrix.sh",
    "packaging/appimage/build-appimage.sh",
    "packaging/flatpak/install-host.sh",
    "packaging/snap/install-wrapper.sh",
    "packaging/snap/hooks/configure",
    "docker/snap-entrypoint.sh",
    "debian/ubuntu-hello.postinst",
    "debian/ubuntu-hello-gtk.postinst",
    "debian/ubuntu-hello.prerm",
]


def _repo_path(relative: str) -> Path:
    return REPO / relative


def _assert_repo_file(relative: str) -> None:
    path = _repo_path(relative)
    try:
        assert path.is_file(), relative
    except OSError as err:
        raise AssertionError(f"failed to stat repository file {path}") from err


def _repo_stat(relative: str) -> os.stat_result:
    path = _repo_path(relative)
    try:
        return path.stat()
    except OSError as err:
        raise AssertionError(f"failed to stat repository file {path}") from err


def _mkdir_repo(path: Path, *, parents: bool = False) -> None:
    try:
        path.mkdir(parents=parents)
    except OSError as err:
        raise AssertionError(f"failed to create directory {path}") from err


def _write_repo_text(path: Path, text: str) -> None:
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as err:
        raise AssertionError(f"failed to write file {path}") from err


def _write_repo_bytes(path: Path, data: bytes) -> None:
    try:
        path.write_bytes(data)
    except OSError as err:
        raise AssertionError(f"failed to write file {path}") from err


def _read_repo(relative: str) -> str:
    path = REPO / relative
    try:
        return path.read_text(encoding="utf-8")
    except OSError as err:
        raise AssertionError(f"failed to read repository file {path}") from err


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, **kwargs)
    except OSError as err:
        raise AssertionError(f"failed to run command {cmd!r}") from err


def _read_version() -> str:
    return _read_repo("VERSION").strip()


def test_version_file_is_semver() -> None:
    version = _read_version()
    parts = version.split(".")
    assert len(parts) == 3, version
    assert all(p.isdigit() for p in parts), version


def test_packaging_metadata_files_exist() -> None:
    for rel in PACKAGING_PATHS + RELEASE_SCRIPTS + DOCKER_RELEASE_IMAGES:
        _assert_repo_file(rel)


def test_release_scripts_are_executable() -> None:
    for rel in RELEASE_SCRIPTS:
        mode = _repo_stat(rel).st_mode
        assert mode & stat.S_IXUSR, rel


def test_read_version_script_matches_version_file() -> None:
    out = _run(
        ["python3", "scripts/read-version.py"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    assert out.stdout.strip() == _read_version()


def test_check_workflow_has_all_packaging_jobs() -> None:
    workflow = _read_repo(".github/workflows/check.yml")
    cell = _read_repo("scripts/ci-packaging-cell.sh")
    pipeline = _read_repo("scripts/ci-pipeline.sh")
    matrix = _read_repo("scripts/ci-packaging-matrix.sh")
    assert "  packaging:" in workflow
    assert "strategy:" in workflow
    assert 'ci-packaging-cell.sh "${{ matrix.format }}"' in workflow
    assert "ci-packaging-matrix.sh" in pipeline
    assert "ci-packaging-cell.sh" in matrix
    for fmt in CI_PACKAGING_FORMATS:
        assert fmt in workflow, fmt
        assert fmt in matrix, fmt
        assert fmt in cell, fmt
    assert "packaging-smoke-verify.sh" in cell
    assert "packaging-e2e-install.sh" in cell
    assert "-e UH_SKIP_ONBOARD=1" in cell
    assert 'KIND}" != "snap"' in cell or '"${KIND}" != "snap"' in cell
    smoke_idx = cell.index('packaging-smoke-verify.sh "${FORMAT}"')
    e2e_idx = cell.index('packaging-e2e-install.sh "${FORMAT}"')
    assert e2e_idx > smoke_idx


def test_ci_packaging_matrix_runs_parallel() -> None:
    text = _read_repo("scripts/ci-packaging-matrix.sh")
    assert "UH_PACKAGING_ARTIFACTS_ISOLATE=1" in text
    assert "sequentially" not in text.lower()
    assert "pkill" not in text
    # Each cell is backgrounded; PIDs are collected then waited on.
    assert ') >"${log}" 2>&1 &' in text
    assert 'PIDS+=("$!")' in text
    assert 'wait "${pid}"' in text
    for fmt in CI_PACKAGING_FORMATS:
        assert fmt in text, fmt


def test_package_gtk_onboard_honors_skip_env() -> None:
    text = _read_repo("scripts/package-gtk-onboard.sh")
    assert '[[ "${UH_SKIP_ONBOARD:-0}" == "1" ]]' in text
    assert "Setup wizard skipped (UH_SKIP_ONBOARD=1)" in text
    # Isolated execution: skip path returns 0 without launching the wizard.
    out = _run(
        ["bash", "-c", "UH_SKIP_ONBOARD=1; . scripts/package-gtk-onboard.sh; uh_package_gtk_onboard"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "UH_SKIP_ONBOARD=1" in out.stdout
    assert "Launching Ubuntu Hello setup wizard" not in out.stdout


def test_ci_snap_build_runs_e2e() -> None:
    text = _read_repo("scripts/ci-snap-build.sh")
    assert '-e UH_SKIP_ONBOARD=1 "${CONTAINER}"' in text or '-e UH_SKIP_ONBOARD=1' in text
    assert "bash ./scripts/packaging-e2e-install.sh snap" in text
    # Propagation: docker exec carries UH_SKIP_ONBOARD into the E2E script.
    exec_block = text[text.index("Snap packaging E2E") :]
    assert "-e UH_SKIP_ONBOARD=1" in exec_block
    assert "packaging-e2e-install.sh snap" in exec_block


def test_packaging_e2e_script_covers_all_formats() -> None:
    text = _read_repo("scripts/packaging-e2e-install.sh")
    for fmt in CI_PACKAGING_FORMATS:
        assert f"\t{fmt})" in text or f"\n\t{fmt})" in text, fmt
    assert "uh_e2e_run_cycle uh_e2e_deb_install uh_e2e_deb_remove" in text
    assert "uh_e2e_run_cycle uh_e2e_rpm_fedora_install uh_e2e_rpm_fedora_remove" in text
    assert "uh_e2e_run_cycle uh_e2e_rpm_opensuse_install uh_e2e_rpm_opensuse_remove" in text
    assert "uh_e2e_run_cycle uh_e2e_arch_install uh_e2e_arch_remove" in text
    assert "uh_e2e_run_cycle uh_e2e_snap_install_host uh_e2e_snap_remove_host_and_snap" in text
    assert "uh_e2e_run_cycle uh_e2e_flatpak_install uh_e2e_flatpak_remove" in text
    assert "uh_e2e_assert_config_restored" in text
    assert "uh_e2e_assert_config_preserved" in text
    assert "uh_e2e_mark_live_config" in text
    assert "E2E upgrade in place" in text
    assert "uh_e2e_assert_removed" in text
    assert "pam_ubuntu_hello.so still present after remove" in text
    # Same-version upgrade paths for native packages / host overlays.
    assert "apt-get install -y --reinstall" in text
    assert "dnf reinstall -y --nogpgcheck" in text
    assert "install --force --allow-unsigned-rpm" in text
    assert "re-running install-host.sh (upgrade)" in text
    # RPM (Fedora/openSUSE) installs PAM under /usr/lib64/security.
    assert "/usr/lib64/security/pam_ubuntu_hello.so" in text
    # Flatpak must install the .flatpak bundle (not build-flatpak/files shortcut).
    assert "build-flatpak/files" not in text
    assert "flatpak install --user" in text
    assert "flatpak install --system" in text
    assert "FLATPAK_SCOPE" in text
    assert "flatpak uninstall --user" in text
    assert "flatpak uninstall --system" in text
    # ART from release-common is absolute (/src/artifacts); never prefix with ./
    assert './${ART}/' not in text
    assert 'compgen -G "${ART}/' in text
    # AppImage prepare must not pollute stdout (command substitution captures the prefix).
    assert 'echo ">>> Extracting AppImage to ${extract_dir}" >&2' in text
    assert "--allow-unsigned-rpm" in text
    assert "--nogpgcheck" in text


def test_release_workflow_exists() -> None:
    text = _read_repo(".github/workflows/release.yml")
    for job in (
        "build-deb",
        "build-rpm-fedora",
        "build-rpm-opensuse",
        "build-arch",
        "build-snap",
        "build-appimage",
        "build-flatpak",
        "github-release",
    ):
        assert job in text, job
    assert "SHA256SUMS" in text
    assert "ppa-release.yml" not in text
    assert "release-verify-tag-version.sh" in text


def test_pkgbuild_reads_version_via_script() -> None:
    pkgbuild = _read_repo("packaging/arch/ubuntu-hello/PKGBUILD")
    assert "read-version.py" in pkgbuild
    assert "release-common.sh" in pkgbuild


def test_shared_configure_installed_by_meson() -> None:
    meson = _read_repo("ubuntu-hello/src/meson.build")
    for name in (
        "package-configure.sh",
        "package-gtk-onboard.sh",
        "package-prerm.sh",
        "install-host.sh",
        "ubuntu-hello.install",
        "ubuntu-hello-gtk.install",
        "UbuntuHello.host.policy",
    ):
        assert name in meson


def test_flatpak_finish_args_are_minimal() -> None:
    manifest = _read_repo("packaging/flatpak/com.github.ventura8.UbuntuHello.yml")
    assert "--filesystem=host" not in manifest
    assert "--device=all" not in manifest
    assert "PolicyKit1" in manifest


def test_package_configure_ensures_dlib_via_pip() -> None:
    """When dlib is missing on the live host, configure installs a pinned pip build."""
    text = _read_repo("scripts/package-configure.sh")
    assert "uh_ensure_dlib" in text
    assert "CMAKE_POLICY_VERSION_MINIMUM" in text
    assert "uh_check_dlib" not in text
    assert "UH_DLIB_PIP_SPEC" in text or "dlib==19.24.2" in text
    assert "UH_DLIB_PIP_MARKER" in text or ".dlib-pip-installed" in text
    assert "pip_cmd" in text or 'pip3' in text
    assert "import dlib" in text


def test_packaging_e2e_requires_dlib() -> None:
    text = _read_repo("scripts/packaging-e2e-install.sh")
    assert "import dlib" in text
    assert "dlib not importable" in text


def test_install_host_ignores_bundle_root_env() -> None:
    text = _read_repo("packaging/flatpak/install-host.sh")
    assert "UH_BUNDLE_ROOT" not in text
    assert "uh_resolve_destdir_root" in text
    assert "ubuntu-hello.install" in text


def test_appimage_pkexec_invokes_install_host_directly() -> None:
    text = _read_repo("packaging/appimage/build-appimage.sh")
    assert 'pkexec env UH_BUNDLE_ROOT' not in text
    assert 'pkexec "${INSTALL_SH}"' in text or "pkexec \"${INSTALL_SH}\"" in text


def test_packaging_jobs_skip_fork_pull_requests() -> None:
    workflow = _read_repo(".github/workflows/check.yml")
    # head.repo.fork reflects whether the repo hosting the PR branch is a
    # fork of some upstream — always true here, since this repo is itself a
    # fork of boltgolt/howdy, regardless of who opened the PR. Compare repo
    # identity instead to actually distinguish same-repo from external-fork
    # PRs (the untrusted-Docker/snapcraft/privileged-build concern below).
    # One packaging matrix job owns the fork-PR skip for all formats.
    assert "  packaging:" in workflow
    assert "pull_request.head.repo.full_name == github.repository" in workflow
    assert workflow.count("pull_request.head.repo.full_name == github.repository") >= 1


def test_release_docs_exist_for_current_version() -> None:
    version = _read_version()
    _assert_repo_file(f"docs/releases/v{version}.md")
    _assert_repo_file(f"docs/releases/v{version}_github_description.md")


def test_debian_changelog_top_matches_version() -> None:
    version = _read_version()
    changelog = _read_repo("debian/changelog")
    assert changelog.startswith(f"ubuntu-hello ({version}-1ppa1)")


def test_docker_release_images_use_pinned_bases() -> None:
    for rel in DOCKER_RELEASE_IMAGES:
        text = _read_repo(rel)
        assert "latest" not in text.lower(), rel
        assert "FROM " in text, rel
        if rel == "docker/Dockerfile.ppa":
            assert "FROM ubuntu:26.04" in text, rel
        if rel == "docker/Dockerfile.arch":
            assert "FROM archlinux:base-devel-" in text, rel
            assert "archive-" not in text, rel


@pytest.mark.parametrize("fmt,files", [
    ("deb", ["ubuntu-hello_{v}_1ppa1_amd64.deb", "ubuntu-hello-gtk_{v}_1ppa1_all.deb"]),
    ("rpm-fedora", [
        "ubuntu-hello-{v}-1.fc44.x86_64.rpm",
        "ubuntu-hello-gtk-{v}-1.fc44.x86_64.rpm",
    ]),
    ("rpm-opensuse", [
        "ubuntu-hello-{v}-1.lp160.x86_64.rpm",
        "ubuntu-hello-gtk-{v}-1.lp160.x86_64.rpm",
    ]),
    ("arch", [
        "ubuntu-hello-{v}-1-x86_64.pkg.tar.zst",
        "ubuntu-hello-gtk-{v}-1-x86_64.pkg.tar.zst",
    ]),
    ("snap", ["ubuntu-hello_{v}_amd64.snap"]),
    ("appimage", ["Ubuntu-Hello-{v}-x86_64.AppImage"]),
    ("flatpak", ["com.github.ventura8.UbuntuHello-{v}-x86_64.flatpak"]),
])
def test_packaging_smoke_verify_passes(tmp_path: Path, fmt: str, files: list[str]) -> None:
    version = _read_version()
    art = tmp_path / "artifacts"
    _mkdir_repo(art)
    for pattern in files:
        _write_repo_text(art / pattern.format(v=version), "mock")
    env = {**os.environ, "UH_ARTIFACTS_DIR": str(art)}
    _run(
        ["bash", "scripts/packaging-smoke-verify.sh", fmt],
        cwd=REPO,
        env=env,
        check=True,
    )


def test_packaging_smoke_verify_fails_when_missing(tmp_path: Path) -> None:
    art = tmp_path / "artifacts"
    _mkdir_repo(art)
    env = {**os.environ, "UH_ARTIFACTS_DIR": str(art)}
    with pytest.raises(subprocess.CalledProcessError):
        _run(
            ["bash", "scripts/packaging-smoke-verify.sh", "deb"],
            cwd=REPO,
            env=env,
            check=True,
        )


def test_shellcheck_on_packaging_scripts() -> None:
    if _run(["which", "shellcheck"], capture_output=True).returncode != 0:
        pytest.skip("shellcheck not installed")
    paths = [str(REPO / rel) for rel in SHELLCHECK_PATHS]
    _run(["shellcheck", *paths], check=True)


def test_ci_matrix_runs_parallel_with_parallel_build_flag() -> None:
    text = _read_repo("scripts/ci-matrix.sh")
    assert "UH_CI_PARALLEL_BUILD=1" in text
    assert "sequentially" not in text.lower()
    assert "pkill" not in text
    docker_sh = _read_repo("scripts/ci-docker.sh")
    assert "build_ci_image_parallel" in docker_sh
    assert "docker build (parallel matrix, no buildx)" in docker_sh


def test_check_yml_cancels_stale_runs_on_new_push() -> None:
    check_yml = _read_repo(".github/workflows/check.yml")
    assert "cancel-in-progress: true" in check_yml
    assert "concurrency:" in check_yml
    assert "github.event.pull_request.number" in check_yml


def test_check_yml_runs_all_jobs_in_parallel() -> None:
    """GHA check.yml should saturate OSS 20-runner concurrency (no needs gates)."""
    check_yml = _read_repo(".github/workflows/check.yml")
    assert "needs:" not in check_yml
    assert check_yml.count("max-parallel: 20") >= 2
    # 4 job definitions (lint, coverage, compat, packaging); matrices expand to
    # 1 + 1 + 8 DE + 7 packaging = 17 concurrent runners.
    job_defs = check_yml.count("runs-on: ubuntu-26.04")
    assert job_defs == 4
    assert check_yml.count("de: [baseline") == 1
    assert "  packaging:" in check_yml
    assert job_defs - 2 + 8 + 7 == 17


def test_arch_pkgbuild_skips_inih_subproject_option() -> None:
    """Arch system libinih provides INIReader; -Dinih:with_INIReader breaks meson."""
    pkgbuild = _read_repo("packaging/arch/ubuntu-hello/PKGBUILD")
    assert "inih:with_INIReader" not in pkgbuild
    assert "meson setup build --prefix=/usr" in pkgbuild
    common = _read_repo("scripts/release-common.sh")
    assert 'pkg-config --exists INIReader' in common
    # Working-tree tarball (not HEAD via git-archive) so packaging sees bind-mount fixes.
    assert "uh_create_source_tarball" in common
    assert "git archive" not in common
    assert "ubuntu-hello-${version}" in common
    arch = _read_repo("scripts/release-arch.sh")
    assert "BUILDDIR=" in arch
    assert "ARCH_MAKEPKG_ROOT" in arch
    assert 's/^pkgver=.*/pkgver=${VERSION}/' in arch or "pkgver=${VERSION}" in arch
    # Single explicit tarball copy into makepkg root (not also via multi-file cp).
    assert 'cp "${TARBALL}" "${ARCH_MAKEPKG_ROOT}/ubuntu-hello-${VERSION}.tar.gz"' in arch
    assert 'cp "${TARBALL}" "${PKGDIR}/PKGBUILD"' not in arch


def test_deb_clean_safe_for_parallel_packaging() -> None:
    """Deb clean / Flatpak state must not race the shared packaging bind mount."""
    rules = _read_repo("debian/rules")
    assert "override_dh_clean" in rules
    assert "dh_auto_clean --buildsystem=meson" in rules
    portable = _read_repo("scripts/release-portable.sh")
    assert "UH_ARTIFACTS_DIR}/.flatpak-work" in portable
    assert "--state-dir=" in portable
    assert 'UH_REPO_ROOT}/build-flatpak"' not in portable
    common = _read_repo("scripts/release-common.sh")
    assert "--warning=no-file-changed" in common
    assert "./.flatpak-builder" in common
    assert "./obj-*" in common
    dockerignore = _read_repo(".dockerignore")
    assert ".flatpak-builder" in dockerignore


def test_snap_libdir_and_ci_wiring() -> None:
    """Snap must not prime Fedora-style usr/lib64 (breaks install-host usr/lib/*/ globs)."""
    text = _read_repo("packaging/snap/snapcraft.yaml")
    assert "--libdir=lib/x86_64-linux-gnu" in text
    assert "--libdir=lib64" not in text

    revision = _read_repo("packaging/snap/SNAPCRAFT_REVISION").strip()
    assert revision.isdigit(), revision
    # Snap builds go through ci-snap-build.sh (snapd needs a real systemd PID 1).
    # check.yml reaches it via ci-packaging-cell.sh; release.yml may call it directly.
    ci_snap_build = _read_repo("scripts/ci-snap-build.sh")
    assert "docker/Dockerfile.snap" in ci_snap_build
    assert "latest/stable" not in ci_snap_build
    assert "ARTIFACT_EXTRA_MOUNTS" in ci_snap_build
    snap_df = _read_repo("docker/Dockerfile.snap")
    # Host E2E model download needs curl/wget inside the snap systemd image.
    assert "curl" in snap_df
    assert "bzip2" in snap_df
    cell = _read_repo("scripts/ci-packaging-cell.sh")
    assert "ci-snap-build.sh" in cell
    assert "ARTIFACT_EXTRA_MOUNTS" in cell
    check_yml = _read_repo(".github/workflows/check.yml")
    assert "ci-packaging-cell.sh" in check_yml
    assert "latest/stable" not in check_yml
    release_yml = _read_repo(".github/workflows/release.yml")
    assert "ci-snap-build.sh" in release_yml
    assert "latest/stable" not in release_yml
    entrypoint = _read_repo("docker/snap-entrypoint.sh")
    assert "--revision=" in entrypoint
    assert "SNAPCRAFT_REVISION" in entrypoint
    skill = _read_repo(".agents/skills/release-packaging/SKILL.md")
    assert "SNAPCRAFT_REVISION" in skill
    assert "Dockerfile.snap" in skill
    assert "latest/stable" not in skill


def test_split_install_copy_with_glob(tmp_path: Path) -> None:
    fakeroot = tmp_path / "fakeroot"
    pkgdir = tmp_path / "pkg"
    lib_dir = fakeroot / "usr" / "lib" / "x86_64-linux-gnu" / "security"
    _mkdir_repo(lib_dir, parents=True)
    _write_repo_bytes(lib_dir / "pam_ubuntu_hello.so", b"mock")
    list_file = tmp_path / "minimal.install"
    _write_repo_text(list_file, "usr/lib/*/security/pam_ubuntu_hello.so\n")
    env = {**os.environ, "UH_REPO_ROOT": str(REPO)}
    _run(
        [
            "bash",
            "scripts/test-split-install-adapter.sh",
            str(fakeroot.resolve()),
            str(pkgdir.resolve()),
            str(list_file.resolve()),
        ],
        cwd=REPO,
        env=env,
        check=True,
    )
    assert (pkgdir / "usr/lib/x86_64-linux-gnu/security/pam_ubuntu_hello.so").is_file()


def test_split_install_fails_on_missing_literal(tmp_path: Path) -> None:
    fakeroot = tmp_path / "fakeroot"
    pkgdir = tmp_path / "pkg"
    _mkdir_repo(fakeroot)
    env = {**os.environ, "UH_REPO_ROOT": str(REPO)}
    with pytest.raises(subprocess.CalledProcessError):
        _run(
            [
                "bash",
                "scripts/test-split-install-adapter.sh",
                str(fakeroot.resolve()),
                str(pkgdir.resolve()),
                "packaging/file-lists/ubuntu-hello.install",
            ],
            cwd=REPO,
            env=env,
            check=True,
        )


def test_check_yml_runs_smoke_verify_for_every_packaging_format() -> None:
    workflow = _read_repo(".github/workflows/check.yml")
    cell = _read_repo("scripts/ci-packaging-cell.sh")
    assert 'ci-packaging-cell.sh "${{ matrix.format }}"' in workflow
    assert "packaging-smoke-verify.sh" in cell
    assert "packaging-e2e-install.sh" in cell
    assert "-e UH_SKIP_ONBOARD=1" in cell
    for fmt in CI_PACKAGING_FORMATS:
        assert fmt in workflow, fmt
        assert fmt in cell, fmt


def test_packaging_installers_complete() -> None:
    """Build meson tree and run every installer hook (install-host, snap, deb-style configure)."""
    if _run(["which", "meson"], capture_output=True).returncode != 0:
        pytest.skip("meson not installed")
    if _run(["which", "ninja"], capture_output=True).returncode != 0:
        pytest.skip("ninja not installed")
    _run(
        ["bash", "scripts/test-packaging-installers.sh"],
        cwd=REPO,
        check=True,
        timeout=900,
    )
