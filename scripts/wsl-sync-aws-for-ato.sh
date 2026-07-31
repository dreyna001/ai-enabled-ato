#!/usr/bin/env bash
# Copy AWS config + SSO cache into ato service user's home for Bedrock under systemd.
# Re-run after `aws sso login` when SSO tokens expire.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run as root (sudo bash scripts/wsl-sync-aws-for-ato.sh)" >&2
  exit 1
fi

SOURCE_HOME="${WSL_AWS_SOURCE_HOME:-/home/dreyna}"
WIN_AWS="/mnt/c/Users/Daniel.Reyna/.aws"
ATO_AWS="/var/lib/ato/.aws"

resolve_config() {
  if [[ -f "$SOURCE_HOME/.aws/config" ]]; then
    cp -L "$SOURCE_HOME/.aws/config" "$1"
    return 0
  fi
  if [[ -f "$WIN_AWS/config" ]]; then
    cp "$WIN_AWS/config" "$1"
    return 0
  fi
  echo "ERROR: no AWS config found under $SOURCE_HOME/.aws or $WIN_AWS" >&2
  exit 1
}

resolve_sso_tree() {
  local dest="$1"
  rm -rf "$dest"
  if [[ -d "$WIN_AWS/sso" ]]; then
    cp -r "$WIN_AWS/sso" "$dest"
    return 0
  fi
  if [[ -d "$SOURCE_HOME/.aws/sso" ]]; then
    cp -rL "$SOURCE_HOME/.aws/sso" "$dest" 2>/dev/null || cp -r "$SOURCE_HOME/.aws/sso" "$dest"
    return 0
  fi
  echo "ERROR: no AWS SSO cache found; run aws sso login first" >&2
  exit 1
}

install -d -o ato -g ato -m 700 "$ATO_AWS"
resolve_config "$ATO_AWS/config"
resolve_sso_tree "$ATO_AWS/sso"
chown -R ato:ato "$ATO_AWS"
chmod -R u=rwX,go= "$ATO_AWS"
chmod 700 "$ATO_AWS"

profile="${AWS_PROFILE:-DevDataScience-180555414983}"
if sudo -u ato env AWS_PROFILE="$profile" HOME=/var/lib/ato \
  /opt/ato-analyzer/venv/bin/python - <<'PY'
import boto3
print(boto3.client("sts", region_name="us-east-1").get_caller_identity())
PY
then
  echo "AWS credentials OK for systemd user 'ato' (profile: $profile)"
else
  echo "ERROR: ato user still cannot resolve AWS credentials" >&2
  exit 1
fi

systemctl restart ato-api.service ato-analyzer-worker.service
echo "Restarted ato-api and ato-analyzer-worker"
