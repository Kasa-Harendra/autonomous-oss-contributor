#!/bin/bash
# ==============================================================================
# Daily Autonomous OSS Contributor
# Runs daily on schedule. Discovers review feedback or picks a new contribution,
# then launches an interactive conversation directly in the Antigravity Chats
# side panel under your project for human review & approval.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Ensure essential paths are available in non-interactive shell
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

if [ -f "$ROOT_DIR/.env" ]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
fi

CONTRIB_BASE="${CONTRIBUTIONS_DIR:-$ROOT_DIR}"
cd "$CONTRIB_BASE"

LOG_DIR="${LOG_DIR:-$HOME/Library/Logs}"
mkdir -p "$LOG_DIR"
EXEC_LOG="$LOG_DIR/daily-oss-contributor.log"

echo "============================================================" >> "$EXEC_LOG"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting Daily Open-Source Contributor Run" >> "$EXEC_LOG"

# Wait for network connectivity
NETWORK_OK=0
for i in {1..12}; do
  if gh auth status >/dev/null 2>&1; then
    NETWORK_OK=1
    break
  fi
  sleep 5
done

if [ "$NETWORK_OK" -eq 0 ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Network or GitHub CLI (gh) authentication not available." >> "$EXEC_LOG"
  exit 1
fi

PYTHON_BIN="python3"
if [ -x "$ROOT_DIR/.venv/bin/python3" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python3"
fi

AGENTAPI_BIN="${AGENTAPI_BIN:-$HOME/.gemini/antigravity/bin/agentapi}"
GMAIL_CHECKER="$SCRIPT_DIR/gmail_pr_checker.py"
TRACKER_SCRIPT="$SCRIPT_DIR/pr_tracker.py"

find_active_hub() {
  python3 - <<'PY'
import glob, json, os
for f in sorted(glob.glob(os.path.expanduser("~/.gemini/antigravity/daemon/hub_*.json")), key=os.path.getmtime, reverse=True):
    try:
        with open(f) as fp:
            d = json.load(fp)
        pid = d.get("pid")
        port = d.get("httpPort")
        csrf = d.get("csrfToken", "")
        if pid and port:
            os.kill(pid, 0)
            print(f"{port} {csrf}")
            break
    except Exception:
        continue
PY
}

# Support manual override flag --force-new
FORCE_NEW=0
if [ "${1:-}" = "--force-new" ]; then
  FORCE_NEW=1
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] --force-new flag provided. Skipping active PR check." >> "$EXEC_LOG"
fi

# ============================================================
# STEP 0: GMAIL PR REVIEW CHECK & ACTIVE LEDGER SYNC
# ============================================================
CHECK_CODE=0
CHECK_OUT=""

if [ "$FORCE_NEW" -eq 0 ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pulling emails from Gmail & checking previous PR statuses..." >> "$EXEC_LOG"
  set +e
  CHECK_OUT=$("$PYTHON_BIN" "$GMAIL_CHECKER" --check-flow 2>> "$EXEC_LOG")
  CHECK_CODE=$?
  set -e
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Gmail PR Check Code: $CHECK_CODE" >> "$EXEC_LOG"

  if [ "$CHECK_CODE" -eq 0 ]; then
    set +e
    TRACKER_OUT=$("$PYTHON_BIN" "$TRACKER_SCRIPT" check-active 2>> "$EXEC_LOG")
    TRACKER_CODE=$?
    set -e
    if [ "$TRACKER_CODE" -eq 10 ] || [ "$TRACKER_CODE" -eq 20 ]; then
      CHECK_CODE=$TRACKER_CODE
      CHECK_OUT="$TRACKER_OUT"
    fi
  fi
fi

# ============================================================
# PREPARE CONVERSATION TITLE AND PROMPT
# ============================================================
TITLE=""
PROMPT=""

if [ "$FORCE_NEW" -eq 0 ] && [ "$CHECK_CODE" -eq 10 ]; then
  parse_field() {
    echo "$CHECK_OUT" | "$PYTHON_BIN" -c "import sys, json, re; m = re.search(r'\{.*\}', sys.stdin.read(), re.DOTALL); print(json.loads(m.group(0)).get('$1', '') if m else '')"
  }
  PR_ID=$(parse_field "pr_id")
  REPO=$(parse_field "repo")
  BRANCH=$(parse_field "branch")
  LOCAL_PATH=$(parse_field "local_path")
  PR_URL=$(parse_field "pr_url")
  REVIEWER=$(parse_field "latest_reviewer")
  FEEDBACK=$(parse_field "feedback")

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Maintainer review detected on PR #$PR_ID ($REPO) by @$REVIEWER. Launching review update workflow..." >> "$EXEC_LOG"

  TITLE="Review Update - $REPO #$PR_ID ($(date '+%b %d'))"

  PROMPT="You are the Autonomous Open-Source Contributor Bot with Human-in-the-Loop review.
Maintainer review feedback has been received on existing Pull Request #${PR_ID} (${REPO}).

CRITICAL INSTRUCTION: Do NOT discover or create a new contribution today. Your single objective is to address the maintainer review feedback on PR #${PR_ID}.

============================================================
ACTIVE PR & REVIEW DETAILS
============================================================
Repository: ${REPO}
Pull Request: #${PR_ID} (${PR_URL})
Branch: ${BRANCH}
Local Directory: ${LOCAL_PATH}
Latest Reviewer: @${REVIEWER}

Maintainer Feedback to Address:
${FEEDBACK}

============================================================
CHECKLIST & EXECUTION PROTOCOL
============================================================
1. WORKSPACE & REPO: Navigate to \"${LOCAL_PATH}\" and checkout \"${BRANCH}\". Fetch remote updates if any.
2. IMPLEMENT REVIEW FIXES: Modify code strictly as requested by the maintainer, adhering to codebase standards.
3. LOCAL TEST VERIFICATION: Run test suite, linters, and typecheck; verify 0 errors.
4. CLEAN COMMIT: Stage only modified project files. Commit with clean conventional message (e.g. fix: address review feedback).
5. CHAT REVIEW & APPROVAL GATE:
   - Present a clear summary of changes and test results right here in this chat.
   - Ask the user: \"Please review the proposed fixes. Reply with 'Approve' to push the updates to your fork and reply to @${REVIEWER} on GitHub.\"
   - Pause and wait for the user's approval in this chat.
6. ONCE APPROVED BY USER IN CHAT:
   - Push branch to fork: git push origin \"${BRANCH}\"
   - Post review reply comment on PR #${PR_ID} on GitHub explaining the changes.
   - Update ledger files in ${CONTRIB_BASE}."

else
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Proceeding with new open-source contribution for today..." >> "$EXEC_LOG"

  TITLE="Daily OSS Contributor - $(date '+%b %d, %Y')"

  read -r -d '' PROMPT <<'EOF_PROMPT' || true
You are the Autonomous Open-Source Contributor Bot with Human-in-the-Loop review. Execute today's daily open-source contribution in the workspace.

============================================================
CHECKLIST & EXECUTION PROTOCOL
============================================================
You must maintain an explicit checklist of all requirements and verify each item before proceeding:
[ ] 1. ENVIRONMENT & PATH: Verify gh auth status.
[ ] 2. REPO EXCLUDE & AUDIT LOG: Initialize timestamped .agent_workflow.log in the cloned repo and add it to .git/info/exclude.
[ ] 3. DISCOVER ISSUE & BAN COLLABORATORS_ONLY:
       - Query recognizable repos (stars > 500) for unassigned issues tagged good first issue, bug, enhancement, documentation, or help wanted.
       - MANDATORY PR POLICY VERIFICATION:
         Before choosing any repository or modifying files, run:
           gh api graphql -f query='query { repository(owner:"<owner>", name:"<repo>") { pullRequestCreationPolicy viewerPermission } }'
         * If pullRequestCreationPolicy is "COLLABORATORS_ONLY" (e.g. 'An owner of this repository has limited the ability to open a pull request to users that are collaborators'), DO NOT CHOOSE THIS REPOSITORY! Immediately reject it and pick another one.
         * You MUST only choose repositories where pullRequestCreationPolicy is "ALL".
[ ] 4. CLEAN BRANCH: Create a clean feature branch fix/issue-<number>.
[ ] 5. IMPLEMENTATION & CODE STYLE: Adhere strictly to CONTRIBUTING.md and style guides.
[ ] 6. LOCAL TEST VERIFICATION: Run test suite; 0 errors required (self-correct until passing).
[ ] 7. CLEAN COMMIT: Stage ONLY changed project files. NEVER stage or commit .agent_workflow.log.
[ ] 8. CHAT REVIEW & APPROVAL GATE:
       - Present the comprehensive review proposal directly in this chat:
         * Target Repository & Issue Link
         * Summary & Technical Approach
         * Modified Files & Functions
         * Exact Test & Lint Results
       - Ask the user: "Please review the proposed contribution. Reply with 'Approve' to push the branch to your fork, create the Pull Request on GitHub, and update the workspace ledger."
       - Pause and wait for the user's confirmation in this chat.
[ ] 9. PUSH & PR UPON APPROVAL:
       - Once the user replies with 'Approve' in this chat:
         * Fork repository if not already forked: gh repo fork --remote=true --clone=false || true
         * Push branch to fork: git push -u origin fix/issue-<number>
         * Open PR: gh pr create --repo <upstream_repo> --head <username>:fix/issue-<number> --title "<clean title>" --body "<body>"
         * Record in ledger: python3 scripts/pr_tracker.py track ...
EOF_PROMPT

fi

# ============================================================
# DISCOVER ANTIGRAVITY HUB AND SPAWN SESSION IN UI CHATS
# ============================================================
HUB_INFO=$(find_active_hub || true)
HUB_PORT=$(echo "$HUB_INFO" | awk '{print $1}')
HUB_CSRF=$(echo "$HUB_INFO" | awk '{print $2}')

if [ -z "$HUB_PORT" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARNING: Antigravity app is not running. Launching Antigravity..." >> "$EXEC_LOG"
  open -a "Antigravity" || open -a "Gemini" || true
  sleep 10
  HUB_INFO=$(find_active_hub || true)
  HUB_PORT=$(echo "$HUB_INFO" | awk '{print $1}')
  HUB_CSRF=$(echo "$HUB_INFO" | awk '{print $2}')
fi

if [ -n "$HUB_PORT" ]; then
  export ANTIGRAVITY_LS_ADDRESS="localhost:$HUB_PORT"
  if [ -n "$HUB_CSRF" ]; then
    export ANTIGRAVITY_CSRF_TOKEN="$HUB_CSRF"
  fi
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Antigravity app detected on $ANTIGRAVITY_LS_ADDRESS." >> "$EXEC_LOG"
fi

if [ -x "$AGENTAPI_BIN" ] && [ -n "${ANTIGRAVITY_LS_ADDRESS:-}" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Spawning interactive conversation in Antigravity Chats: '$TITLE'" >> "$EXEC_LOG"
  cd "$CONTRIB_BASE"
  PROJECT_ENV=""
  if [ -n "${ANTIGRAVITY_PROJECT_ID:-}" ]; then
    PROJECT_ENV="ANTIGRAVITY_PROJECT_ID=${ANTIGRAVITY_PROJECT_ID}"
  fi
  env $PROJECT_ENV "$AGENTAPI_BIN" new-conversation --title="$TITLE" "$PROMPT" >> "$EXEC_LOG" 2>&1
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Conversation successfully spawned in UI Chats panel. Completed Daily Runner." >> "$EXEC_LOG"
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Unable to connect to Antigravity Hub on port '$HUB_PORT'." >> "$EXEC_LOG"
  exit 1
fi
