#!/usr/bin/env python3
"""
PR Tracker and Manager for Daily OSS Contributions.
Manages contributions.json, generates CONTRIBUTIONS.md in the workspace,
and provides instant CLI management: goto-pr, list-pr, and continue-pr.
"""

import sys
import os
import re
import json
import argparse
import subprocess
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

WORKSPACE_DIR = os.environ.get("CONTRIBUTIONS_DIR", ROOT_DIR)
TRACKER_JSON = os.path.join(WORKSPACE_DIR, "contributions.json")
TRACKER_MD = os.path.join(WORKSPACE_DIR, "CONTRIBUTIONS.md")

os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")

GH_BIN = "/opt/homebrew/bin/gh" if os.path.exists("/opt/homebrew/bin/gh") else "gh"
TOPIC = os.environ.get("NTFY_TOPIC", "kasa-oss-contributor")
NTFY_TOPIC = TOPIC

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
    return login_lower in BOT_LOGINS or login_lower.endswith("[bot]") or "bot" in login_lower


def load_ledger():
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
    except Exception:
        return {
            "stats": {"total_prs_made": 0, "total_prs_completed": 0, "total_prs_closed": 0},
            "prs": []
        }


def load_prs():
    ledger = load_ledger()
    prs = ledger.get("prs", [])
    chrono = sorted(prs, key=lambda x: x.get("created_at", ""))
    for idx, p in enumerate(chrono, 1):
        if not p.get("contribution_no"):
            p["contribution_no"] = idx
        if not p.get("session_date"):
            p["session_date"] = p.get("created_at", "")[:10]
    return prs


def save_prs(prs, stats=None):
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    ledger = load_ledger()
    if stats:
        ledger["stats"] = stats
    elif "stats" not in ledger:
        ledger["stats"] = {
            "total_prs_made": len(prs),
            "total_prs_completed": sum(1 for p in prs if "MERGED" in str(p.get("status", "")).upper()),
            "total_prs_closed": sum(1 for p in prs if "CLOSED" in str(p.get("status", "")).upper())
        }

    chrono = sorted(prs, key=lambda x: x.get("created_at", ""))
    for idx, p in enumerate(chrono, 1):
        p["contribution_no"] = idx
        if not p.get("session_date"):
            p["session_date"] = p.get("created_at", "")[:10]

    ledger["prs"] = chrono
    with open(TRACKER_JSON, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2, ensure_ascii=False)
    generate_markdown(chrono, ledger["stats"])


def generate_markdown(prs, stats=None):
    if stats is None:
        stats = load_ledger().get("stats", {})

    total_made = stats.get("total_prs_made", len(prs))
    total_completed = stats.get("total_prs_completed", 0)
    active_count = len(prs)

    lines = [
        "# 🚀 Open-Source Contributions Ledger",
        "",
        "This file tracks all pull requests created by the autonomous contributor bot.",
        "",
        "### 📊 Overall Statistics",
        f"- **Total PRs Made**: {total_made}",
        f"- **Successfully Completed / Merged**: {total_completed}",
        f"- **Currently Active / In Review**: {active_count}",
        "",
        "### Quick CLI Shortcuts:",
        "- `goto-pr <NO|ID>` — Jump directly to a contribution workspace and checkout branch",
        "- `list-pr` — Table of all contributions with live statuses",
        "- `list-pr --latest` — Full detailed inspection of the latest PR",
        "- `continue-pr <NO|ID> --accept` — Manually approve & push without waiting for phone",
        "- `continue-pr <NO|ID> --reject` — Manually discard pending PR proposal",
        "",
        "---",
        "",
        "## 📋 Quick Jump Table",
        "",
        "| No. | PR ID | Repository | Branch | Status | Session | Issue | Local Path |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

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
        "## 📜 Detailed Contribution Logs",
        "",
    ])

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


def find_pr(prs, target):
    """Find PR by contribution_no, PR ID, or repo keyword."""
    if not target:
        return None
    target_clean = str(target).strip().lstrip("#")

    for p in prs:
        if str(p.get("contribution_no")) == target_clean:
            return p

    for p in prs:
        if str(p.get("id")) == target_clean:
            return p

    target_lower = target_clean.lower()
    for p in prs:
        if target_lower in p.get("repo", "").lower():
            return p

    return None


