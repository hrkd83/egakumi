#!/usr/bin/env bash
# 役割：管理 VM の時刻同期、認証ログ、SSH の基本設定をそろえる初回・再適用処理です。
# 入力：Bicep が埋め込む NTP サーバー一覧。出力：秘密を含まない JSON の状態と失敗段階。
# root / systemd / OpenSSH / Python を前提とし、必要なサービスがない場合は黙って省略しません。
# NTP は証明書・token の時刻判定に必要です。企業 DNS/FW の切替時も名前解決と UDP/123 を確認します。
# ログイン成功/失敗等は残し、コマンド本文を含む sudo/cron イベントは AMA 転送前に収集対象から除外します。
# Prepare standard Linux authentication/security logs for Azure Monitor Agent.
# No execve/PROCTITLE, shell history, sudo I/O, or secret-file contents are collected.
# Cloud credentials must be passed in protected files, never command arguments.
set -euo pipefail
umask 077
# IaC replaces this placeholder with base64 JSON, preserving the first-line Bash shebang.
export CORE_NTP_SERVERS_B64='__CORE_NTP_SERVERS_BASE64__'
stage=platform
trap 'printf "{\"status\":\"failed\",\"stage\":\"%s\",\"code\":\"LinuxAuditConfigurationFailed\"}\n" "$stage"' ERR

[[ $(id -u) == 0 ]]
[[ $(uname -s) == Linux ]]
command -v systemctl >/dev/null
command -v sshd >/dev/null
command -v python3 >/dev/null

# BEGIN TIME CONFIG HELPERS
# Azure Ubuntu には Chrony/PTP を使うイメージもあるため、既存 unit の種類を選びます。
# この関数自体はサービスを操作しません。引数は systemd の LoadState 読取結果です。
select_time_service() {
    if [[ $1 == loaded ]]; then
        printf 'chrony.service'
    elif [[ $2 == loaded ]]; then
        printf 'chronyd.service'
    elif [[ $3 == loaded ]]; then
        printf 'systemd-timesyncd.service'
    else
        return 1
    fi
}

# Ubuntu 標準の sources.d が実際に読み込まれることを確認し、管理するファイル一つだけを書きます。
# 既存の PHC/refclock・confdir・他の source 設定は変更しません。外部宛先は FW でも制限します。
write_chrony_sources() {
    python3 - "$1" "$2" "$3" <<'PY_CHRONY'
from pathlib import Path
import shlex
import sys

servers, config_path, source_path = sys.argv[1:]
directory = Path(source_path)
lines = [shlex.split(line, comments=True) for line in Path(config_path).read_text().splitlines()]
if not any(parts and parts[0] == 'sourcedir' and str(directory) in parts[1:] for parts in lines):
    raise SystemExit('Chrony configuration must load the configured sources directory')
directory.mkdir(mode=0o755, parents=True, exist_ok=True)
target = directory / '90-core-platform.sources'
target.write_text('# Managed external NTP sources; existing PHC and OS settings are retained.\n'
                  + ''.join(f'server {server} iburst\n' for server in servers.split()))
target.chmod(0o644)
PY_CHRONY
}

# timesyncd が既存のイメージでは、従来どおり専用 drop-in だけを管理します。
write_timesyncd_config() {
    install -d -m 0755 "$(dirname "$2")"
    printf '[Time]\nNTP=%s\nFallbackNTP=\n' "$1" > "$2"
    chmod 0644 "$2"
}
# END TIME CONFIG HELPERS

# 1. 入力を JSON として検証し、既存の時刻同期サービスへ NTP 一覧を設定します。
stage=ntp
# Azure's NTP destination allowlist and the guest clock must use the same names.
# Decode a JSON array rather than interpolate caller text into shell commands.
ntp_list=$(python3 - <<'PY'
import base64, json, os, re
values = json.loads(base64.b64decode(os.environ.get('CORE_NTP_SERVERS_B64', 'W10='), validate=True))
if not isinstance(values, list) or not values:
    raise SystemExit('NTP servers are required')
if any(not isinstance(v, str) or not re.fullmatch(r'(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', v) for v in values):
    raise SystemExit('Invalid NTP server')
print(' '.join(values))
PY
)
if ! time_service=$(select_time_service \
    "$(systemctl show chrony.service --property=LoadState --value 2>/dev/null || true)" \
    "$(systemctl show chronyd.service --property=LoadState --value 2>/dev/null || true)" \
    "$(systemctl show systemd-timesyncd.service --property=LoadState --value 2>/dev/null || true)"); then
    printf '{"status":"failed","stage":"ntp","code":"UnsupportedTimeService"}\n'
    exit 1
