#!/bin/bash
set -euo pipefail

# Determine script locations
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Ensure essential paths are available in non-interactive shell
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# Load local environment configuration if present
if [ -f "$ROOT_DIR/.env" ]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
elif [ -f "$SCRIPT_DIR/.env" ]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env"
fi

# Configuration Defaults
CONTRIB_BASE="${CONTRIBUTIONS_DIR:-$HOME/Projects/Git Contributions}"
NTFY_TOPIC="${NTFY_TOPIC:-kasa-oss-contributor}"
LOG_DIR="${LOG_DIR:-$HOME/Library/Logs}"
mkdir -p "$LOG_DIR" "$CONTRIB_BASE"
EXEC_LOG="$LOG_DIR/daily-oss-contributor.log"

echo "============================================================" >> "$EXEC_LOG"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting Daily Open-Source Contributor Run" >> "$EXEC_LOG"

# Wait for network connectivity (in case machine just woke up from sleep)
NETWORK_OK=0
for i in {1..15}; do
  if curl -s --connect-timeout 2 https://api.github.com > /dev/null 2>&1; then
    NETWORK_OK=1
    break
  fi
  sleep 2
done

# Verify GitHub CLI authentication (with retry loop for system wake-up)
GH_AUTH_OK=0
for i in {1..5}; do
  if gh auth status > /dev/null 2>&1; then
    GH_AUTH_OK=1
    break
  fi
  sleep 2
done

