#!/bin/bash
# 役割：管理VMの運用ツールを準備し、固定版 PowerShell で証明書 worker を起動します。
# 入力：公開設定と protectedParameters の環境変数。出力：成功/失敗と安全な段階名だけ。
# この前処理は root / Ubuntu 24.04 amd64 専用です。OS の追加対応を推測して続行しません。
# 秘密入力は shell の内部配列へ退避して環境から消し、root だけが読める /run の tmpfs に一時保存。
# APT や curl は秘密を継承しません。worker 起動直前だけ環境を渡し、終了時は一時領域を回収します。
# 初回は Ubuntu と Microsoft の配布先への通信が必要です。FW の許可と署名検証を前提とします。
# Azure Linux Managed Run Command passes named/protected parameters as environment
# values. Only root-private /run tmpfs files temporarily hold those inputs;
# dependency installers inherit none of them. Nothing is put in argv or logs.
# 未定義変数・コマンド失敗を見逃さず、トレース出力と自動 export も無効にします。
set -euo pipefail
set +x
set +a
umask 077
stage='runtime-inspection'
work=''
# Capture and unexport inputs using Bash builtins BEFORE any child process,
# including identity/runtime inspection or mktemp. Bash arrays aren't exported.
input_names=(Action PublicConfigJson DnsPluginArgumentsJson PreparedPfxBase64 PreparedPfxPassword L7IngressSecret RealtimeTicketKey)
declare -a saved_input=()
for input_name in "${input_names[@]}"; do
    saved_input+=("${!input_name-}")
    unset "$input_name"
