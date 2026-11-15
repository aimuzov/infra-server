#!/usr/bin/env bash
# Собирает vpn/config.json для Xray из подписки Alesha Vpnov и перезапускает
# контейнер vpn, если конфиг поменялся. Профиль "Всё и сразу" берём как есть:
# в нём свои балансировщики и запасные ноды на случай, если основные лежат.
# Мы только выкидываем метаданные, приглушаем лог и переносим прокси на 0.0.0.0
# с фиксированными портами, чтобы до них достучались контейнеры из сети vpn.
set -euo pipefail

cd "$(dirname "$0")"
umask 077

allow_stale=false
if [[ $# == 1 && $1 == --allow-stale ]]; then
	allow_stale=true
elif [[ $# != 0 ]]; then
	echo 'usage: update.sh [--allow-stale]' >&2
	exit 2
fi

# shellcheck source=/dev/null
[[ -f ../.env ]] && source ../.env
# shellcheck source=/dev/null
[[ -f subscription.env ]] && source subscription.env
: "${VPN_SUBSCRIPTION_URL:?VPN_SUBSCRIPTION_URL is not set}"

exec 8>/tmp/infra-vpn-update.lock
flock 8
sub=$(mktemp)
next=$(mktemp)
previous=$(mktemp)
validation=$(mktemp)
staged=$(mktemp .config.XXXXXX)
trap 'rm -f "$sub" "$next" "$previous" "$validation" "$staged"' EXIT

validate_config() {
	docker compose run --rm --no-deps -T --user 0:0 \
		-v "$1:/etc/xray/config.json:ro" vpn run -test -c /etc/xray/config.json \
		>"$validation" 2>&1
}

update_failed() {
	local reason=$1
	if $allow_stale && [[ -s config.json ]] && validate_config "$PWD/config.json"; then
		printf '::warning title=VPN refresh::%s; keeping the validated existing config\n' "$reason"
		exit 0
	fi
	printf 'vpn: %s; no replacement was installed\n' "$reason" >&2
	exit 1
}

if ! curl -fsS --max-time 30 -H 'Accept: application/json' -o "$sub" "$VPN_SUBSCRIPTION_URL" 2>"$validation"; then
	update_failed 'subscription download failed'
fi
if ! jq -e 'type == "array" and length > 0' "$sub" >/dev/null 2>&1; then
	update_failed 'subscription is empty or invalid'
fi
if ! jq -e '
	[.[] | select((.remarks | type) == "string") | select(.remarks | contains("Всё и сразу"))]
	| if length == 1 then .[0] else error("ambiguous or missing profile") end
	| del(.remarks, .meta)
	| .log = {loglevel: "warning"}
	| .inbounds |= map(
		if .tag == "http" then .listen = "0.0.0.0" | .port = 3128
		elif .tag == "socks" then .listen = "0.0.0.0" | .port = 1080
		else . end
	)
	| select(([.inbounds[] | select(.tag == "http" and .protocol == "http")] | length) == 1)
	| select(([.inbounds[] | select(.tag == "socks" and .protocol == "socks")] | length) == 1)
	| select(any(.outbounds[]; .protocol == "vless"))
' "$sub" >"$next" 2>"$validation"; then
	update_failed 'subscription has no unique compatible profile'
fi

if ! validate_config "$next"; then
	update_failed 'new VPN config failed Xray validation'
fi
if cmp -s "$next" config.json; then
	echo 'vpn: config unchanged and validated'
	exit 0
fi
if ! existing=$(docker compose ps --all --quiet vpn); then
	update_failed 'cannot inspect VPN container state'
fi

had_previous=false
if [[ -f config.json ]]; then
	cp config.json "$previous"
	had_previous=true
fi
install -m 644 "$next" "$staged"
mv -f "$staged" config.json

if [[ -n $existing ]] && ! docker compose up -d --no-deps --force-recreate vpn; then
	if $had_previous; then
		install -m 644 "$previous" "$staged"
		mv -f "$staged" config.json
		if ! docker compose up -d --no-deps --force-recreate vpn; then
			echo 'vpn: container recreation and rollback both failed' >&2
			exit 1
		fi
		update_failed 'container recreation failed; previous VPN config was restored'
	fi
	rm -f config.json
	echo 'vpn: container recreation failed and no previous config exists' >&2
	exit 1
fi
echo "vpn: config updated and validated ($(jq '[.outbounds[] | select(.protocol == "vless")] | length' config.json) nodes)"
