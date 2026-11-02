"""分割したJSONスキーマをローカルだけで解決し、検証中の外部取得を禁止する。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def local_schema_validator(schema):
    """paramsの共通定義を再利用し、相対参照を検証端末のURL設定へ依存させない。"""
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    resources = []
    for path in (ROOT / 'schemas').glob('*.schema.json'):
        resource = Resource.from_contents(json.loads(path.read_text(encoding='utf-8')))
        resources.extend(((path.name, resource), (path.resolve().as_uri(), resource)))
    return Draft202012Validator(schema, registry=Registry().with_resources(resources))
