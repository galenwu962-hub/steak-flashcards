#!/usr/bin/env bash
# Weekly data pull from the 顾客说 portal, in the steps the owner takes part in.
#
#   scripts/portal.sh start [phone]   open the login page in a background browser, request the SMS code,
#                                     save the captcha to scripts/captcha.png (send it to the owner)
#   scripts/portal.sh captcha ABCD    type the captcha the owner read
#   scripts/portal.sh sms 123456      type the SMS code the owner relayed; saves scripts/state.json
#   scripts/portal.sh status          what the helper is doing
#   scripts/portal.sh stop            close the browser
#   scripts/portal.sh pull            download reviews + traffic with the saved session, then import
#
# All Playwright traffic goes through $HTTPS_PROXY; the helper only listens on 127.0.0.1:8765.
set -euo pipefail
cd "$(dirname "$0")"
PORT=8765
DEFAULT_PHONE=18576420071
api() { curl -s --noproxy '*' -X POST --data-binary "${2:-}" "http://127.0.0.1:$PORT/$1"; }

case "${1:-}" in
  start)
    if curl -s --noproxy '*' "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then echo "helper already running: $(curl -s --noproxy '*' http://127.0.0.1:$PORT/)"; exit 0; fi
    rm -f captcha.png after_captcha.png after_sms.png now.png
    nohup node portal_login_server.js "${2:-$DEFAULT_PHONE}" > portal_login.log 2>&1 &
    for _ in $(seq 1 120); do
      sleep 1
      st=$(curl -s --noproxy '*' "http://127.0.0.1:$PORT/" 2>/dev/null || true)
      case "$st" in captcha-ready*|already-logged-in*|error*) break;; esac
    done
    echo "${st:-timeout}"
    [ -f captcha.png ] && echo "captcha: $(pwd)/captcha.png"
    ;;
  captcha) api captcha "$2" ;;
  sms)     api sms "$2" ;;
  status)  curl -s --noproxy '*' "http://127.0.0.1:$PORT/" || echo "not running" ;;
  stop)    api quit || true; pkill -f portal_login_server.js || true; echo stopped ;;
  pull)
    [ -f state.json ] || { echo "no saved session, run: scripts/portal.sh start"; exit 1; }
    node pull_portal.js reviews x 6882d4c699553aa3d9aea3fa 6882d4c699553aa3d9aea3fb 200
    node pull_portal.js traffic x 6894da1fdf282a4cfe62d7f7 6894da5ef112c312aed5374e 500
    cd .. && python -m app.importers scripts/data/reviews.ndjson && python -m app.import_metrics scripts/data/traffic.ndjson
    ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
