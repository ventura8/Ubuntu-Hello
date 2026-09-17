"""Booted-OS tier: a real Ubuntu, Fedora or Arch boots under QEMU/KVM and scenario
scripts run inside it.

Containers cannot host what this tier covers -- PAM as a login path, the polkit
helper's systemd sandbox, a TPM, reboot persistence, and package install, upgrade
and remove on a real root -- which is where every defect that only manual testing
found during v1.2.0 lived. The driver is scripts/vm/vm.sh; the scenarios under
scripts/vm/scenarios/ each print one JSON result, and the tests here read those.

Run with (needs /dev/kvm, qemu-system-x86, swtpm, cloud-image-utils; ~15 min per distro):
    UH_VM=1 pytest tests/vm/
    UH_VM=1 UH_VM_DISTROS="ubuntu fedora arch" UH_VM_PKG_DIR=<packages> pytest tests/vm/

Every test runs once per distro in UH_VM_DISTROS (default: ubuntu). Fedora and
Arch have no PPA, so they need UH_VM_PKG_DIR pointing at packages built from the
working tree (scripts/ci-packaging-cell.sh rpm-fedora | arch); with several
distros, UH_VM_PKG_DIR may be a directory holding one subdirectory per distro.
UH_VM_RESULTS=<dir> reads results from a previous run instead of booting again.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

if os.environ.get("UH_VM") != "1":
	pytest.skip("requires UH_VM=1 (boots a virtual machine; needs KVM)", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "scripts" / "vm" / "vm.sh"
SCENARIOS = ("00-install", "10-config-migration", "20-pam-sudo", "25-greeter-pam", "30-polkit-sandbox",
             "40-reboot", "40-reboot.after-reboot", "90-remove", "95-upgrade-from-previous")
DISTROS = tuple(os.environ.get("UH_VM_DISTROS", "ubuntu").split())


def _results_dir(distro):
	override = os.environ.get("UH_VM_RESULTS")
	if override:
		return Path(override) / distro
	return Path(os.environ.get("UH_VM_DIR", ROOT / ".cache" / "vm")) / "results" / distro


def _package_dir(distro):
	"""UH_VM_PKG_DIR itself, or its per-distro subdirectory when there is one."""
	base = os.environ.get("UH_VM_PKG_DIR") or os.environ.get("UH_VM_DEB_DIR")
	if not base:
		return None
	sub = Path(base) / distro
	return str(sub if sub.is_dir() else base)


@pytest.fixture(scope="session", params=DISTROS)
def results(request):
	"""scenario name -> parsed JSON result for one distro, booting it once unless told to reuse."""
	distro = request.param
	directory = _results_dir(distro)
	if not os.environ.get("UH_VM_RESULTS"):
		for tool in ("qemu-system-x86_64", "swtpm", "cloud-localds"):
			if shutil.which(tool) is None:
				pytest.skip("missing " + tool)
		if not os.path.exists("/dev/kvm"):
			pytest.skip("no /dev/kvm")
		env = dict(os.environ, UH_VM_DISTRO=distro)
		packages = _package_dir(distro)
		if packages:
			env["UH_VM_PKG_DIR"] = packages
		elif distro != "ubuntu":
			pytest.skip(distro + " has no PPA; set UH_VM_PKG_DIR")
		subprocess.run([str(DRIVER), "prepare"], check=True, env=env)
		subprocess.run([str(DRIVER), "run"], check=True, env=env)
	out = {"distro": distro}
	for name in SCENARIOS:
		path = directory / f"{name}.json"
		if not path.is_file():
			out[name] = None
			continue
		text = path.read_text(encoding="utf-8").strip()
		try:
			out[name] = json.loads(text.splitlines()[-1]) if text else None
		except json.JSONDecodeError:
			out[name] = None
	return out


def checks(results, scenario):
	data = results.get(scenario)
	if data is None:
		log = _results_dir(results["distro"]) / f"{scenario}.log"
		tail = log.read_text(encoding="utf-8")[-2000:] if log.is_file() else "(no log)"
		pytest.fail("scenario %s on %s produced no result; log tail:\n%s" % (scenario, results["distro"], tail))
	return data["checks"], data.get("info", {})
