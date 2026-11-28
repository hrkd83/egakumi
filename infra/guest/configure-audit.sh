#!/usr/bin/env bash
# 役割：管理 VM の時刻同期、認証ログ、SSH の基本設定をそろえる初回・再適用処理です。
# 入力：固定の Azure ホスト PHC 構成。出力：秘密を含まない JSON の状態と失敗段階。
# root / systemd / OpenSSH / Python を前提とし、必要なサービスがない場合は黙って省略しません。
# 証明書・token の時刻判定には正確な同期が必要です。ホスト PHC 同期は外部 UDP/123 を使いません。
# ログイン成功/失敗等は残し、コマンド本文を含む sudo/cron イベントは AMA 転送前に収集対象から除外します。
# Prepare standard Linux authentication/security logs for Azure Monitor Agent.
# No execve/PROCTITLE, shell history, sudo I/O, or secret-file contents are collected.
# Cloud credentials must be passed in protected files, never command arguments.
set -Eeuo pipefail
umask 077
# 先行移行moduleだけがこの一意のliteralをtrueへ置換します。呼出入力や環境変数では切り替えません。
readonly core_host_time_only=false
# 関数内コマンドの stdout 抑止中にも、失敗の JSON を元の出力先へ返します。
exec 3>&1
stage=platform
time_transaction_pending=false
time_services_changed=false
audit_failed() {
    local failure=$? rollback_failed=false
    trap - ERR
    if [[ $time_transaction_pending == true ]] && time_configuration owns "$time_transaction_token" "${time_paths[@]}" >/dev/null 2>&1; then
        time_configuration rollback "$time_transaction_token" "${time_paths[@]}" || rollback_failed=true
        if [[ $time_services_changed == true && $rollback_failed == false ]]; then
            if [[ $chrony_was_enabled != enabled ]]; then systemctl disable "$time_service" >/dev/null 2>&1 || rollback_failed=true; fi
            if [[ $chrony_was_active == true ]]; then systemctl restart "$time_service" || rollback_failed=true
            else systemctl stop "$time_service" || rollback_failed=true; fi
            if [[ $timesyncd_was_enabled == enabled ]]; then systemctl enable systemd-timesyncd.service >/dev/null 2>&1 || rollback_failed=true; fi
            if [[ $timesyncd_was_active == true ]]; then systemctl start systemd-timesyncd.service || rollback_failed=true; fi
        fi
    fi
    if [[ $rollback_failed == true ]]; then
        printf '{"status":"failed","stage":"%s","code":"TimeConfigurationRollbackFailed"}\n' "$stage" >&3
    else
        printf '{"status":"failed","stage":"%s","code":"LinuxAuditConfigurationFailed"}\n' "$stage" >&3
    fi
    exit "$failure"
}
trap audit_failed ERR

[[ $(id -u) == 0 ]]
[[ $(uname -s) == Linux ]]
command -v systemctl >/dev/null
if [[ $core_host_time_only == false ]]; then command -v sshd >/dev/null; fi
command -v python3 >/dev/null

# BEGIN TIME CONFIG HELPERS
# Azure ホストの PHC を使える既存 Chrony unit を選びます。外部 NTP へ fallback しません。
# この関数自体はサービスを操作しません。引数は systemd の LoadState 読取結果です。
select_time_service() {
    if [[ $1 == loaded ]]; then
        printf 'chrony.service'
    elif [[ $2 == loaded ]]; then
        printf 'chronyd.service'
    else
        return 1
    fi
}

