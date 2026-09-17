#!/usr/bin/env bash
# Booted-OS test tier: boot a clean cloud image (Ubuntu, Fedora or Arch) under
# QEMU/KVM with an emulated TPM, and run scenario scripts inside it over SSH.
#
# Containers cannot reach what this tier covers: a real init system, PAM as a
# login path, the polkit helper's systemd sandbox, a TPM, reboot persistence,
# and a package install/upgrade/remove cycle on a real root. Every defect that
# manual testing alone found during v1.2.0 lived in one of those layers.
#
#   scripts/vm/vm.sh prepare              download the image, build the seed
#   scripts/vm/vm.sh run [scenario ...]   boot, run scenarios (default: all), collect results
#   scripts/vm/vm.sh shell                boot and drop into an SSH shell
#   scripts/vm/vm.sh clean                remove the overlay and results (keeps the image)
#
# Environment:
#   UH_VM_DISTRO   ubuntu (default) | fedora | arch       UH_VM_CPUS / UH_VM_MEM_MB
#   UH_VM_SERIES   ubuntu: series (resolute); fedora: release (44); arch: latest
#   UH_VM_DIR      work dir (default: .cache/vm)           UH_VM_PPA (default: ppa:ventura8/ubuntu-hello)
#   UH_VM_KEEP=1   leave the VM running after the run      UH_VM_SSH_PORT (default: 2222/2223/2224 per distro)
#   UH_VM_REUSE=1  run scenarios against the guest left running by a previous UH_VM_KEEP=1
#   UH_VM_PKG_DIR  install these local packages (.deb/.rpm/.pkg.tar.zst built from the
#                  working tree) instead of the PPA's published build; dependencies still
#                  come from the distro's archive. Required for fedora and arch, which
#                  have no PPA. UH_VM_DEB_DIR is the older name for the same thing.
#   UH_VM_IMAGE_URL  override the cloud image to boot
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
DISTRO="${UH_VM_DISTRO:-ubuntu}"
EXTRA_WRITE_FILES=""
WORK="${UH_VM_DIR:-$REPO/.cache/vm}"
CPUS="${UH_VM_CPUS:-8}"
MEM="${UH_VM_MEM_MB:-8192}"
PPA="${UH_VM_PPA:-ppa:ventura8/ubuntu-hello}"
PKG_DIR="${UH_VM_PKG_DIR:-${UH_VM_DEB_DIR:-}}"
case "$DISTRO" in
	ubuntu)
		SERIES="${UH_VM_SERIES:-resolute}"; PORT="${UH_VM_SSH_PORT:-2222}"
		IMAGE_URL="https://cloud-images.ubuntu.com/${SERIES}/current/${SERIES}-server-cloudimg-amd64.img"
		GUEST_PACKAGES="openssh-server, jq, tpm2-tools, polkitd"
		SSH_UNIT=ssh ;;
	fedora)
		SERIES="${UH_VM_SERIES:-44}"; PORT="${UH_VM_SSH_PORT:-2223}"
		# The GA image name carries a compose number; -1.7 is Fedora 44's.
		IMAGE_URL="https://download.fedoraproject.org/pub/fedora/linux/releases/${SERIES}/Cloud/x86_64/images/Fedora-Cloud-Base-Generic-${SERIES}-1.7.x86_64.qcow2"
		GUEST_PACKAGES="openssh-server, jq, tpm2-tools, polkit"
		SSH_UNIT=sshd ;;
	arch)
		SERIES="${UH_VM_SERIES:-latest}"; PORT="${UH_VM_SSH_PORT:-2224}"
		IMAGE_URL="https://geo.mirror.pkgbuild.com/images/${SERIES}/Arch-Linux-x86_64-cloudimg.qcow2"
		GUEST_PACKAGES="openssh, jq, tpm2-tools, polkit, libarchive"
		SSH_UNIT=sshd
		# The default mirrorlist (fastly first) times out intermittently, and a
		# half-synced pacman db then silently drops runtime deps (libinih). Pin
		# the geo redirect plus two large mirrors, and let pacman retry downloads.
		EXTRA_WRITE_FILES=$'\n  - path: /etc/pacman.d/mirrorlist\n    content: |\n      Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch\n      Server = https://mirror.rackspace.com/archlinux/$repo/os/$arch\n      Server = https://america.mirror.pkgbuild.com/$repo/os/$arch' ;;
	*) echo "unknown UH_VM_DISTRO: $DISTRO" >&2; exit 2 ;;
