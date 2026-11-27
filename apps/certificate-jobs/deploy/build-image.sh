#!/usr/bin/env bash
# 既存管理VMで公開ソースだけをビルドし、非公開ACRへ登録する。
# Entraアプリのsecretや人のAzureログイン情報は受け取らない。
set -euo pipefail
set +x
umask 077
stage=prepare
work=''
image=''
finish() {
    result=$?
    trap - EXIT
    if [ -n "$image" ]; then docker image rm "$image" >/dev/null 2>&1 || true; fi
    if [ -n "$work" ]; then rm -rf -- "$work"; fi
    if [ "$result" -ne 0 ]; then
        printf 'CORE_CERTIFICATE_IMAGE_RESULT={"status":"failed","stage":"%s"}\n' "$stage"
    fi
    exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
[ "$EUID" = 0 ]
. /etc/os-release
[ "$ID" = ubuntu ] && [ "$VERSION_ID" = 24.04 ]
exec 9>/run/lock/egakumi-certificate-image.lock
flock -n 9
work=$(mktemp -d /var/tmp/egakumi-certificate-image.XXXXXXXX)
export DOCKER_CONFIG="$work/docker"
mkdir -p "$DOCKER_CONFIG" "$work/source"
export CORE_BUILD_REGISTRY='__REGISTRY__'
export CORE_BUILD_TENANT='__TENANT__'
image="$CORE_BUILD_REGISTRY/certificate-jobs:__BUILD_TAG__"
stage=source
cat > "$work/source.b64" <<'CORE_SOURCE'
__SOURCE_BASE64__
CORE_SOURCE
python3 - "$work" <<'PY'
import base64, hashlib, io, pathlib, tarfile, sys
folder = pathlib.Path(sys.argv[1])
data = base64.b64decode((folder / 'source.b64').read_bytes().strip(), validate=True)
if hashlib.sha256(data).hexdigest() != '__SOURCE_SHA256__':
    raise SystemExit(1)
with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
    required = {'Dockerfile', 'job.py', 'certificate-job.ps1', 'certificate.ps1', 'portal-auth-certificate.ps1', 'staging-ca.crt'}
    members = archive.getmembers()
    if len(members) != len(required) or {member.name for member in members} != required:
        raise SystemExit(1)
    for item in members:
        if not item.isfile() or not 0 < item.size <= 512 * 1024:
            raise SystemExit(1)
        target = folder / 'source' / item.name
        target.write_bytes(archive.extractfile(item).read())
PY
# Ubuntuの署名付きパッケージを利用。既存Dockerの設定・資格情報は変更しない。
stage=docker-install
if ! command -v docker >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 update >"$work/install.log" 2>&1
    DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends docker.io >>"$work/install.log" 2>&1
fi
systemctl start docker >"$work/docker-start.log" 2>&1
docker info >"$work/docker-info.log" 2>&1
stage=image-build
# hostネットワークで既存のVM→Firewall経路を使う。Dockerのiptables設定は変更しない。
DOCKER_BUILDKIT=0 docker build --pull --platform linux/amd64 --network host --tag "$image" "$work/source" >"$work/build.log" 2>&1
stage=image-startup-check
# Azure identity and certificate inputs are excluded from this offline test.
docker run --rm --network none --entrypoint /usr/bin/pwsh "$image" -NoLogo -NoProfile -NonInteractive -Command '
    $ErrorActionPreference="Stop"
    if ((id -u) -ne "10001" -or $PSVersionTable.PSVersion.ToString() -ne "7.6.7") { exit 1 }
    Import-Module Posh-ACME -RequiredVersion 4.34.0
    foreach ($path in (Get-ChildItem /app/*.ps1)) {
        $tokens=$null; $errors=$null
        $null=[System.Management.Automation.Language.Parser]::ParseFile($path.FullName,[ref]$tokens,[ref]$errors)
        if ($errors.Count -ne 0) { exit 1 }
    }
' >"$work/startup-check.log" 2>&1
stage=registry-login
# VM自身のMIでトークンを取得し、標準ACR交換APIへ渡す。
# プロキシ・HTTPリダイレクト・例外本文の出力は使わず、tokenはstdinだけでDockerへ渡す。
cat > "$work/registry-login.py" <<'PY'
import json, os, sys
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
try:
    opener = build_opener(ProxyHandler({}), NoRedirect())
    url = '__IMDS_ORIGIN__/metadata/identity/oauth2/token?' + urlencode({
        'api-version': '2018-02-01', 'resource': 'https://management.azure.com/'})
    with opener.open(Request(url, headers={'Metadata': 'true'}), timeout=20) as response:
        token = json.load(response)['access_token']
    registry = os.environ['CORE_BUILD_REGISTRY']
    form = urlencode({'grant_type': 'access_token', 'service': registry,
                      'tenant': os.environ['CORE_BUILD_TENANT'], 'access_token': token}).encode()
    request = Request('https://' + registry + '/oauth2/exchange', data=form,
                      headers={'Content-Type': 'application/x-www-form-urlencoded'})
    with opener.open(request, timeout=30) as response:
        refresh = json.load(response)['refresh_token']
    sys.stdout.write(refresh)
except Exception:
    raise SystemExit(1) from None
PY
# 新規のロール割当が反映されるまで最大5分だけ待つ。同じ完成イメージのpushだけを再試行。
pushed=false
for attempt in $(seq 1 30); do
    if python3 "$work/registry-login.py" | docker login "$CORE_BUILD_REGISTRY" \
        --username 00000000-0000-0000-0000-000000000000 --password-stdin >"$work/login.log" 2>&1; then
        stage=image-push
        if docker push "$image" >"$work/push.log" 2>&1; then pushed=true; break; fi
    fi
    sleep 10
done
[ "$pushed" = true ]
stage=image-digest
docker image inspect "$image" --format '{{json .RepoDigests}}' > "$work/digests.json"
python3 - "$work/digests.json" <<'PY'
import json, os, re, sys
prefix = os.environ['CORE_BUILD_REGISTRY'] + '/certificate-jobs@'
with open(sys.argv[1]) as source:
    matches = [value[len(prefix):] for value in json.load(source) if value.startswith(prefix)]
if len(matches) != 1 or not re.fullmatch(r'sha256:[0-9a-f]{64}', matches[0]):
    raise SystemExit(1)
print('CORE_CERTIFICATE_IMAGE_RESULT=' + json.dumps({
    'status': 'built', 'imageDigest': matches[0], 'sourceSha256': '__SOURCE_SHA256__'}))
PY
