"""購読プロファイル接続を配備時だけ設定し、利用者の保存操作からXML更新を分離する。"""
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import parse_qsl, quote, urlsplit
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


def _managed_apis(definitions):
    """設定にあるAPI本体と、保護RESTを呼ぶ生成MCPだけを対象にする。"""
    if not isinstance(definitions, list) or not definitions:
        raise ValueError('No Core API definitions found; deploy Gateway definitions first')
    targets = {}

    def add(name, api_type, inline):
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9._;=-]{1,150}', name)
                or api_type not in ('http', 'websocket', 'grpc', 'mcp') or name.casefold() in targets):
            raise ValueError('Invalid managed API definition during subscription profile activation')
        targets[name.casefold()] = (name, api_type, inline)

    for api in definitions:
        if not isinstance(api, dict):
            raise ValueError('Invalid managed API definition during subscription profile activation')
        add(api.get('name'), api.get('apiType', 'http'), True)
        if 'mcp' in api:
            if not isinstance(api['mcp'], dict):
                raise ValueError('Invalid generated MCP definition during subscription profile activation')
            # 生成MCPの表口は、同時に検証する保護RESTの制限を利用する。
            add(api['mcp'].get('name'), 'mcp', False)
    return targets


def _policy_xml(value, tag):
    """読み戻したXMLを構造として検証し、本文をエラーへ転載しない。"""
    try:
        if not isinstance(value, str) or '<!DOCTYPE' in value or '<!ENTITY' in value:
            raise ValueError
        root = ET.fromstring(value, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
        if root.tag != tag:
            raise ValueError
        return root
    except (ET.ParseError, ValueError):
        raise ValueError('Gateway profile policy XML is incomplete; publish all Core APIs before enabling profiles') from None


def _profile_policy_ready(value):
    """inboundの照会処理がrate-limitより前に揃っていることを確認する。"""
    root = _policy_xml(value, 'policies')
    inbound = root.findall('inbound')
    if len(inbound) != 1:
        raise ValueError('Gateway API publication is incomplete; publish all Core APIs before enabling profiles')
    children = list(inbound[0])
    markers = [index for index, node in enumerate(children)
               if node.tag is ET.Comment and (node.text or '').strip().startswith('core-subscription-profile-v1:')]
    limits = [index for index, node in enumerate(children)
              if node.tag == 'include-fragment' and node.get('fragment-id') == 'core-rate-limit']
    if len(markers) != 1 or len(limits) != 1 or markers[0] >= limits[0]:
        raise ValueError('Gateway API publication is incomplete; publish all Core APIs before enabling profiles')
    nodes = children[markers[0] + 1:limits[0]]
    initialized = any(node.tag == 'set-variable' and node.get('name') == 'gatewaySubscriptionProfile' for node in nodes)
    configured = False
    for node in nodes:
        for when in node.iter('when'):
            if '{{' + PREFIX + 'enabled}}' not in when.get('condition', ''):
                continue
            for request in when.iter('send-request'):
                configured |= (request.get('response-variable-name') == 'gatewayProfileResponse'
                    and request.findtext('set-url') == '{{' + PREFIX + 'url}}'
                    and request.findtext('set-method') == 'GET'
                    and request.findtext("set-header[@name='X-Core-Profile-Key']/value") == '{{' + PREFIX + 'secret}}'
                    and when.find(".//set-variable[@name='gatewaySubscriptionProfile']") is not None)
    if not initialized or not configured:
        raise ValueError('Gateway API profile lookup is incomplete; publish all Core APIs before enabling profiles')


def verify_inline_profiles(apim_id, transport, api_definitions):
    """設定由来の全APIを、HTTP等のAPI scopeとWSのonHandshake scopeで検証する。"""
    expected = _managed_apis(api_definitions)
    url = apim_id + '/apis?api-version=' + APIM_API
    seen, pages = set(), set()
    for _ in range(100):
        if url in pages:
            raise ValueError('API inventory continuation repeated during profile activation')
        pages.add(url)
        inventory = checked(transport, 'GET', url, label='Read APIs before profile activation')
        if not isinstance(inventory, dict) or not isinstance(inventory.get('value'), list):
            raise ValueError('API inventory is incomplete during subscription profile activation')
        for api in inventory['value']:
            if not isinstance(api, dict) or not isinstance(api.get('name'), str):
                raise ValueError('API inventory is incomplete during subscription profile activation')
            key = api['name'].casefold()
            if key not in expected:
                continue
            name, api_type, inline = expected[key]
            # 現行API契約のtypeは任意。HTTP設定の一覧で属性が省略された場合だけ許容する。
            # 明示的なnull・別の型や、WS/gRPC/MCP設定での省略は一致としない。
            if (key in seen or not isinstance(api.get('id'), str)
                    or api['id'].casefold() != (apim_id + '/apis/' + name).casefold()
                    or not isinstance(api.get('properties'), dict) or api['properties'].get('type', 'http') != api_type):
                raise ValueError('Managed API metadata differs from the current Gateway definitions')
            seen.add(key)
            if not inline:
                continue
            scope = '/operations/onHandshake' if api_type == 'websocket' else ''
            policy = checked(transport, 'GET', apim_id + '/apis/' + quote(name, safe=';=') + scope
                             + '/policies/policy?api-version=' + APIM_API + '&format=rawxml',
                             allowed=(200, 404), label='Read API profile policy')
            _profile_policy_ready(policy.get('properties', {}).get('value'))
        next_link = inventory.get('nextLink')
        if not next_link:
            break
        try:
            parsed = urlsplit(next_link) if isinstance(next_link, str) else None
            if (parsed is None or parsed.scheme != 'https' or parsed.hostname != 'management.azure.com'
                    or parsed.port not in (None, 443) or parsed.username is not None or parsed.password is not None
                    or parsed.fragment or parsed.path.casefold() != (apim_id + '/apis').casefold()
                    or [value for key, value in parse_qsl(parsed.query) if key == 'api-version'] != [APIM_API]):
                raise ValueError
        except ValueError:
            raise ValueError('Unexpected API inventory continuation') from None
        url = next_link
    else:
        raise ValueError('API inventory exceeded the activation check limit')
    if seen != set(expected):
        raise ValueError('Core API inventory is incomplete; publish all Core APIs before enabling profiles')


def activate_profile_settings(apim_id, transport, api_definitions, *, timeout=180, sleep=time.sleep, monotonic=time.monotonic):
    """固定fragmentとKV参照が準備された後にだけ、プロファイル参照を有効化する。"""
    fragment = checked(transport, 'GET', apim_id + '/policyFragments/core-rate-limit?api-version=' + APIM_API,
                       label='Read subscription profile policy')
    fragment_xml = _policy_xml(fragment.get('properties', {}).get('value'), 'fragment')
    if not any(node.tag is ET.Comment and (node.text or '').strip().startswith('core-subscription-profile-v1:')
               for node in fragment_xml.iter()):
        raise ValueError('Deploy the current Gateway definitions before enabling Portal subscription profiles')
    verify_inline_profiles(apim_id, transport, api_definitions)
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
