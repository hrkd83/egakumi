"""GatewayのOBOで使う公開証明書と、署名鍵の固定参照を引き渡す。"""
import base64
import hashlib
import json
import re

from .configuration import entra_app_separation_enabled
from .portal_auth_certificate import application_vault_origin, certificate_record, save_public_certificate
from .private_permissions import check_private_permissions

CERTIFICATE_NAME = 'gateway-obo'


def owner(config):
    """認証用アプリと、Key Vaultを持つAzure環境を結び付ける。"""
    return {field: config[field] for field in ('subscriptionId', 'azureTenantId', 'entraTenantId', 'resourceStem')}


def store_initial(config, workspace, result, save_json):
    """同じ証明書のみを再取得し、Entra登録に渡す公開部分を保存する。"""
    result = certificate_record(result, config, certificate_name=CERTIFICATE_NAME)
    path = workspace.checked(workspace.state / 'gateway-obo-certificate.json')
    binding = {**owner(config), 'apiClientId': config['gateway']['authentication']['entra']['audience']}
    if path.exists():
        check_private_permissions(path)
        previous = json.loads(path.read_text(encoding='utf-8'))
        if previous != {**binding, 'certificate': result}:
            raise ValueError('The saved Gateway OBO certificate differs; inspect the existing registration before changing its key')
    else:
        save_json(path, {**binding, 'certificate': result})
    save_public_certificate(workspace, result, 'gateway-obo-public.cer')


def deployment_settings(config, workspace):
    """この環境・API用アプリに属するKey Vault鍵版を配備へ渡す。"""
    entra = config['gateway']['authentication'].get('entra', {})
    override = entra.get('obo')
    if not override and not entra_app_separation_enabled(config):
        return {}
    if override:
        key_url = override['keyId']
        fingerprint = bytes.fromhex(override['certificateThumbprint'])
    else:
        state = workspace.checked(workspace.state / 'gateway-obo-certificate.json')
        if not state.is_file():
            raise ValueError('Complete Core and register the Gateway OBO public certificate before deploying Gateway')
        check_private_permissions(state)
        record = json.loads(state.read_text(encoding='utf-8'))
        binding = {**owner(config), 'apiClientId': entra['audience']}
        if not isinstance(record, dict) or any(record.get(key) != value for key, value in binding.items()):
            raise ValueError('The saved Gateway OBO certificate belongs to another environment or API registration')
        cert = certificate_record(record.get('certificate'), config, certificate_name=CERTIFICATE_NAME)
        key_url = cert['keyId']
        fingerprint = hashlib.sha256(base64.b64decode(cert['publicCertificate'], validate=True)).digest()
    expected = application_vault_origin(config["resourceStem"]) + f"/keys/{CERTIFICATE_NAME}/"
    if not re.fullmatch(re.escape(expected) + r'[a-f0-9]{32}', key_url) or len(fingerprint) != 32:
        raise ValueError('Gateway OBO requires the fixed signing key of the selected Core vault')
    return {'tenantId': config['entraTenantId'], 'clientId': entra['audience'], 'keyUrl': key_url,
            'certificateThumbprint': base64.urlsafe_b64encode(fingerprint).decode('ascii').rstrip('=')}


def runtime_environment(settings):
    """配備と準備済みBrokerの読戻しに使う同じ環境変数を組み立てる。"""
    if not settings:
        return {}
    return {'BROKER_OBO_TENANT_ID': settings['tenantId'], 'BROKER_OBO_CLIENT_ID': settings['clientId'],
            'BROKER_OBO_KEY_URL': settings['keyUrl'], 'BROKER_OBO_CERTIFICATE_THUMBPRINT': settings['certificateThumbprint']}
