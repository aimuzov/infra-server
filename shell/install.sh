#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID != 0 || $(uname -s) != Linux || $(dpkg --print-architecture) != amd64 ]]; then
	echo 'The server shell installer requires root on Debian amd64.' >&2
	exit 1
fi

cd "$(dirname "$0")"
exec 8>/run/lock/infra-shell.lock
flock 8

packages=(fish zoxide fd-find bat ca-certificates curl ncurses-bin)
missing=()
for package in "${packages[@]}"; do
	if [[ $(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true) != 'install ok installed' ]]; then
		missing+=("$package")
	fi
done
if ((${#missing[@]})); then
	export DEBIAN_FRONTEND=noninteractive
	apt-get update -qq
	apt-get install -y -qq --no-install-recommends "${missing[@]}"
fi

ln -sfn /usr/bin/fdfind /usr/local/bin/fd
ln -sfn /usr/bin/batcat /usr/local/bin/bat
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

fzf_version=0.74.4
if [[ $(fzf --version 2>/dev/null | cut -d' ' -f1) != "$fzf_version" ]]; then
	curl -fsSL --retry 3 --connect-timeout 10 --max-time 120 -o "$tmp/fzf.tar.gz" \
		"https://github.com/junegunn/fzf/releases/download/v$fzf_version/fzf-$fzf_version-linux_amd64.tar.gz"
	echo "05e6813a337cc722c3ed07e54a764b75cc5d671e2e60459db0ba696ee5fa7504  $tmp/fzf.tar.gz" | sha256sum -c --quiet
	tar -xzf "$tmp/fzf.tar.gz" -C "$tmp" fzf
	install -m 755 "$tmp/fzf" /usr/local/bin/fzf
fi

omp_version=31.4.1
if [[ $(oh-my-posh --version 2>/dev/null) != "$omp_version" ]]; then
	curl -fsSL --retry 3 --connect-timeout 10 --max-time 120 -o "$tmp/oh-my-posh" \
		"https://github.com/JanDeDobbeleer/oh-my-posh/releases/download/v$omp_version/posh-linux-amd64"
	echo "bfd0ccab85dc031c848b09b68226f1be942d2ce18978f94c9659b6e7e1b259af  $tmp/oh-my-posh" | sha256sum -c --quiet
	install -m 755 "$tmp/oh-my-posh" /usr/local/bin/oh-my-posh
fi

while IFS= read -r -d '' source; do
	fish --no-config --no-execute "$source"
done < <(find config/fish -type f -name '*.fish' -print0)
bash -n bashrc
backup=/root/.local/state/infra-server/shell-backup
while IFS= read -r -d '' source; do
	relative=${source#config/}
	target=/root/.config/$relative
	if ! cmp -s "$source" "$target"; then
		if [[ -f $target && ! -e $backup/.config/$relative ]]; then
			install -D -m 600 "$target" "$backup/.config/$relative"
		fi
		install -D -m 644 "$source" "$target"
	fi
done < <(find config -type f -print0)

if ! grep -q '^# BEGIN infra-server fish$' /root/.bashrc; then
	if [[ -f /root/.bashrc && ! -e $backup/.bashrc ]]; then
		install -D -m 600 /root/.bashrc "$backup/.bashrc"
	fi
	printf '\n# BEGIN infra-server fish\n. /root/.config/fish/ssh.bash\n# END infra-server fish\n' >> /root/.bashrc
fi
install -m 644 bashrc /root/.config/fish/ssh.bash

if [[ ! -f /etc/terminfo/x/xterm-ghostty || xterm-ghostty.terminfo -nt /etc/terminfo/x/xterm-ghostty ]]; then
	tic -x -o /etc/terminfo xterm-ghostty.terminfo
fi
printf 'Server shell profile installed.\n'