if [ "$GH_AUTH_OK" -eq 0 ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: GitHub CLI (gh) is not authenticated." >> "$EXEC_LOG"
  curl -s -H "Title: ⚠️ Contributor: Auth Error" \
       -H "Priority: high" \
       -H "Tags: warning,octocat" \
       -d "GitHub CLI authentication failed. Please check gh authentication." \
       "https://ntfy.sh/$NTFY_TOPIC" || true
  exit 1
fi

# Locate Python binary (prefer virtualenv)
PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  if [ -x "$ROOT_DIR/.venv/bin/python3" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python3"
  elif [ -x "$SCRIPT_DIR/.venv/bin/python3" ]; then
    PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python3"
  else
    PYTHON_BIN="python3"
  fi
fi

TRACKER_SCRIPT="$SCRIPT_DIR/pr_tracker.py"
GMAIL_CHECKER="$SCRIPT_DIR/gmail_pr_checker.py"
WAIT_APPROVAL_SCRIPT="$SCRIPT_DIR/wait_for_approval.py"

# Support manual override flag --force-new
FORCE_NEW=0
if [ "${1:-}" = "--force-new" ]; then
  FORCE_NEW=1
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] --force-new flag provided. Skipping active PR check." >> "$EXEC_LOG"
fi

# ============================================================
# STEP 0: PULL EMAILS & SYNC PREVIOUS PRs FROM GMAIL
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

  # Fallback to tracker script if Gmail checker exited 0 or 20 but tracker detects specific active PR status
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

# CASE A: Awaiting Maintainer (Code 20) -> Send status ping to phone and proceed with Mode C
if [ "$FORCE_NEW" -eq 0 ] && [ "$CHECK_CODE" -eq 20 ]; then
  PR_MSG=$(echo "$CHECK_OUT" | "$PYTHON_BIN" -c 'import sys, json, re; m = re.search(r"\{.*\}", sys.stdin.read(), re.DOTALL); print(json.loads(m.group(0)).get("message", "Active PR awaiting review.") if m else "Active PR awaiting maintainer review.")' 2>/dev/null || echo "Active PR awaiting maintainer review.")
  PR_URL=$(echo "$CHECK_OUT" | "$PYTHON_BIN" -c 'import sys, json, re; m = re.search(r"\{.*\}", sys.stdin.read(), re.DOTALL); print(json.loads(m.group(0)).get("pr_url", "") if m else "")' 2>/dev/null || echo "")

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Active PR Check: $PR_MSG" >> "$EXEC_LOG"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Active PR has no pending review feedback. Proceeding with new contribution (Mode C)..." >> "$EXEC_LOG"
  
  if [ -n "$PR_URL" ]; then
    curl -s -H "Title: ⏳ Contributor Daily Check" \
         -H "Priority: low" \
         -H "Tags: hourglass,eyes,octocat" \
         -H "Actions: view, View Pull Request, $PR_URL" \
         -d "$PR_MSG Proceeding with today's contribution." \
         "https://ntfy.sh/$NTFY_TOPIC" || true
  else
    curl -s -H "Title: ⏳ Contributor Daily Check" \
         -H "Priority: low" \
         -H "Tags: hourglass,eyes,octocat" \
         -d "$PR_MSG Proceeding with today's contribution." \
         "https://ntfy.sh/$NTFY_TOPIC" || true
  fi
fi

# CASE B: Maintainer Feedback / Changes Requested (Code 10) -> Address Review!
if [ "$FORCE_NEW" -eq 0 ] && [ "$CHECK_CODE" -eq 10 ]; then
  parse_field() {
    echo "$CHECK_OUT" | "$PYTHON_BIN" -c "import sys, json, re; m = re.search(r'\{.*\}', sys.stdin.read(), re.DOTALL); print(json.loads(m.group(0)).get('$1', '') if m else '')"
  }
  PR_ID=$(parse_field "pr_id")
  REPO=$(parse_field "repo")
  BRANCH=$(parse_field "branch")
  LOCAL_PATH=$(parse_field "local_path")
  PR_URL=$(parse_field "pr_url")
  ISSUE_URL=$(parse_field "issue_url")
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
CHECKLIST & EXECUTION PROTOCOL (MANDATORY REQUIREMENT TRACKING)
============================================================
You must maintain an explicit checklist of all requirements and verify each item before proceeding:
[ ] 1. WORKSPACE & REPO: cd \"${LOCAL_PATH}\" and checkout \"${BRANCH}\". Fetch remote updates if any.
[ ] 2. REPO EXCLUDE & AUDIT LOG: Ensure .agent_workflow.log is appended and excluded in .git/info/exclude.
[ ] 3. IMPLEMENT REVIEW FIXES: Modify code strictly as requested by the maintainer, maintaining codebase standards.
[ ] 4. LOCAL TEST VERIFICATION: Run test suite, linters, and typecheck; verify 0 errors.
[ ] 5. CLEAN COMMIT: Stage only modified project files. Commit with clean conventional message (e.g. fix: address review feedback).
[ ] 6. SAVE REPLY SUMMARY: Save detailed markdown reply explaining changes to .pending_pr_body.md.
[ ] 7. SAVE PENDING METADATA: Save .pending_pr.txt with ACTION=UPDATE_PR.
[ ] 8. PHONE NOTIFICATION & 1-TAP GATE: Send interactive alert with 1-tap approval buttons to phone via ntfy.sh.
[ ] 9. PUSH & REPLY UPON APPROVAL: Upon 1-tap approval, push update to fork and reply to reviewer on GitHub.

============================================================
METADATA & NOTIFICATION REQUIREMENTS
============================================================
Save .pending_pr.txt in ${LOCAL_PATH}:
  echo \"ACTION=UPDATE_PR\" > .pending_pr.txt
  echo \"PR_ID=${PR_ID}\" >> .pending_pr.txt
  echo \"BRANCH=${BRANCH}\" >> .pending_pr.txt
  echo \"UPSTREAM_REPO=${REPO}\" >> .pending_pr.txt
  echo \"PR_URL=${PR_URL}\" >> .pending_pr.txt
  echo \"ISSUE_URL=${ISSUE_URL}\" >> .pending_pr.txt
  echo \"CREATED_AT_EPOCH=\$(date +%s)\" >> .pending_pr.txt

Immediately exclude:
  echo \".pending_pr.txt\" >> .git/info/exclude
  echo \".pending_pr_body.md\" >> .git/info/exclude

Trigger phone alert on https://ntfy.sh/${NTFY_TOPIC}:
  curl -H \"Title: 💬 Contributor: Review Addressed\" \\
       -H \"Priority: high\" \\
       -H \"Tags: speech_balloon,sparkles,octocat\" \\
       -H \"Actions: view, 🔍 View PR, ${PR_URL}; http, 🚀 Approve & Push Update, https://ntfy.sh/${NTFY_TOPIC}, body=APPROVE; http, ❌ Reject, https://ntfy.sh/${NTFY_TOPIC}, body=REJECT\" \\
       -d \"Addressed reviewer feedback for ${REPO} #${PR_ID}. All tests passed. Tap Approve or Reject.\" \\
       https://ntfy.sh/${NTFY_TOPIC}

Log: [YYYY-MM-DD HH:MM:SS] Review fixes implemented. Interactive alert sent to phone.
Turn complete. Exit cleanly for phone approval."

else
  # CASE C: All Previous PRs Merged or Closed (Code 0) -> Discover & Create New PR
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] All previous PRs merged/closed (or forced). Proceeding with new contribution..." >> "$EXEC_LOG"

  TITLE="Daily OSS Contributor - $(date '+%b %d, %Y')"

  PROMPT="You are the Autonomous Open-Source Contributor Bot with Human-in-the-Loop review. Execute the daily workflow with strict requirement enforcement.

