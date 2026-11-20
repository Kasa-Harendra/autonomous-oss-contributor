#!/usr/bin/env bash
# ==============================================================================
# Autonomous OSS Contributor - Installation & Setup Script
# ==============================================================================
set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo -e "${BOLD}${BLUE}=== Installing Autonomous Open Source Contributor ===${NC}\n"

# 1. Check Python 3
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}Error: python3 is not installed or not in PATH.${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo -e "${GREEN}✓ Found Python ${PYTHON_VERSION}${NC}"

# 2. Virtual Environment Setup
VENV_DIR="$ROOT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${BLUE}→ Creating virtual environment at .venv...${NC}"
    python3 -m venv "$VENV_DIR"
fi

echo -e "${BLUE}→ Installing Python dependencies...${NC}"
if [ -x "$VENV_DIR/bin/pip" ]; then
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet -r requirements.txt
elif command -v uv &>/dev/null; then
    uv pip install --quiet -r requirements.txt --python "$VENV_DIR"
fi
echo -e "${GREEN}✓ Dependencies installed successfully.${NC}"

# 3. Check GitHub CLI
if command -v gh &>/dev/null; then
    echo -e "${GREEN}✓ GitHub CLI (gh) found.${NC}"
else
    echo -e "${YELLOW}⚠ GitHub CLI (gh) was not found in PATH.${NC}"
    echo -e "  Please install gh (e.g. 'brew install gh' or via https://cli.github.com)."
fi

# 4. Create .env from .env.example if missing
if [ ! -f "$ROOT_DIR/.env" ]; then
    echo -e "${BLUE}→ Creating .env from .env.example...${NC}"
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
    echo -e "${YELLOW}⚠ Please edit '$ROOT_DIR/.env' to add your NTFY_TOPIC and directory preferences.${NC}"
else
    echo -e "${GREEN}✓ Existing .env found.${NC}"
fi

# 5. Set executable permissions
chmod +x "$ROOT_DIR/scripts/"*.sh "$ROOT_DIR/scripts/"*.py "$ROOT_DIR/bin/"* || true

# 6. Install CLI Symlinks to ~/.local/bin
TARGET_BIN_DIR="$HOME/.local/bin"
mkdir -p "$TARGET_BIN_DIR"

for cmd in list-pr goto-pr continue-pr sync-pr; do
    if [ -f "$ROOT_DIR/bin/$cmd" ]; then
        ln -sf "$ROOT_DIR/bin/$cmd" "$TARGET_BIN_DIR/$cmd"
        echo -e "${GREEN}✓ Symlinked $cmd -> $TARGET_BIN_DIR/$cmd${NC}"
    fi
done

if [[ ":$PATH:" != *":$TARGET_BIN_DIR:"* ]]; then
    echo -e "\n${YELLOW}Note: $TARGET_BIN_DIR is not currently in your PATH.${NC}"
    echo -e "Add this to your ~/.zshrc or ~/.bashrc:"
    echo -e "  ${BOLD}export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
fi

# 7. Scheduler configuration (macOS launchd / Linux cron & systemd)
if [[ "$OSTYPE" == "darwin"* ]]; then
    PLIST_NAME="com.user.daily-oss-contributor.plist"
    LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

    echo -e "\n${BLUE}→ macOS detected. Launchd service available in templates/${PLIST_NAME}${NC}"
    echo -e "  To enable scheduled automated runs daily at 22:00:"
    echo -e "    1. sed -e \"s|/REPLACE_WITH_FULL_PATH_TO_REPO|$ROOT_DIR|g\" -e \"s|/REPLACE_WITH_HOME|$HOME|g\" templates/$PLIST_NAME > \"$LAUNCH_AGENTS_DIR/$PLIST_NAME\""
    echo -e "    2. launchctl load -w \"$LAUNCH_AGENTS_DIR/$PLIST_NAME\""
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo -e "\n${BLUE}→ Linux detected.${NC}"
    echo -e "  To enable scheduled automated runs daily at 22:00:"
    echo -e "  Option A (cron): Run 'crontab -e' and add the line from templates/crontab.example"
    echo -e "  Option B (systemd user timer): Copy templates/daily-oss-contributor.* to ~/.config/systemd/user/"
    echo -e "    systemctl --user enable --now daily-oss-contributor.timer"
fi

echo -e "\n${BOLD}${GREEN}=== Setup Complete! ===${NC}"
echo -e "Next steps:"
echo -e "  1. Place your Google Cloud OAuth client credentials JSON in this repo as 'credentials.json'."
echo -e "  2. Run: ${BOLD}.venv/bin/python3 scripts/gmail_pr_checker.py --auth-only${NC} to authenticate Gmail."
echo -e "  3. Configure your phone with the ntfy app (subscribe to your chosen topic in .env)."
echo -e "  4. Test run: ${BOLD}./scripts/daily_oss_contributor.sh${NC}\n"
