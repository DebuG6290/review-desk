#!/usr/bin/env bash
# Run a whole Switch stack on a Linux box WITHOUT Docker:
#   PostgreSQL (distro package) + Mattermost (release tarball) + switch-core
#   (Python, from source) + the Switch headless agent controller (npm release).
#
# This is how Review Desk was built and recorded (a sandbox with no Docker).
# On a laptop, the supported path is Switch Console + its local server instead;
# see the README.
#
#   scripts/stack_nodocker.sh up      # install/start everything (idempotent)
#   scripts/stack_nodocker.sh status
#
# Env knobs: RD_RUN (runtime dir, default /tmp/rd-run), RD_CACHE (download
# cache, default $HOME/.cache/review-desk), SWITCH_REF (switch git commit).
set -euo pipefail

RUN="${RD_RUN:-/tmp/rd-run}"
CACHE="${RD_CACHE:-$HOME/.cache/review-desk}"
SWITCH_REF="${SWITCH_REF:-0a761a2}"
CTL_VERSION="0.31.1-dev.374.g0a761a2"     # controller release built from SWITCH_REF
MM_VERSION="10.11.3"
ARCH="$(uname -m)"; case "$ARCH" in aarch64|arm64) MMARCH=arm64;; *) MMARCH=amd64;; esac
export NO_PROXY="localhost,127.0.0.1,::1" no_proxy="localhost,127.0.0.1,::1"
mkdir -p "$RUN" "$CACHE"

log() { printf '\033[1m==> %s\033[0m\n' "$*"; }
wait_http() { for _ in $(seq 1 90); do curl -sf --noproxy '*' "$1" >/dev/null && return 0; sleep 2; done; echo "timeout waiting for $1" >&2; return 1; }

