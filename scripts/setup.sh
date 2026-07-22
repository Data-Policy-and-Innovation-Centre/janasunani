#!/bin/bash
set -euo pipefail

USER_BIN="${HOME}/.local/bin"
USER_OPT="${HOME}/.local/opt"
BOX_REMOTE="${BOX_REMOTE:-box}"
PATH="${USER_BIN}:${PATH}"

echo "=== DPIC Workspace Setup ==="

guard_supported_location() {
    if [ "$(id -u)" -eq 0 ]; then
        echo "Do not run setup as root or with sudo."
        echo "Exit the root shell and run: make setup"
        exit 1
    fi

    local repo_dir
    repo_dir="$(pwd -P)"
    if grep -qi microsoft /proc/version 2>/dev/null && [[ "${repo_dir}" == /mnt/* ]]; then
        echo "This repository is on the Windows filesystem: ${repo_dir}"
        echo "Python virtual environments often fail there under WSL."
        echo "Clone the repo inside the WSL Linux filesystem, for example:"
        echo "  mkdir -p ~/Documents/GitHub"
        echo "  cd ~/Documents/GitHub"
        echo "  git clone <repo-url>"
        echo "Then rerun: make setup"
        exit 1
    fi
}

ensure_user_bin_on_path() {
    mkdir -p "${USER_BIN}" "${USER_OPT}"

    local path_line='export PATH="${HOME}/.local/bin:${PATH}"'
    for profile in "${HOME}/.bashrc" "${HOME}/.profile"; do
        if [ -e "${profile}" ] && ! grep -Fq '.local/bin' "${profile}"; then
            printf '\n# DPIC workspace tools\n%s\n' "${path_line}" >> "${profile}"
        fi
    done
}

require_command() {
    command -v "$1" >/dev/null || (echo "Missing required tool: $1" && exit 1)
}

install_uv() {
    echo "Installing uv into the current user account..."
    require_command curl
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="${HOME}/.cargo/bin:${USER_BIN}:${PATH}"
}

platform_suffix() {
    local os arch
    os="$(uname -s)"
    arch="$(uname -m)"

    case "${os}:${arch}" in
        Linux:x86_64) echo "linux-amd64" ;;
        Linux:aarch64|Linux:arm64) echo "linux-arm64" ;;
        Darwin:x86_64) echo "osx-amd64" ;;
        Darwin:arm64) echo "osx-arm64" ;;
        *) echo "Unsupported platform for automatic install: ${os} ${arch}" >&2; return 1 ;;
    esac
}

install_rclone() {
    echo "Installing rclone into ${USER_BIN}..."
    require_command curl
    require_command unzip

    local suffix tmpdir
    suffix="$(platform_suffix)"
    tmpdir="$(mktemp -d)"

    curl -fsSL "https://downloads.rclone.org/rclone-current-${suffix}.zip" -o "${tmpdir}/rclone.zip"
    unzip -q "${tmpdir}/rclone.zip" -d "${tmpdir}"
    install -m 0755 "${tmpdir}"/rclone-*-*/rclone "${USER_BIN}/rclone"
    rm -rf "${tmpdir}"
}

install_aws() {
    echo "Installing AWS CLI into the current user account..."
    require_command curl
    require_command unzip

    local os arch aws_arch tmpdir install_dir bin_dir
    os="$(uname -s)"
    arch="$(uname -m)"
    install_dir="${USER_OPT}/aws-cli"
    bin_dir="${USER_BIN}"
    tmpdir="$(mktemp -d)"

    if [ "${os}" != "Linux" ]; then
        echo "Automatic AWS CLI install is only configured for Linux/WSL."
        echo "On macOS, install it without sudo using Homebrew: brew install awscli"
        echo "Then rerun make setup."
        exit 1
    fi

    case "${arch}" in
        x86_64) aws_arch="x86_64" ;;
        aarch64|arm64) aws_arch="aarch64" ;;
        *) echo "Unsupported AWS CLI architecture: ${arch}" >&2; exit 1 ;;
    esac

    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${aws_arch}.zip" -o "${tmpdir}/awscliv2.zip"
    unzip -q "${tmpdir}/awscliv2.zip" -d "${tmpdir}"
    "${tmpdir}/aws/install" --install-dir "${install_dir}" --bin-dir "${bin_dir}" --update
    rm -rf "${tmpdir}"
}

# Resolve the tesseract binary the same way the runtime does
# (pytesseract_backend.py / features.py): $TESSERACT_CMD, then PATH, then the
# repo's no-sudo ~/.local install. Echoes the path on success.
resolve_tesseract() {
    local candidate
    for candidate in "${TESSERACT_CMD:-}" "$(command -v tesseract 2>/dev/null)" \
        "${HOME}/.local/tesseract/usr/bin/tesseract"; do
        if [ -n "${candidate}" ] && [ -x "${candidate}" ]; then
            echo "${candidate}"
            return 0
        fi
    done
    return 1
}

# Resolve a poppler binary (pdfinfo/pdftoppm) the same way the runtime does
# (page_renderer.py / stage.py): the ~/.local poppler dir first, then PATH.
resolve_poppler_bin() {
    local user_bin="${HOME}/.local/poppler/usr/bin/$1"
    if [ -x "${user_bin}" ]; then
        echo "${user_bin}"
        return 0
    fi
    command -v "$1" 2>/dev/null
}

