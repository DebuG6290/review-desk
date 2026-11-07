#!/usr/bin/env python3
"""Review Desk tool: the hands the Fixer and Checker agents use.

The agents run inside a Switch room. This tool is deliberately small and does
only things that are easy to audit:

  rd review <pr-url>        fetch the PR's review comments (GitHub API, or a demo fixture)
  rd checkout <pr-url>      make a working copy of the PR head from the SANDBOX remote
  rd diff <pr-url>          show what has changed against the PR head
  rd check <pr-url>         run the repo's tests/linters on the working copy, record the result
  rd save-reply <pr-url>    store the reply to the reviewer (text on stdin)
  rd status <pr-url>        show where this PR stands
  rd publish <pr-url>       push + "post" the reply, ONLY if a human approved in the Switch room

`publish` is the human gate. It refuses unless:
  * the last `check` passed and nothing changed since (same HEAD commit), and
  * a person (not a bot/agent) wrote "approved, push" in the room's Mattermost
    channel AFTER that check finished. The tool reads that from Mattermost
    itself; an agent cannot fake it by passing a flag.

It never pushes to GitHub: the push target is the local bare repository that
sandbox/remotes.json names, and anything that looks like a network URL is
refused. The reply is written to the sandbox outbox, not posted to GitHub.

Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REMOTES_FILE = ROOT / "sandbox" / "remotes.json"
CONFIG_FILE = Path(os.environ.get("REVIEW_DESK_CONFIG", ROOT / "review-desk.config.json"))
APPROVAL_RE = re.compile(r"\bapproved?\b[\s,.:;!-]*\bpush\b", re.IGNORECASE)
PR_RE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")


class Fail(Exception):
    pass


# ── config ────────────────────────────────────────────────────────────────────


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        raise Fail(f"missing {CONFIG_FILE}; run scripts/setup_review_desk.py first")
    return json.loads(CONFIG_FILE.read_text())


def sandbox_dir(cfg: dict) -> Path:
    return Path(cfg["sandbox"]).expanduser()


def parse_pr(url: str) -> tuple[str, int]:
    m = PR_RE.search(url)
    if not m:
        raise Fail(f"not a GitHub pull request URL: {url}")
    return f"{m.group(1)}/{m.group(2)}", int(m.group(3))


def repo_entry(cfg: dict, repo: str) -> dict:
    remotes = json.loads(REMOTES_FILE.read_text())["repos"]
    if repo not in remotes:
        raise Fail(
            f"{repo} is not configured in sandbox/remotes.json, so Review Desk will not touch it"
        )
    entry = dict(remotes[repo])
    entry["remote"] = entry["remote"].replace("{sandbox}", str(sandbox_dir(cfg)))
    return entry


def slug(repo: str, n: int) -> str:
    return f"{repo.replace('/', '__')}__{n}"


def state_dir(cfg: dict, repo: str, n: int) -> Path:
    d = sandbox_dir(cfg) / "state" / slug(repo, n)
    d.mkdir(parents=True, exist_ok=True)
    return d


def work_dir(cfg: dict, repo: str, n: int) -> Path:
    return sandbox_dir(cfg) / "work" / slug(repo, n)


def git(cwd: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise Fail(f"git {' '.join(args)} failed:\n{r.stderr.strip()}")
    return r.stdout.strip()


# ── review ────────────────────────────────────────────────────────────────────


def http_json(url: str, token: str | None = None) -> object:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "review-desk"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_review(cfg: dict, repo: str, n: int, entry: dict, refresh: bool) -> dict:
    cache = state_dir(cfg, repo, n) / "review.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())
    if entry["review_source"] == "fixture":
        data = json.loads((ROOT / entry["review_fixture"]).read_text())
        data["source"] = f"demo fixture {entry['review_fixture']}"
    else:
        token = os.environ.get("GITHUB_TOKEN") or cfg.get("github_token")
        api = f"https://api.github.com/repos/{repo}/pulls/{n}"
        try:
            data = {
                "pull": http_json(api, token),
                "reviews": http_json(api + "/reviews?per_page=100", token),
                "comments": http_json(api + "/comments?per_page=100", token),
                "source": api,
            }
        except (urllib.error.URLError, TimeoutError) as e:
            raise Fail(f"could not reach the GitHub API ({e})")
    data["fetched_at"] = int(time.time())
    cache.write_text(json.dumps(data, indent=2))
    return data


def cmd_review(a, cfg):
    repo, n = parse_pr(a.pr)
    entry = repo_entry(cfg, repo)
    data = fetch_review(cfg, repo, n, entry, a.refresh)
    pull = data["pull"]
    out = [f"# {repo}#{n}: {pull['title']}", f"Source: {data['source']}", ""]
    for r in data["reviews"]:
        if r.get("body"):
            out.append(f"Review by @{r['user']['login']} ({r['state']}): {r['body']}")
    out.append("")
    comments = [c for c in data["comments"] if not c.get("in_reply_to_id")]
    out.append(f"{len(comments)} inline review comments:")
    for i, c in enumerate(comments, 1):
        line = c.get("line") or c.get("original_line")
        out.append(f"\n[{i}] {c['path']}:{line} (@{c['user']['login']}, id {c['id']})")
        out.append(c["body"].strip())
    print("\n".join(out))


# ── checkout / diff ───────────────────────────────────────────────────────────


def pr_branch(entry: dict, n: int) -> str:
    b = entry["pr_branches"].get(str(n))
    if not b:
        raise Fail(f"no sandbox branch is configured for PR #{n}")
    return b


def check_local_remote(remote: str) -> None:
    if "://" in remote or re.match(r"^[\w.-]+@", remote) or not Path(remote).is_dir():
        raise Fail(f"refusing: {remote} is not a local sandbox repository")


def cmd_checkout(a, cfg):
    repo, n = parse_pr(a.pr)
    entry = repo_entry(cfg, repo)
    branch = pr_branch(entry, n)
    check_local_remote(entry["remote"])
    wd = work_dir(cfg, repo, n)
    if wd.exists():
        if not a.fresh:
            print(f"Working copy already exists: {wd}")
            print(f"Branch: {git(wd, 'rev-parse', '--abbrev-ref', 'HEAD')}  HEAD: {git(wd, 'rev-parse', '--short', 'HEAD')}")
            return
        shutil.rmtree(wd)
    wd.parent.mkdir(parents=True, exist_ok=True)
    git(wd.parent, "clone", "--quiet", "--branch", branch, entry["remote"], wd.name)
    git(wd, "config", "user.name", cfg.get("git_name", "Review Desk Fixer"))
    git(wd, "config", "user.email", cfg.get("git_email", "review-desk@localhost"))
    git(wd, "checkout", "--quiet", "-b", f"review-desk/pr-{n}")
    head = git(wd, "rev-parse", "HEAD")
    (state_dir(cfg, repo, n) / "base.txt").write_text(head)
    print(f"Working copy: {wd}")
    print(f"PR head ({branch}) = {head[:10]}; edit files there and commit.")


def cmd_diff(a, cfg):
    repo, n = parse_pr(a.pr)
    wd = work_dir(cfg, repo, n)
    base = (state_dir(cfg, repo, n) / "base.txt").read_text().strip()
    print(git(wd, "log", "--oneline", f"{base}..HEAD") or "(no commits yet)")
    print(git(wd, "diff", "--stat", base))
    if not a.stat:
        print(git(wd, "diff", base))


# ── check ─────────────────────────────────────────────────────────────────────


def python_for(cfg: dict, entry: dict) -> str:
    key = entry.get("python", "default")
    return cfg.get("pythons", {}).get(key) or sys.executable


def cmd_check(a, cfg):
    repo, n = parse_pr(a.pr)
    entry = repo_entry(cfg, repo)
    wd = work_dir(cfg, repo, n)
    if not wd.exists():
        raise Fail("no working copy; run `rd checkout` first")
    dirty = git(wd, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise Fail("the working copy has uncommitted changes; commit them before checking:\n" + dirty)
    py = python_for(cfg, entry)
    results, ok = [], True
    for c in entry["checks"]:
        cmd = [part.replace("{python}", py) for part in c["cmd"]]
        env = {**os.environ, **c.get("env", {})}
        t0 = time.time()
        try:
            r = subprocess.run(cmd, cwd=wd, capture_output=True, text=True, env=env, timeout=c.get("timeout", 600))
            code, output = r.returncode, (r.stdout + r.stderr)
        except subprocess.TimeoutExpired:
            code, output = -1, "timed out"
        except FileNotFoundError as e:
            code, output = -1, f"could not run the check: {e}"
        passed = code == 0
        ok = ok and passed
        results.append({"name": c["name"], "cmd": " ".join(cmd), "passed": passed,
                        "seconds": round(time.time() - t0, 1), "output_tail": output[-2500:]})
        print(f"{'PASS' if passed else 'FAIL'}  {c['name']}  ({results[-1]['seconds']}s)")
        if not passed:
            print(output[-2500:])
    record = {"passed": ok, "head": git(wd, "rev-parse", "HEAD"),
              "finished_at_ms": int(time.time() * 1000), "results": results}
    (state_dir(cfg, repo, n) / "check.json").write_text(json.dumps(record, indent=2))
    print(f"\nOverall: {'PASSED' if ok else 'FAILED'} at {record['head'][:10]}")
    sys.exit(0 if ok else 1)


def cmd_save_reply(a, cfg):
    repo, n = parse_pr(a.pr)
    text = sys.stdin.read().strip()
    if not text:
        raise Fail("empty reply")
    (state_dir(cfg, repo, n) / "reply.md").write_text(text + "\n")
    print(f"Reply draft saved ({len(text)} chars).")


# ── approval + publish ────────────────────────────────────────────────────────


def mm_get(cfg: dict, path: str) -> object:
    mm = cfg["mattermost"]
    req = urllib.request.Request(mm["url"].rstrip("/") + "/api/v4" + path,
                                 headers={"Authorization": f"Bearer {mm['token']}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def find_approval(cfg: dict, since_ms: int) -> dict | None:
    channel = cfg["mattermost"]["channel_id"]
    data = mm_get(cfg, f"/channels/{channel}/posts?since={since_ms}")
    posts = sorted(data.get("posts", {}).values(), key=lambda p: p["create_at"])
    users: dict[str, dict] = {}
    for p in posts:
        if p["create_at"] <= since_ms or p.get("delete_at"):
            continue
        if p.get("props", {}).get("from_bot") or p.get("props", {}).get("from_webhook"):
            continue
        if not APPROVAL_RE.search(p.get("message", "")):
            continue
        u = users.get(p["user_id"]) or mm_get(cfg, f"/users/{p['user_id']}")
        users[p["user_id"]] = u
        if u.get("is_bot"):
            continue
        return {"post_id": p["id"], "user": u["username"], "message": p["message"], "create_at": p["create_at"]}
    return None


def load_check(cfg, repo, n) -> dict:
    f = state_dir(cfg, repo, n) / "check.json"
    if not f.exists():
        raise Fail("no check has been run; run `rd check` first")
    return json.loads(f.read_text())


def cmd_status(a, cfg):
    repo, n = parse_pr(a.pr)
    sd = state_dir(cfg, repo, n)
    wd = work_dir(cfg, repo, n)
    print(f"PR {repo}#{n}")
    print(f"working copy: {wd if wd.exists() else '(none)'}")
    if (sd / "check.json").exists():
        c = json.loads((sd / "check.json").read_text())
        print(f"last check: {'PASSED' if c['passed'] else 'FAILED'} at {c['head'][:10]}")
        appr = find_approval(cfg, c["finished_at_ms"]) if c["passed"] else None
        print(f"human approval in room since then: {appr['user'] + ': ' + appr['message'] if appr else 'none yet'}")
    print(f"reply draft: {'yes' if (sd / 'reply.md').exists() else 'no'}")
    if (sd / "published.json").exists():
        print("published:", (sd / "published.json").read_text())


def cmd_publish(a, cfg):
    repo, n = parse_pr(a.pr)
    entry = repo_entry(cfg, repo)
    branch = pr_branch(entry, n)
    check_local_remote(entry["remote"])
    sd, wd = state_dir(cfg, repo, n), work_dir(cfg, repo, n)
    check = load_check(cfg, repo, n)
    if not check["passed"]:
        raise Fail("the last check FAILED; nothing is published")
    head = git(wd, "rev-parse", "HEAD")
    if head != check["head"]:
        raise Fail("HEAD changed after the last check; run `rd check` again")
    if not (sd / "reply.md").exists():
        raise Fail("no reply draft; save one with `rd save-reply`")
    approval = find_approval(cfg, check["finished_at_ms"])
    if not approval:
        raise Fail(
            "BLOCKED: no human has written 'approved, push' in the room since the checks passed. "
            "Ask the person in the room to approve; do not publish."
        )
    git(wd, "push", "--quiet", entry["remote"], f"HEAD:refs/heads/{branch}")
    outbox = sandbox_dir(cfg) / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    reply = (sd / "reply.md").read_text()
    out = outbox / f"{slug(repo, n)}-reply.md"
    out.write_text(
        f"<!-- Review Desk reply for {repo}#{n}; approved by {approval['user']} "
        f"(Mattermost post {approval['post_id']}); pushed {head[:10]} to sandbox {branch} -->\n\n{reply}"
    )
    rec = {"head": head, "branch": branch, "remote": entry["remote"], "approval": approval,
           "reply_file": str(out), "published_at": int(time.time())}
    (sd / "published.json").write_text(json.dumps(rec, indent=2))
    print(f"Approved by @{approval['user']} (\"{approval['message']}\").")
    print(f"Pushed {head[:10]} to sandbox remote {entry['remote']} branch {branch}.")
    print(f"Reply to the reviewer written to {out} (sandbox outbox; nothing was posted to GitHub).")


def main() -> None:
    p = argparse.ArgumentParser(prog="rd", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("review"); s.add_argument("pr"); s.add_argument("--refresh", action="store_true"); s.set_defaults(f=cmd_review)
    s = sub.add_parser("checkout"); s.add_argument("pr"); s.add_argument("--fresh", action="store_true"); s.set_defaults(f=cmd_checkout)
    s = sub.add_parser("diff"); s.add_argument("pr"); s.add_argument("--stat", action="store_true"); s.set_defaults(f=cmd_diff)
    s = sub.add_parser("check"); s.add_argument("pr"); s.set_defaults(f=cmd_check)
    s = sub.add_parser("save-reply"); s.add_argument("pr"); s.set_defaults(f=cmd_save_reply)
    s = sub.add_parser("status"); s.add_argument("pr"); s.set_defaults(f=cmd_status)
    s = sub.add_parser("publish"); s.add_argument("pr"); s.set_defaults(f=cmd_publish)
    a = p.parse_args()
    try:
        a.f(a, load_config())
    except Fail as e:
        print(f"rd: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
