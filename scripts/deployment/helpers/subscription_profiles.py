"""購読プロファイル接続を配備時だけ設定し、利用者の保存操作からXML更新を分離する。"""
import re
import time
from urllib.parse import quote
from .credential_image import checked

APIM_API = '2024-05-01'
PREFIX = 'core-subscription-profile-'
SECRET_NAME = PREFIX + 'key'
DEFAULTS = {'enabled': 'false', 'url': 'https://profile.invalid', 'secret': 'profile-not-configured'}


def initialize_profile_settings(apim_id, transport):
    """未作成の接続設定だけを初期化する。Portalが設定済みの値は上書きしない。"""
    for suffix, value in DEFAULTS.items():
        name = PREFIX + suffix
        path = apim_id + '/namedValues/' + name + '?api-version=' + APIM_API
        current = transport.request('GET', path)
        if current.get('statusCode') == 200:
            continue
        if current.get('statusCode') != 404:
            raise ValueError('Subscription profile settings could not be read')
        response = transport.request('PUT', path, {'properties': {
            'displayName': name, 'secret': suffix == 'secret', 'value': value,
        }}, headers={'If-None-Match': '*'})
        if response.get('statusCode') not in (200, 201, 202, 412):
            raise ValueError('Subscription profile connection could not be initialized')


def verify_inline_profiles(apim_id, transport):
    """全てのCore APIに照会処理があることを読み合わせ、部分配備では有効化しない。"""
    url = apim_id + '/apis?api-version=' + APIM_API
    count = 0
    for _ in range(100):
        inventory = checked(transport, 'GET', url, label='Read APIs before profile activation')
        for api in inventory.get('value', []):
            name = api.get('name', '')
            if not re.fullmatch(r'[A-Za-z0-9._;=-]{1,150}', name):
                raise ValueError('Invalid API identifier during subscription profile activation')
            policy = checked(transport, 'GET', apim_id + '/apis/' + quote(name, safe=';=')
                             + '/policies/policy?api-version=' + APIM_API + '&format=rawxml',
                             allowed=(200, 404), label='Read API profile policy')
            xml = policy.get('properties', {}).get('value', '')
            if 'fragment-id="core-rate-limit"' not in xml:
                continue
            count += 1
            if ('core-subscription-profile-v1' not in xml
                    or xml.index('core-subscription-profile-v1') > xml.index('fragment-id="core-rate-limit"')):
                raise ValueError('Gateway API publication is incomplete; publish all Core APIs before enabling profiles')
        next_link = inventory.get('nextLink')
        if not next_link:
            break
        prefix = 'https://management.azure.com' + apim_id + '/apis?'
        if not isinstance(next_link, str) or not next_link.lower().startswith(prefix.lower()):
            raise ValueError('Unexpected API inventory continuation')
        url = next_link
    else:
        raise ValueError('API inventory exceeded the activation check limit')
    if count == 0:
        raise ValueError('No Core API profile policies found; deploy Gateway definitions first')


def activate_profile_settings(apim_id, transport, *, timeout=180, sleep=time.sleep, monotonic=time.monotonic):
    """固定fragmentとKV参照が準備された後にだけ、プロファイル参照を有効化する。"""
    fragment = checked(transport, 'GET', apim_id + '/policyFragments/core-rate-limit?api-version=' + APIM_API,
                       label='Read subscription profile policy')
    if 'core-subscription-profile-v1' not in fragment.get('properties', {}).get('value', ''):
        raise ValueError('Deploy the current Gateway definitions before enabling Portal subscription profiles')
    verify_inline_profiles(apim_id, transport)
    deadline = monotonic() + timeout
    while True:
        secret = checked(transport, 'GET', apim_id + '/namedValues/' + PREFIX + 'secret?api-version=' + APIM_API,
                         label='Read subscription profile secret reference')
        properties = secret.get('properties', {})
        if not properties.get('secret') or not properties.get('keyVault', {}).get('secretIdentifier', '').endswith('/' + SECRET_NAME):
            raise ValueError('Subscription profile Key Vault reference is invalid')
        if properties.get('keyVault', {}).get('lastStatus', {}).get('code', '').lower() == 'success':
            break
        if monotonic() >= deadline:
            raise ValueError('Subscription profile Key Vault reference is not ready; retry after propagation')
        sleep(5)
    path = apim_id + '/namedValues/' + PREFIX + 'enabled?api-version=' + APIM_API
    current = checked(transport, 'GET', path, allowed=(200, 404), label='Read subscription profile activation')
    if current.get('properties', {}).get('value') != 'true':
        checked(transport, 'PUT', path, {'properties': {
            'displayName': PREFIX + 'enabled', 'secret': False, 'value': 'true',
        }}, label='Enable subscription profile connection')
    # NamedValueのPUTは202を返すことがあり、直後のGETには更新前の値が残ります。
    # 更新要求は再送せず、既存の待機期限内で読み戻しだけを続けます。
    while True:
        confirmed = checked(transport, 'GET', path, label='Confirm subscription profile activation')
        if confirmed.get('properties', {}).get('value') == 'true':
            break
        if monotonic() >= deadline:
            raise ValueError('Subscription profile activation was not confirmed')
        sleep(5)
    return {'enabled': True, 'cacheTtlSeconds': 30, 'runtimeVerified': False}
