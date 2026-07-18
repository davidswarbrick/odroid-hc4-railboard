#!/usr/bin/env bash
#
# install.sh — deploy railboard on the NAS (Debian/Armbian).
# Run from a checkout of the repo, as root:
#
#     sudo ./install.sh
#
# Idempotent: safe to re-run after `git pull` to upgrade.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_USER="${SUDO_USER:-${USER}}"
PIPX_HOME="/opt/pipx"
PIPX_BIN_DIR="/usr/local/bin"
CONF_DIR="/etc/railboard"
CONF="${CONF_DIR}/config.yaml"
ENV_FILE="${CONF_DIR}/railboard.env"
UNIT="/etc/systemd/system/railboard.service"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!! \033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx \033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Please run as root (sudo ./install.sh)."
command -v apt-get >/dev/null || die "This installer expects a Debian/Armbian system (apt)."
[ -n "$TARGET_USER" ] && id "$TARGET_USER" >/dev/null 2>&1 || die "Could not determine target user."

# --- 1. system dependencies ------------------------------------------------
say "Checking system dependencies (i2c-tools, pipx)…"
need_pkgs=()
command -v i2cdetect >/dev/null || need_pkgs+=(i2c-tools)
command -v pipx      >/dev/null || need_pkgs+=(pipx)
if [ "${#need_pkgs[@]}" -gt 0 ]; then
    say "Installing: ${need_pkgs[*]}"
    apt-get update -qq
    apt-get install -y "${need_pkgs[@]}"
else
    say "All system dependencies already present."
fi

# --- 2. i2c access for the service user ------------------------------------
if getent group i2c >/dev/null; then
    if id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx i2c; then
        say "User '$TARGET_USER' already in the i2c group."
    else
        say "Adding '$TARGET_USER' to the i2c group (for /dev/i2c-* access)…"
        usermod -aG i2c "$TARGET_USER"
    fi
else
    warn "No 'i2c' group on this system — the service will use SupplementaryGroups=i2c anyway."
fi

# --- 3. python package via pipx (isolated venv, pins Pillow/luma etc.) ------
say "Installing railboard[hardware] with pipx into ${PIPX_BIN_DIR}…"
export PIPX_HOME PIPX_BIN_DIR
pipx install --force "${REPO_DIR}[hardware]"
command -v railboard >/dev/null || export PATH="$PIPX_BIN_DIR:$PATH"

# --- 4. config (don't clobber an existing one) -----------------------------
install -d -m 0755 "$CONF_DIR"
if [ -f "$CONF" ]; then
    warn "Keeping existing $CONF (compare with config.example.yaml for new keys)."
else
    # Prefer a config.yaml shipped alongside install.sh, else the example.
    src="$REPO_DIR/config.example.yaml"
    [ -f "$REPO_DIR/config.yaml" ] && src="$REPO_DIR/config.yaml"
    say "Installing config -> $CONF (from $(basename "$src"))"
    install -m 0644 "$src" "$CONF"
    warn "Edit $CONF — set your stations, journeys and the display i2c_port/rotate."
fi

# --- 5. API key env file ---------------------------------------------------
if [ -f "$ENV_FILE" ]; then
    say "Keeping existing $ENV_FILE."
else
    say "Creating $ENV_FILE (add your Rail Data Marketplace key)…"
    printf 'RDM_API_KEY=\n' > "$ENV_FILE"
    chown "root:$(id -gn "$TARGET_USER")" "$ENV_FILE"
    chmod 0640 "$ENV_FILE"
    warn "Put your consumer key in $ENV_FILE: RDM_API_KEY=xxxxxxxx"
fi

# --- 6. systemd unit (templated with the target user) ----------------------
say "Installing systemd unit for user '$TARGET_USER'…"
sed -e "s/@USER@/${TARGET_USER}/g" \
    -e "s#@RAILBOARD@#${PIPX_BIN_DIR}/railboard#g" \
    -e "s#@CONF@#${CONF}#g" \
    -e "s#@ENV@#${ENV_FILE}#g" \
    "$REPO_DIR/railboard.service" > "$UNIT"
systemctl daemon-reload
systemctl enable railboard.service

cat <<EOF

------------------------------------------------------------------------
 ALMOST DONE
------------------------------------------------------------------------
 1. Put your API key in:   $ENV_FILE   (RDM_API_KEY=...)
 2. Check stations/display in: $CONF
 3. If migrating from the stock OLED script, disable it so it stops
    fighting for the panel:

        sudo systemctl disable --now sys-oled.service

 4. Start railboard:

        sudo systemctl start railboard.service
        journalctl -u railboard.service -f

 NOTE: adding a user to a group only takes effect on new logins — the
 systemd unit already grants i2c via SupplementaryGroups, so a start is
 enough; a reboot is the surest way if the panel stays dark.
------------------------------------------------------------------------
EOF

say "Install complete."