# Accelerated Networking の別 PHC と混同しないよう、安定 symlink と sysfs の両方を確認します。
validate_host_clock() {
    [[ -L $1 && -c $1 ]] || return 1
    local resolved leaf
    resolved=$(readlink -f -- "$1") || return 1
    leaf=${resolved##*/}
    [[ $resolved == /dev/"$leaf" && $leaf =~ ^ptp[0-9]+$ ]] || return 1
    [[ $(cat -- "$2/$leaf/clock_name") == hyperv ]]
}

# 全変更を検査してから適用します。未知の企業 source/include は上書きせず失敗します。
# 初回 backup は保持し、構文検査・サービス起動・同期確認の失敗時は直前の設定へ復旧します。
time_configuration() {
    python3 - "$@" <<'PY_CHRONY'
from pathlib import Path
import json
import os
import shlex
import stat
import sys
import tempfile

mode, token, *arguments = sys.argv[1:]
config, confdir, sources, dhcp, timesyncd, transaction = map(Path, arguments)
legacy = sources / '90-core-platform.sources'
manifest = transaction / 'changes.json'
host_line = 'refclock PHC /dev/ptp_hyperv poll 3 dpoll -2 offset 0 stratum 2 refid AZUR'
ubuntu_sources = {'ntp.ubuntu.com', *(f'{i}.ubuntu.pool.ntp.org' for i in range(4))}

def regular(path, optional=False):
    if path.is_symlink():
        raise SystemExit('Time configuration symlinks require manual review')
    if not path.exists() and optional:
        return None
    if not path.is_file():
        raise SystemExit('Expected a regular time configuration file')
    return path.read_text(encoding='utf-8')

def files(directory, pattern):
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise SystemExit('Time configuration directories require manual review')
    return sorted(directory.glob(pattern))

def atomic_write(path, content, permissions):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix='.core-host-time-', delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(content)
            output.flush()
            os.fchmod(output.fileno(), permissions)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

def allowed(path):
    return path in (config, legacy, timesyncd) or (path.parent == confdir and path.suffix == '.conf')

if mode in ('owns', 'rollback', 'commit'):
    if transaction.is_symlink() or not transaction.is_dir():
        raise SystemExit('Invalid time configuration transaction')
    state = json.loads(regular(manifest))
    if state.get('version') != 1 or state.get('config') != str(config) or state.get('token') != token:
        raise SystemExit('Invalid time configuration transaction')
    if mode == 'owns':
        raise SystemExit(0)
    if any(path.is_symlink() for path in (config.parent, confdir, sources, dhcp, timesyncd.parent, transaction.parent)):
        raise SystemExit('Changed time configuration directories require manual review')
    if mode == 'commit':
        expected_files = [item['path'] for item in state['checks'] if Path(item['path']).parent == confdir]
        if [str(path) for path in files(confdir, '*.conf')] != expected_files:
            raise SystemExit('Changed Chrony include files require manual review')
        for item in state['checks']:
            path = Path(item['path'])
            if not allowed(path):
                raise SystemExit('Invalid time configuration verification target')
            current = regular(path, optional=True)
            if current != item['content'] or (current is not None and
                    stat.S_IMODE(path.stat().st_mode) != item['permissions']):
                raise SystemExit('Changed time configuration requires manual review')
    if mode == 'rollback':
        for item in state['changes']:
            path = Path(item['path'])
            if not allowed(path):
                raise SystemExit('Invalid time configuration restore target')
            current = regular(path, optional=True)
            if current not in (item['original'], item['content']) or (current is not None and
                    stat.S_IMODE(path.stat().st_mode) != item['permissions']):
                raise SystemExit('Changed time configuration requires manual restoration')
        for item in state['changes']:
            path = Path(item['path'])
            if item['original'] is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, item['original'], item['permissions'])
    manifest.unlink()
    transaction.rmdir()
    raise SystemExit(0)
if mode not in ('prepare', 'check') or transaction.exists() or transaction.is_symlink():
    raise SystemExit('Pending time configuration requires manual review')
if config.parent.is_symlink() or timesyncd.parent.is_symlink() or transaction.parent.is_symlink():
    raise SystemExit('Time configuration parent directories require manual review')

# Only complete, unmodified files generated by the former external-NTP implementation are removed.
legacy_content = regular(legacy, optional=True)
if legacy_content is not None and legacy_content != (
        '# Managed external NTP sources; existing PHC and OS settings are retained.\n'
        'server ntp.ubuntu.com iburst\n'):
    raise SystemExit('Modified managed NTP sources require manual review')
timesyncd_content = regular(timesyncd, optional=True)
if timesyncd_content is not None and timesyncd_content != '[Time]\nNTP=ntp.ubuntu.com\nFallbackNTP=\n':
    raise SystemExit('Modified managed timesyncd configuration requires manual review')

# Even disabled source directories are checked before migration, preserving unknown enterprise settings.
for directory in (sources, dhcp):
    for path in files(directory, '*.sources'):
        if path == legacy:
            continue
        for line in regular(path).splitlines():
            parts = shlex.split(line, comments=True)
            if parts and (len(parts) < 2 or parts[0] not in ('server', 'pool') or parts[1] not in ubuntu_sources):
                raise SystemExit('Unknown NTP sources require manual review')

changes = []
checks = []
managed_count = 0
for path in [config, *files(confdir, '*.conf')]:
    original = regular(path)
    updated = []
    for line in original.splitlines(keepends=True):
        parts = shlex.split(line, comments=True)
        disable = False
        if parts:
            directive = parts[0]
            if directive in ('include', 'peer', 'local', 'manual', 'initstepslew'):
                raise SystemExit('Unsupported time source configuration requires manual review')
            if directive == 'confdir' and (path != config or parts != ['confdir', str(confdir)]):
                raise SystemExit('Unknown Chrony include directory requires manual review')
            if directive == 'sourcedir':
                if len(parts) != 2 or parts[1] not in (str(sources), str(dhcp)):
                    raise SystemExit('Unknown Chrony sources directory requires manual review')
                disable = True
            elif directive in ('server', 'pool'):
                if len(parts) < 2 or parts[1] not in ubuntu_sources:
                    raise SystemExit('Unknown NTP source requires manual review')
                disable = True
            elif directive == 'refclock':
                if path == config and line.strip() == host_line:
                    managed_count += 1
                else:
                    options = parts[3:]
                    known = {'poll': {'3'}, 'dpoll': {'-2'}, 'offset': {'0', '0.0'}, 'stratum': {'2'}}
                    if (parts[:3] != ['refclock', 'PHC', '/dev/ptp_hyperv'] or len(options) % 2 or
                            any(options[i] not in known or options[i + 1] not in known[options[i]]
                                for i in range(0, len(options), 2))):
                        raise SystemExit('Unknown PHC source configuration requires manual review')
                    disable = True
        updated.append('# Disabled for Azure host time: ' + line if disable else line)
    if path == config:
        if managed_count > 1:
            raise SystemExit('Duplicate managed PHC sources require manual review')
        if managed_count == 0:
            if updated and not updated[-1].endswith('\n'):
                updated[-1] += '\n'
            updated.extend(['\n# Managed Azure host time; no network NTP sources.\n', host_line + '\n'])
    content = ''.join(updated)
    checks.append({'path': str(path), 'content': content, 'permissions': stat.S_IMODE(path.stat().st_mode)})
    if content != original:
        backup = Path(str(path) + '.core-platform-original')
        regular(backup, optional=True)
        changes.append({'path': str(path), 'original': original, 'content': content,
                        'permissions': stat.S_IMODE(path.stat().st_mode)})
for path, original in ((legacy, legacy_content), (timesyncd, timesyncd_content)):
    checks.append({'path': str(path), 'content': None, 'permissions': None})
    if original is not None:
        changes.append({'path': str(path), 'original': original, 'content': None,
                        'permissions': stat.S_IMODE(path.stat().st_mode)})

if mode == 'check':
    print(json.dumps({'status': 'checked', 'configurationKnown': True,
                      'hostConfigurationCurrent': not changes,
                      'plannedConfigurationFileChanges': len(changes)}, separators=(',', ':')))
    raise SystemExit(0)

transaction.mkdir(mode=0o700)
atomic_write(manifest, json.dumps({'version': 1, 'config': str(config), 'token': token,
                                 'changes': changes, 'checks': checks}), 0o600)
for item in changes:
    path = Path(item['path'])
    if item['content'] is None:
        path.unlink()
    else:
        backup = Path(str(path) + '.core-platform-original')
        if not backup.exists():
            atomic_write(backup, item['original'], 0o600)
        atomic_write(path, item['content'], item['permissions'])
PY_CHRONY
}