============================================================
CHECKLIST & EXECUTION PROTOCOL (MANDATORY REQUIREMENT TRACKING)
============================================================
You must maintain an explicit checklist of all requirements and verify each item before proceeding:
[ ] 1. ENVIRONMENT & PATH: Homebrew in PATH, gh auth status verified.
[ ] 2. REPO EXCLUDE & AUDIT LOG: Initialize timestamped .agent_workflow.log in the cloned repo and add it to .git/info/exclude.
[ ] 3. DISCOVER ISSUE & VERIFY PR POLICY: Query repos (stars > 500) where pullRequestCreationPolicy is ALL (banning COLLABORATORS_ONLY restrictions), select one viable task.
[ ] 4. CLEAN BRANCH: Create a clean feature branch fix/issue-<number>.
[ ] 5. IMPLEMENTATION & CODE STYLE: Adhere strictly to CONTRIBUTING.md and style guides.
[ ] 6. LOCAL TEST VERIFICATION: Run test suite; 0 errors required (self-correct until passing).
[ ] 7. CLEAN COMMIT: Stage ONLY changed project files. NEVER stage or commit .agent_workflow.log.
[ ] 8. PHONE NOTIFICATION & 1-TAP GATE: Send interactive alert with 1-tap approval buttons to phone via ntfy.sh.
[ ] 9. PUSH & PR UPON APPROVAL: Upon 1-tap phone approval or chat confirmation, push branch to fork, create PR, and notify phone.
[ ] 10. RECORD IN WORKSPACE LEDGER: Record PR in ${CONTRIB_BASE}/CONTRIBUTIONS.md and contributions.json.

============================================================
AUDIT LOGGING REQUIREMENT (DO NOT COMMIT)
============================================================
- Throughout execution, continuously append every action and tool invocation to .agent_workflow.log inside the cloned repository root with a local timestamp:
  [YYYY-MM-DD HH:MM:SS] <Action Description / Result>
- CRITICAL RULE: This file is for local execution tracking only and MUST NEVER BE COMMITTED OR PUSHED. Immediately append .agent_workflow.log to .git/info/exclude. Run git status to verify it remains untracked.

============================================================
WORKFLOW STEPS
============================================================

1. ENVIRONMENT & PATH PREPARATION:
   - Ensure PATH includes Homebrew: export PATH=\"/opt/homebrew/bin:/usr/local/bin:\$PATH\".
   - Verify GitHub CLI authentication: gh auth status.

