#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════╗
# ║                    Ubuntu Hello — Installer                      ║
# ║    Windows Hello™ style facial authentication for Ubuntu         ║
# ╚══════════════════════════════════════════════════════════════════╝
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/ventura8/ubuntu-hello/master/install.sh | sudo bash
#
# Or clone first, then run:
#   git clone https://github.com/ventura8/ubuntu-hello.git
#   cd ubuntu-hello
#   sudo bash install.sh

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
NC='\033[0m' # No Color

CHECKMARK="${GREEN}✔${NC}"
CROSSMARK="${RED}✘${NC}"
ARROW="${CYAN}➜${NC}"

banner() {
    echo ""
    echo -e "${MAGENTA}${BOLD}"
    echo "  ╔══════════════════════════════════════════════════════════╗"
    echo "  ║              Ubuntu Hello — Installer                   ║"
    echo "  ║      Facial Authentication for Ubuntu Linux             ║"
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

# Must be root
if [ "$EUID" -ne 0 ]; then
    fail "This installer must be run as root. Please use: sudo bash install.sh"
fi

# Must be Ubuntu/Debian
if ! command -v apt-get &>/dev/null; then
    fail "This installer requires apt-get (Ubuntu/Debian). Your system is not supported."
fi

# Detect distro
if [ -f /etc/os-release ]; then
    . /etc/os-release
    success "Detected: ${PRETTY_NAME:-$ID}"
else
    warn "Could not detect distribution, proceeding anyway..."
fi

# Detect the real non-root user who invoked sudo
REAL_USER="${SUDO_USER:-}"
if [ -z "$REAL_USER" ] || [ "$REAL_USER" = "root" ]; then
    REAL_USER=$(loginctl list-sessions 2>/dev/null | awk 'NR>1 && $3 != "" && $3 != "root" {print $3; exit}' || true)
fi
if [ -z "$REAL_USER" ] || [ "$REAL_USER" = "root" ]; then
    for d in /home/*/; do
        d="${d%/}"
        u="${d##*/}"
        if [ "$u" != "lost+found" ]; then
            REAL_USER="$u"
            break
        fi
    done
fi
if [ -n "$REAL_USER" ]; then
    success "Installing for user: ${BOLD}$REAL_USER${NC}"
else
    warn "Could not detect desktop user — GUI won't auto-launch after install."
fi

# ─────────────────────────────────────────────────────────────────────
# Step 1: Install system dependencies
# ─────────────────────────────────────────────────────────────────────
step "Installing system dependencies"

export DEBIAN_FRONTEND=noninteractive

apt-get update -qq

apt-get install -y -qq \
    python3 python3-pip python3-dev python3-setuptools python3-wheel \
    python3-numpy python3-opencv python3-gi python3-gi-cairo \
    gir1.2-gtk-3.0 \
    cmake make build-essential g++ \
    libpam0g-dev libinih-dev libevdev-dev libopencv-dev \
    libboost-all-dev pkg-config \
    meson ninja-build \
    git curl wget bzip2 \
    v4l-utils \
    libopenblas-dev liblapack-dev \
    tpm2-tools \
    2>&1 | tail -1

success "All system dependencies installed"

# ─────────────────────────────────────────────────────────────────────
# Step 2: Get the source code
# ─────────────────────────────────────────────────────────────────────
step "Preparing source code"

REPO_URL="https://github.com/ventura8/ubuntu-hello.git"
INSTALL_FROM_CLONE=false

# Check if we're already inside the repo
if [ -f "meson.build" ] && grep -q "ubuntu-hello" meson.build 2>/dev/null; then
    SOURCE_DIR="$(pwd)"
    success "Using existing source directory: $SOURCE_DIR"
else
    SOURCE_DIR=$(mktemp -d /tmp/ubuntu-hello-build-XXXXXX)
    echo -e "  Cloning repository..."
    git clone --depth 1 "$REPO_URL" "$SOURCE_DIR" 2>&1 | tail -1
    INSTALL_FROM_CLONE=true
    success "Source cloned to $SOURCE_DIR"
fi

# ─────────────────────────────────────────────────────────────────────
# Step 3: Install Python dependencies (dlib)
# ─────────────────────────────────────────────────────────────────────
step "Installing Python dependencies (dlib — this may take a few minutes)"

# Determine pip flags for system-managed Python (Ubuntu 23.04+)
PIP_FLAGS=""
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
    PIP_FLAGS="--break-system-packages"
fi

# Install dlib via pip (compiles from source with cmake)
if python3 -c "import dlib" 2>/dev/null; then
    success "dlib is already installed"
else
    echo -e "  ${YELLOW}Building dlib from source — this can take 2-5 minutes...${NC}"
    pip3 install dlib $PIP_FLAGS 2>&1 | tail -3
    success "dlib installed successfully"
fi

# Install face_recognition_models if not present
if ! python3 -c "import face_recognition_models" 2>/dev/null; then
    pip3 install face_recognition_models $PIP_FLAGS 2>/dev/null || true
fi

success "Python dependencies ready"

# ─────────────────────────────────────────────────────────────────────
# Step 4: Build Ubuntu Hello with Meson
# ─────────────────────────────────────────────────────────────────────
step "Building Ubuntu Hello"

BUILD_DIR="$SOURCE_DIR/builddir"

# Clean previous build if it exists
if [ -d "$BUILD_DIR" ]; then
    rm -rf "$BUILD_DIR"
fi