# waitsync に加え、選択 source と leap status を確認します。daemon 稼働だけで成功とは扱いません。
verify_host_time() {
    python3 - "$1" "$2" <<'PY_SYNC'
import csv
import re
import sys

rows = list(csv.reader(sys.argv[1].splitlines()))
if len(rows) != 1 or len(rows[0]) < 3 or rows[0][:3] != ['#', '*', 'AZUR']:
    raise SystemExit('Azure host PHC must be the only selected time source')
tracking = sys.argv[2]
if (not re.search(r'^Reference ID\s*:\s*415A5552\s+\(AZUR\)\s*$', tracking, re.MULTILINE) or
        not re.search(r'^Leap status\s*:\s*Normal\s*$', tracking, re.MULTILINE)):
    raise SystemExit('Azure host time must be synchronised')
PY_SYNC
}
# END TIME CONFIG HELPERS

# 1. Azure ホスト PHC へ継続同期します。driver/device 不備は外部 NTP へ逃がさず明示失敗します。
stage=host-time-platform
if ! time_service=$(select_time_service \
    "$(systemctl show chrony.service --property=LoadState --value 2>/dev/null || true)" \
    "$(systemctl show chronyd.service --property=LoadState --value 2>/dev/null || true)"); then
    printf '{"status":"failed","stage":"host-time-platform","code":"UnsupportedTimeService"}\n'
    exit 1
