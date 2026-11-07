#!/usr/bin/env python3
"""Render the demo video's title cards (HTML -> PNG, 1280x720) with Chromium."""

from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent / "cards"
CSS = """
body{margin:0;width:1280px;height:720px;background:#0f172a;color:#e2e8f0;
font-family:'DejaVu Sans',sans-serif;display:flex;flex-direction:column;justify-content:center;padding:0 90px;box-sizing:border-box}
h1{font-size:64px;margin:0 0 18px;color:#fff} h2{font-size:34px;margin:0 0 28px;color:#38bdf8;font-weight:normal}
p,li{font-size:27px;line-height:1.5;margin:6px 0} ul{padding-left:30px;margin:0}
.k{color:#fbbf24} .m{font-family:'DejaVu Sans Mono',monospace;font-size:24px;color:#a5f3fc}
.flow{display:flex;align-items:center;gap:14px;margin:26px 0 10px;flex-wrap:nowrap}
.box{border:2px solid #38bdf8;border-radius:12px;padding:14px 16px;font-size:21px;text-align:center;min-width:120px}
.arr{font-size:30px;color:#64748b} .small{font-size:20px;color:#94a3b8}
"""

CARDS = {
    "01-title": """<h1>Review Desk</h1>
<h2>A maintainer's PR review &rarr; a tested fix &rarr; pushed only after a human says so</h2>
<p>Two Codex agents working in one <span class="k">Switch</span> room (bridged to Mattermost).</p>
<p class="small">WeMakeDevs &ldquo;Flip the Switch&rdquo; hackathon &middot; Best Business Workflow</p>""",
    "02-how": """<h1 style="font-size:46px">How it works</h1>
<div class="flow"><div class="box">You<br><span class="small">drop PR link</span></div><div class="arr">&rarr;</div>
<div class="box">Fixer<br><span class="small">Codex: reads review,<br>patches, commits</span></div><div class="arr">&rarr;</div>
<div class="box">Checker<br><span class="small">Codex: tests, lint,<br>drafts reply</span></div><div class="arr">&rarr;</div>
<div class="box">You<br><span class="small">&ldquo;approved, push&rdquo;</span></div><div class="arr">&rarr;</div>
<div class="box">rd publish<br><span class="small">verifies approval<br>in the room, pushes</span></div></div>
<ul><li>The <span class="k">Switch room</span> is the hand-off: agents address each other there; nothing else connects them.</li>
<li>Switch's <span class="k">agent controller</span> starts each Codex session when the agent is addressed.</li>
<li>The push is gated on a human's message <i>in the room</i>, read back from Mattermost.</li></ul>""",
    "03-live": """<h1 style="font-size:50px">Live run</h1>
<h2>Toy repo <span class="m">billsplit</span> PR #7 &middot; 3 review comments</h2>
<p>Everything you see is real: Codex agents running through Switch on a self-hosted stack.</p>
<p class="small">Waiting time is sped up; the speed is shown on screen.</p>""",
    "04-bambi": """<h1 style="font-size:50px">Real-world run</h1>
<h2>bambinos/bambi PR #1090 &middot; 14 inline comments from the maintainer</h2>
<p>Review comments fetched live from GitHub. The Fixer patched them; the Checker ran
<span class="m">black</span> and the R&sup2; tests (pass) but sent one comment back:
&ldquo;build the docs and check the formula renders&rdquo;. The Fixer then rendered the docs and inspected the formula.</p>
<p class="small">Pushed only to a local sandbox copy. Nothing is posted to GitHub.</p>""",
    "05-end": """<h1 style="font-size:52px">Switch is the workflow</h1>
<ul><li>Room = shared context + hand-off between Fixer and Checker</li>
<li>Agent controller = where the Codex agents live and wake up</li>
<li>Room template = the whole desk in one file</li>
<li>Human approval read from the room before anything is pushed</li></ul>
<p class="small" style="margin-top:30px">github.com/DebuG6290/review-desk &middot; MIT</p>""",
}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])
        page = b.new_page(viewport={"width": 1280, "height": 720})
        for name, body in CARDS.items():
            page.set_content(f"<html><head><style>{CSS}</style></head><body>{body}</body></html>")
            page.screenshot(path=str(OUT / f"{name}.png"))
            print("card", name)
        b.close()


if __name__ == "__main__":
    main()
