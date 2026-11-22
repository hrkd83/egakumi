"""Portal管理グループを対話準備し、標準の検証用購読を内部生成する。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from uuid import UUID

from .configuration import load_configuration
from .released_apis import RELEASED_PRODUCT_ID, RELEASED_PRODUCT_NAME


def available_products(config):
    """検証用Productの選択候補を返す。通常の発行製品とは分けて管理する。"""
    definitions = config.get('apiDefinitions', [])
    if not definitions:
        from standard_apis import DEFINITION_FILES, load_api_definitions
        definitions = [api for path in DEFINITION_FILES for api in load_api_definitions(path)]
    return sorted({api['productId'] for api in definitions
                   if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', str(api.get('productId', '')))})


def approved_product_catalog(config):
    """通常キーの発行先を公開製品に固定する。検証用の既存承認は台帳に維持する。"""
    return [{'productId': RELEASED_PRODUCT_ID, 'displayName': RELEASED_PRODUCT_NAME, 'maxSubscriptions': 5}]


def verification_subscriptions(settings):
    """検証用購読の宣言を返す。未設定の初回だけ、基盤管理グループの承認製品から作る。"""
    if settings.get('verificationSubscriptions'):
        return settings['verificationSubscriptions']
    platform = next((group for group in settings.get('groups', [])
                     if group['groupId'] == settings.get('platformGroupId')), None)
    return [{'subscriptionId': 'platform-' + product['productId'], 'productId': product['productId'],
             'displayName': ('Platform verification - ' + product['productId'])[:80]}
            for product in (platform or {}).get('products', [])]


def _answer(prompt, reader):
    try:
        value = reader(prompt).strip()
    except (KeyboardInterrupt, EOFError):
        raise ValueError('APIキー所有グループの入力を中止しました。設定は保存していません。') from None
    if value.lower() in ('q', 'quit'):
        raise ValueError('APIキー所有グループの入力を中止しました。設定は保存していません。')
    return value


def _snapshot(path):
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ValueError('APIキーの所有グループはリンク経由の設定へ保存できません。')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o222:
        raise ValueError('APIキーの所有グループを保存できる通常のparamsファイルを指定してください。')
    data = path.read_bytes()
    signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, stat.S_IMODE(info.st_mode))
    return data, signature, info


def prepare_api_key_groups(source, config, *, reader=input, interactive=None):
    """管理グループと名前の条件だけを対話入力し、派生設定は保存しない。"""
    settings = config.get('portalApiKeys', {})
    if settings.get('platformGroupId') and settings.get('groupNamePattern', '').strip():
        return config
    if not (sys.stdin.isatty() if interactive is None else interactive):
        raise ValueError('Portalの管理グループと名前の対象条件が未指定です。portalApiKeys.platformGroupId・groupNamePatternをparamsへ設定するか、対話端末で再実行してください。')
    path = Path(source).expanduser().absolute()
    if path.name.endswith('.example.json'):
        raise ValueError('exampleを直接変更せず、企業別のlocal paramsを指定してください。')
    original, signature, info = _snapshot(path)
    if load_configuration(path) != config:
        raise ValueError('読込後にparamsが変更されました。APIキー設定を保存せず停止しました。')
    raw = json.loads(original)
    if 'configFile' not in raw:
        raise ValueError('初期Portal設定にはconfigFileを持つlocal paramsを指定してください。')
    if not available_products(config):
        raise ValueError('標準APIの正本に検証用Productがありません。')
    pattern = settings.get('groupNamePattern', '')
    if not pattern.strip():
        pattern = _answer('対象セキュリティグループ名の正規表現（例: ^ORG_、既定値なし）: ', reader)
        if not pattern or len(pattern) > 512 or re.search(r'[\x00-\x1f\x7f]', pattern):
            raise ValueError('グループ名の正規表現は1〜512文字で明示してください。')
    group_id = settings.get('platformGroupId')
    if not group_id:
        print('管理ダッシュボードと標準検証用購読を所有するEntraグループを設定します。', flush=True)
        group_id = _answer('EntraグループのObject ID: ', reader)
        try:
            group_id = str(UUID(group_id))
            if UUID(group_id).int == 0:
                raise ValueError()
        except ValueError:
            raise ValueError('有効なEntraグループのObject IDを指定してください。') from None
    raw['portalApiKeys'] = {**raw.get('portalApiKeys', {}),
                            'groupNamePattern': pattern, 'platformGroupId': group_id}
    return _save_configuration(path, raw, original, signature, info)


def _save_configuration(path, raw, original, signature, info):
    """対話の前後で変更がない同じ正本へだけ保存する。"""
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(prefix='.portal-api-keys-', suffix='.json', dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            if os.name != 'nt':
                os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode) & 0o666)
            stream.write(json.dumps(raw, ensure_ascii=False, indent=2) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        updated = load_configuration(temporary)
        current, current_signature, _ = _snapshot(path)
        if current != original or current_signature != signature:
            raise ValueError('入力中にparamsが変更されました。APIキー設定を保存せず停止しました。')
        os.replace(temporary, path)
        print('管理グループとグループ名の条件をparamsのportalApiKeysへ保存しました。', flush=True)
        return updated
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
