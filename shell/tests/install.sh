#!/usr/bin/env bash
set -euo pipefail

test -f /.dockerenv
cd "$(dirname "$0")/../.."
rm -f /root/.config/fish/conf.d/zz-invalid.fish
printf 'export KEEP_CUSTOM_BASHRC=yes\n' > /root/.bashrc
mkdir -p /root/.config/fish
printf '# existing profile\n' > /root/.config/fish/config.fish
bash shell/install.sh
grep -q 'existing profile' /root/.local/state/infra-server/shell-backup/.config/fish/config.fish
test "$(bash -c 'source /root/.bashrc; printf %s "$KEEP_CUSTOM_BASHRC"')" = yes
test "$(SSH_TTY=/dev/test bash -c 'source /root/.bashrc; printf %s "$0"')" = bash
test "$(getent passwd root | cut -d: -f7)" = /bin/bash
fish -ic "test \"\$fish_key_bindings\" = fish_vi_key_bindings; and functions -q fzf_configure_bindings; and functions -q z; and functions -q fish_prompt; or exit 1"
printf 'status is-interactive; and printf "__%%s__\\n" FISH_LOGIN_OK; exit\n' |
	script -qec 'env SHLVL=0 SSH_TTY=/dev/test bash --noprofile -i' /dev/null > /tmp/shell-login.log
grep -q '__FISH_LOGIN_OK__' /tmp/shell-login.log
TERM=xterm-ghostty infocmp >/dev/null

find /root/.config/fish /root/.config/oh-my-posh -type f -exec sha256sum {} + | sort > /tmp/shell-before
mkdir -p /tmp/no-network
printf '#!/bin/sh\nexit 1\n' > /tmp/no-network/curl
cp /tmp/no-network/curl /tmp/no-network/apt-get
chmod +x /tmp/no-network/*
PATH="/tmp/no-network:$PATH" bash shell/install.sh
find /root/.config/fish /root/.config/oh-my-posh -type f -exec sha256sum {} + | sort > /tmp/shell-after
cmp /tmp/shell-before /tmp/shell-after
test "$(grep -c '^# BEGIN infra-server fish$' /root/.bashrc)" = 1
broken=$(mktemp -d)
cp -a shell/. "$broken/"
printf 'function broken\n' > "$broken/config/fish/conf.d/zz-invalid.fish"
if PATH="/tmp/no-network:$PATH" bash "$broken/install.sh" > /tmp/shell-invalid.log 2>&1; then
	echo 'Installer accepted an invalid fish profile.' >&2
	exit 1
fi
test ! -e /root/.config/fish/conf.d/zz-invalid.fish
find /root/.config/fish /root/.config/oh-my-posh -type f -exec sha256sum {} + | sort > /tmp/shell-rejected
cmp /tmp/shell-before /tmp/shell-rejected
printf 'Shell install and repeat install passed.\n'
