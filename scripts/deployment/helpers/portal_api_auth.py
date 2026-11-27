"""PortalのAPI用OAuthストレージ鍵を、選択したVaultの固定版へ結び付ける。"""
import json
import re
import secrets

from .portal_auth_certificate import application_vault_origin
from .private_permissions import check_private_permissions

SECRET = 'core-portal-api-auth-key'
CONTENT_TYPE = 'application/x-egakumi-portal-api-auth-key'
TAGS = {'egakumi-purpose': 'portal-api-auth', 'egakumi-format': 'base64url-32-v1'}
GENERATION = 'egakumi-portal-api-auth-generation'
API_VERSION = '2024-11-01'


def deployment_settings(config, portal, identity_scope, *, management=False):
    """API試用のscopeと用途専用の読取IDを照合する。"""
    settings = config['portal'].get('apiAuthentication', {})
    trial_enabled = settings.get('enabled') is True
    if not (trial_enabled or management):
        return {}
    authentication = config['portal']['authentication']
    gateway = config['gateway']['authentication']['entra']
    identity = portal.get('apiAuthIdentity')
    expected = identity_scope + 'id-' + config['resourceStem'] + '-portal-api-auth'
    if (authentication['tenantId'] != config['entraTenantId']
            or gateway['requiredScope'] != 'Gateway.Invoke'
            or not isinstance(identity, dict) or str(identity.get('id', '')).casefold() != expected.casefold()):
        raise ValueError('Portal API authentication requires the dedicated identity and the Gateway API registration')
    return {'identity': identity, 'scope': 'api://' + gateway['audience'] + '/Gateway.Invoke' if trial_enabled else '',
            'previousKeyVersions': settings.get('previousKeyVersions', [])}


def _key_metadata(config, resource, body):
    """ARMから取得する版付き参照と公開メタデータだけを照合する。"""
    if not isinstance(body, dict) or str(body.get('id', '')).casefold() != resource.casefold():
        raise ValueError('Portal API storage key is outside the selected vault')
    tags = body.get('tags') or {}
    props = body.get('properties') or {}
    uri = props.get('secretUriWithVersion', '')
    prefix = application_vault_origin(config["resourceStem"]) + f"/secrets/{SECRET}/"
    generation = tags.get(GENERATION, '')
    if (any(tags.get(key) != value for key, value in TAGS.items())
            or not isinstance(generation, str) or not re.fullmatch(r'[a-f0-9]{32}', generation)
            or props.get('contentType') != CONTENT_TYPE or props.get('attributes', {}).get('enabled') is not True
            or not isinstance(uri, str) or not re.fullmatch(re.escape(prefix) + r'[a-f0-9]{32}', uri)):
        raise ValueError('Portal API storage key has unexpected metadata; existing credentials were preserved')
    return uri, generation


def ensure_initial_key(config, workspace, transport, save_json):
    """初回作成を1回だけ要求し、再試行では同じ世代・版を照合する。"""
    scope = (f"/subscriptions/{config['subscriptionId']}/resourceGroups/{config['resourceGroupNames']['svc']}"
             f"/providers/Microsoft.KeyVault/vaults/kv-{config['resourceStem']}/secrets/{SECRET}")
    url = scope + '?api-version=' + API_VERSION
    path = workspace.checked(workspace.state / 'portal-api-auth-key.json')
    binding = {key: config[key] for key in ('subscriptionId', 'azureTenantId', 'resourceStem')}
    settings = config.get('portal', {}).get('apiAuthentication', {})
    pinned_version = settings.get('keyVersion')
    record = {}
    if path.exists():
        check_private_permissions(path)
        record = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(record, dict) or any(record.get(key) != value for key, value in binding.items())
                or record.get('status') not in ('requested', 'ready')
                or not re.fullmatch(r'[a-f0-9]{32}', str(record.get('generation', '')))):
            raise ValueError('Portal API storage key state belongs to another environment or is invalid')
    response = transport.request('GET', url)
    if response['statusCode'] == 404:
        if pinned_version:
            raise ValueError('The configured Portal API storage key version is missing')
        if record:
            raise ValueError('Portal API storage key creation is pending or the recorded version is missing; inspect the existing request')
        generation = secrets.token_hex(16)
        record = {**binding, 'generation': generation, 'status': 'requested'}
        save_json(path, record)
        body = {'tags': {**TAGS, GENERATION: generation}, 'properties': {
            'value': secrets.token_urlsafe(32), 'contentType': CONTENT_TYPE, 'attributes': {'enabled': True}}}
        created = transport.request('PUT', url, body=body)
        if created['statusCode'] not in (200, 201, 202):
            if created['statusCode'] in (400, 401, 403, 404):
                path.unlink(missing_ok=True)
            raise ValueError('Portal API storage key creation did not complete; inspect the Azure request')
        response = transport.request('GET', url)
    if response['statusCode'] != 200:
        raise ValueError('Portal API storage key is not yet readable; retain its protected state')
    uri, generation = _key_metadata(config, scope, response.get('body'))
    version = uri.rsplit('/', 1)[-1]
    if (pinned_version and pinned_version != version) or version in settings.get('previousKeyVersions', []):
        raise ValueError('Configured Portal API storage key versions differ from the verified deployment')
    if record and (record['generation'] != generation or record.get('keyUrl', uri) != uri):
        raise ValueError('Portal API storage key changed outside the recorded deployment; existing credentials were preserved')
    if not record:
        raise ValueError('Portal API storage key already exists without matching protected state; inspect its ownership')
    if record['status'] != 'ready':
        save_json(path, {**binding, 'generation': generation, 'keyUrl': uri, 'status': 'ready'})
    return uri