2. DISCOVER TRENDING ISSUES:
   - Search recognizable open-source repositories (stars > 500) for unassigned issues tagged good first issue, bug, enhancement, documentation, or help wanted:
     gh search issues \"stars:>500\" \"state:open\" \"no:assignee\" \"(\" label:\"good first issue\" OR label:enhancement OR label:documentation OR label:bug OR label:\"help wanted\" \")\" --sort=updated --order=desc --limit=20
   - Prioritize highly recognizable repositories with active maintainers and clear guidelines.
   - MANDATORY PR POLICY VERIFICATION (BAN COLLABORATORS_ONLY):
     Before selecting a repository or writing code, check whether outside contributors are allowed to open pull requests:
     gh api graphql -f query='query { repository(owner:\"<owner>\", name:\"<repo>\") { pullRequestCreationPolicy viewerPermission } }'
     * If pullRequestCreationPolicy is 'COLLABORATORS_ONLY' (e.g. 'An owner of this repository has limited the ability to open a pull request to users that are collaborators'), DO NOT CHOOSE THIS REPOSITORY! Immediately reject it and pick another one.
     * You MUST only choose repositories where pullRequestCreationPolicy is 'ALL' (or where viewerPermission is WRITE/ADMIN/MAINTAIN).
   - Select a self-contained, high-quality, viable contribution (bug fix, documentation improvement, unit test addition, or well-scoped enhancement).
   - Inspect issue details: gh issue view <issue-number> --repo <owner/repo>.

3. WORKSPACE & REPOSITORY SETUP:
   - Working directory root: ${CONTRIB_BASE}.
   - Clone or fork the repository into ${CONTRIB_BASE}/<repo-name> if not present, and cd into it.
   - Immediately configure git exclusion: echo \".agent_workflow.log\" >> .git/info/exclude
   - Initialize and append to .agent_workflow.log: [YYYY-MM-DD HH:MM:SS] Initialized workflow for issue <issue-number>.
   - Read CONTRIBUTING.md, testing documentation, and code style configurations.
   - Checkout a clean branch: git checkout -b fix/issue-<number>.

4. IMPLEMENT & VERIFY:
   - Log each step: [YYYY-MM-DD HH:MM:SS] Beginning implementation of fix for issue <issue-number>.
   - Make the necessary code modifications adhering to repository guidelines.
   - Run the local test suite (e.g. pytest, npm test, cargo test).
   - If tests fail, analyze the stack trace, self-correct, and re-run until all tests pass with 0 errors.
   - Log: [YYYY-MM-DD HH:MM:SS] All local tests passed successfully.

5. STAGING & COMMIT (PRESERVE CLEAN TREE):
   - Check git status to ensure only intended project files are staged.
   - NEVER commit .agent_workflow.log.
   - Create a clean conventional commit for the bug fix.

6. PHONE NOTIFICATION & PR PROPOSAL RECORDING:
   - Format a complete Review Artifact showing the problem, code diff, checklist status, and proposed PR title/body.
   - Save the comprehensive, detailed PR description to .pending_pr_body.md in cloned repository root.
   - Save PR metadata file .pending_pr.txt in cloned repository root:
     echo \"ACTION=CREATE_PR\" > .pending_pr.txt
     echo \"BRANCH=fix/issue-<number>\" >> .pending_pr.txt
     echo \"UPSTREAM_REPO=<owner/repo>\" >> .pending_pr.txt
     echo \"PR_TITLE=<clean conventional title>\" >> .pending_pr.txt
     echo \"ISSUE_URL=<issue_url>\" >> .pending_pr.txt
     echo \"CREATED_AT_EPOCH=\$(date +%s)\" >> .pending_pr.txt
   - Immediately exclude both from git:
     echo \".pending_pr.txt\" >> .git/info/exclude
     echo \".pending_pr_body.md\" >> .git/info/exclude
   - Trigger interactive push notification to phone with 3 buttons on single channel https://ntfy.sh/${NTFY_TOPIC}:
     curl -H \"Title: 🛠️ Contributor: Fix Ready for Review\" \\
          -H \"Priority: high\" \\
          -H \"Tags: robot,sparkles,octocat\" \\
          -H \"Actions: view, 🔍 Open Issue, <issue_url>; http, 🚀 Approve & Create PR, https://ntfy.sh/${NTFY_TOPIC}, body=APPROVE; http, ❌ Reject, https://ntfy.sh/${NTFY_TOPIC}, body=REJECT\" \\
          -d \"Resolved issue in <repo>. All tests passed. Open issue to review, then tap Approve or Reject.\" \\
          https://ntfy.sh/${NTFY_TOPIC}
   - Log: [YYYY-MM-DD HH:MM:SS] Interactive notification sent to phone. Ready for review.
   - Turn complete. Exit cleanly for approval."
