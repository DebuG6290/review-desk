#!/usr/bin/env python3
"""Screenshot a Mattermost channel (e.g. the bambi PR #1090 room) as the room's person.

  python demo/shoot_room.py --channel bambi-review-desk --out demo/screenshot-bambi.png [--scroll-to "Review comment [1]"]
"""
import argparse
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "localhost,127.0.0.1"
MM = "http://localhost:8065"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True, help="channel name in team 'switch'")
    ap.add_argument("--out", required=True)
    ap.add_argument("--scroll-to", help="text of a post to scroll into view")
    ap.add_argument("--switch-env", default="/tmp/rd-run/switch/.env")
    a = ap.parse_args()
    env = dict(l.split("=", 1) for l in Path(a.switch_env).read_text().splitlines() if "=" in l and not l.startswith("#"))
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox", "--no-proxy-server"])
        ctx = b.new_context(viewport={"width": 1280, "height": 720})
        r = ctx.request.post(f"{MM}/api/v4/users/login", data={"login_id": env.get("MATTERMOST_USER", "user"),
                             "password": env["MATTERMOST_USER_PASSWORD"]}, headers={"X-Requested-With": "XMLHttpRequest"})
        assert r.ok, r.text()
        ctx.add_init_script("""try { localStorage.setItem('__landingPageSeen__', 'true'); } catch (e) {}
            document.addEventListener('DOMContentLoaded', () => { const s = document.createElement('style');
            s.textContent = '.announcement-bar { display: none !important; }'; document.head.appendChild(s); });""")
        p = ctx.new_page()
        p.goto(f"{MM}/switch/channels/{a.channel}")
        p.wait_for_selector("#post_textbox", timeout=60000)
        p.wait_for_timeout(3000)
        if a.scroll_to:
            loc = p.get_by_text(a.scroll_to, exact=False).last
            loc.scroll_into_view_if_needed()
            p.evaluate("(t) => { const el = [...document.querySelectorAll('.post-message__text')].reverse().find(e => e.innerText.includes(t)); if (el) el.scrollIntoView({block: 'end'}); }", a.scroll_to)
            p.wait_for_timeout(1500)
        p.screenshot(path=a.out)
        b.close()


if __name__ == "__main__":
    main()
