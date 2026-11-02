#!/usr/bin/env bash
# 既存管理VMでPortalの公開ソースだけをビルドし、非公開ACRへ登録する。
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
        printf 'CORE_PORTAL_IMAGE_RESULT={"status":"failed","stage":"%s"}\n' "$stage"
    fi
    exit "$result"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
[ "$EUID" = 0 ]
. /etc/os-release
[ "$ID" = ubuntu ] && [ "$VERSION_ID" = 24.04 ]
stage=build-lock
exec 9>/run/lock/egakumi-portal-image.lock
flock -n 9
work=$(mktemp -d /var/tmp/egakumi-portal-image.XXXXXXXX)
export DOCKER_CONFIG="$work/docker"
mkdir -p "$DOCKER_CONFIG" "$work/source"
export CORE_BUILD_REGISTRY='__REGISTRY__'
export CORE_BUILD_TENANT='__TENANT__'
image="$CORE_BUILD_REGISTRY/portal:__BUILD_TAG__"
stage=source
cat > "$work/source.b64" <<'CORE_SOURCE'
__SOURCE_BASE64__
CORE_SOURCE
python3 - "$work" <<'PY'
import base64, hashlib, io, pathlib, tarfile, sys
folder = pathlib.Path(sys.argv[1])
data = base64.b64decode((folder / 'source.b64').read_bytes().replace(b'\n', b''), validate=True)
if hashlib.sha256(data).hexdigest() != '__SOURCE_SHA256__':
    raise SystemExit(1)
top = {'Dockerfile', '.dockerignore', 'package.json', 'package-lock.json', 'next.config.ts',
       'tsconfig.json', 'proxy.ts'}
extra = {'scripts/migrate-auth.ts', 'scripts/auth-migrations.ts', 'scripts/auth-runtime-grants.ts',
         'scripts/api-key-group-config.ts', 'scripts/api-key-administration-config.ts',
         'scripts/reconcile-api-key-groups.ts',
         'scripts/api-key-subscription-import.ts', 'scripts/bootstrap-api-key-subscriptions.ts',
         'scripts/prepare-model-catalog.ts',
         'scripts/prepare-realtime-worklet.ts', 'scripts/serve-portal.ts',
         'deploy/build-image.sh', 'styles/shadcn.LICENSE.md'}
extensions = {
    'app': {'.ts', '.tsx', '.css', '.json', '.ico', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.avif'},
    'components': {'.ts', '.tsx', '.css'}, 'lib': {'.ts', '.tsx', '.json'}, 'styles': {'.css'},
    'public': {'.ico', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.avif', '.gif', '.woff', '.woff2', '.wav'}, 'sql': {'.sql'},
}
seen, total = set(), 0
# Python標準のXZ読取を使い、追加のxzコマンドやパッケージには依存しない。
with tarfile.open(fileobj=io.BytesIO(data), mode='r:xz') as archive:
    for item in archive.getmembers():
        path = pathlib.PurePosixPath(item.name)
        total += item.size
        allowed = item.name in top or item.name in extra or (
            len(path.parts) > 1 and path.suffix.lower() in extensions.get(path.parts[0], set()))
        if (not item.isfile() or path.is_absolute() or '..' in path.parts or '\\' in item.name or ':' in item.name
                or item.name in seen or item.size > 5_000_000 or total > 20_000_000 or len(seen) >= 1000
                or any(part in ('node_modules', '.next', '.git', 'tests', 'secrets') or part.startswith('.env') for part in path.parts)
                or not allowed):
            raise SystemExit(1)
        seen.add(item.name)
        target = folder / 'source' / item.name
        target.parent.mkdir(parents=True, exist_ok=True)
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
DOCKER_BUILDKIT=0 docker build --pull --network host --tag "$image" "$work/source" >"$work/build.log" 2>&1
stage=image-content-check
# 配備時と同じ非rootユーザーで本体とSQL資材の読み取りを確認する。
# Azureの秘密情報は渡さず、ネットワークも切った状態で検査する。
docker run --rm --network none --entrypoint node "$image" --conditions=react-server --experimental-strip-types --input-type=module -e \
    'import fs from "node:fs"; if (process.getuid() === 0) throw Error("root runtime"); for (const file of ["server.js", "sql/001_initial.sql", "sql/004_group_lifecycle.sql", "sql/005_api_key_administration.sql", "sql/006_group_name_pattern.sql", "sql/007_apim_subscription_identity.sql", "sql/008_key_usage_companies.sql", "sql/009_subscription_sync.sql", "lib/company-administration/repository.ts", "scripts/migrate-auth.ts", "scripts/api-key-group-config.ts", "scripts/api-key-administration-config.ts", "scripts/api-key-subscription-import.ts", "scripts/bootstrap-api-key-subscriptions.ts", "scripts/reconcile-api-key-groups.ts", "scripts/serve-portal.ts", "lib/realtime-relay.ts", "public/realtime-audio-worklet.js"]) fs.accessSync(`/app/${file}`); await import("./scripts/serve-portal.ts");' \
    >"$work/startup-check.log" 2>&1
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
    url = 'http://169.254.169.254/metadata/identity/oauth2/token?' + urlencode({
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
prefix = os.environ['CORE_BUILD_REGISTRY'] + '/portal@'
with open(sys.argv[1]) as source:
    matches = [value[len(prefix):] for value in json.load(source) if value.startswith(prefix)]
if len(matches) != 1 or not re.fullmatch(r'sha256:[0-9a-f]{64}', matches[0]):
    raise SystemExit(1)
print('CORE_PORTAL_IMAGE_RESULT=' + json.dumps({
    'status': 'built', 'imageDigest': matches[0], 'sourceSha256': '__SOURCE_SHA256__'}))
PY
