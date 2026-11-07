#!/usr/bin/env python3
"""Put Review Desk on a running Switch server (stdlib only).

  1. signs in to the Switch gateway as the admin from the server's .env
  2. enrolls this machine's headless agent controller and starts it
     (the controller is what runs the Codex agents for Switch)
  3. runs switch/review-desk.template.yaml on the server: it creates the
     Fixer and Checker Codex agents on this machine and the "Review Desk" room
     on the default bridge (Mattermost), with the person in it
  4. writes review-desk.config.json for tools/rd: sandbox dir, the Mattermost
     channel of the room, and a Mattermost token rd uses to READ the room when
     it checks for a human's "approved, push"

  python3 scripts/setup_review_desk.py --switch-dir /tmp/rd-run/switch \
      --controller /tmp/rd-run/ctl/bin/switch-agent-controller --sandbox /tmp/rd-sandbox
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "localhost,127.0.0.1,::1"


def read_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class Http:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.jar)
        )
        self.headers: dict[str, str] = {}

    def call(self, method: str, path: str, body=None, ok=(200, 201, 204)):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", **self.headers})
        try:
            with self.opener.open(req, timeout=60) as r:
                raw, hdrs = r.read(), r.headers
        except urllib.error.HTTPError as e:
            raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode()[:800]}")
        self.last_headers = hdrs
        return json.loads(raw) if raw else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--switch-dir", required=True, help="switch checkout holding the server's .env")
    ap.add_argument("--controller", required=True, help="path of switch-agent-controller")
    ap.add_argument("--controller-data", default="/tmp/rd-run/ctl-data")
    ap.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    ap.add_argument("--sandbox", default="/tmp/rd-sandbox")
    ap.add_argument("--switch-url", default="http://localhost:8000")
    ap.add_argument("--mattermost-url", default="http://localhost:8065")
    ap.add_argument("--model", default=None)
    ap.add_argument("--effort", default=None)
    ap.add_argument("--room-name", default="Review Desk", help="room to use; a new one is created for the existing agents")
    ap.add_argument("--sync-instructions", action="store_true", help="room exists: re-apply the template's agent instructions")
    ap.add_argument("--python", action="append", default=[], help="name=path of a python for checks, e.g. bambi=/venv/bin/python")
    a = ap.parse_args()

    env = read_env(Path(a.switch_dir) / ".env")
    sw = Http(a.switch_url)
    sw.call("POST", "/gateway/auth/login", {"email": env["GATEWAY_ADMIN_EMAIL"], "password": env["GATEWAY_ADMIN_PASSWORD"]})
    print("signed in to the Switch gateway")

    # 2. enroll + start the agent controller
    data = Path(a.controller_data)
    ctl_env = {**os.environ, "CODEX_HOME": a.codex_home}
    if not (data / "secrets").exists():
        code = sw.call("POST", "/gateway/management/enrollment-codes")["code"]
        subprocess.run([a.controller, "enroll", "--server", a.switch_url, "--code", code, "--name", "review-desk-box",
                        "--description", "Runs the Review Desk Codex agents", "--data-dir", str(data),
                        "--secret-store", "file"], check=True, env=ctl_env)
    running = subprocess.run(["pgrep", "-f", f"switch-agent-controller run --data-dir {data}"], capture_output=True).stdout
    if not running:
        log = open(data.parent / "controller.log", "a")
        subprocess.Popen([a.controller, "run", "--data-dir", str(data)], env=ctl_env, stdout=log, stderr=log,
                         start_new_session=True)
        print("agent controller started")
    machines = []
    for _ in range(30):
        machines = sw.call("GET", "/gateway/management/controllers")
        machines = machines.get("controllers", machines) if isinstance(machines, dict) else machines
        if machines:
            break
        time.sleep(1)
    machine = next(m for m in machines if m.get("name") == "review-desk-box")
    print("machine:", machine["id"], machine.get("status") or machine.get("connected"))

    # 3. the template: agents + room
    rooms = sw.call("GET", "/gateway/rooms")
    rooms = rooms.get("rooms", rooms) if isinstance(rooms, dict) else rooms
    room = next((r for r in rooms if r.get("name") == a.room_name), None)
    agents_exist = any(r.get("name") == "Review Desk" for r in rooms)
    if room is None and agents_exist:
        # Agents already exist (made by the template run): another room for them.
        import yaml
        doc = yaml.safe_load((ROOT / "switch" / "review-desk.template.yaml").read_text())["room"]
        bridges = sw.call("GET", "/gateway/collaborations")
        bridges = bridges.get("bridges", bridges) if isinstance(bridges, dict) else bridges
        bridge = next(b for b in bridges if b.get("is_default")) if bridges else None
        room = sw.call("POST", "/gateway/rooms", {
            "name": a.room_name, "description": doc["description"], "instructions": doc["instructions"],
            "channel_type": "channel_public", "agent_names": ["fixer", "checker"], "user_names": [env.get("MATTERMOST_USER", "user")],
            **({"bridge_id": bridge["bridge_id"]} if bridge else {})})
        print("created room", a.room_name)
    elif room is None:
        inputs = {"machine": machine["id"], "rd": str(ROOT / "tools" / "rd")}
        template = (ROOT / "switch" / "review-desk.template.yaml").read_text()
        if a.model:
            inputs["model"] = a.model
        else:  # leave the model to the Codex default
            template = template.replace('    model: "{model}"\n', "")
            template = template.replace(template[template.index("  model:\n    type"):template.index("  effort:\n")], "")
        if a.effort:
            inputs["effort"] = a.effort
        result = sw.call("POST", "/gateway/rooms/from-template-document", {
            "template": template,
            "inputs": inputs, "template_name": "Review Desk"})
        print("template run:", json.dumps(result.get("created_agents"), indent=1))
        rooms = sw.call("GET", "/gateway/rooms")
        rooms = rooms.get("rooms", rooms) if isinstance(rooms, dict) else rooms
        room = next(r for r in rooms if r.get("name") == "Review Desk")
        a.room_name = "Review Desk"
    elif a.sync_instructions:
        # Room already there: push the template's current instructions onto the agents.
        import yaml  # PyYAML (present in switch-core's environment)
        doc = yaml.safe_load((ROOT / "switch" / "review-desk.template.yaml").read_text())
        managed = sw.call("GET", "/gateway/management/agents")
        managed = managed.get("agents", managed) if isinstance(managed, dict) else managed
        for spec in doc["agents"]:
            m = next(x for x in managed if x.get("name") == spec["name"])
            definition = dict(m["definition"])
            definition["instructions"] = spec["instructions"].replace("{rd}", str(ROOT / "tools" / "rd"))
            if a.model:
                definition["model"] = a.model
            if a.effort:
                definition["advanced_config"] = {"effort": a.effort}
            definition = {k: v for k, v in definition.items() if k in (
                "provider", "model", "advanced_config", "instructions", "auto_mode", "auto_approve", "directory", "isolation")}
            sw.call("PATCH", f"/gateway/management/agents/{m['agent_id'] if 'agent_id' in m else m['id']}", {"definition": definition})
            print("updated instructions of", spec["name"])
    detail = sw.call("GET", f"/gateway/rooms/{room['id']}")
    channel_id = detail.get("external_channel_id") or detail.get("bridge_channel_id")
    print("room:", room["id"], "mattermost channel:", channel_id)

    # 4. Mattermost token (admin) that rd uses to read the room for approvals
    mm = Http(a.mattermost_url)
    mm.call("POST", "/api/v4/users/login", {"login_id": env["MATTERMOST_ADMIN_USER"], "password": env["MATTERMOST_ADMIN_PASSWORD"]})
    mm.headers["Authorization"] = "Bearer " + mm.last_headers["Token"]
    me = mm.call("GET", "/api/v4/users/me")
    tok = mm.call("POST", f"/api/v4/users/{me['id']}/tokens", {"description": "review-desk approval reader"})["token"]
    pythons = dict(p.split("=", 1) for p in a.python)
    cfg = {"sandbox": a.sandbox, "pythons": pythons, "room_id": room["id"],
           "mattermost": {"url": a.mattermost_url, "token": tok, "channel_id": channel_id}}
    (ROOT / "review-desk.config.json").write_text(json.dumps(cfg, indent=2))
    print("wrote review-desk.config.json")


if __name__ == "__main__":
    main()