fi

# ============================================================
# AGENT EXECUTION (SPAWN UI CONVERSATION OR HEADLESS AGY)
# ============================================================
AGENTAPI_BIN="${AGENTAPI_BIN:-$HOME/.gemini/antigravity/bin/agentapi}"

if [ -z "${AGY_BIN:-}" ]; then
  if command -v agy &>/dev/null; then
    AGY_BIN="$(command -v agy)"
  elif [ -x "/Applications/Gemini.app/Contents/Helpers/agy" ]; then
    AGY_BIN="/Applications/Gemini.app/Contents/Helpers/agy"
  elif [ -x "/Applications/Antigravity.app/Contents/Helpers/agy" ]; then
    AGY_BIN="/Applications/Antigravity.app/Contents/Helpers/agy"
  elif [ -x "$HOME/.local/bin/agy" ]; then
    AGY_BIN="$HOME/.local/bin/agy"
  elif [ -x "/usr/local/bin/agy" ]; then
    AGY_BIN="/usr/local/bin/agy"
  else
    AGY_BIN="agy"
  fi
fi

# Dynamically discover Antigravity Hub port if app is running and LS address is unset
if [ -z "${ANTIGRAVITY_LS_ADDRESS:-}" ]; then
  # 1. Inspect daemon hub json files (most reliable under launchd/background daemons)
  HUB_PORT=$(python3 -c '
import glob, json, os
for f in sorted(glob.glob(os.path.expanduser("~/.gemini/antigravity/daemon/hub_*.json")), key=os.path.getmtime, reverse=True):
    try:
        with open(f) as fp:
            d = json.load(fp)
        pid = d.get("pid")
        port = d.get("httpPort")
        if pid and port:
            os.kill(pid, 0)
            print(port)
            break
    except Exception:
        continue
' 2>/dev/null || true)

  # 2. Fallback to process search
  if [ -z "$HUB_PORT" ]; then
    HUB_PORT=$(ps aux | grep -oE '\-\-hub-port=[0-9]+' | head -n 1 | cut -d= -f2 || true)
  fi

  if [ -n "$HUB_PORT" ]; then
    export ANTIGRAVITY_LS_ADDRESS="localhost:$HUB_PORT"
  fi
fi

SPAWNED=0
if [ -x "$AGENTAPI_BIN" ] && [ -n "${ANTIGRAVITY_LS_ADDRESS:-}" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Antigravity app detected on $ANTIGRAVITY_LS_ADDRESS. Spawning interactive session in UI sidebar: '$TITLE'" >> "$EXEC_LOG"
  PROJECT_ARG=""
  if [ -n "${ANTIGRAVITY_PROJECT_ID:-}" ]; then
    PROJECT_ARG="ANTIGRAVITY_PROJECT_ID=${ANTIGRAVITY_PROJECT_ID}"
  fi
  if env $PROJECT_ARG "$AGENTAPI_BIN" new-conversation --title="$TITLE" "$PROMPT" >> "$EXEC_LOG" 2>&1; then
    SPAWNED=1
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] agentapi spawn failed. Falling back to headless agy runner..." >> "$EXEC_LOG"
  fi
fi

if [ "$SPAWNED" -eq 0 ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running headlessly via agy binary with project assignment..." >> "$EXEC_LOG"
  PROJECT_OPT=""
  if [ -n "${ANTIGRAVITY_PROJECT_ID:-}" ]; then
    PROJECT_OPT="--project=${ANTIGRAVITY_PROJECT_ID}"
  fi
  if [ -x "$AGY_BIN" ]; then
    "$AGY_BIN" $PROJECT_OPT --add-dir="$CONTRIB_BASE" --dangerously-skip-permissions --mode accept-edits -p "$PROMPT" >> "$EXEC_LOG" 2>&1
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Neither agentapi nor agy binary was available to execute the prompt." >> "$EXEC_LOG"
    exit 1
  fi
fi

# ============================================================
# RELIABLE DAEMON PHONE APPROVAL LISTENER
# ============================================================
# Wait up to 30 minutes for the session to complete implementation and write .pending_pr.txt
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Waiting for session to complete implementation and write .pending_pr.txt..." >> "$EXEC_LOG"
PENDING_FILE=""
for i in {1..180}; do
  PENDING_FILE=$(ls -t "$CONTRIB_BASE"/*/.pending_pr.txt 2>/dev/null | head -n 1 || true)
  if [ -n "$PENDING_FILE" ] && [ -f "$PENDING_FILE" ]; then
    break
  fi
  sleep 10
done

if [ -n "$PENDING_FILE" ] && [ -f "$PENDING_FILE" ]; then
  REPO_DIR=$(dirname "$PENDING_FILE")
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Found pending action in $REPO_DIR. Waiting up to 2 hours for phone approval..." >> "$EXEC_LOG"
  
  ACTION=$(grep "^ACTION=" "$PENDING_FILE" 2>/dev/null | cut -d= -f2- || echo "CREATE_PR")
  BRANCH=$(grep "^BRANCH=" "$PENDING_FILE" | cut -d= -f2-)
  UPSTREAM_REPO=$(grep "^UPSTREAM_REPO=" "$PENDING_FILE" | cut -d= -f2-)
  PR_TITLE=$(grep "^PR_TITLE=" "$PENDING_FILE" 2>/dev/null | cut -d= -f2- || echo "")
  PR_ID=$(grep "^PR_ID=" "$PENDING_FILE" 2>/dev/null | cut -d= -f2- || echo "")
  PR_URL=$(grep "^PR_URL=" "$PENDING_FILE" 2>/dev/null | cut -d= -f2- || echo "")

  EPOCH=$(grep "^CREATED_AT_EPOCH=" "$PENDING_FILE" 2>/dev/null | cut -d= -f2- || true)
  # Validate that EPOCH is strictly numeric digits; otherwise fall back to file mtime
  if ! [[ "$EPOCH" =~ ^[0-9]+$ ]]; then
    EPOCH=$(stat -f %m "$PENDING_FILE" 2>/dev/null || date +%s)
  fi

  SIGNAL=$("$PYTHON_BIN" "$WAIT_APPROVAL_SCRIPT" --topic "$NTFY_TOPIC" --since-epoch "$EPOCH" --timeout 7200 || echo "TIMEOUT")

  if [ "$SIGNAL" = "APPROVE" ]; then
    cd "$REPO_DIR"

    if [ "$ACTION" = "UPDATE_PR" ]; then
      PUSH_REMOTE="origin"
      if git remote | grep -q "^fork$"; then
        PUSH_REMOTE="fork"
      fi
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] Phone approval received (APPROVE)! Pushing review fixes to $PUSH_REMOTE/$BRANCH..." >> "$EXEC_LOG"
      git push "$PUSH_REMOTE" "$BRANCH" >> "$EXEC_LOG" 2>&1
      
      BODY_FILE="$REPO_DIR/.pending_pr_body.md"
      if [ -f "$BODY_FILE" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Posting review reply comment to PR #$PR_ID..." >> "$EXEC_LOG"
        gh pr comment "$PR_ID" --repo "$UPSTREAM_REPO" --body-file "$BODY_FILE" >> "$EXEC_LOG" 2>&1 || true
      fi

      "$PYTHON_BIN" "$TRACKER_SCRIPT" track \
        --id "$PR_ID" \
        --repo "$UPSTREAM_REPO" \
        --branch "$BRANCH" \
        --local-path "$REPO_DIR" \
        --status "OPEN" \
        --summary-file "$BODY_FILE" >> "$EXEC_LOG" 2>&1 || true

      rm -f "$PENDING_FILE" "$REPO_DIR/.pending_pr_body.md"

      curl -s -H "Title: 🎉 PR #$PR_ID Updated Successfully!" \
           -H "Priority: high" \
           -H "Tags: party_popper,tada,octocat" \
           -H "Actions: view, View Pull Request, $PR_URL" \
           -d "Pushed review fixes and posted comment to $UPSTREAM_REPO #$PR_ID." \
           "https://ntfy.sh/$NTFY_TOPIC" || true

    else
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] Phone approval received (APPROVE)! Pushing and opening PR..." >> "$EXEC_LOG"
      gh repo fork --remote=true --clone=false || true
      PUSH_REMOTE="origin"
      if git remote | grep -q "^fork$"; then
        PUSH_REMOTE="fork"
      fi
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pushing to $PUSH_REMOTE/$BRANCH..." >> "$EXEC_LOG"
      git push -u "$PUSH_REMOTE" "$BRANCH" >> "$EXEC_LOG" 2>&1
      
      GITHUB_USER=$(gh api user --jq .login 2>/dev/null || echo "")
      HEAD_ARG="${GITHUB_USER}:${BRANCH}"
      if [ -z "$GITHUB_USER" ]; then
        HEAD_ARG="$BRANCH"
      fi

      BODY_FILE="$REPO_DIR/.pending_pr_body.md"
      if [ -f "$BODY_FILE" ]; then
        PR_URL=$(gh pr create --repo "$UPSTREAM_REPO" --head "$HEAD_ARG" --title "$PR_TITLE" --body-file "$BODY_FILE" 2>&1 || true)
      else
        PR_URL=$(gh pr create --repo "$UPSTREAM_REPO" --head "$HEAD_ARG" --title "$PR_TITLE" --fill 2>&1 || true)
      fi
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] PR Result: $PR_URL" >> "$EXEC_LOG"
      
      NEW_PR_ID=$(echo "$PR_URL" | grep -oE '[0-9]+$' || true)
      if [ -n "$NEW_PR_ID" ]; then
        ISSUE_URL=$(grep "^ISSUE_URL=" "$PENDING_FILE" 2>/dev/null | cut -d= -f2- || true)
        "$PYTHON_BIN" "$TRACKER_SCRIPT" track \
          --id "$NEW_PR_ID" \
          --repo "$UPSTREAM_REPO" \
          --branch "$BRANCH" \
          --issue-url "$ISSUE_URL" \
          --pr-url "$PR_URL" \
          --title "$PR_TITLE" \
          --local-path "$REPO_DIR" \
          --status "OPEN" \
          --summary-file "$BODY_FILE" >> "$EXEC_LOG" 2>&1 || true
      fi
      
      rm -f "$PENDING_FILE" "$REPO_DIR/.pending_pr_body.md"
      
      curl -s -H "Title: 🎉 PR Created Successfully!" \
           -H "Priority: high" \
           -H "Tags: party_popper,tada,octocat" \
           -H "Actions: view, View Pull Request, $PR_URL" \
           -d "PR created for $UPSTREAM_REPO: '$PR_TITLE'." \
           "https://ntfy.sh/$NTFY_TOPIC" || true
    fi

  elif [ "$SIGNAL" = "REJECT" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Action rejected from phone. Keeping local changes without pushing." >> "$EXEC_LOG"
    rm -f "$PENDING_FILE"
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Approval listener timed out or ended without signal." >> "$EXEC_LOG"
  fi
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Completed Daily Open-Source Contributor Run" >> "$EXEC_LOG"
