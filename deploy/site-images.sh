#!/usr/bin/env bash

parse_site_images() {
	local project=$1 request=$2
	local digest='sha256:[0-9a-f]{64}'
	local release='([a-z0-9]+-)?v[0-9]+\.[0-9]+\.[0-9]+'
	local first second
	local -a services=() names=() digests=()

	site_ref=
	site_revision=
	site_override=
	site_services=()
	site_containers=()
	site_images=()

	if [[ -z $request || $request =~ ^$release$ ]]; then
		site_ref=$request
		return 0
	fi
	if [[ ! $request =~ ^images\ ($release)\ ([0-9a-f]{40})\ ($digest)(\ ($digest))?$ ]]; then
		echo 'Invalid deployment request' >&2
		return 2
	fi

	site_ref=${BASH_REMATCH[1]}
	site_revision=${BASH_REMATCH[3]}
	first=${BASH_REMATCH[4]}
	second=${BASH_REMATCH[6]}
	case "$project" in
		sublya)
			[[ -n $second ]] || { echo 'Sublya requires two image digests' >&2; return 2; }
			services=(site bot)
			names=(sublya-site sublya-bot)
			site_containers=(sublya-site sublya-bot)
			digests=("$first" "$second")
			;;
		thinks-mcp)
			[[ -z $second ]] || { echo 'Thinks MCP requires one image digest' >&2; return 2; }
			services=(app)
			names=(thinks-mcp)
			site_containers=(thinks-mcp)
			digests=("$first")
			;;
		*)
			echo 'Registry deployment is disabled for this project' >&2
			return 2
			;;
	esac

	local i
	site_services=("${services[@]}")
	site_override='services:'
	for i in "${!services[@]}"; do
		site_images+=("ghcr.io/aimuzov/${names[i]}@${digests[i]}")
		printf -v site_override '%s\n  %s:\n    image: %s' \
			"$site_override" "${services[i]}" "${site_images[i]}"
	done
}

site_check_image_revisions() {
	local image revision
	for image in "${site_images[@]}"; do
		revision=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image") || return
		if [[ $revision != "$site_revision" ]]; then
			echo "Image revision does not match the requested release: $image" >&2
			return 1
		fi
	done
}

site_image_health() {
	local url
	local -a urls=()
	case "$1" in
		sublya) urls=(https://sublya.aimuzov.online/ https://sublya.aimuzov.online/admin/api/health) ;;
		thinks-mcp) urls=(https://thinks-mcp.aimuzov.online/health) ;;
		*) return 2 ;;
	esac
	for url in "${urls[@]}"; do
		local host=${url#https://}
		host=${host%%/*}
		curl --fail --silent --show-error --output /dev/null --max-time 5 \
			--retry 6 --retry-delay 3 --retry-all-errors --resolve "$host:443:127.0.0.1" "$url" || return
	done
}

site_save_rollback() {
	local state=$1 compose=$2 image tag i
	install -d -m 0700 "$state"
	git rev-parse HEAD > "$state/revision"
	printf '%s\n' "$compose" > "$state/compose"
	printf 'services:\n' > "$state/images.yml"
	for i in "${!site_services[@]}"; do
		image=$(docker inspect --type container --format '{{.Image}}' "${site_containers[i]}") || return
		[[ $image =~ ^sha256:[0-9a-f]{64}$ ]] || return 2
		tag="site-rollback/${site_containers[i]}:${image:7}"
		docker image tag "$image" "$tag" || return
		printf '  %s:\n    image: %s\n' "${site_services[i]}" "$tag" >> "$state/images.yml"
	done
}

site_restore_rollback() {
	local state=$1 project=$2 revision compose
	revision=$(<"$state/revision") || return
	compose=$(<"$state/compose") || return
	git checkout --quiet --detach "$revision" || return
	docker compose -f "$compose" -f "$state/images.yml" up -d \
		--no-build --pull never --remove-orphans || return
	site_image_health "$project"
}
