#!/bin/bash
# HTTPS証明書工程で準備済みのPowerShellを使い、認証専用workerを配置・実行します。
set -euo pipefail
set +x
umask 077
task_config="${PortalAuthConfigJson-}"
unset PortalAuthConfigJson
[ "$EUID" = 0 ]
[ "$(pwsh -NoLogo -NoProfile -NonInteractive -Command '$PSVersionTable.PSVersion.ToString()')" = 7.6.7 ]
task_dir=/var/lib/egakumi-portal-auth
[ ! -L "$task_dir" ] && [ ! -L "$task_dir/worker.ps1" ]
install -d -m 0700 "$task_dir"
task_tmp=$(mktemp "$task_dir/worker.XXXXXXXX")
trap 'rm -f -- "$task_tmp"' EXIT
printf '%s' '__PORTAL_AUTH_WORKER_BASE64__' | base64 --decode > "$task_tmp"
chmod 0600 "$task_tmp"
mv -- "$task_tmp" "$task_dir/worker.ps1"
export PortalAuthConfigJson="$task_config"
unset task_config
pwsh -NoLogo -NoProfile -NonInteractive -Command '& /var/lib/egakumi-portal-auth/worker.ps1 -Action Initialize -ConfigJson $env:PortalAuthConfigJson'