def cmd_track(args):
    ledger = load_ledger()
    prs = ledger.get("prs", [])
    stats = ledger.get("stats", {})
    pr_id = str(args.id).strip().lstrip("#")

    existing = None
    for p in prs:
        if str(p.get("id")) == pr_id:
            existing = p
            break

    if existing is None:
        stats["total_prs_made"] = stats.get("total_prs_made", len(prs)) + 1
        existing = {"id": pr_id}
        prs.append(existing)

    if args.repo:
        existing["repo"] = args.repo
    if args.branch:
        existing["branch"] = args.branch
    if args.issue_url:
        existing["issue_url"] = args.issue_url
    if args.pr_url:
        existing["pr_url"] = args.pr_url
    if args.title:
        existing["title"] = args.title
    if args.local_path:
        existing["local_path"] = args.local_path
    if args.status:
        existing["status"] = args.status
    if not existing.get("created_at"):
        existing["created_at"] = datetime.now().isoformat()
    if not existing.get("session_date"):
        existing["session_date"] = existing["created_at"][:10]

    summary_text = ""
    if args.summary_file and os.path.exists(args.summary_file):
        with open(args.summary_file, "r", encoding="utf-8") as f:
            summary_text = f.read()
    elif args.summary:
        summary_text = args.summary

    if summary_text:
        existing["summary"] = summary_text

    save_prs(prs, stats=stats)
    print(f"✅ Successfully tracked PR #{pr_id} in {TRACKER_MD} and {TRACKER_JSON}")


def cmd_goto(args):
    prs = load_prs()
    found = find_pr(prs, args.id)

    if not found:
        target_id = str(args.id).strip().lstrip("#")
        print(f"⚠️ Target '{args.id}' not found in local tracker. Querying GitHub CLI...")
        res = subprocess.run([GH_BIN, "pr", "view", target_id, "--json", "number,url,title,headRefName"],
                             capture_output=True, text=True)
        if res.returncode == 0:
            info = json.loads(res.stdout)
            found = {
                "contribution_no": "?",
                "id": str(info.get("number")),
                "pr_url": info.get("url"),
                "title": info.get("title"),
                "branch": info.get("headRefName"),
                "local_path": "",
            }
        else:
            print(f"❌ Could not find contribution or PR '{args.id}' locally or on GitHub.")
            sys.exit(1)

    local_path = found.get("local_path")
    branch = found.get("branch")
    repo = found.get("repo")
    c_no = found.get("contribution_no", "?")

    if getattr(args, "cd_path", False):
        if local_path and os.path.exists(local_path):
            print(local_path)
            return
        sys.exit(1)

    print("=" * 70)
    print(f"🎯 CONTRIBUTION #{c_no}: PR #{found.get('id')} — {found.get('title')}")
    print(f"📦 Repository:    {repo}")
    print(f"🌿 Branch:        {branch}")
    print(f"🔗 PR URL:        {found.get('pr_url')}")
    print(f"📂 Local Path:    {local_path}")
    print("=" * 70)

    if local_path and os.path.exists(local_path):
        print(f"\n📂 Checking out branch '{branch}' in {local_path}...")
        subprocess.run(["git", "checkout", branch], cwd=local_path)
        subprocess.run(["git", "status", "-s"], cwd=local_path)

        if repo and found.get("id"):
            print("\n💬 Latest Review / Comments on GitHub:")
            subprocess.run([GH_BIN, "pr", "view", str(found["id"]), "--repo", repo, "--comments"])
        print("\n" + "=" * 70)
        print(f"✨ Ready! You are now set up in Contribution #{c_no} (PR #{found.get('id')}).")
        print(f"To navigate directly in your current shell: cd \"{local_path}\"")
        print("=" * 70)
    else:
        print(f"⚠️ Local directory '{local_path}' does not exist on disk.")