fi
if [[ $time_service == chrony.service || $time_service == chronyd.service ]]; then
    command -v chronyd >/dev/null
    command -v chronyc >/dev/null
    write_chrony_sources "$ntp_list" /etc/chrony/chrony.conf /etc/chrony/sources.d
    # -p は構文検査だけです。デーモンを二重起動したり、時計を直接変更したりしません。
    chronyd -p -f /etc/chrony/chrony.conf >/dev/null
else
    write_timesyncd_config "$ntp_list" /etc/systemd/timesyncd.conf.d/90-core-platform.conf
fi
systemctl enable "$time_service" >/dev/null 2>&1
systemctl restart "$time_service"
systemctl is-active --quiet "$time_service"
if [[ $time_service == chrony.service || $time_service == chronyd.service ]]; then
    # 管理ソケットへの応答を確認します。同期元・同期精度の受入確認とは分けて扱います。
    chronyc tracking >/dev/null
fi

# 2. 認証ログは保持し、コマンド本文を含むイベントを除外します。設定検査後に rsyslog を再起動します。
stage=syslog
if ! command -v rsyslogd >/dev/null; then
    # The standard Ubuntu server image normally includes rsyslog. Install it if
    # an approved replacement image omits it; egress to its apt mirror is needed.
    command -v apt-get >/dev/null
    DEBIAN_FRONTEND=noninteractive apt-get -qq update >/dev/null 2>&1
    DEBIAN_FRONTEND=noninteractive apt-get -qq install -y rsyslog >/dev/null 2>&1
fi
install -d -m 0755 /etc/rsyslog.d /etc/ssh/sshd_config.d

# This runs before AMA's 10-azuremonitoragent-omfwd.conf and normal file sinks.
# PAM の認証・account/session は保持し、sudo の COMMAND 行と cron 本文を除外します。
# rsyslog の通常プロパティ $msg は set で書き換えられないため、マスク済みイベントは新造しません。
# Do not install process/auditd forwarding or collect journal files wholesale.
cat > /etc/rsyslog.d/00-core-command-redaction.conf <<'RSYSLOG'
if ($programname == "sudo" and $msg contains "COMMAND=") then {
    stop
}
if (($programname == "cron" or $programname == "CRON") and not re_match($msg, "^[[:space:]]*pam_[[:alnum:]_]+[(]cron:(auth|account|session)[)]:")) then {
    stop
}
RSYSLOG
chmod 0644 /etc/rsyslog.d/00-core-command-redaction.conf
stage=syslog-configuration
rsyslogd -N1 >/dev/null 2>&1
stage=syslog-service
systemctl enable rsyslog >/dev/null 2>&1
systemctl restart rsyslog

# 3. パスワード認証と root 直ログインを禁止し、公開鍵/Entra SSH 証明書の経路を維持します。
# sshd -t と実効設定の確認が通ってから reload し、不正設定を稼働中サービスへ適用しません。
stage=ssh
# OpenSSH uses the first matching value. Keep this before cloud-init's drop-in.
# Existing sessions survive reload; Entra SSH certificates still use publickey.
cat > /etc/ssh/sshd_config.d/00-core-access.conf <<'SSHD'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
SyslogFacility AUTHPRIV
LogLevel VERBOSE
SSHD
chmod 0644 /etc/ssh/sshd_config.d/00-core-access.conf
sshd -t
# grep -q は一致した時点で入力を閉じるため、パイプでつないだ sshd が
# SIGPIPE で終了すると pipefail により正常な設定まで失敗扱いになります。
# 実効設定を一度だけメモリーへ読み、各条件を照合してから破棄します。
ssh_effective=$(sshd -T)
grep -qx 'passwordauthentication no' <<< "$ssh_effective"
grep -qx 'kbdinteractiveauthentication no' <<< "$ssh_effective"
grep -qx 'permitrootlogin no' <<< "$ssh_effective"
grep -qx 'pubkeyauthentication yes' <<< "$ssh_effective"
unset ssh_effective
systemctl reload ssh

# 4. サービスが実際に動いていることを読み戻し、安全な認証系イベントを１件記録します。
stage=readback
systemctl is-active --quiet rsyslog
systemctl is-active --quiet ssh
logger -p authpriv.notice -t core-management-audit 'Linux authentication logging configured; command arguments are not collected'
printf '{"status":"configured","timeService":"%s","authentication":"public-key-only","syslog":"auth/authpriv and system warnings","commandArgumentCollection":false}\n' "$time_service"
