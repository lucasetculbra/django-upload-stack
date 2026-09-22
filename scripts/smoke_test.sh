#!/usr/bin/env bash
# End-to-end check of the running stack, used locally and in CI.
#
#   1. the app answers through Nginx
#   2. an upload posted with a CSRF token succeeds (302) and the same POST without one
#      is rejected (403)
#   3. the uploaded file is listed and served byte-for-byte from /media/ by Nginx
#   4. after `docker compose down` + `up` the record (PostgreSQL volume) and the file
#      (media volume) are both still there  <- the acceptance criterion
#
# Usage: bash scripts/smoke_test.sh [base_url]
set -euo pipefail

BASE="${1:-${BASE_URL:-http://localhost:8080}}"
COMPOSE=(docker compose)
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

step()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()    { printf '    \033[32mOK\033[0m   %s\n' "$*"; }
fail()  { printf '    \033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

cd "$WORKDIR"

# A unique name keeps a re-run from matching a file left behind by a previous run.
NAME="smoke-$(date +%Y%m%d-%H%M%S)-$$.bin"
head -c 65536 /dev/urandom > "$NAME"
ok "generated test file $NAME ($(wc -c < "$NAME") bytes)"

step "1/6 application answers through Nginx"
curl -fsS --retry 15 --retry-connrefused --retry-all-errors --retry-delay 2 -o /dev/null "$BASE/" \
    || fail "GET / did not return 2xx"
ok "GET / -> 200"
curl -fsS "$BASE/healthz/" | grep -q '"database": "ok"' || fail "/healthz/ is not healthy"
ok "GET /healthz/ reports database ok"

step "2/6 upload is rejected without a CSRF token"
CODE="$(curl -sS -o /dev/null -w '%{http_code}' -F "file=@$NAME" "$BASE/")"
[ "$CODE" = "403" ] || fail "expected 403 without CSRF token, got $CODE"
ok "POST without token -> 403"

step "3/6 upload with a CSRF token"
JAR="$WORKDIR/cookies.txt"
curl -fsS -c "$JAR" -o /dev/null "$BASE/"
TOKEN="$(awk '$6 == "csrftoken" { print $7 }' "$JAR")"
[ -n "$TOKEN" ] || fail "no csrftoken cookie was set by GET /"
# Origin/Referer emulate a real browser, which is what actually exercises Django's
# CSRF origin check (plain curl sends neither).
CODE="$(curl -sS -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -H "Origin: $BASE" \
    -H "Referer: $BASE/" \
    -F "csrfmiddlewaretoken=$TOKEN" \
    -F "file=@$NAME" \
    "$BASE/")"
[ "$CODE" = "302" ] || fail "expected 302 after a valid upload, got $CODE"
ok "POST with token -> 302"

step "4/6 file is listed and served by Nginx from the media volume"
HREF="$(curl -fsS "$BASE/" | grep -o "/media/[^\"]*${NAME%.bin}[^\"]*" | head -1)"
[ -n "$HREF" ] || fail "$NAME does not appear in the listing"
ok "listing links to $HREF"
curl -fsS --retry 10 --retry-all-errors --retry-delay 2 -o downloaded.bin "$BASE$HREF" || fail "GET $HREF failed"
cmp -s "$NAME" downloaded.bin || fail "served bytes differ from the uploaded file"
ok "downloaded file is byte-for-byte identical"

step "5/6 recreating the stack (docker compose down, then up)"
cd - >/dev/null
"${COMPOSE[@]}" down
"${COMPOSE[@]}" up -d --wait --wait-timeout 300
cd "$WORKDIR"
ok "containers recreated"

step "6/6 data survived the recreation"
curl -fsS --retry 15 --retry-connrefused --retry-all-errors --retry-delay 2 -o listing.html "$BASE/"
grep -q "$NAME" listing.html || fail "record is gone from the listing (PostgreSQL volume lost)"
ok "record still listed (postgres_data volume persisted)"
curl -fsS --retry 10 --retry-all-errors --retry-delay 2 -o after.bin "$BASE$HREF" || fail "GET $HREF failed after restart (media volume lost)"
cmp -s "$NAME" after.bin || fail "file content changed after restart"
ok "file still served with identical bytes (media_data volume persisted)"

printf '\n\033[1;32mSMOKE TEST PASSED\033[0m - uploads survive down/up\n'
