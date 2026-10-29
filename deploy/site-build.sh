#!/usr/bin/env bash
# Сборка сайта на сервере: чекаут запрошенного релизного тега (без тега --
# свежего main) и пересборка его контейнеров.
# Запускается внутри site-deploy@<project>.service, который стартует site.sh,
# поэтому оборванное SSH-соединение не убивает сборку на середине.
set -euo pipefail

# Тело обёрнуто в блок: bash читает его целиком до того, как pull этого репо заменит файл.
{
	project=${1:-}
	if [[ ! $project =~ ^[a-z0-9-]+$ || ! -d /srv/$project/.git ]]; then
		echo "usage: site-build.sh <project>, a clone in /srv/<project>" >&2
		exit 2
	fi

	# Файл пишет site.sh. Проверяем повторно, так как здесь это единственный ввод снаружи.
	ref=$(cat "/run/site-deploy/$project.ref" 2>/dev/null || true)
	if [[ -n $ref && ! $ref =~ ^([a-z0-9]+-)?v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
		echo "not a release tag: $ref" >&2
		exit 2
	fi

	# Один деплой за раз на все сайты: параллельные сборки душат маленький сервер,
	# поэтому деплой любого сайта ждёт здесь предыдущий. fetch стоит после блокировки,
	# чтобы дождавшийся деплой увидел свежие теги и main.
	exec 9>/tmp/sites-deploy.lock
	flock 9

	cd "/srv/$project"
	git fetch --quiet --tags origin
	target=origin/main
	[[ -n $ref ]] && target=refs/tags/$ref
	git checkout --quiet --detach "$target"
	git log -1 --format="%h %s ($(git describe --tags --always))"

	# .env делается один раз руками из .env.example и из git никогда не приходит.
	if [[ -f .env.example && ! -f .env ]]; then
		echo "no /srv/$project/.env: fill one in from .env.example" >&2
		exit 1
	fi

	# --remove-orphans убирает контейнеры сервисов, которых в compose-файле уже нет.
	# Монорепо выкладывает подпроекты из отдельных клонов, у каждого свой compose-файл
	# по имени проекта: /srv/reabica-app берёт deploy/reabica-app.yml. Без него -- общий.
	compose=deploy/docker-compose.yml
	[[ -f deploy/$project.yml ]] && compose=deploy/$project.yml

	docker compose -f "$compose" up -d --build --quiet-pull --remove-orphans
	docker image prune -f >/dev/null
	exit
}
