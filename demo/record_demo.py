#!/usr/bin/env python3
"""Record a real Review Desk run in the Mattermost web UI (headless Chromium).

A person (Mattermost user `user`) types the PR link for @fixer, the agents run
for real through Switch, and when the Checker asks for approval the person
types "@checker approved, push". The raw browser video plus a timeline of
when each step happened (used by make_video.sh to speed up the waiting parts)
is written to demo/raw/.

  python demo/record_demo.py --channel-id <mattermost channel> --pr <url>
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
MM = "http://localhost:8065"
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "localhost,127.0.0.1"


def env_file(path: str) -> dict:
    out = {}
    for line in Path(path).read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def mm(path: str, token: str):
    req = urllib.request.Request(MM + "/api/v4" + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=15) as r:
        return json.loads(r.read())


def wait_for_post(token, channel, since_ms, needle, who, timeout_s, page):
    """Wait until a bot (agent) post containing `needle` appears; keep the UI scrolled."""
    users = {}
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        posts = mm(f"/channels/{channel}/posts?since={since_ms}", token).get("posts", {}).values()
        for p in sorted(posts, key=lambda p: p["create_at"]):
            if p["create_at"] <= since_ms or needle.lower() not in p["message"].lower():
                continue
            uid = p["user_id"]
            users[uid] = users.get(uid) or mm(f"/users/{uid}", token)
            if users[uid]["username"] == who:
                return p
        page.wait_for_timeout(3000)
    raise SystemExit(f"timed out waiting for {who}: {needle!r}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel-id", required=True)
    ap.add_argument("--pr", required=True)
    ap.add_argument("--switch-env", default="/tmp/rd-run/switch/.env")
    ap.add_argument("--token", help="Mattermost token that can read the channel (default: review-desk.config.json)")
    ap.add_argument("--chromium", default="/usr/bin/chromium")
    ap.add_argument("--dry", action="store_true", help="only sign in, open the room and take a screenshot")
    a = ap.parse_args()
    env = env_file(a.switch_env)
    token = a.token or json.loads((HERE.parent / "review-desk.config.json").read_text())["mattermost"]["token"]
    ch = mm(f"/channels/{a.channel_id}", token)
    team = mm(f"/teams/{ch['team_id']}", token)
    RAW.mkdir(exist_ok=True)
    for f in RAW.glob("*.webm"):
        f.unlink()
    timeline = {}
    start = time.time()

    def mark(name):
        timeline[name] = round(time.time() - start, 2)
        print(f"{timeline[name]:7.1f}s  {name}", flush=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=a.chromium, headless=True, args=["--no-sandbox", "--no-proxy-server"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 720}, record_video_dir=str(RAW),
                                  record_video_size={"width": 1280, "height": 720})
        # Sign in through the API (sets the session cookie), so the video opens on the room.
        r = ctx.request.post(f"{MM}/api/v4/users/login", data={
            "login_id": env.get("MATTERMOST_USER", "user"), "password": env["MATTERMOST_USER_PASSWORD"]},
            headers={"X-Requested-With": "XMLHttpRequest"})
        # Skip Mattermost's "open in desktop app?" page and hide the preview-mode banner.
        ctx.add_init_script("""try { localStorage.setItem('__landingPageSeen__', 'true'); } catch (e) {}
            document.addEventListener('DOMContentLoaded', () => { const s = document.createElement('style');
            s.textContent = '.announcement-bar { display: none !important; }'; document.head.appendChild(s); });""")
        assert r.ok, r.text()
        page = ctx.new_page()
        start = time.time()
        page.goto(f"{MM}/{team['name']}/channels/{ch['name']}")
        page.wait_for_selector("#post_textbox", timeout=60000)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(HERE / "screenshot-room.png"))
        mark("room_open")
        if a.dry:
            ctx.close(); browser.close(); return

        msg = f"@fixer please handle the review on {a.pr}"
        page.click("#post_textbox")
        page.keyboard.type(msg, delay=35)
        page.wait_for_timeout(400)
        page.keyboard.press("Escape")  # close any mention autocomplete
        page.click("[data-testid=SendMessageButton]")  # Enter alone does not send in headless MM 10
        sent_ms = int(time.time() * 1000)
        mark("pr_link_sent")

        plan = wait_for_post(token, a.channel_id, sent_ms, "", "fixer", 120, page)
        mark("fixer_first_reply")
        handoff = wait_for_post(token, a.channel_id, sent_ms, "verify", "fixer", 900, page)
        mark("fixer_handoff")
        prompt = wait_for_post(token, a.channel_id, handoff["create_at"], "approved, push", "checker", 1200, page)
        mark("checker_asks_approval")
        page.wait_for_timeout(5000)
        page.screenshot(path=str(HERE / "screenshot-approval.png"))

        page.click("#post_textbox")
        page.keyboard.type("@checker approved, push", delay=45)
        page.wait_for_timeout(400)
        page.keyboard.press("Escape")
        page.click("[data-testid=SendMessageButton]")  # Enter alone does not send in headless MM 10
        approve_ms = int(time.time() * 1000)
        mark("approval_sent")
        wait_for_post(token, a.channel_id, approve_ms, "publish", "checker", 300, page)
        mark("published")
        page.wait_for_timeout(7000)
        page.screenshot(path=str(HERE / "screenshot-published.png"))
        mark("end")
        video = page.video.path()
        ctx.close()
        browser.close()
    (RAW / "timeline.json").write_text(json.dumps({"video": str(video), **timeline}, indent=2))
    print("video:", video)


if __name__ == "__main__":
    main()