esac
IMAGE_URL="${UH_VM_IMAGE_URL:-$IMAGE_URL}"

IMAGE="$WORK/$DISTRO-$SERIES-base.img"
OVERLAY="$WORK/$DISTRO-$SERIES-overlay.qcow2"
SEED="$WORK/$DISTRO-seed.iso"
KEY="$WORK/id_ed25519"
# Per-distro runtime state and a per-distro default SSH port, so the three
# guests can run side by side.
# UH_VM_TAG lets a second run of the same distro use its own state and socket
# paths, so a leftover swtpm (e.g. one an AppArmor profile makes unkillable)
# cannot collide with it.
_TAG="${UH_VM_TAG:+-$UH_VM_TAG}"
TPM_DIR="$WORK/$DISTRO$_TAG-tpm"
TPM_SOCK="$WORK/$DISTRO$_TAG-swtpm.sock"
PIDFILE="$WORK/$DISTRO$_TAG-qemu.pid"
SWTPM_PIDFILE="$WORK/$DISTRO$_TAG-swtpm.pid"
CONSOLE="$WORK/$DISTRO$_TAG-console.log"
RESULTS="$WORK/results/$DISTRO"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# -o Port= rather than -p: scp reads -p as "preserve times" and takes the number
# for a file name.
SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR
          -o ConnectTimeout=5 -o "Port=$PORT" -i "$KEY")
vssh() { ssh "${SSH_OPTS[@]}" tester@127.0.0.1 "$@"; }
vscp() { scp "${SSH_OPTS[@]}" "$@"; }

prepare() {
	mkdir -p "$WORK" "$RESULTS" "$TPM_DIR"
	if [ ! -f "$IMAGE" ]; then
		log "downloading $IMAGE_URL"
		curl -fL --progress-bar -o "$IMAGE.part" "$IMAGE_URL" && mv "$IMAGE.part" "$IMAGE"
	fi
	if [ ! -f "$KEY" ]; then
		ssh-keygen -q -t ed25519 -N "" -f "$KEY"
	fi
	local pubkey; pubkey="$(cat "$KEY.pub")"
	# Two users: `tester` drives the run (key login, passwordless sudo);
	# `alice` is the person whose logins the PAM scenarios exercise (password
	# "alice-pass", sudo *with* a password so the PAM stack really runs). An
	# explicit sudo rule rather than a group: the admin group is `sudo` on Ubuntu
	# and `wheel` elsewhere, and Arch ships the wheel rule commented out.
	local alice_hash; alice_hash="$(openssl passwd -6 'alice-pass')"
	cat > "$WORK/$DISTRO-user-data" <<EOF
#cloud-config
hostname: uh-vm
users:
  - name: tester
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys: ["$pubkey"]
  - name: alice
    shell: /bin/bash
    lock_passwd: false
    passwd: "$alice_hash"
    sudo: ALL=(ALL) ALL
ssh_pwauth: false
package_update: true
packages: [$GUEST_PACKAGES]
write_files:
  - path: /etc/ssh/sshd_config.d/99-uh.conf
    content: "PasswordAuthentication no\n"$EXTRA_WRITE_FILES
runcmd:
  - systemctl restart $SSH_UNIT
EOF
	printf 'instance-id: uh-vm-%s\nlocal-hostname: uh-vm\n' "$(date +%s)" > "$WORK/$DISTRO-meta-data"
	cloud-localds "$SEED" "$WORK/$DISTRO-user-data" "$WORK/$DISTRO-meta-data"
	log "seed built at $SEED"
}