def cmd_list(args):
    prs = load_prs()
    if not prs:
        print("No PRs recorded yet.")
        return

    target = getattr(args, "target", None)
    latest = getattr(args, "latest", False)

    if latest:
        sorted_by_no = sorted(prs, key=lambda x: int(x.get("contribution_no", 0)), reverse=True)
        print_detailed_pr(sorted_by_no[0])
        return

    if target:
        found = find_pr(prs, target)
        if found:
            print_detailed_pr(found)
            return
        else:
            print(f"❌ Contribution or PR '{target}' not found in ledger.")
            return

    ledger = load_ledger()
    stats = ledger.get("stats", {})
    total_made = stats.get("total_prs_made", len(prs))
    total_completed = stats.get("total_prs_completed", 0)

    print("\n" + "=" * 90)
    print(f"{'NO.':<5} {'PR ID':<8} {'REPOSITORY':<26} {'BRANCH':<18} {'STATUS':<15} {'SESSION DATE'}")
    print("=" * 90)
    for p in sorted(prs, key=lambda x: int(x.get("contribution_no", 0)), reverse=True):
        c_no = f"#{p.get('contribution_no', '')}"
        pr_id = f"#{p.get('id', '')}"
        repo = p.get('repo', '')
        branch = p.get('branch', '')
        status = p.get('status', 'OPEN')
        session = p.get('session_date', p.get('created_at', '')[:10])
        print(f"{c_no:<5} {pr_id:<8} {repo:<26} {branch:<18} {status:<15} {session}")
    print("=" * 90)
    print(f"Total PRs Made: {total_made} | Completed/Merged: {total_completed} | Active: {len(prs)} | Tip: run 'list-pr --latest' for latest PR.\n")


def print_detailed_pr(p):
    c_no = p.get("contribution_no", "?")
    pr_id = p.get("id", "")
    repo = p.get("repo", "")
    branch = p.get("branch", "")
    pr_url = p.get("pr_url", "")
    issue_url = p.get("issue_url", "")
    title = p.get("title", "")
    local_path = p.get("local_path", "")
    status = p.get("status", "OPEN")
    session = p.get("session_date", p.get("created_at", "")[:10])
    summary = p.get("summary", "").strip().replace("\\n", "\n")

    print("\n" + "=" * 80)
    print(f"🎯 CONTRIBUTION #{c_no} — {repo}")
    print("=" * 80)
    print(f"📌 Title:         {title}")
    print(f"🏷️  PR Number:     #{pr_id}")
    print(f"🔗 Pull Request:   {pr_url}")
    print(f"🎯 Issue:          {issue_url}")
    print(f"🌿 Branch:         {branch}")
    print(f"📂 Local Path:     {local_path}")
    print(f"📅 Session Date:   {session}")
    print(f"⚡ Status:         {status}")
    print("-" * 80)
    print("📜 SUMMARY OF CHANGES:")
    print(summary if summary else "No summary recorded.")
    print("-" * 80)
    print(f"🚀 Quick Commands:")
    print(f"  • Jump to workspace:  goto-pr {c_no}   (or goto-pr {pr_id})")
    print(f"  • View on GitHub:     gh pr view {pr_id} --repo {repo}")
    print("=" * 80 + "\n")


