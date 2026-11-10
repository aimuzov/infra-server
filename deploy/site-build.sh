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
	request=$(cat "/run/site-deploy/$project.ref" 2>/dev/null || true)
	source "$(dirname "${BASH_SOURCE[0]}")/site-images.sh"
	parse_site_images "$project" "$request"

	# Один деплой за раз на все сайты: параллельные сборки душат маленький сервер,
	# поэтому деплой любого сайта ждёт здесь предыдущий. fetch стоит после блокировки,
	# чтобы дождавшийся деплой увидел свежие теги и main.
	exec 9>/tmp/sites-deploy.lock
	flock 9

	cd "/srv/$project"
	git fetch --quiet --tags origin
	target=origin/main
	[[ -n $site_ref ]] && target=refs/tags/$site_ref
	if [[ -n $site_revision && $(git rev-parse "${target}^{commit}") != "$site_revision" ]]; then
		echo 'The release tag does not match the image source revision' >&2
		exit 2
	fi
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

	if [[ -n $site_override ]]; then
		available=$(df -Pk /var/lib/docker | awk 'NR == 2 {print $4}')
		if (( available < 4 * 1024 * 1024 )); then
			echo 'At least 4 GiB free is required before pulling pilot images' >&2
			exit 1
		fi
		install -d -m 0700 "/var/lib/site-deploy/$project"
		state=$(mktemp -d "/var/lib/site-deploy/$project/rollback.XXXXXX")
		site_save_rollback "$state" "$compose"
		echo "Rollback state: $state"
		override=$(mktemp "/run/site-deploy/$project.images.XXXXXX")
		trap 'rm -f "$override"' EXIT
		printf '%s\n' "$site_override" > "$override"
		git checkout --quiet --detach "$target"
		if ! docker compose -f "$compose" -f "$override" pull "${site_services[@]}" ||
			! site_check_image_revisions; then
			git checkout --quiet --detach "$(<"$state/revision")"
			exit 1
		fi
		available=$(df -Pk /var/lib/docker | awk 'NR == 2 {print $4}')
		if (( available < 2 * 1024 * 1024 )); then
			echo 'Less than 2 GiB free after pulling images' >&2
			git checkout --quiet --detach "$(<"$state/revision")"
			exit 1
		fi
		if ! docker compose -f "$compose" -f "$override" up -d --no-build --pull never --remove-orphans ||
			! site_image_health "$project"; then
			echo 'Deployment failed, restoring the previous revision and images' >&2
			site_restore_rollback "$state" "$project"
			exit 1
		fi
	else
		git checkout --quiet --detach "$target"
		git log -1 --format="%h %s ($(git describe --tags --always))"
		docker compose -f "$compose" up -d --build --quiet-pull --remove-orphans
		docker image prune -f >/dev/null
	fi
	exit
}
