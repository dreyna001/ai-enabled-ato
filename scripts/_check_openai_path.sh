#!/usr/bin/env bash
# Permission + TLS sanity checks (no secret output).
set -euo pipefail

ENV_FILE="/etc/ato-analyzer/credentials/ato-local.env"
UNIT="/etc/systemd/system/ato-api.service"

echo "=== Permissions ==="
sudo ls -lad /etc/ato-analyzer/credentials
DIR_PERMS="$(sudo stat -c '%a' /etc/ato-analyzer/credentials 2>/dev/null || echo '?')"
DIR_OWNER="$(sudo stat -c '%U:%G' /etc/ato-analyzer/credentials 2>/dev/null || echo '?')"
if [[ "$DIR_PERMS" == "700" ]] && [[ "$DIR_OWNER" == "root:root" ]]; then
  echo "credentials dir: ${DIR_OWNER} mode ${DIR_PERMS} — ato cannot traverse; fix with:"
  echo "  sudo install -d -o root -g ato -m 710 /etc/ato-analyzer/credentials"
fi
sudo ls -la "$ENV_FILE"
if sudo -u ato test -r "$ENV_FILE"; then
  echo "ato can read ato-local.env: yes"
else
  echo "ato can read ato-local.env: NO"
fi
if sudo grep -q '^ATO_TEXT_MODEL_API_KEY=.' "$ENV_FILE"; then
  echo "ATO_TEXT_MODEL_API_KEY line: present"
else
  echo "ATO_TEXT_MODEL_API_KEY line: MISSING"
fi

echo ""
echo "=== systemd ato-api ==="
grep -E '^(User=|EnvironmentFile|Environment=ATO_LOCAL)' "$UNIT" || true
systemctl is-active ato-api.service || true

echo ""
echo "=== API process env (names only) ==="
PID="$(pgrep -f 'venv/bin/ato-service' | head -1 || true)"
echo "pid=${PID:-none}"
if [[ -n "$PID" ]]; then
  sudo tr '\0' '\n' <"/proc/$PID/environ" | grep -E '^(ATO_TEXT_MODEL_API_KEY|ATO_LOCAL_ENV_FILE)=' \
    | sed 's/ATO_TEXT_MODEL_API_KEY=.*/ATO_TEXT_MODEL_API_KEY=<set>/'
fi

echo ""
echo "=== Text model smoke (no secret printed) ==="
if [[ -x /home/dreyna/ai-coe-projects/ai-enabled-ato/scripts/_diagnose_text_model.sh ]]; then
  sudo bash /home/dreyna/ai-coe-projects/ai-enabled-ato/scripts/_diagnose_text_model.sh 2>&1 | tail -6
fi

echo ""
echo "=== TLS to api.openai.com (issuer hint) ==="
if command -v openssl >/dev/null; then
  ISSUER="$(echo | openssl s_client -connect api.openai.com:443 -servername api.openai.com 2>/dev/null \
    | openssl x509 -noout -issuer 2>/dev/null || true)"
  SUBJECT="$(echo | openssl s_client -connect api.openai.com:443 -servername api.openai.com 2>/dev/null \
    | openssl x509 -noout -subject 2>/dev/null || true)"
  echo "issuer: ${ISSUER:-handshake failed}"
  echo "subject: ${SUBJECT:-handshake failed}"
  case "$ISSUER" in
    *Zscaler*|*zscaler*|*ZSCL*) echo "zscaler_like_cert: likely YES (TLS inspection)" ;;
    *DigiCert*|*Cloudflare*|*Let*) echo "zscaler_like_cert: likely NO (public CA path)" ;;
    *) echo "zscaler_like_cert: inconclusive — inspect issuer string above" ;;
  esac
else
  echo "openssl not installed"
fi

echo ""
echo "=== curl result ==="
curl -sS -o /dev/null -w "http_code:%{http_code} err:%{errormsg}\n" --max-time 12 \
  https://api.openai.com/v1/models 2>&1 || true

echo ""
echo "=== proxy env in API process ==="
if [[ -n "$PID" ]]; then
  sudo tr '\0' '\n' <"/proc/$PID/environ" | grep -iE '^(https?_proxy|no_proxy)=' || echo "(no proxy vars in API process)"
fi
