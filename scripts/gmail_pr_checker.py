#!/usr/bin/env python3
"""
Gmail PR Checker and Daily Flow Synchronizer.
Pulls PR-related messages from Gmail via Google Cloud OAuth2, detects review feedback,
requested changes, merges, and closures.
Automatically cleans up merged/closed PRs from ledger logs, removes cloned directories,
and preserves the lifetime count of all PRs made and completed.
"""

import sys
import os
import re
import json
import base64
import shutil
import argparse
import subprocess
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

# Automatically detect and attach virtual environment packages if present
POSSIBLE_VENVS = [
    os.path.join(ROOT_DIR, ".venv"),
    os.path.join(SCRIPT_DIR, ".venv"),
]
for venv in POSSIBLE_VENVS:
    if os.path.exists(venv):
        for py_dir in os.listdir(os.path.join(venv, "lib")):
            site_pkgs = os.path.join(venv, "lib", py_dir, "site-packages")
            if os.path.exists(site_pkgs) and site_pkgs not in sys.path:
                sys.path.insert(0, site_pkgs)
        break

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

WORKSPACE_DIR = os.environ.get("CONTRIBUTIONS_DIR", ROOT_DIR)
TRACKER_JSON = os.path.join(WORKSPACE_DIR, "contributions.json")
TRACKER_MD = os.path.join(WORKSPACE_DIR, "CONTRIBUTIONS.md")

DEFAULT_CREDENTIALS_PATHS = [
    os.path.join(ROOT_DIR, "credentials.json"),
    os.path.join(SCRIPT_DIR, "credentials.json"),
    os.path.join(WORKSPACE_DIR, "credentials.json"),
    os.path.expanduser("~/.config/oss-contributor/credentials.json"),
]

DEFAULT_TOKEN_PATHS = [
    os.path.join(ROOT_DIR, "token.json"),
    os.path.join(SCRIPT_DIR, "token.json"),
    os.path.join(WORKSPACE_DIR, "token.json"),
    os.path.expanduser("~/.config/oss-contributor/token.json"),
]

GH_BIN = "/opt/homebrew/bin/gh" if os.path.exists("/opt/homebrew/bin/gh") else "gh"

BOT_LOGINS = {
    "copilot-pull-request-reviewer",
    "codspeed",
    "cubic-dev-ai",
    "codecov",
    "dependabot",
    "github-actions",
}


def is_bot_user(login):
    if not login:
        return True
    login_lower = login.lower()
    return login_lower in BOT_LOGINS or login_lower.endswith("[bot]")


def resolve_credentials_file(custom_path=None):
    if custom_path and os.path.exists(custom_path):
        return custom_path
    env_path = os.environ.get("GCP_CREDENTIALS_PATH")
    if env_path and os.path.exists(env_path):
        return env_path
    for p in DEFAULT_CREDENTIALS_PATHS:
        if os.path.exists(p):
            return p
    return DEFAULT_CREDENTIALS_PATHS[0]


def resolve_token_file(custom_path=None):
    if custom_path:
        return custom_path
    env_path = os.environ.get("GCP_TOKEN_PATH")
    if env_path:
        return env_path
    for p in DEFAULT_TOKEN_PATHS:
        if os.path.exists(p):
            return p
    return DEFAULT_TOKEN_PATHS[0]


def get_gmail_credentials(credentials_file=None, token_file=None, interactive=True):
    """
    Authenticate with Google OAuth2 using client secrets and preserve token.json.
    """
    if not GOOGLE_LIBS_AVAILABLE:
        raise RuntimeError(
            "Google API libraries not installed. Please run:\n"
            "  pip install -r requirements.txt"
        )

    creds_path = resolve_credentials_file(credentials_file)
    token_path = resolve_token_file(token_file)

    creds = None
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception as e:
            print(f"⚠️ Warning: Could not load existing token from {token_path}: {e}", file=sys.stderr)
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            print("🔄 Refreshing expired GCP access token using refresh_token...", file=sys.stderr)
            creds.refresh(Request())
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            os.chmod(token_path, 0o600)
            print(f"✅ Token refreshed and saved to {token_path}", file=sys.stderr)
            return creds
        except Exception as e:
            print(f"⚠️ Warning: Token refresh failed: {e}. Re-authenticating...", file=sys.stderr)
            creds = None

    if not interactive:
        return None

    if not os.path.exists(creds_path):
        raise FileNotFoundError(
            f"GCP credentials file not found at: '{creds_path}'.\n"
            f"Please place your OAuth 2.0 Client ID JSON file from GCP at '{creds_path}'\n"
            f"or specify --credentials /path/to/credentials.json."
        )

    print(f"🔑 Initiating Google OAuth2 authorization flow using: {creds_path}...", file=sys.stderr)
    flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
    creds = flow.run_local_server(
        port=0,
        authorization_prompt_message="\n👉 Please visit this URL to authorize the application in your browser:\n{url}\n",
        success_message="✅ Authentication complete! Token has been preserved. You may now close this browser tab.",
        open_browser=True
    )

    # Preserve token.json
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    os.chmod(token_path, 0o600)
    print(f"✅ Authentication successful! Preserved token at: {token_path}", file=sys.stderr)
    return creds