done
# 通常終了・失敗のどちらでも同じ後始末を通し、機密一時ファイルを残さない関数です。
finish() {
    status=$?
    trap - EXIT
    for input_name in "${input_names[@]}"; do unset "$input_name"; done
    unset saved_input
    if [ -n "$work" ]; then rm -rf -- "$work"; fi
    if [ "$status" -ne 0 ]; then
        printf 'Public gateway certificate setup failed at %s. Review dependency access.\n' "$stage"
    fi
    exit "$status"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
[ "$EUID" = 0 ] || exit 1
[ "${OSTYPE:-}" = linux-gnu ] || exit 1
run_tmpfs=false
while read -r fs_source fs_mount fs_type remainder; do
    if [ "$fs_mount" = /run ] && [ "$fs_type" = tmpfs ]; then run_tmpfs=true; break; fi
done < /proc/mounts
[ "$run_tmpfs" = true ] || exit 1
# The deployment's supported management image is Ubuntu 24.04 LTS amd64.
. /etc/os-release
[ "$ID" = ubuntu ] && [ "$VERSION_ID" = 24.04 ] || exit 1
[ "$(dpkg --print-architecture)" = amd64 ] || exit 1
work=$(mktemp -d /run/egakumi-certificate.XXXXXXXX)
chmod 700 "$work"
for ((input_index=0; input_index<${#input_names[@]}; input_index++)); do
    # umask 077 makes each redirected file 0600 from its first write.
    printf '%s' "${saved_input[$input_index]}" > "$work/input.${input_names[$input_index]}"
done
unset saved_input
# PowerShell は証明書の実行環境、Azure CLI は人がログ表示・コンテナー操作等に使う管理ツールです。
# Azure CLI を証明書 worker の呼出しには使いません。それぞれ独立に導入状態を調べ、
# PowerShell だけが既に存在するVMでも Azure CLI の準備を省略しないようにします。
# バージョンを固定し、再実行で同版が存在すればリポジトリ通信も再インストールもしません。
expected='7.6.7'
current=''
if command -v pwsh >/dev/null 2>&1; then
    current=$(pwsh -NoLogo -NoProfile -NonInteractive -Command '$PSVersionTable.PSVersion.ToString()' 2>/dev/null) || current=''
fi
azure_cli_expected='2.90.0'
azure_cli_package='2.90.0-1~noble'
azure_cli_current=''
if command -v az >/dev/null 2>&1; then
    # versionはAzureへ接続しません。初期化用の設定も一時領域へ閉じ込め、
    # 人のログイン情報を作成・取得したり、VMのMIでログインしたりしません。
    azure_cli_current=$(AZURE_CONFIG_DIR="$work/azure-cli-inspection" AZURE_CORE_COLLECT_TELEMETRY=0 \
        az version --query '"azure-cli"' --output tsv --only-show-errors 2>/dev/null) || azure_cli_current=''
fi
if [ "$current" != "$expected" ] || [ "$azure_cli_current" != "$azure_cli_expected" ]; then
    stage='management-tool-dependencies'
    export DEBIAN_FRONTEND=noninteractive
    # Azureの初期化やOS更新がAPTを使っている場合も、ロックを破壊せず上限300秒待ちます。
    apt-get -o DPkg::Lock::Timeout=300 update >/dev/null 2>&1
    apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends ca-certificates curl gnupg >/dev/null 2>&1
fi
if [ "$current" != "$expected" ]; then
    stage='powershell-runtime-installation'
    curl --proto '=https' --tlsv1.2 -fsSL --retry 3 \
        https://packages.microsoft.com/config/ubuntu/24.04/packages-microsoft-prod.deb \
        -o "$work/packages-microsoft-prod.deb" >/dev/null 2>&1
    apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends \
        "$work/packages-microsoft-prod.deb" >/dev/null 2>&1
    apt-get -o DPkg::Lock::Timeout=300 update >/dev/null 2>&1
    # APT verifies the Microsoft repository package signature and pinned version.
    apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends --allow-downgrades \
        powershell=7.6.7-1.deb >/dev/null 2>&1
    current=$(pwsh -NoLogo -NoProfile -NonInteractive -Command '$PSVersionTable.PSVersion.ToString()' 2>/dev/null)
    [ "$current" = "$expected" ] || exit 1
fi
if [ "$azure_cli_current" != "$azure_cli_expected" ]; then
    stage='azure-cli-management-tool-installation'
    # Azure CLIにはPowerShellとは別の公式APTリポジトリが必要です。
    # 鍵をこのリポジトリだけのSigned-Byへ指定し、署名検証を無効にはしません。
    # Noble/amd64は冒頭で確認済みなので、別OSのパッケージへ置き換えて続行しません。
    curl --proto '=https' --tlsv1.2 -fsSL --retry 3 \
        https://packages.microsoft.com/keys/microsoft.asc \
        -o "$work/microsoft.asc" >/dev/null 2>&1
    gpg --batch --yes --dearmor --output "$work/microsoft.gpg" "$work/microsoft.asc" >/dev/null 2>&1
    install -d -m 0755 /etc/apt/keyrings
    install -m 0644 "$work/microsoft.gpg" /etc/apt/keyrings/egakumi-azure-cli.gpg
    cat > "$work/azure-cli.sources" <<'AZURE_CLI_REPOSITORY'
Types: deb
URIs: https://packages.microsoft.com/repos/azure-cli/
Suites: noble
Components: main
Architectures: amd64
Signed-By: /etc/apt/keyrings/egakumi-azure-cli.gpg
AZURE_CLI_REPOSITORY
    install -m 0644 "$work/azure-cli.sources" /etc/apt/sources.list.d/egakumi-azure-cli.sources
    apt-get -o DPkg::Lock::Timeout=300 update >/dev/null 2>&1
    apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends --allow-downgrades \
        "azure-cli=$azure_cli_package" >/dev/null 2>&1
    azure_cli_current=$(AZURE_CONFIG_DIR="$work/azure-cli-inspection" AZURE_CORE_COLLECT_TELEMETRY=0 \
        az version --query '"azure-cli"' --output tsv --only-show-errors 2>/dev/null)
    [ "$azure_cli_current" = "$azure_cli_expected" ] || exit 1
fi
# Bicep が同梱した worker 本文を復号します。URL から最新コードを勝手に取得する方式ではありません。
stage='certificate-maintenance'
printf '%s' '__CERTIFICATE_WORKER_BASE64__' | base64 --decode > "$work/certificate.ps1"
chmod 600 "$work/certificate.ps1"
# Restore the inputs only for the actual certificate worker, after all external
# installation, inspection, decoding and chmod commands have completed.
for input_name in "${input_names[@]}"; do
    IFS= read -r -d '' "$input_name" < "$work/input.$input_name" || true
    export "$input_name"
done
pwsh -NoLogo -NoProfile -NonInteractive -File "$work/certificate.ps1" -ReadProtectedEnvironment