def cmd_continue(args):
    accept = getattr(args, "accept", False)
    reject = getattr(args, "reject", False)

    if not accept and not reject:
        print("❌ Please specify either --accept or --reject.")
        print("Usage: continue-pr [contribution-no | pr-id | repo] --accept")
        print("       continue-pr [contribution-no | pr-id | repo] --reject")
        sys.exit(1)

    subprocess.run(["pkill", "-f", "wait_for_approval.py"], capture_output=True)

    target = getattr(args, "target", None)
    pending_file = None

    if target:
        prs = load_prs()
        matched = find_pr(prs, target)
        if matched and matched.get("local_path"):
            cand = os.path.join(matched["local_path"], ".pending_pr.txt")
            if os.path.exists(cand):
                pending_file = cand
        if not pending_file:
            cand = os.path.join(WORKSPACE_DIR, target, ".pending_pr.txt")
            if os.path.exists(cand):
                pending_file = cand

    if not pending_file:
        res = subprocess.run(["find", WORKSPACE_DIR, "-maxdepth", "2", "-name", ".pending_pr.txt"],
                             capture_output=True, text=True)
        found_files = [f.strip() for f in res.stdout.splitlines() if f.strip()]
        if found_files:
            found_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            pending_file = found_files[0]

    if not pending_file or not os.path.exists(pending_file):
        print("ℹ️ No pending PR creation or review update found in the workspace.")
        sys.exit(0)

    repo_dir = os.path.dirname(pending_file)
    body_file = os.path.join(repo_dir, ".pending_pr_body.md")

    meta = {}
    with open(pending_file, "r", encoding="utf-8") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                meta[k] = v

    action = meta.get("ACTION", "CREATE_PR")
    branch = meta.get("BRANCH", "")
    upstream_repo = meta.get("UPSTREAM_REPO", "")
    pr_title = meta.get("PR_TITLE", "")
    pr_id = meta.get("PR_ID", "")
    pr_url = meta.get("PR_URL", "")
    issue_url = meta.get("ISSUE_URL", "")

    if reject:
        print(f"🚫 Rejecting pending {action} for {upstream_repo} ({branch})...")
        if os.path.exists(pending_file):
            os.remove(pending_file)
        if os.path.exists(body_file):
            os.remove(body_file)
        print("✅ Pending action canceled. Local branch preserved without pushing.")
        sys.exit(0)

    print(f"🚀 Accepting and executing {action} for {upstream_repo} on branch '{branch}'...")
    os.chdir(repo_dir)

    if action == "UPDATE_PR":
        remotes_res = subprocess.run(["git", "remote"], capture_output=True, text=True)
        push_remote = "fork" if "fork" in remotes_res.stdout.splitlines() else "origin"
        print(f"📤 Pushing updated commits to {push_remote}/{branch}...")
        subprocess.run(["git", "push", push_remote, branch], check=True)

        if os.path.exists(body_file):
            print(f"💬 Posting response comment to PR #{pr_id} on {upstream_repo}...")
            subprocess.run([GH_BIN, "pr", "comment", str(pr_id), "--repo", upstream_repo, "--body-file", body_file], check=True)

        prs = load_prs()
        cmd_track(argparse.Namespace(
            id=pr_id, repo=upstream_repo, branch=branch,
            issue_url=issue_url, pr_url=pr_url, title=pr_title,
            local_path=repo_dir, status="OPEN", summary=None, summary_file=body_file
        ))

        if os.path.exists(pending_file):
            os.remove(pending_file)
        if os.path.exists(body_file):
            os.remove(body_file)

        subprocess.run(["curl", "-s",
                        "-H", f"Title: 🎉 PR #{pr_id} Updated Successfully!",
                        "-H", "Priority: high",
                        "-H", "Tags: party_popper,tada,octocat",
                        "-H", f"Actions: view, View Pull Request, {pr_url}",
                        "-d", f"Manually accepted: Pushed review fixes to {upstream_repo} #{pr_id}.",
                        f"https://ntfy.sh/{TOPIC}"])

        print(f"\n🎉 Successfully updated PR #{pr_id} on {upstream_repo}!")
        print(f"🔗 View on GitHub: {pr_url}")

    else:
        print(f"🍴 Ensuring fork exists...")
        subprocess.run([GH_BIN, "repo", "fork", "--remote=true", "--clone=false"], capture_output=True)

        remotes_res = subprocess.run(["git", "remote"], capture_output=True, text=True)
        push_remote = "fork" if "fork" in remotes_res.stdout.splitlines() else "origin"
        print(f"📤 Pushing branch '{branch}' to {push_remote}...")
        subprocess.run(["git", "push", "-u", push_remote, branch], check=True)

        print(f"📝 Creating Pull Request on {upstream_repo}...")
        user_res = subprocess.run([GH_BIN, "api", "user", "--jq", ".login"], capture_output=True, text=True)
        gh_user = user_res.stdout.strip() if user_res.returncode == 0 else ""
        head_arg = f"{gh_user}:{branch}" if gh_user else branch

        create_cmd = [GH_BIN, "pr", "create", "--repo", upstream_repo, "--head", head_arg, "--title", pr_title]
        if os.path.exists(body_file):
            create_cmd.extend(["--body-file", body_file])
        else:
            create_cmd.append("--fill")

        res = subprocess.run(create_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"❌ Failed to create PR: {res.stderr.strip()}")
            sys.exit(1)

        new_pr_url = res.stdout.strip()
        print(f"PR Result: {new_pr_url}")

        new_id_match = re.search(r'(\d+)$', new_pr_url)
        new_pr_id = new_id_match.group(1) if new_id_match else ""

        if new_pr_id:
            cmd_track(argparse.Namespace(
                id=new_pr_id, repo=upstream_repo, branch=branch,
                issue_url=issue_url, pr_url=new_pr_url, title=pr_title,
                local_path=repo_dir, status="OPEN", summary=None, summary_file=body_file
            ))

        if os.path.exists(pending_file):
            os.remove(pending_file)
        if os.path.exists(body_file):
            os.remove(body_file)

        subprocess.run(["curl", "-s",
                        "-H", "Title: 🎉 PR Created Successfully!",
                        "-H", "Priority: high",
                        "-H", "Tags: party_popper,tada,octocat",
                        "-H", f"Actions: view, View Pull Request, {new_pr_url}",
                        "-d", f"Manually accepted: PR created for {upstream_repo}: '{pr_title}'.",
                        f"https://ntfy.sh/{TOPIC}"])

        print(f"\n🎉 Successfully created PR #{new_pr_id} on {upstream_repo}!")
        print(f"🔗 View on GitHub: {new_pr_url}")


