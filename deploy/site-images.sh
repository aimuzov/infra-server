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
		panels)
			[[ -n $second ]] || { echo 'Panels requires two image digests' >&2; return 2; }
			services=(site api)
			names=(panels-site panels-api)
			site_containers=(panels panels-api)
			digests=("$first" "$second")
			;;
		prestige-logist)
			[[ -n $second ]] || { echo 'Prestige Logist requires two image digests' >&2; return 2; }
			services=(site api)
			names=(prestige-logist-site prestige-logist-api)
			site_containers=(prestige-logist prestige-logist-api)
			digests=("$first" "$second")
			;;
		peeshet)
			[[ -z $second ]] || { echo 'Peeshet requires one image digest' >&2; return 2; }
			services=(app)
			names=(peeshet)
			site_containers=(peeshet)
			digests=("$first")
			;;
		wodlog)
			[[ -n $second ]] || { echo 'Wodlog requires two image digests' >&2; return 2; }
			services=(site api)
			names=(wodlog-site wodlog-api)
			site_containers=(wodlog wodlog-api)
			digests=("$first" "$second")
			;;
		paralpac)
			[[ -z $second ]] || { echo 'Paralpac requires one image digest' >&2; return 2; }
			services=(app)
			names=(paralpac)
			site_containers=(paralpac)
			digests=("$first")
			;;
		skator)
			[[ -n $second ]] || { echo 'Skator requires application and mail digests' >&2; return 2; }
			services=(site bot mail)
			names=(skator skator skator-mail)
			site_containers=(skator-site skator-bot skator-mail)
			digests=("$first" "$first" "$second")
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
		prestige-logist) urls=(https://logist.aimuzov.online/ https://logist.aimuzov.online/api/health) ;;
		wodlog) urls=(https://wodlog.aimuzov.online/ https://wodlog.aimuzov.online/api/health) ;;
		panels) urls=(https://pufikmebel.ru/ https://pufikmebel.ru/api/health) ;;
		peeshet) urls=(https://peeshet.aimuzov.online/health) ;;
		skator) urls=(https://skator.ru/robots.txt) ;;
		paralpac)
			local attempt
			for attempt in {1..7}; do
				if docker exec paralpac node --input-type=module -e '
					const user = process.env.CHATMELD_USER || "admin";
					const password = process.env.CHATMELD_PASSWORD;
					if (!password) process.exit(1);
					const url = "http://127.0.0.1:" + (process.env.CHATMELD_PORT || "4321") + "/api/state";
					const authorization = "Basic " + Buffer.from(user + ":" + password).toString("base64");
					const response = await fetch(url, { headers: { authorization }, signal: AbortSignal.timeout(5000) });
					if (!response.ok || (await response.json()).role !== "owner") process.exit(1);
				' >/dev/null 2>&1; then return 0; fi
				sleep 3
			done
			echo 'Paralpac authenticated health check failed' >&2
			return 1
			;;
		*) return 2 ;;
	esac
	for url in "${urls[@]}"; do
		local host=${url#https://}
		host=${host%%/*}
		curl --fail --silent --show-error --output /dev/null --max-time 5 \
			--retry 6 --retry-delay 3 --retry-all-errors --resolve "$host:443:127.0.0.1" "$url" || return
	done
	if [[ $1 == skator ]]; then
		[[ $(docker inspect -f '{{.State.Running}}' skator-bot) == true ]] &&
			[[ $(docker inspect -f '{{.State.Health.Status}}' skator-mail) == healthy ]]
	fi
}

site_save_rollback() {
	local state=$1 compose=$2 image manifest local_manifest tag i
	install -d -m 0700 "$state"
	git rev-parse HEAD > "$state/revision"
	printf '%s\n' "$compose" > "$state/compose"
	printf 'services:\n' > "$state/images.yml"
	for i in "${!site_services[@]}"; do
		image=$(docker inspect --type container --format '{{.Config.Image}}' "${site_containers[i]}") || return
		manifest=$(docker inspect --type container --format '{{.ImageManifestDescriptor.Digest}}' "${site_containers[i]}") || return
		local_manifest=$(docker image inspect --platform linux/amd64 --format '{{.Descriptor.Digest}}' "$image") || return
		if [[ ! $manifest =~ ^sha256:[0-9a-f]{64}$ || $local_manifest != "$manifest" ]]; then
			echo "Cannot pin the running image for ${site_containers[i]}: local manifest mismatch" >&2
			return 2
		fi
		tag="site-rollback/${site_containers[i]}:${manifest:7}"
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