fresh_overlay() {
	rm -f "$OVERLAY"
	qemu-img create -q -f qcow2 -b "$IMAGE" -F qcow2 "$OVERLAY" 40G
}

start_tpm() {
	# A fresh TPM every boot: each run installs onto a fresh overlay, so there is
	# nothing to carry over, and stale state made swtpm fail CMD_INIT.
	rm -f "$TPM_SOCK"
	rm -rf "$TPM_DIR"; mkdir -p "$TPM_DIR"
	swtpm socket --tpm2 --tpmstate "dir=$TPM_DIR" --ctrl "type=unixio,path=$TPM_SOCK" \
		--pid "file=$SWTPM_PIDFILE" --daemon
}

boot() {
	[ -f "$IMAGE" ] || die "no image; run: $0 prepare"
	[ -f "$OVERLAY" ] || fresh_overlay
	start_tpm
	: > "$CONSOLE"
	qemu-system-x86_64 -enable-kvm -cpu host -smp "$CPUS" -m "$MEM" \
		-drive "file=$OVERLAY,if=virtio,format=qcow2" \
		-drive "file=$SEED,if=virtio,format=raw,readonly=on" \
		-netdev "user,id=n0,hostfwd=tcp:127.0.0.1:$PORT-:22" -device virtio-net-pci,netdev=n0 \
		-chardev "socket,id=chrtpm,path=$TPM_SOCK" -tpmdev emulator,id=tpm0,chardev=chrtpm -device tpm-tis,tpmdev=tpm0 \
		-display none -serial "file:$CONSOLE" -daemonize -pidfile "$PIDFILE"
	log "qemu started (pid $(cat "$PIDFILE")); waiting for ssh on $PORT"
	local i
	for i in $(seq 1 90); do
		if vssh true 2>/dev/null; then
			log "guest is up"
			# cloud-init's package install may still be running; wait it out
			# (as root: Fedora keeps /run/cloud-init root-only).
			vssh 'sudo cloud-init status --wait >/dev/null 2>&1 || true'
			return 0
		fi
		sleep 2
	done
	die "guest never answered on ssh; see $CONSOLE"
}

reboot_guest() {
	log "rebooting guest"
	vssh 'sudo systemctl reboot' 2>/dev/null || true
	sleep 5
	local i
	for i in $(seq 1 90); do
		if vssh 'test "$(cat /proc/sys/kernel/random/boot_id)" != "'"${1:-}"'"' 2>/dev/null; then
			log "guest is back"
			return 0
		fi
		sleep 2
	done
	die "guest did not come back after reboot"
}

stop() {
	if [ -f "$PIDFILE" ]; then
		vssh 'sudo systemctl poweroff' 2>/dev/null || true
		local i
		for i in $(seq 1 30); do
			kill -0 "$(cat "$PIDFILE")" 2>/dev/null || break
			sleep 1
		done
		kill "$(cat "$PIDFILE")" 2>/dev/null || true
		rm -f "$PIDFILE"
	fi
	# Prefer swtpm's own control channel: an AppArmor profile can make swtpm
	# unkillable by signal from this shell, but it always honours a shutdown
	# on its control socket.
	swtpm_ioctl -s --unix "$TPM_SOCK" 2>/dev/null || true
	if [ -f "$SWTPM_PIDFILE" ]; then
		kill "$(cat "$SWTPM_PIDFILE")" 2>/dev/null || true
		rm -f "$SWTPM_PIDFILE"
	fi
}

