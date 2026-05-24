#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════╗
# ║                   Ubuntu Hello — Uninstaller                     ║
# ║    Completely remove Ubuntu Hello from your system               ║
# ╚══════════════════════════════════════════════════════════════════╝
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/ventura8/ubuntu-hello/master/uninstall.sh | sudo bash
#
# Or from the repo:
#   sudo bash uninstall.sh

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────
# Colors & formatting
# ─────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

CHECKMARK="${GREEN}✔${NC}"
CROSSMARK="${RED}✘${NC}"
ARROW="${CYAN}➜${NC}"

banner() {
    echo ""
    echo -e "${RED}${BOLD}"
    echo "  ╔══════════════════════════════════════════════════════════╗"
    echo "  ║             Ubuntu Hello — Uninstaller                  ║"
    echo "  ╚══════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

step() {
    echo ""
    echo -e "  ${ARROW} ${BOLD}$1${NC}"
    echo -e "  ${BLUE}────────────────────────────────────────────────${NC}"
}

success() {
    echo -e "  ${CHECKMARK}  $1"
}

warn() {
    echo -e "  ${YELLOW}⚠  $1${NC}"
}

fail() {
    echo -e "  ${CROSSMARK}  ${RED}$1${NC}"
    exit 1
}

# ─────────────────────────────────────────────────────────────────────
# Pre-flight checks
# ─────────────────────────────────────────────────────────────────────
banner

if [ "$EUID" -ne 0 ]; then
    fail "This uninstaller must be run as root. Please use: sudo bash uninstall.sh"
fi

# ─────────────────────────────────────────────────────────────────────
# Confirmation prompt (skip if piped)
# ─────────────────────────────────────────────────────────────────────
if [ -t 0 ]; then
    echo -e "  ${YELLOW}${BOLD}This will completely remove Ubuntu Hello from your system.${NC}"
    echo -e "  ${YELLOW}This includes all face models, configuration, and data files.${NC}"
    echo ""
    read -p "  Are you sure? [y/N] " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "  ${CYAN}Uninstallation cancelled.${NC}"
        exit 0
    fi
fi

# ─────────────────────────────────────────────────────────────────────
# Step 1: Remove PAM configuration
# ─────────────────────────────────────────────────────────────────────
step "Removing PAM configuration"

if [ -f /usr/share/pam-configs/ubuntu-hello ]; then
    rm -f /usr/share/pam-configs/ubuntu-hello
    pam-auth-update --package 2>/dev/null || true
    success "PAM configuration removed"
else
    success "PAM configuration already absent"
fi

# ─────────────────────────────────────────────────────────────────────
# Step 2: Remove Polkit configuration
# ─────────────────────────────────────────────────────────────────────
step "Removing Polkit configuration"

# Polkit policy file
rm -f /usr/share/polkit-1/actions/com.github.ventura8.ubuntu-hello-gtk.policy 2>/dev/null || true

# Systemd override
OVERRIDE_DIR="/etc/systemd/system/polkit-agent-helper@.service.d"
if [ -d "$OVERRIDE_DIR" ]; then
    rm -f "$OVERRIDE_DIR/override.conf"
    rmdir "$OVERRIDE_DIR" 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true
    success "Polkit override removed"
else
    success "Polkit override already absent"
fi

# ─────────────────────────────────────────────────────────────────────
# Step 3: Remove installed binaries and libraries
# ─────────────────────────────────────────────────────────────────────
step "Removing installed files"

# Binaries
rm -f /usr/bin/ubuntu-hello 2>/dev/null || true
rm -f /usr/bin/ubuntu-hello-gtk 2>/dev/null || true
rm -f /usr/local/bin/ubuntu-hello 2>/dev/null || true
rm -f /usr/local/bin/ubuntu-hello-gtk 2>/dev/null || true
success "Binaries removed"

# Python modules / lib directories
rm -rf /usr/lib/ubuntu-hello 2>/dev/null || true
rm -rf /usr/lib/ubuntu-hello-gtk 2>/dev/null || true
rm -rf /lib/security/ubuntu-hello 2>/dev/null || true
success "Libraries removed"

# PAM shared object
find /usr/lib/ /lib/ -name "pam_ubuntu_hello.so" -delete 2>/dev/null || true
success "PAM module removed"

# Desktop file
rm -f /usr/share/applications/ubuntu-hello-gtk.desktop 2>/dev/null || true
success "Desktop entry removed"

# Pixmap / icon
rm -f /usr/share/pixmaps/ubuntu-hello-gtk.png 2>/dev/null || true
success "Icon removed"

# Data directories
rm -rf /usr/share/ubuntu-hello 2>/dev/null || true
rm -rf /usr/share/ubuntu-hello-gtk 2>/dev/null || true
success "Data files removed"

# Man page
rm -f /usr/share/man/man1/ubuntu-hello.1 2>/dev/null || true
rm -f /usr/share/man/man1/ubuntu-hello.1.gz 2>/dev/null || true
success "Man page removed"

# Bash completion
rm -f /usr/share/bash-completion/completions/ubuntu-hello 2>/dev/null || true
success "Bash completion removed"

# ─────────────────────────────────────────────────────────────────────
# Step 4: Remove configuration & data
# ─────────────────────────────────────────────────────────────────────
step "Removing configuration and data"

if [ -d /etc/ubuntu-hello ]; then
    rm -rf /etc/ubuntu-hello
    success "Configuration directory removed (/etc/ubuntu-hello)"
else
    success "Configuration directory already absent"
fi

# Log directory
if [ -d /var/log/ubuntu-hello ]; then
    rm -rf /var/log/ubuntu-hello
    success "Log directory removed"
else
    success "Log directory already absent"
fi

# Keyring keys
rm -rf /etc/ubuntu-hello/keyring-keys 2>/dev/null || true

# ─────────────────────────────────────────────────────────────────────
# Step 5: Uninstall dlib Python package
# ─────────────────────────────────────────────────────────────────────
step "Uninstalling Python packages"

PIP_FLAGS=""
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
    PIP_FLAGS="--break-system-packages"
fi

if python3 -c "import dlib" 2>/dev/null; then
    pip3 uninstall dlib -y $PIP_FLAGS 2>/dev/null || true
    success "dlib uninstalled"
else
    success "dlib was not installed via pip"
fi

if python3 -c "import face_recognition_models" 2>/dev/null; then
    pip3 uninstall face_recognition_models -y $PIP_FLAGS 2>/dev/null || true
    success "face_recognition_models uninstalled"
else
    success "face_recognition_models was not installed via pip"
fi

# ─────────────────────────────────────────────────────────────────────
# Done!
# ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}"
echo "  ╔══════════════════════════════════════════════════════════╗"
echo "  ║          ✔ Ubuntu Hello has been removed                ║"
echo "  ╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  All files, configuration, face models, and data have been removed."
echo -e "  System dependencies (python3, opencv, etc.) were ${BOLD}not${NC} removed"
echo -e "  as they may be used by other programs."
echo ""
echo -e "  ${ARROW} To reinstall: ${CYAN}curl -fsSL https://raw.githubusercontent.com/ventura8/ubuntu-hello/master/install.sh | sudo bash${NC}"
echo ""