def cmd_check_active(args):
    prs = load_prs()
    if not prs:
        res = {"status": "READY_FOR_NEW_CONTRIBUTION", "message": "No PRs tracked yet."}
        print(json.dumps(res, indent=2))
        sys.exit(0)

    sorted_prs = sorted(prs, key=lambda x: x.get("created_at", ""), reverse=True)
    last_pr = sorted_prs[0]
    pr_id = str(last_pr.get("id"))
    repo = last_pr.get("repo")
    local_path = last_pr.get("local_path")
    branch = last_pr.get("branch")
    pr_url = last_pr.get("pr_url")
    title = last_pr.get("title")
    issue_url = last_pr.get("issue_url")

    gh_cmd = [
        GH_BIN, "pr", "view", pr_id, "--repo", repo,
        "--json", "state,reviewDecision,comments,reviews,latestReviews,url,title,headRefName"
    ]
    p_run = subprocess.run(gh_cmd, capture_output=True, text=True)
    if p_run.returncode != 0:
        res = {
            "status": "AWAITING_MAINTAINER",
            "pr_id": pr_id,
            "repo": repo,
            "branch": branch,
            "local_path": local_path,
            "message": f"Unable to reach GitHub for PR #{pr_id} ({repo}). Retrying next run."
        }
        print(json.dumps(res, indent=2))
        sys.exit(20)

    try:
        data = json.loads(p_run.stdout)
    except Exception:
        data = {}

    state = data.get("state", "OPEN")
    review_decision = data.get("reviewDecision", "")

    current_status = review_decision if review_decision else state
    if last_pr.get("status") != current_status:
        last_pr["status"] = current_status
        save_prs(prs)

    if state in ("MERGED", "CLOSED"):
        res = {
            "status": "READY_FOR_NEW_CONTRIBUTION",
            "pr_id": pr_id,
            "repo": repo,
            "state": state,
            "message": f"Last PR #{pr_id} ({repo}) is {state}. Ready for a new contribution."
        }
        print(json.dumps(res, indent=2))
        sys.exit(0)

    needs_action = False
    action_reason = ""
    latest_reviewer = ""
    review_feedback_text = []

    if review_decision == "CHANGES_REQUESTED":
        needs_action = True
        action_reason = "Review status is CHANGES_REQUESTED"

    if review_decision != "APPROVED":
        for rev in data.get("latestReviews", []):
            author = rev.get("author", {}).get("login", "")
            rev_state = rev.get("state", "")
            if not is_bot_user(author) and rev_state == "CHANGES_REQUESTED":
                needs_action = True
                latest_reviewer = author
                body = rev.get("body", "").strip()
                if body:
                    review_feedback_text.append(f"[{author}]: {body}")

    comments = data.get("comments", [])
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
            if review_decision == "APPROVED" or "approved" in body_lower or "ready to merge" in body_lower:
                needs_action = False
            else:
                needs_action = True
                latest_reviewer = last_maintainer_comment.get("author", {}).get("login", "")
                action_reason = f"New maintainer comment from @{latest_reviewer}"
                review_feedback_text.append(f"[@{latest_reviewer}]: {last_maintainer_comment.get('body', '').strip()}")

    if needs_action:
        res = {
            "status": "NEEDS_REVIEW_ACTION",
            "pr_id": pr_id,
            "repo": repo,
            "branch": branch,
            "local_path": local_path,
            "pr_url": data.get("url", pr_url),
            "issue_url": issue_url,
            "title": data.get("title", title),
            "reason": action_reason,
            "latest_reviewer": latest_reviewer,
            "feedback": "\n\n".join(review_feedback_text) if review_feedback_text else "Maintainer requested changes."
        }
        print(json.dumps(res, indent=2))
        sys.exit(10)
    else:
        status_msg = "approved and awaiting merge" if review_decision == "APPROVED" else "awaiting maintainer review"
        res = {
            "status": "AWAITING_MAINTAINER",
            "pr_id": pr_id,
            "repo": repo,
            "branch": branch,
            "local_path": local_path,
            "pr_url": data.get("url", pr_url),
            "review_decision": review_decision,
            "message": f"Last PR #{pr_id} in {repo} is currently {status_msg}. No changes required today."
        }
        print(json.dumps(res, indent=2))
        sys.exit(20)


