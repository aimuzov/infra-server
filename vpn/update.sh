#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
umask 077
exec 8>/tmp/infra-vpn-update.lock
flock 8

# shellcheck source=/dev/null
[[ -f ../.env ]] && source ../.env
# shellcheck source=/dev/null
[[ -f subscription.env ]] && source subscription.env
: "${VPN_SUBSCRIPTION_URL:?VPN_SUBSCRIPTION_URL is not set}"

sub="$(mktemp)"
next="$(mktemp "$PWD/.config-next.XXXXXX")"
previous="$(mktemp "$PWD/.config-prev.XXXXXX")"
trap 'rm -f "$sub" "$next" "$previous"' EXIT

if ! curl -fsS --max-time 30 -o "$sub" "$VPN_SUBSCRIPTION_URL" 2>/dev/null; then
    echo "vpn: subscription download failed, keeping the current config" >&2
    exit 1
fi

if ! jq -e '
    if type != "array" then error("expected profile list") else . end
    | [.[] | select(.remarks? | strings | contains("Всё и сразу"))]
    | if length != 1 then error("expected one aggregate profile") else .[0] end
    | del(.remarks, .meta)
    | .log = {loglevel: "warning"}
    | .inbounds |= map(
        if .tag == "http" then .listen = "0.0.0.0" | .port = 3128
        elif .tag == "socks" then .listen = "0.0.0.0" | .port = 1080
        else . end
    )
    | if ([.inbounds[] | select(.tag == "http")] | length) != 1
        or ([.inbounds[] | select(.tag == "socks")] | length) != 1
      then error("missing proxy inbounds") else . end
' "$sub" >"$next" 2>/dev/null; then
    echo "vpn: subscription profile rejected, keeping the current config" >&2
    exit 1
fi

if cmp -s "$next" config.json; then
    echo "vpn: config unchanged"
    exit 0
fi

chmod 644 "$next"
if ! docker compose run --rm --no-deps -v "$next:/etc/xray/config.json:ro" vpn run -test -c /etc/xray/config.json >/dev/null 2>&1; then
    echo "vpn: Xray rejected the new config, keeping the current config" >&2
    exit 1
fi

had_previous=false
if [[ -f config.json ]]; then
    cp -p config.json "$previous"
    had_previous=true
fi
if ! existing="$(docker compose ps --all --quiet vpn 2>/dev/null)"; then
    echo "vpn: container lookup failed, keeping the current config" >&2
    exit 1
fi
mv -f "$next" config.json

if [[ -n $existing ]]; then
    if ! docker compose up -d --no-deps --force-recreate vpn >/dev/null 2>&1; then
        if [[ $had_previous == true ]]; then
            mv -f "$previous" config.json
            docker compose up -d --no-deps --force-recreate vpn >/dev/null 2>&1 || true
        else
            rm -f config.json
        fi
        echo "vpn: container update failed, previous config restored" >&2
        exit 1
    fi
fi
echo "vpn: config updated"
