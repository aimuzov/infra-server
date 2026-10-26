#!/usr/bin/env bash
# Деплой сайта, запускается его ключом из GitHub Actions через authorized_keys:
#   command="/srv/infra-server/deploy/site.sh <project>",restrict ssh-ed25519 ...
# Релизный тег Actions передаёт как ssh-команду, он приходит в SSH_ORIGINAL_COMMAND.
# Без тега деплоится свежий main.
# Сама сборка идёт в site-deploy@<project>.service: если SSH-соединение оборвётся,
# systemd её доведёт. Этот скрипт стартует юнит, показывает его лог и выходит
# с его результатом.
set -euo pipefail

# Тело обёрнуто в блок: bash читает его целиком до того, как pull этого репо заменит файл.
{
	project=${1:-}
	if [[ ! $project =~ ^[a-z0-9-]+$ || ! -d /srv/$project/.git ]]; then
		echo "usage: site.sh <project> [[component-]vX.Y.Z], a clone in /srv/<project>" >&2
		exit 2
	fi

	ref=${2:-${SSH_ORIGINAL_COMMAND:-}}
	# В монорепо тег начинается с имени подпроекта: docs-v1.3.0.
	if [[ -n $ref && ! $ref =~ ^([a-z0-9]+-)?v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
		echo "not a release tag: $ref" >&2
		exit 2
	fi

	unit="site-deploy@$project.service"

	# Сборка этого сайта ещё идёт, например после обрыва соединения. Даём ей закончиться
	# и собираем заново, чтобы запрошенная версия тоже выехала.
	while [[ $(systemctl show -p ActiveState --value "$unit") == activating ]]; do
		echo "waiting for the running $unit"
		sleep 5
	done

	# Юнит не принимает аргументов кроме проекта, поэтому тег передаём через файл.
	install -d /run/site-deploy
	printf '%s\n' "$ref" > "/run/site-deploy/$project.ref"

	journalctl -u "$unit" -f -n 0 -o cat &
	log=$!
	trap 'kill "$log" 2>/dev/null' EXIT

	# Блокируется до конца сборки и падает вместе с ней. Джоба принадлежит systemd,
	# поэтому переживёт смерть этого клиента вместе с соединением.
	status=0
	systemctl start "$unit" || status=$?

	# journalctl может немного отставать от юнита, даём ему дописать хвост лога.
	sleep 1
	exit "$status"
}