def cmd_sync(args):
    prs = load_prs()
    print("🔄 Syncing PR statuses from GitHub...")
    updated = False
    for p in prs:
        pr_id = p.get("id")
        repo = p.get("repo")
        if not repo or not pr_id:
            continue
        res = subprocess.run([GH_BIN, "pr", "view", str(pr_id), "--repo", repo, "--json", "state,reviewDecision"],
                             capture_output=True, text=True)
        if res.returncode == 0:
            try:
                data = json.loads(res.stdout)
                decision = data.get("reviewDecision")
                state = data.get("state")
                status = decision if decision else state
                if p.get("status") != status:
                    p["status"] = status
                    updated = True
                    print(f"  • PR #{pr_id} ({repo}): {status}")
            except Exception:
                pass
    save_prs(prs)
    print("✅ Sync complete. Ledger updated.")


def main():
    parser = argparse.ArgumentParser(description="PR Tracker and Navigator for Autonomous OSS Contributions")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_track = subparsers.add_parser("track", help="Record or update a PR")
    p_track.add_argument("--id", required=True, help="PR number/ID")
    p_track.add_argument("--repo", help="GitHub repo owner/name")
    p_track.add_argument("--branch", help="Git branch name")
    p_track.add_argument("--issue-url", help="URL of the fixed issue")
    p_track.add_argument("--pr-url", help="URL of the pull request")
    p_track.add_argument("--title", help="Title of the pull request")
    p_track.add_argument("--local-path", help="Local directory path")
    p_track.add_argument("--status", default="OPEN", help="PR status (OPEN, MERGED, etc.)")
    p_track.add_argument("--summary", help="Markdown summary string")
    p_track.add_argument("--summary-file", help="Path to markdown summary file")

    p_goto = subparsers.add_parser("goto", help="Navigate to and checkout a PR by Contribution No or PR ID")
    p_goto.add_argument("id", help="Contribution Number (#1, 1), PR ID (#5597), or repo search keyword")
    p_goto.add_argument("--cd-path", action="store_true", help="Print path only for shell integration")

    p_list = subparsers.add_parser("list", help="List all tracked PRs")
    p_list.add_argument("target", nargs="?", help="Specific Contribution No or PR ID to inspect")
    p_list.add_argument("--latest", action="store_true", help="Inspect details of the latest PR")

    p_cont = subparsers.add_parser("continue", help="Manually resume/complete or reject a pending PR action")
    p_cont.add_argument("target", nargs="?", help="Optional Contribution No, PR ID, or repo name")
    p_cont.add_argument("--accept", action="store_true", help="Accept and push the PR creation/update")
    p_cont.add_argument("--reject", action="store_true", help="Reject and cancel the pending PR creation/update")

    subparsers.add_parser("sync", help="Sync latest status of all PRs from GitHub")
    subparsers.add_parser("check-active", help="Check active PR status and determine if review updates or new contribution are needed")

    args = parser.parse_args()

    if args.command == "track":
        cmd_track(args)
    elif args.command == "goto":
        cmd_goto(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "continue":
        cmd_continue(args)
    elif args.command == "sync":
        cmd_sync(args)
    elif args.command == "check-active":
        cmd_check_active(args)


if __name__ == "__main__":
    main()
