#!/usr/bin/env bash
# Деплой на самом сервере: подтянуть main, обновить VPN и применить конфиг Caddy.
# GitHub Actions запускает его на каждый пуш в main и при ручном запуске workflow.
# Это единственная команда, разрешённая ключу Actions (command= в authorized_keys).
set -euo pipefail

# Тело обёрнуто в блок: bash читает его целиком до того, как git pull заменит этот файл.
{
	cd /srv/infra-server

	# Два пуша подряд не должны деплоиться одновременно, второй ждёт первого.
	exec 9>/tmp/infra-deploy.lock
	flock 9

	git pull --ff-only --quiet
	git log -1 --oneline

	vpn/update.sh
	systemctl link --quiet /srv/infra-server/vpn/vpn-update.service /srv/infra-server/vpn/vpn-update.timer
	systemctl link --quiet /srv/infra-server/docker/docker-prune.service /srv/infra-server/docker/docker-prune.timer
	systemctl link --quiet /srv/infra-server/deploy/site-deploy@.service
	systemctl daemon-reload
	systemctl enable --now --quiet vpn-update.timer docker-prune.timer

	# sshd -t проверяет весь конфиг с новым файлом до reload. Сломанный файл откатываем
	# на предыдущий, чтобы деплой не мог закрыть доступ всем.
	conf=/etc/ssh/sshd_config.d/10-no-passwords.conf
	if ! cmp -s ssh/10-no-passwords.conf "$conf"; then
		[[ -f $conf ]] && cp -p "$conf" "$conf.prev"
		install -m 644 ssh/10-no-passwords.conf "$conf"
		if sshd -t; then
			rm -f "$conf.prev"
			systemctl reload ssh
		else
			if [[ -f $conf.prev ]]; then mv "$conf.prev" "$conf"; else rm "$conf"; fi
			echo "sshd rejected ssh/10-no-passwords.conf, the old config stays" >&2
			exit 1
		fi
	fi

	# Сломанный конфиг останавливает деплой здесь, и Caddy продолжает работать со старым.
	# Проверка стоит до up -d, потому что тот пересоздаёт Caddy при любой правке
	# его сервиса в compose.yml. Запускаем в одноразовом контейнере, так как он видит свежие файлы.
	docker compose run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile

	docker compose up -d --quiet-pull

	# Caddyfile смонтирован отдельным файлом, а git pull кладёт на его место новый файл,
	# то есть запущенный контейнер видит старую версию, пока его не пересоздать.
	# С директорией sites/ такой проблемы нет, ей хватает reload.
	if docker exec caddy cat /etc/caddy/Caddyfile | cmp -s - Caddyfile; then
		docker exec caddy caddy reload --config /etc/caddy/Caddyfile
	else
		docker compose up -d --force-recreate --no-deps caddy
	fi

	bash shell/install.sh
	exit
}