up() {
  # 1. PostgreSQL
  if ! ls /usr/lib/postgresql/*/bin/initdb >/dev/null 2>&1; then
    log "installing postgresql"; sudo apt-get update -qq && sudo apt-get install -y -qq postgresql >/dev/null
  fi
  PGB="$(ls -d /usr/lib/postgresql/*/bin | tail -1)"
  if [ ! -d "$RUN/pgdata" ]; then "$PGB/initdb" -D "$RUN/pgdata" -U postgres --auth=trust >/dev/null; fi
  if ! "$PGB/pg_isready" -h localhost -q; then
    log "starting postgres"; "$PGB/pg_ctl" -D "$RUN/pgdata" -l "$RUN/pg.log" -o "-k $RUN -p 5432 -c listen_addresses=localhost" start >/dev/null
    sleep 2
  fi

  # 2. switch-core source + .env
  if [ ! -d "$RUN/switch" ]; then
    log "cloning switch @ $SWITCH_REF"
    git clone -q https://github.com/sandbox-quantum/switch "$RUN/switch"
    git -C "$RUN/switch" checkout -q "$SWITCH_REF"
  fi
  cd "$RUN/switch"
  if [ ! -f .env ]; then
    cp .env.example .env && bash deploy/local/generate-env-secrets.sh .env local
    sed -i "s|^FEATURE_FLAGS_ENABLED=.*|FEATURE_FLAGS_ENABLED=agent_management|; s|^CONTROLLER_TOKEN_SECRET=.*|CONTROLLER_TOKEN_SECRET=$(openssl rand -base64 48 | tr -d '\n')|" .env
    echo "GATEWAY_PUBLIC_URL=http://localhost:8000" >> .env
  fi
  set -a; . ./.env; set +a
  psql -h localhost -U postgres -qtc "SELECT 1 FROM pg_database WHERE datname='switch'" | grep -q 1 || {
    psql -h localhost -U postgres -q -c "ALTER ROLE postgres PASSWORD '$DB_OWNER_PASSWORD';" -c "CREATE DATABASE switch;" -c "CREATE DATABASE mattermost;" \
      -c "CREATE ROLE \"$DB_USER\" LOGIN PASSWORD '$DB_PASSWORD' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;"
  }
  log "uv sync + migrations"
  uv sync -q --project core
  uv run -q --project core alembic -c core/alembic.ini upgrade head 2>&1 | tail -1

  # 3. Mattermost
  if [ ! -x "$RUN/mattermost/bin/mattermost" ]; then
    TGZ="$CACHE/mattermost-$MM_VERSION-linux-$MMARCH.tar.gz"
    [ -f "$TGZ" ] || { log "downloading mattermost"; curl -sSL -o "$TGZ" "https://releases.mattermost.com/$MM_VERSION/mattermost-$MM_VERSION-linux-$MMARCH.tar.gz"; }
    tar xzf "$TGZ" -C "$RUN"
  fi
  if ! curl -sf --noproxy '*' localhost:8065/api/v4/system/ping >/dev/null; then
    log "starting mattermost"
    ( cd "$RUN/mattermost"
      export MM_SQLSETTINGS_DRIVERNAME=postgres \
        MM_SQLSETTINGS_DATASOURCE="postgres://postgres:$DB_OWNER_PASSWORD@localhost:5432/mattermost?sslmode=disable" \
        MM_SERVICESETTINGS_SITEURL=http://localhost:8065 MM_SERVICESETTINGS_LISTENADDRESS=:8065 \
        MM_SERVICESETTINGS_ENABLELOCALMODE=true MM_SERVICESETTINGS_LOCALMODESOCKETLOCATION="$RUN/mm_local.socket" \
        MM_SERVICESETTINGS_ENABLEBOTACCOUNTCREATION=true MM_SERVICESETTINGS_ENABLEUSERACCESSTOKENS=true \
        MM_SERVICESETTINGS_ALLOWEDUNTRUSTEDINTERNALCONNECTIONS="localhost 127.0.0.1" \
        MM_TEAMSETTINGS_ENABLEOPENSERVER=true MM_TEAMSETTINGS_TEAMMATENAMEDISPLAY=full_name \
        MM_SERVICESETTINGS_ENABLEONBOARDINGFLOW=false MM_DISPLAYSETTINGS_CUSTOMURLSCHEMES=switchdash \
        MM_LOGSETTINGS_ENABLEDIAGNOSTICS=false MM_LOGSETTINGS_ENABLESENTRY=false \
        MM_SERVICESETTINGS_ENABLESECURITYFIXALERT=false MM_ANNOUNCEMENTSETTINGS_ADMINNOTICESENABLED=false \
        MM_ANNOUNCEMENTSETTINGS_USERNOTICESENABLED=false MM_PLUGINSETTINGS_ENABLEREMOTEMARKETPLACE=false \
        MM_PLUGINSETTINGS_ENABLE=false
      nohup ./bin/mattermost > "$RUN/mattermost.log" 2>&1 & )
  fi

  # 4. switch-core
  if ! curl -sf --noproxy '*' localhost:8000/health >/dev/null; then
    log "starting switch-core"
    ( cd "$RUN/switch"; GATEWAY_COOKIE_SECURE=false nohup uv run --project core python -m switch_core.main > "$RUN/core.log" 2>&1 & )
  fi
  wait_http localhost:8000/health
  wait_http localhost:8065/api/v4/system/ping

  # 5. Mattermost team/users + register it as Switch's default bridge (Switch's own setup script)
  if [ ! -f "$RUN/.mm-setup-done" ]; then
    log "provisioning mattermost <-> switch bridge"
    SWITCH_URL=http://localhost:8000 MATTERMOST_URL=http://localhost:8065 MATTERMOST_URL_FOR_SWITCH=http://localhost:8065 \
    MATTERMOST_CALLBACK_BASE_URL=http://localhost:8081 PYTHONUNBUFFERED=1 \
      uv run -q --project core python deploy/shared_resources/setup.py > "$RUN/setup.log" 2>&1
    tail -3 "$RUN/setup.log"; touch "$RUN/.mm-setup-done"
  fi

  # 6. Headless agent controller (runs the Codex agents)
  if [ ! -x "$RUN/ctl/bin/switch-agent-controller" ]; then
    CT="$CACHE/switch-agent-controller-$CTL_VERSION.tgz"
    [ -f "$CT" ] || curl -sSL -o "$CT" "https://github.com/sandbox-quantum/switch/releases/download/switch-agent-controller-v$CTL_VERSION/switch-agent-controller-$CTL_VERSION.tgz"
    npm install -s --prefix "$RUN/ctl" --global --ignore-scripts "$CT" >/dev/null
  fi
  # 7. Python for Review Desk's own checks (toy repo) and the demo recorder
  [ -x "$RUN/rdvenv/bin/python" ] || { uv venv -q "$RUN/rdvenv" && VIRTUAL_ENV="$RUN/rdvenv" uv pip install -q pytest black playwright; }
  log "stack is up: switch-core :8000, mattermost :8065 (controller is enrolled by setup_review_desk.py)"
}

status() {
  for u in localhost:8000/health localhost:8065/api/v4/system/ping; do
    printf '%-40s %s\n' "$u" "$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' "$u")"
  done
  pgrep -af "switch-agent-controller run" >/dev/null && echo "agent controller: running" || echo "agent controller: not running"
}

"${1:-up}"
