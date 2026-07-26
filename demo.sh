#!/usr/bin/env bash
#
# demo.sh — end-to-end proof that dizzchat works, including the cross-replica bonus.
#
# Runs the full assignment "way to see it work" against a live stack started with
# `docker compose up`:
#   sign up -> log in -> create a conversation -> open a socket -> send a message ->
#   see the broadcast + mock assistant reply -> prove idempotent send ->
#   reconnect to the OTHER replica and replay missed messages via Redis.
#
# Requires: curl, jq, websocat (https://github.com/vi/websocat), and the stack running
# on :8000 (api) and :8001 (api2). Run `docker compose up --build` first.

set -euo pipefail

API1="http://localhost:8000"
API2="http://localhost:8001"
WS1="ws://localhost:8000"
WS2="ws://localhost:8001"
READ_SECS="${READ_SECS:-2}"          # how long to keep each socket open to read replies
CMID="11111111-1111-1111-1111-111111111111"   # fixed client_message_id, to show idempotency

bold=$(printf '\033[1m'); dim=$(printf '\033[2m'); reset=$(printf '\033[0m')
step() { printf '\n%s==> %s%s\n' "$bold" "$1" "$reset"; }
note() { printf '%s    %s%s\n' "$dim" "$1" "$reset"; }

# --- preflight -------------------------------------------------------------
for bin in curl jq websocat; do
  command -v "$bin" >/dev/null 2>&1 || { echo "error: '$bin' is required but not installed." >&2; exit 1; }
done
for base in "$API1" "$API2"; do
  curl -fsS "$base/health" >/dev/null 2>&1 || {
    echo "error: no healthy API at $base — run 'docker compose up --build' first." >&2; exit 1; }
done
note "both replicas healthy: $API1 and $API2"

# Send the given frames on a socket, then keep it open READ_SECS to print the server replies.
ws() {  # ws <ws_url> <frame> [frame ...]
  local url="$1"; shift
  { for f in "$@"; do printf '%s\n' "$f"; done; sleep "$READ_SECS"; } | websocat "$url"
}

# --- 1. account ------------------------------------------------------------
step "1. Sign up and log in"
EMAIL="demo-$(date +%s)@example.com"
PASS="demo-password-123"
curl -fsS -X POST "$API1/auth/signup" -H 'content-type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" >/dev/null
note "signed up $EMAIL"
TOKEN=$(curl -fsS -X POST "$API1/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" | jq -r .access_token)
[ -n "$TOKEN" ] && [ "$TOKEN" != null ] || { echo "error: login did not return an access token" >&2; exit 1; }
note "got access token"

# --- 2. conversation -------------------------------------------------------
step "2. Create a conversation"
CID=$(curl -fsS -X POST "$API1/conversations" \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"title":"demo"}' | jq -r .id)
[ -n "$CID" ] && [ "$CID" != null ] || { echo "error: conversation was not created" >&2; exit 1; }
note "conversation id: $CID"

# --- 3. send on replica #1 -------------------------------------------------
step "3. Send a message on replica #1 ($WS1)"
note "expect: auth.ok, then message.ack + message.new(user) + message.new(assistant)"
ws "$WS1/ws/conversations/$CID" \
  "{\"type\":\"auth\",\"payload\":{\"token\":\"$TOKEN\"}}" \
  "{\"type\":\"message.send\",\"payload\":{\"content\":\"hello from the demo\",\"client_message_id\":\"$CMID\"}}"

# --- 4. idempotent send ----------------------------------------------------
step "4. Idempotent send — resend the SAME client_message_id"
note "expect: only a message.ack (no new row, no re-broadcast, no second assistant reply)"
ws "$WS1/ws/conversations/$CID" \
  "{\"type\":\"auth\",\"payload\":{\"token\":\"$TOKEN\"}}" \
  "{\"type\":\"message.send\",\"payload\":{\"content\":\"hello from the demo\",\"client_message_id\":\"$CMID\"}}"

# --- 5. cross-replica reconnect replay -------------------------------------
step "5. Reconnect to replica #2 ($WS2) and replay via Redis"
note "auth with last_seen_seq:0 -> the server replays every message.new for the conversation,"
note "proving fan-out state is shared across replicas over Redis (message was sent to #1, read from #2)."
ws "$WS2/ws/conversations/$CID" \
  "{\"type\":\"auth\",\"payload\":{\"token\":\"$TOKEN\",\"last_seen_seq\":0}}"

step "Done."
note "REST history cross-check: curl -s $API1/conversations/$CID/messages -H \"authorization: Bearer \$TOKEN\" | jq"