meson setup "$BUILD_DIR" "$SOURCE_DIR" \
    -Dprefix=/usr \
    -Dsysconfdir=/etc \
    -Dlibdir=lib \
    -Ddlib_data_dir=/etc/ubuntu-hello/dlib-data \
    -Dconfig_dir=/etc/ubuntu-hello \
    -Duser_models_dir=/etc/ubuntu-hello/models \
    -Dinstall_pam_config=true \
    -Dwith_polkit=true \
    -Dfetch_dlib_data=true \
    -Dinih:with_INIReader=true \
    2>&1 | tail -5

meson compile -C "$BUILD_DIR" 2>&1 | tail -5

success "Build completed"

# ─────────────────────────────────────────────────────────────────────
# Step 5: Install system-wide
# ─────────────────────────────────────────────────────────────────────
step "Installing to system"

meson install -C "$BUILD_DIR" 2>&1 | tail -5

success "Ubuntu Hello installed to system"

# ─────────────────────────────────────────────────────────────────────
# Step 6: Download dlib face recognition models
# ─────────────────────────────────────────────────────────────────────
step "Downloading face recognition models"

MODELS_DIR="/etc/ubuntu-hello/dlib-data"
mkdir -p "$MODELS_DIR"

MODELS=(
    "dlib_face_recognition_resnet_model_v1.dat"
    "mmod_human_face_detector.dat"
    "shape_predictor_5_face_landmarks.dat"
)
BASE_URL="https://github.com/davisking/dlib-models/raw/master"

all_present=true
for model in "${MODELS[@]}"; do
    if [ ! -f "$MODELS_DIR/$model" ]; then
        all_present=false
        break
    fi
done

if [ "$all_present" = true ]; then
    success "All face recognition models already present"
else
    for model in "${MODELS[@]}"; do
        if [ -f "$MODELS_DIR/$model" ]; then
            success "$model ✓"
            continue
        fi
        echo -e "  Downloading $model..."
        ARCHIVE="$MODELS_DIR/${model}.bz2"
        wget -q --tries=5 --show-progress -O "$ARCHIVE" "${BASE_URL}/${model}.bz2" 2>&1 || \
            curl -fsSL --retry 5 -o "$ARCHIVE" "${BASE_URL}/${model}.bz2"
        bunzip2 -f "$ARCHIVE"
        success "$model ✓"
    done
fi

# ─────────────────────────────────────────────────────────────────────
# Step 7: Configure PAM
# ─────────────────────────────────────────────────────────────────────
step "Configuring PAM authentication"

if [ -f /usr/share/pam-configs/ubuntu-hello ]; then
    pam-auth-update --package 2>/dev/null || true
    success "PAM configured for face authentication"
else
    warn "PAM config not found — you may need to configure PAM manually"
fi

# ─────────────────────────────────────────────────────────────────────
# Step 8: Set permissions
# ─────────────────────────────────────────────────────────────────────
step "Setting permissions"

# Ensure config directory is accessible
chmod 755 /etc/ubuntu-hello 2>/dev/null || true
chmod 755 /etc/ubuntu-hello/dlib-data 2>/dev/null || true

# Ensure models directory exists
mkdir -p /etc/ubuntu-hello/models
chmod 700 /etc/ubuntu-hello/models

# Ensure tpm keys directory exists
mkdir -p /etc/ubuntu-hello/tpm-keys
chmod 700 /etc/ubuntu-hello/tpm-keys

# Ensure log directory exists
mkdir -p /var/log/ubuntu-hello
chmod 755 /var/log/ubuntu-hello

success "Permissions set"

# ─────────────────────────────────────────────────────────────────────
# Step 9: Configure Polkit for App Center face auth
# ─────────────────────────────────────────────────────────────────────
step "Configuring Polkit"

OVERRIDE_DIR="/etc/systemd/system/polkit-agent-helper@.service.d"
mkdir -p "$OVERRIDE_DIR"
cat > "$OVERRIDE_DIR/override.conf" <<EOF
[Service]
PrivateDevices=no
DeviceAllow=char-video4linux rw
DeviceAllow=/dev/uinput rw
EOF
chmod 644 "$OVERRIDE_DIR/override.conf"
systemctl daemon-reload 2>/dev/null || true

success "Polkit configured for face authentication"

# ─────────────────────────────────────────────────────────────────────
# Step 10: Clean up build artifacts
# ─────────────────────────────────────────────────────────────────────
if [ "$INSTALL_FROM_CLONE" = true ] && [ -d "$SOURCE_DIR" ]; then
    step "Cleaning up"
    rm -rf "$SOURCE_DIR"
    success "Temporary build files removed"
fi

# ─────────────────────────────────────────────────────────────────────
# Step 11: Launch the GUI
# ─────────────────────────────────────────────────────────────────────
# Note: The GUI is already automatically launched by the meson post-install script run_after_install.py.
# So we do not need to launch it a second time here.


# ─────────────────────────────────────────────────────────────────────
# Done!
# ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}"
echo "  ╔══════════════════════════════════════════════════════════╗"
echo "  ║            ✔ Installation complete!                     ║"
echo "  ╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  ${BOLD}Next steps:${NC}"
echo -e "  ${ARROW} Run ${CYAN}ubuntu-hello-gtk${NC} to open the settings GUI and run the setup wizard"
echo -e "  ${ARROW} Once setup is complete, try ${CYAN}sudo -i${NC} to test face authentication"
echo ""
echo -e "  ${BOLD}To uninstall:${NC}"
echo -e "  ${ARROW} ${CYAN}curl -fsSL https://raw.githubusercontent.com/ventura8/ubuntu-hello/master/uninstall.sh | sudo bash${NC}"
echo ""