# Poppler (pdfinfo/pdftoppm) and tesseract are system packages the OCR pipeline
# shells out to at runtime; without them it crashes on the first PDF (pdf2image
# PDFInfoNotInstalledError). Unlike uv/rclone/aws they need a system package
# manager, so on Debian/WSL (apt) we install them with sudo; elsewhere we print
# the platform's install command and fail early. A valid ~/.local (no-sudo) or
# $TESSERACT_CMD install is accepted here too — matching the runtime resolvers —
# so it isn't false-failed. See docs/DEMO.md.
check_system_ocr_deps() {
    local tesseract_bin poppler_ok=1
    resolve_poppler_bin pdfinfo  >/dev/null || poppler_ok=0
    resolve_poppler_bin pdftoppm >/dev/null || poppler_ok=0
    tesseract_bin="$(resolve_tesseract || true)"

    if [ "${poppler_ok}" -eq 1 ] && [ -n "${tesseract_bin}" ]; then
        warn_missing_odia_traineddata "${tesseract_bin}"
        return
    fi

    echo "Missing system OCR/PDF binaries (poppler and/or tesseract)."

    # Debian/Ubuntu/WSL: install directly. sudo elevates just this command,
    # which is fine even though setup itself must not run as root.
    if command -v apt-get >/dev/null; then
        echo "Installing poppler-utils + tesseract-ocr (+ Odia) via apt..."
        if sudo apt-get update && sudo apt-get install -y \
            poppler-utils tesseract-ocr tesseract-ocr-ori; then
            warn_missing_odia_traineddata
            return
        fi
        echo "Automatic install failed — install the packages above manually,"
        echo "then rerun: make setup"
        exit 1
    fi

    # No apt (macOS / other distros): can't assume a package manager or sudo.
    if [ "$(uname -s)" = "Darwin" ]; then
        echo "Install with: brew install poppler tesseract tesseract-lang"
    else
        echo "Install poppler-utils, tesseract-ocr and tesseract-ocr-ori with"
        echo "your system package manager."
    fi
    echo "Then rerun: make setup"
    exit 1
}

# The tesseract binary can be present without the Odia (ori) traineddata the
# pipeline needs for its ori OCR pass. Warn rather than fail; eng-only still runs.
# $1 = resolved tesseract path (defaults to PATH lookup after an apt install).
warn_missing_odia_traineddata() {
    local tesseract_bin="${1:-tesseract}"
    if ! "${tesseract_bin}" --list-langs 2>/dev/null | grep -qx ori; then
        echo "WARNING: tesseract 'ori' (Odia) traineddata not found."
        echo "  Debian/WSL: sudo apt-get install -y tesseract-ocr-ori"
        echo "  macOS:      brew install tesseract-lang"
    fi
}

install_missing_prereqs() {
    ensure_user_bin_on_path

    echo "[1/7] Checking prerequisites..."
    command -v git >/dev/null || {
        echo "Git is required before setup can continue."
        echo "On WSL, install it with: sudo apt-get update && sudo apt-get install -y git"
        exit 1
    }
    command -v uv >/dev/null || install_uv
    command -v rclone >/dev/null || install_rclone
    command -v aws >/dev/null || install_aws
    check_system_ocr_deps

    command -v git >/dev/null
    command -v uv >/dev/null
    command -v rclone >/dev/null
    command -v aws >/dev/null
}

ensure_box_remote() {
    local remote_name
    remote_name="${BOX_REMOTE%:}"

    if ! rclone listremotes | grep -Fxq "${remote_name}:"; then
        echo "Creating rclone Box remote '${remote_name}'..."
        rclone config create "${remote_name}" box
    fi

    rclone config reconnect "${remote_name}:"
}

guard_supported_location
install_missing_prereqs

echo "[2/7] Initializing Git repository..."
if [ ! -e .git ]; then
    git init
fi

echo "[3/7] Setting up Python environment..."

# The dpic dependency is fetched from GitHub over SSH. uv runs git in a
# subprocess with no interactive terminal, so a passphrase-protected key has
# nowhere to prompt and the sync hangs indefinitely. Probe GitHub SSH
# non-interactively first and fail loudly with instructions if it can't auth.
# Note: `ssh -T git@github.com` always exits non-zero, even on success, so we
# match on the "successfully authenticated" banner instead of the exit code.
ssh_probe="$(SSH_ASKPASS=/bin/false GIT_TERMINAL_PROMPT=0 \
    ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -T git@github.com 2>&1 || true)"
if ! printf '%s' "${ssh_probe}" | grep -q "successfully authenticated"; then
    echo "ERROR: Cannot authenticate to GitHub over SSH non-interactively."
    echo "If your SSH key has a passphrase, load it into ssh-agent first:"
    echo "    eval \"\$(ssh-agent -s)\""
    echo "    ssh-add ~/.ssh/id_ed25519"
    echo "Then rerun: make setup"
    exit 1
fi

uv sync

echo "[4/7] Installing skills..."
uv run dpic-sync-opencode-skills

echo "[5/7] Configuring DVC credentials..."
uv run dvc remote modify --local s3remote profile default

echo "[6/7] Authenticating with Box (browser will open)..."
ensure_box_remote

echo "[7/7] Pulling latest data..."
make pull

uv run nbstripout --install

echo ""
echo "=== Setup complete. Run 'make help' to see available commands. ==="
