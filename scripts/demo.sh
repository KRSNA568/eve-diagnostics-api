#!/usr/bin/env bash
# End-to-end walkthrough of every requirement against a running stack.
#
#   docker compose up -d --build && docker compose exec api eve seed && scripts/demo.sh
#
# Native run (API on localhost:8000, worker running):  EVE_CLI="uv run eve" scripts/demo.sh
# Signup is rate limited to 5/minute per IP: wait a minute between repeated runs.
set -euo pipefail
cd "$(dirname "$0")/.."

API="${API:-http://localhost:8000/api/v1}"
EVE_CLI="${EVE_CLI:-docker compose exec -T api eve}"
WEBHOOK_URL="${WEBHOOK_URL:-http://localhost:8000/api/v1/payments/webhook/}"
PASSWORD="Password123"

step() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
show() { printf '   %s\n' "$*"; }
# json '<python expression over d>'  - parse JSON from stdin without requiring jq
json() { python3 -c "import sys, json; d = json.load(sys.stdin); print($1)"; }
days_from_now() {
  python3 -c "from datetime import datetime, timedelta, timezone as tz; \
print((datetime.now(tz.utc) + timedelta(days=$1)).replace(minute=0, second=0, microsecond=0).isoformat())"
}
# call METHOD PATH [BODY] [extra curl args...] -> prints "<status> <body>"
call() {
  local method=$1 path=$2 body=${3:-} ; shift $(( $# < 3 ? $# : 3 ))
  curl -sS -X "$method" "$API$path" -H 'Content-Type: application/json' \
    ${TOKEN:+-H "Authorization: Bearer $TOKEN"} ${body:+-d "$body"} "$@" \
    -w '\n%{http_code}' | python3 -c "import sys; *b, s = sys.stdin.read().split('\n'); print(s, '\n'.join(b))"
}
status() { head -c 3; }
body() { cut -d' ' -f2-; }

step "Health"
show "$(curl -sS "$API/health/ready/")"

step "Sign up (returns user + JWT access/refresh tokens)"
EMAIL="demo.$(date +%s)@example.com"
TOKEN=""
SIGNUP=$(call POST /auth/signup/ "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"full_name\":\"Demo Patient\"}")
TOKEN=$(echo "$SIGNUP" | body | json 'd["tokens"]["access_token"]')
show "$(echo "$SIGNUP" | status) user=$(echo "$SIGNUP" | body | json 'd["user"]["email"]')"

step "Catalog (public, cached in Redis): the same query twice"
for _ in 1 2; do
  show "GET /centres/?city=mumbai -> $(curl -sS -o /dev/null -D - "$API/centres/?city=mumbai" | grep -i '^x-cache' | tr -d '\r')"
done
CENTRE=$(curl -sS "$API/centres/?city=mumbai" | json 'd["items"][0]["id"]')
TEST=$(curl -sS "$API/tests/?q=cbc" | json 'd["items"][0]["id"]')
show "$(curl -sS "$API/centres/$CENTRE/" | json 'd["name"] + ": " + ", ".join(o["test"]["code"] + " " + o["price"] + " " + o["currency"] for o in d["offerings"])')"

step "Book a test (PENDING, price snapshotted server-side)"
BOOKING_BODY="{\"centre_id\":\"$CENTRE\",\"test_id\":\"$TEST\",\"appointment_at\":\"$(days_from_now 3)\"}"
BOOKED=$(call POST /bookings/ "$BOOKING_BODY")
BOOKING=$(echo "$BOOKED" | body | json 'd["id"]')
show "$(echo "$BOOKED" | status) $(echo "$BOOKED" | body | json 'd["status"] + " amount=" + d["amount"] + " " + d["currency"]')"
show "same slot again -> $(call POST /bookings/ "$BOOKING_BODY" | body | json 'd["error"]["code"]')"

step "Pay (simulated gateway) with an Idempotency-Key, then retry the same request"
PAY="{\"booking_id\":\"$BOOKING\",\"payment_method\":\"mock_card_success\"}"
FIRST=$(call POST /payments/ "$PAY" -H "Idempotency-Key: demo-$BOOKING")
show "$(echo "$FIRST" | status) payment=$(echo "$FIRST" | body | json 'd["status"]') booking=$(echo "$FIRST" | body | json 'd["booking"]["status"]')"
RETRY=$(call POST /payments/ "$PAY" -H "Idempotency-Key: demo-$BOOKING")
show "retry -> $(echo "$RETRY" | status) same payment: $([ "$(echo "$FIRST" | body | json 'd["id"]')" = "$(echo "$RETRY" | body | json 'd["id"]')" ] && echo yes || echo NO)"
show "pay again (new key) -> $(call POST /payments/ "$PAY" | body | json 'd["error"]["code"]')"

step "A declined card fails the booking"
DECLINED_BOOKING=$(call POST /bookings/ "{\"centre_id\":\"$CENTRE\",\"test_id\":\"$TEST\",\"appointment_at\":\"$(days_from_now 4)\"}" | body | json 'd["id"]')
DECLINED=$(call POST /payments/ "{\"booking_id\":\"$DECLINED_BOOKING\",\"payment_method\":\"mock_card_declined\"}")
show "$(echo "$DECLINED" | body | json 'd["status"] + " (" + d["failure_reason"] + "), booking " + d["booking"]["status"]')"

step "Provider webhook: the same signed event delivered 3 times"
WEBHOOK_BOOKED=$(call POST /bookings/ "{\"centre_id\":\"$CENTRE\",\"test_id\":\"$TEST\",\"appointment_at\":\"$(days_from_now 5)\"}" | body)
WEBHOOK_BOOKING=$(echo "$WEBHOOK_BOOKED" | json 'd["id"]')
AMOUNT=$(echo "$WEBHOOK_BOOKED" | json 'd["amount"]')
$EVE_CLI send-webhook --booking-id "$WEBHOOK_BOOKING" --outcome succeeded --amount "$AMOUNT" \
  --repeat 3 --url "$WEBHOOK_URL" | sed 's/^/   /'
for _ in $(seq 1 30); do
  STATE=$(call GET "/bookings/$WEBHOOK_BOOKING/" | body | json 'd["status"]')
  [ "$STATE" = "CONFIRMED" ] && break
  sleep 0.5
done
show "booking after the worker processed the event: $STATE"
show "forged signature -> $(TOKEN="" call POST /payments/webhook/ '{"event_id":"evt_forged"}' -H 'X-Eve-Signature: t=1,v1=00' | status)"

step "Authorization: another user cannot see this booking"
OTHER=$(call POST /auth/signup/ "{\"email\":\"other.$(date +%s)@example.com\",\"password\":\"$PASSWORD\",\"full_name\":\"Someone Else\"}" | body | json 'd["tokens"]["access_token"]')
show "GET /bookings/<first user's id> as another user -> $(TOKEN="$OTHER" call GET "/bookings/$BOOKING/" | status)"

step "Rate limiting: repeated failed logins from one IP"
CODES=""
for _ in 1 2 3 4 5 6; do
  CODES="$CODES $(TOKEN="" call POST /auth/login/ '{"email":"nobody@example.com","password":"wrong-password-1"}' | status)"
done
show "login attempts ->$CODES"

printf '\n\033[1;32mDone.\033[0m Swagger UI: %s\n' "${API%/api/v1}/docs"