def load_ledger():
    """
    Load contributions ledger from contributions.json.
    Supports both new schema:
      {"stats": {"total_prs_made": X, "total_prs_completed": Y}, "prs": [...]}
    and legacy list schema:
      [...]
    """
    if not os.path.exists(TRACKER_JSON):
        return {
            "stats": {"total_prs_made": 0, "total_prs_completed": 0, "total_prs_closed": 0},
            "prs": []
        }

    try:
        with open(TRACKER_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            total_made = len(data)
            total_completed = sum(1 for p in data if "MERGED" in str(p.get("status", "")).upper())
            total_closed = sum(1 for p in data if "CLOSED" in str(p.get("status", "")).upper())
            return {
                "stats": {
                    "total_prs_made": total_made,
                    "total_prs_completed": total_completed,
                    "total_prs_closed": total_closed,
                },
                "prs": data
            }
        elif isinstance(data, dict):
            if "prs" not in data:
                data["prs"] = []
            if "stats" not in data:
                data["stats"] = {
                    "total_prs_made": len(data["prs"]),
                    "total_prs_completed": sum(1 for p in data["prs"] if "MERGED" in str(p.get("status", "")).upper()),
                    "total_prs_closed": sum(1 for p in data["prs"] if "CLOSED" in str(p.get("status", "")).upper())
                }
            return data
    except Exception as e:
        print(f"⚠️ Error reading {TRACKER_JSON}: {e}", file=sys.stderr)
        return {
            "stats": {"total_prs_made": 0, "total_prs_completed": 0, "total_prs_closed": 0},
            "prs": []
        }


def save_ledger(ledger):
    """Save ledger to contributions.json and regenerate CONTRIBUTIONS.md."""
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    with open(TRACKER_JSON, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2, ensure_ascii=False)
    generate_markdown(ledger)


def generate_markdown(ledger):
    """Generate CONTRIBUTIONS.md tracking active PRs and lifetime counters."""
    stats = ledger.get("stats", {})
    prs = ledger.get("prs", [])

    total_made = stats.get("total_prs_made", len(prs))
    total_completed = stats.get("total_prs_completed", 0)
    active_count = len(prs)

    lines = [
        "# 🚀 Open-Source Contributions Ledger",
        "",
        "This file tracks pull requests created by the autonomous contributor bot.",
        "",
        "### 📊 Overall Statistics",
        f"- **Total PRs Made**: {total_made}",
        f"- **Successfully Completed / Merged**: {total_completed}",
        f"- **Currently Active / In Review**: {active_count}",
        "",
        "### Quick CLI Shortcuts:",
        "- `goto-pr <NO|ID>` — Jump directly to a contribution workspace and checkout branch",
        "- `list-pr` — Table of active contributions with live statuses",
        "- `list-pr --latest` — Full detailed inspection of the latest PR",
        "- `continue-pr <NO|ID> --accept` — Manually approve & push without waiting for phone",
        "- `continue-pr <NO|ID> --reject` — Manually discard pending PR proposal",
        "",
        "---",
        "",
        "## 📋 Active PRs Under Review",
        "",
        "| No. | PR ID | Repository | Branch | Status | Session | Issue | Local Path |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    if not prs:
        lines.append("| - | - | _No active PRs pending review_ | - | - | - | - | - |")
    else:
        for p in sorted(prs, key=lambda x: int(x.get("contribution_no", 0)), reverse=True):
            c_no = p.get("contribution_no", "")
            pr_id = p.get("id", "")
            repo = p.get("repo", "")
            branch = p.get("branch", "")
            status = p.get("status", "OPEN")
            issue_url = p.get("issue_url", "")
            pr_url = p.get("pr_url", "")
            session = p.get("session_date", p.get("created_at", "")[:10])
            local_path = p.get("local_path", "")

            status_badge = "🟢 OPEN"
            if "MERGED" in status.upper():
                status_badge = "🟣 MERGED"
            elif "CHANGES_REQUESTED" in status.upper():
                status_badge = "🟠 CHANGES REQUESTED"
            elif "APPROVED" in status.upper():
                status_badge = "🟢 APPROVED"
            elif "CLOSED" in status.upper():
                status_badge = "🔴 CLOSED"

            pr_link = f"[#{pr_id}]({pr_url})" if pr_url else f"#{pr_id}"
            issue_link = f"[Issue]({issue_url})" if issue_url else "-"
            repo_link = f"[{repo}](https://github.com/{repo})"
            path_link = f"`{local_path}`" if local_path else "-"

            lines.append(f"| **#{c_no}** | {pr_link} | {repo_link} | `{branch}` | {status_badge} | {session} | {issue_link} | {path_link} |")

    lines.extend([
        "",
        "---",
        "",
        "## 📜 Detailed Active Contribution Logs",
        "",
    ])

    if not prs:
        lines.append("_All previous PRs are completed or merged! No active PRs requiring changes._\n")
    else:
        for p in sorted(prs, key=lambda x: int(x.get("contribution_no", 0)), reverse=True):
            c_no = p.get("contribution_no", "")
            pr_id = p.get("id", "")
            repo = p.get("repo", "")
            branch = p.get("branch", "")
            title = p.get("title", "")
            status = p.get("status", "OPEN")
            issue_url = p.get("issue_url", "")
            pr_url = p.get("pr_url", "")
            session = p.get("session_date", p.get("created_at", "")[:10])
            local_path = p.get("local_path", "")
            summary = p.get("summary", "").strip().replace("\\n", "\n")

            lines.extend([
                f"### Contribution #{c_no}: PR #{pr_id} — {repo}",
                "",
                f"- **Session Date**: {session}",
                f"- **Title**: {title}",
                f"- **Pull Request**: [{pr_url}]({pr_url})" if pr_url else f"- **Pull Request**: #{pr_id}",
                f"- **Associated Issue**: [{issue_url}]({issue_url})" if issue_url else "- **Associated Issue**: None",
                f"- **Branch**: `{branch}`",
                f"- **Local Directory**: `{local_path}`",
                f"- **Status**: `{status}`",
                "",
                "#### Summary of Changes",
                summary if summary else "_No summary provided._",
                "",
                "#### Fast Navigation",
                "```bash",
                f"goto-pr {c_no}    # or goto-pr {pr_id}",
                "```",
                "",
                "---",
                "",
            ])

    with open(TRACKER_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + "\n")


def decode_body_payload(payload):
    """Extract and decode text/plain or text/html from a Gmail message payload."""
    body_text = ""
    parts = [payload]
    while parts:
        part = parts.pop(0)
        mime_type = part.get("mimeType", "")
        data = part.get("body", {}).get("data", "")
        if data:
            try:
                decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                if mime_type == "text/plain":
                    body_text += "\n" + decoded
                elif mime_type == "text/html" and not body_text:
                    body_text += "\n" + decoded
            except Exception:
                pass
        sub_parts = part.get("parts", [])
        if sub_parts:
            parts.extend(sub_parts)
    return body_text.strip()


def query_gmail_pr_messages(service, query="from:notifications@github.com newer_than:30d", max_results=50):
    """Fetch message threads matching query from Gmail."""
    try:
        results = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        messages = results.get("messages", [])
        parsed_emails = []

        for m in messages:
            msg_id = m.get("id")
            msg_full = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
            payload = msg_full.get("payload", {})
            headers = {h.get("name", "").lower(): h.get("value", "") for h in payload.get("headers", [])}

            subject = headers.get("subject", "")
            date_str = headers.get("date", "")
            from_str = headers.get("from", "")
            snippet = msg_full.get("snippet", "")
            body = decode_body_payload(payload)

            parsed_emails.append({
                "id": msg_id,
                "subject": subject,
                "date": date_str,
                "from": from_str,
                "snippet": snippet,
                "body": body,
            })
        return parsed_emails
    except Exception as e:
        print(f"⚠️ Error fetching Gmail messages: {e}", file=sys.stderr)
        return []


def parse_pr_from_email(email_item, tracked_prs):
    """Match an email against known active tracked PRs."""
    subject = email_item.get("subject", "")
    body = email_item.get("body", "")
    snippet = email_item.get("snippet", "")
    full_text = f"{subject}\n{snippet}\n{body}"

    for pr in tracked_prs:
        pr_id = str(pr.get("id", ""))
        repo = pr.get("repo", "")
        repo_name_only = repo.split("/")[-1] if "/" in repo else repo

        id_pattern = rf"(?:#|PR\s*#|pull/){pr_id}\b"
        if re.search(id_pattern, full_text) and (repo.lower() in full_text.lower() or repo_name_only.lower() in full_text.lower()):
            status_type = "UNKNOWN"
            feedback = ""

            if re.search(r"Merged\s+#" + pr_id, full_text, re.IGNORECASE) or \
               "[Merged by GitHub]" in subject or \
               re.search(r"merged commit [0-9a-f]{7,40} into", full_text, re.IGNORECASE):
                status_type = "MERGED"

            elif re.search(r"Closed\s+#" + pr_id, full_text, re.IGNORECASE) or \
                 re.search(r"closed this pull request", full_text, re.IGNORECASE) or \
                 "[Closed]" in subject:
                status_type = "CLOSED"

            elif re.search(r"requested changes", full_text, re.IGNORECASE) or \
                 re.search(r"changes requested", full_text, re.IGNORECASE):
                status_type = "CHANGES_REQUESTED"
                feedback = snippet

            elif re.search(r"approved these changes", full_text, re.IGNORECASE):
                status_type = "APPROVED"

            elif re.search(r"commented on this pull request", full_text, re.IGNORECASE) or \
                 re.search(r"left a comment", full_text, re.IGNORECASE):
                status_type = "COMMENT"
                feedback = snippet

            return pr, status_type, feedback

    return None, None, None


def query_gh_pr_state(pr_id, repo):
    """Fallback/cross-verification via GitHub CLI (gh)."""
    cmd = [
        GH_BIN, "pr", "view", str(pr_id), "--repo", repo,
        "--json", "state,reviewDecision,comments,latestReviews,url,title"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        try:
            return json.loads(res.stdout)
        except Exception:
            return None
    return None


def cleanup_pr_directory(local_path, remaining_prs):
    """Remove local repository directory cloned for this PR if no remaining active PR relies on it."""
    if not local_path or not os.path.exists(local_path):
        return False, "Directory does not exist on disk."

    other_users = [
        p for p in remaining_prs
        if p.get("local_path") and os.path.abspath(p.get("local_path", "")) == os.path.abspath(local_path)
    ]

    if other_users:
        return False, f"Directory preserved: Still in use by active PR(s): {[p.get('id') for p in other_users]}"

    try:
        shutil.rmtree(local_path)
        return True, f"Successfully removed cloned directory: {local_path}"
    except Exception as e:
        return False, f"Failed to remove directory {local_path}: {e}"


def cleanup_orphan_directories(active_prs):
    """Remove any directories in WORKSPACE_DIR that were cloned for PRs but have no active PRs remaining."""
    active_paths = {os.path.abspath(p.get("local_path")) for p in active_prs if p.get("local_path")}
    cleaned = []
    if not os.path.exists(WORKSPACE_DIR):
        return cleaned
    for item in os.listdir(WORKSPACE_DIR):
        full_path = os.path.join(WORKSPACE_DIR, item)
        if os.path.isdir(full_path) and not item.startswith("."):
            if os.path.exists(os.path.join(full_path, ".git")):
                if os.path.abspath(full_path) not in active_paths:
                    try:
                        shutil.rmtree(full_path)
                        cleaned.append(full_path)
                        print(f"🗑️ Removed stale cloned repository: {full_path}", file=sys.stderr)
                    except Exception as e:
                        print(f"⚠️ Failed to remove stale repository {full_path}: {e}", file=sys.stderr)
    return cleaned


def sync_and_clean_prs(credentials_file=None, token_file=None, interactive=False):
    """
    Main sync and clean routine:
    1. Authenticate with Gmail API (if token exists or credentials available).
    2. Pull messages for active PRs.
    3. Update statuses according to email / gh CLI.
    4. Delete closed/merged PRs from logs while preserving lifetime counts.
    5. Remove cloned directories for merged/closed PRs.
    6. Return action report.
    """
    ledger = load_ledger()
    stats = ledger["stats"]
    active_prs = ledger["prs"]

    emails = []
    try:
        creds = get_gmail_credentials(credentials_file, token_file, interactive=interactive)
        if creds:
            service = build("gmail", "v1", credentials=creds)
            print("📬 Querying Gmail for GitHub PR notifications...", file=sys.stderr)
            emails = query_gmail_pr_messages(service)
            print(f"📧 Retrieved {len(emails)} recent GitHub notification emails from Gmail.", file=sys.stderr)
    except Exception as e:
        print(f"ℹ️ Gmail API note: {e}. Falling back to GitHub CLI check.", file=sys.stderr)

    remaining_prs = []
    to_remove = []
    removed_prs = []
    pending_review_action = None

    # Step 1: Detect status for all currently tracked PRs
    for pr in active_prs:
        pr_id = str(pr.get("id"))
        repo = pr.get("repo")
        local_path = pr.get("local_path")
        current_status = pr.get("status", "OPEN")

        detected_status = current_status
        feedback_text = ""

        # 1. Check Gmail notifications
        for em in emails:
            m_pr, m_status, m_feedback = parse_pr_from_email(em, [pr])
            if m_pr and m_status:
                detected_status = m_status
                if m_feedback:
                    feedback_text = m_feedback
                break

        # 2. Cross-verify with GitHub CLI if available
        gh_data = query_gh_pr_state(pr_id, repo)
        if gh_data:
            gh_state = gh_data.get("state", "").upper()
            gh_decision = (gh_data.get("reviewDecision") or "").upper()
            if gh_state in ("MERGED", "CLOSED"):
                detected_status = gh_state
            elif gh_decision == "CHANGES_REQUESTED":
                detected_status = "CHANGES_REQUESTED"
                reviews = gh_data.get("latestReviews", [])
                for r in reviews:
                    if r.get("state") == "CHANGES_REQUESTED" and r.get("body"):
                        feedback_text = r["body"]
            elif gh_decision == "APPROVED":
                detected_status = "APPROVED"
            elif gh_state == "OPEN" and detected_status == "COMMENT":
                detected_status = "OPEN"

            # Check for actionable maintainer comments
            comments = gh_data.get("comments", [])
            last_maintainer_comment = None
            last_author_comment = None
            for c in comments:
                c_author = c.get("author", {}).get("login", "")
                is_viewer = c.get("viewerDidAuthor", False)
                if is_viewer:
                    last_author_comment = c
                elif not is_bot_user(c_author):
                    last_maintainer_comment = c

            if last_maintainer_comment:
                m_time = last_maintainer_comment.get("createdAt", "")
                a_time = last_author_comment.get("createdAt", "") if last_author_comment else ""
                if not a_time or m_time > a_time:
                    body_lower = last_maintainer_comment.get("body", "").lower()
                    if gh_decision == "APPROVED" or "approved" in body_lower or "ready to merge" in body_lower:
                        pass
                    else:
                        detected_status = "CHANGES_REQUESTED"
                        latest_reviewer = last_maintainer_comment.get("author", {}).get("login", "")
                        feedback_text = f"[@{latest_reviewer}]: {last_maintainer_comment.get('body', '').strip()}"

        if detected_status == "COMMENT":
            detected_status = "OPEN"

        print(f"🔍 Checked PR #{pr_id} ({repo}): Status = {detected_status}", file=sys.stderr)

        if detected_status in ("MERGED", "CLOSED"):
            to_remove.append({"pr": pr, "status": detected_status})
        else:
            pr["status"] = detected_status
            if feedback_text:
                pr["feedback"] = feedback_text
            remaining_prs.append(pr)

            if detected_status == "CHANGES_REQUESTED":
                pending_review_action = {
                    "status": "NEEDS_REVIEW_ACTION",
                    "pr_id": pr_id,
                    "repo": repo,
                    "branch": pr.get("branch"),
                    "local_path": local_path,
                    "pr_url": pr.get("pr_url"),
                    "issue_url": pr.get("issue_url"),
                    "feedback": feedback_text or "Maintainer requested changes in review.",
                }

    # Step 2: Process removals and directory cleanup
    for item in to_remove:
        pr = item["pr"]
        status = item["status"]
        pr_id = str(pr.get("id"))
        repo = pr.get("repo")
        local_path = pr.get("local_path")

        if status == "MERGED":
            stats["total_prs_completed"] = stats.get("total_prs_completed", 0) + 1
        elif status == "CLOSED":
            stats["total_prs_closed"] = stats.get("total_prs_closed", 0) + 1

        deleted_dir, dir_msg = cleanup_pr_directory(local_path, remaining_prs)
        removed_prs.append({
            "pr_id": pr_id,
            "repo": repo,
            "status": status,
            "dir_cleaned": deleted_dir,
            "dir_message": dir_msg
        })
        badge = "🟣" if status == "MERGED" else "🔴"
        print(f"{badge} PR #{pr_id} ({repo}) is {status}. Removed from logs. {dir_msg}", file=sys.stderr)

    # Step 3: Remove any leftover/orphan cloned repositories whose PRs have already been closed/merged
    orphans = cleanup_orphan_directories(remaining_prs)
    for o in orphans:
        removed_prs.append({
            "pr_id": "orphan",
            "repo": os.path.basename(o),
            "status": "CLEANED",
            "dir_cleaned": True,
            "dir_message": f"Cleaned up stale repository directory: {o}"
        })

    # Ensure total_prs_made preserves all PRs ever made
    ledger["stats"] = stats
    ledger["prs"] = remaining_prs
    save_ledger(ledger)

    return {
        "stats": stats,
        "removed_prs": removed_prs,
        "active_prs_count": len(remaining_prs),
        "pending_review_action": pending_review_action,
    }


def main():
    parser = argparse.ArgumentParser(description="Gmail PR Sync & Daily OSS Flow Controller")
    parser.add_argument("--auth", action="store_true", help="Trigger interactive GCP OAuth authentication flow and save token.json")
    parser.add_argument("--sync-and-clean", action="store_true", help="Pull emails, sync PR statuses, remove closed/merged PRs and their directories")
    parser.add_argument("--check-flow", action="store_true", help="Check if review updates are needed before proceeding with new PR")
    parser.add_argument("--stats", action="store_true", help="Print overall statistics of PRs made, completed, and active")
    parser.add_argument("--credentials", help="Custom path to GCP credentials.json")
    parser.add_argument("--token", help="Custom path to token.json")

    args = parser.parse_args()

    if args.auth:
        try:
            get_gmail_credentials(args.credentials, args.token, interactive=True)
            print("✨ Authentication complete! token.json is ready for daily headless runs.")
        except Exception as e:
            print(f"❌ Authentication failed: {e}", file=sys.stderr)
            sys.exit(1)
        return

    if args.stats:
        ledger = load_ledger()
        s = ledger.get("stats", {})
        print("\n" + "=" * 50)
        print("📊 CONTRIBUTIONS OVERALL PERFORMANCE")
        print("=" * 50)
        print(f"• Total PRs Made:                 {s.get('total_prs_made', len(ledger.get('prs', [])))}")
        print(f"• Successfully Completed/Merged:  {s.get('total_prs_completed', 0)}")
        print(f"• Currently Active / In Review:   {len(ledger.get('prs', []))}")
        print("=" * 50 + "\n")
        return

    if args.check_flow or args.sync_and_clean:
        result = sync_and_clean_prs(args.credentials, args.token, interactive=False)

        if result.get("pending_review_action"):
            act = result["pending_review_action"]
            print(json.dumps(act, indent=2))
            sys.exit(10)
        else:
            summary = {
                "status": "READY_FOR_NEW_CONTRIBUTION",
                "message": "All previous PRs checked. Merged/closed PRs cleaned. Ready for today's new contribution.",
                "stats": result["stats"],
                "active_prs": result["active_prs_count"],
                "removed_prs": result["removed_prs"],
            }
            print(json.dumps(summary, indent=2))
            sys.exit(0)

    parser.print_help()


if __name__ == "__main__":
    main()