fi
command -v chronyd >/dev/null
command -v chronyc >/dev/null
validate_host_clock /dev/ptp_hyperv /sys/class/ptp
time_paths=(/etc/chrony/chrony.conf /etc/chrony/conf.d /etc/chrony/sources.d /run/chrony-dhcp
            /etc/systemd/timesyncd.conf.d/90-core-platform.conf /run/core-management-time)
chrony_was_enabled=$(systemctl is-enabled "$time_service" 2>/dev/null || true)
chrony_was_active=false
if systemctl is-active --quiet "$time_service"; then chrony_was_active=true; fi
timesyncd_was_enabled=$(systemctl is-enabled systemd-timesyncd.service 2>/dev/null || true)
timesyncd_was_active=false
if systemctl is-active --quiet systemd-timesyncd.service; then timesyncd_was_active=true; fi
stage=host-time-configuration
time_transaction_token=$(python3 -c 'import uuid; print(uuid.uuid4())')
time_transaction_pending=true
time_configuration prepare "$time_transaction_token" "${time_paths[@]}"
# -p は構文検査だけです。稼働中の時計や daemon はまだ変更しません。
chronyd -p -f /etc/chrony/chrony.conf >/dev/null
stage=host-time-service
time_services_changed=true
if [[ $(systemctl show systemd-timesyncd.service --property=LoadState --value 2>/dev/null || true) == loaded ]]; then
    systemctl disable --now systemd-timesyncd.service >/dev/null 2>&1
fi
systemctl enable "$time_service" >/dev/null 2>&1
systemctl restart "$time_service"
systemctl is-active --quiet "$time_service"
stage=host-time-synchronization
# 最大30回・2秒間隔。残補正100ms以下での同期を確認し、長時間の無制限待機を避けます。
chronyc waitsync 30 0.1 0 2 >/dev/null
verify_host_time "$(chronyc -c sources)" "$(chronyc tracking)"
time_configuration commit "$time_transaction_token" "${time_paths[@]}"
time_transaction_pending=false

if [[ $core_host_time_only == true ]]; then
    printf '{"status":"configured","timeService":"%s","timeSource":"azure-host-phc","timeSynchronized":true,"timeOnly":true}\n' "$time_service"
    exit 0
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
printf '{"status":"configured","timeService":"%s","timeSource":"azure-host-phc","timeSynchronized":true,"authentication":"public-key-only","syslog":"auth/authpriv and system warnings","commandArgumentCollection":false}\n' "$time_service"