run_scenario() {
	local script="$1" name
	name="$(basename "$script" .sh)"
	log "scenario $name"
	vscp "$script" "$HERE/lib.sh" "$HERE/pam_probe.py" "tester@127.0.0.1:/tmp/" >/dev/null
	local source="ppa"
	if [ -n "$PKG_DIR" ]; then
		vssh 'rm -rf /tmp/pkgs && mkdir -p /tmp/pkgs'
		vscp "${LOCAL_PKGS[@]}" "tester@127.0.0.1:/tmp/pkgs/" >/dev/null
		source="local"
	fi
	local rc=0
	vssh "UH_PPA='$PPA' UH_SERIES='$SERIES' UH_SOURCE='$source' bash /tmp/$(basename "$script")" > "$RESULTS/$name.json" 2> "$RESULTS/$name.log" || rc=$?
	if [ "$rc" -ne 0 ]; then
		log "scenario $name exited $rc (see $RESULTS/$name.log)"
	fi
	# A scenario that asks for a reboot gets one, then its post-reboot half runs.
	if [ -f "$HERE/scenarios/$name.after-reboot.sh" ]; then
		local boot_id; boot_id="$(vssh 'cat /proc/sys/kernel/random/boot_id')"
		reboot_guest "$boot_id"
		# /tmp is emptied by the reboot; the helper library goes over again.
		vscp "$HERE/scenarios/$name.after-reboot.sh" "$HERE/lib.sh" "tester@127.0.0.1:/tmp/" >/dev/null
		vssh "bash /tmp/$name.after-reboot.sh" > "$RESULTS/$name.after-reboot.json" 2>> "$RESULTS/$name.log" || true
	fi
	return 0
}

# The two packages to install from PKG_DIR: the main one and the GTK one, not
# the debuginfo/debug companions the RPM and Arch builds also produce.
local_packages() {
	case "$DISTRO" in
		ubuntu) ls "$PKG_DIR"/ubuntu-hello_*.deb; ls "$PKG_DIR"/ubuntu-hello-gtk_*.deb ;;
		fedora) ls "$PKG_DIR"/ubuntu-hello-[0-9]*.x86_64.rpm; ls "$PKG_DIR"/ubuntu-hello-gtk-[0-9]*.x86_64.rpm ;;
		arch)   ls "$PKG_DIR"/ubuntu-hello-[0-9]*.pkg.tar.zst; ls "$PKG_DIR"/ubuntu-hello-gtk-[0-9]*.pkg.tar.zst ;;
	esac
}

run() {
	mkdir -p "$RESULTS"
	if [ -n "$PKG_DIR" ]; then
		mapfile -t LOCAL_PKGS < <(local_packages 2>/dev/null)
		[ ${#LOCAL_PKGS[@]} -eq 2 ] || die "expected the main and the GTK package for $DISTRO in $PKG_DIR"
	elif [ "$DISTRO" != ubuntu ]; then
		die "$DISTRO has no PPA; point UH_VM_PKG_DIR at packages built from the working tree"
	fi
	if [ "${UH_VM_REUSE:-0}" = 1 ] && vssh true 2>/dev/null; then
		log "reusing the running guest"
	else
		rm -f "$RESULTS"/*.json "$RESULTS"/*.log
		fresh_overlay
		boot
	fi
	trap 'if [ "${UH_VM_KEEP:-0}" != 1 ]; then stop; fi' EXIT
	local scenarios=("$@")
	if [ ${#scenarios[@]} -eq 0 ]; then
		mapfile -t scenarios < <(ls "$HERE"/scenarios/[0-9][0-9]-*.sh | grep -v after-reboot | sort)
	else
		local resolved=() s
		for s in "${scenarios[@]}"; do
			resolved+=("$(ls "$HERE"/scenarios/*-"$s".sh 2>/dev/null | head -1 || true)")
		done
		scenarios=("${resolved[@]}")
	fi
	local s
	for s in "${scenarios[@]}"; do
		[ -n "$s" ] && run_scenario "$s"
	done
	log "results in $RESULTS"
	ls "$RESULTS"
}

case "${1:-}" in
	prepare) prepare ;;
	run) shift; run "$@" ;;
	shell) [ -f "$OVERLAY" ] || fresh_overlay; boot; vssh; ;;
	stop) stop ;;
	clean) stop; rm -rf "$OVERLAY" "$RESULTS" ;;
	*) sed -n '2,20p' "$0"; exit 2 ;;
esac
