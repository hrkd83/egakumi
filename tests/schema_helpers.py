"""分割したJSONスキーマをローカルだけで解決し、検証中の外部取得を禁止する。"""
import json
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _local_schemas():
    """リポジトリのschemaだけを読み、取得を必要としないaliasを登録する。"""
    store = {}
    for path in (ROOT / 'schemas').glob('*.schema.json'):
        document = json.loads(path.read_text(encoding='utf-8'))
        aliases = [path.name, path.resolve().as_uri()]
        if document.get('$id'):
            aliases.append(document['$id'])
        for alias in aliases:
            if alias in store and store[alias] != document:
                raise ValueError('Conflicting local schema aliases')
            store[alias] = document
    return store


def _deny_retrieval(_uri):
    """URI・入力値を例外へ含めず、未知schemaのファイル・ネットワーク取得を拒否する。"""
    raise ValueError('Schema reference is not available in the local store')


class _LocalValidator:
    """参照失敗にURIが自動付加されるjsonschemaの例外を、公開しない。"""
    def __init__(self, validator):
        self._validator = validator

    @property
    def schema(self):
        return self._validator.schema

    def iter_errors(self, instance, *args, **kwargs):
        from jsonschema.exceptions import RefResolutionError
        try:
            yield from self._validator.iter_errors(instance, *args, **kwargs)
        except RefResolutionError:
            raise ValueError('Schema reference is not available in the local store') from None

    def validate(self, instance, *args, **kwargs):
        for error in self.iter_errors(instance, *args, **kwargs):
            raise error

    def is_valid(self, instance, *args, **kwargs):
        return next(self.iter_errors(instance, *args, **kwargs), None) is None


def local_schema_validator(schema):
    """新旧jsonschemaで共通定義をオフライン解決し、未知取得を必ず拒否する。"""
    from jsonschema import Draft202012Validator

    store = _local_schemas()
    # referencingが存在しても、旧jsonschemaはregistry引数を受け取れません。
    constructor = inspect.signature(Draft202012Validator).parameters
    if 'registry' in constructor and '_resolver' in constructor:
        try:
            from referencing import Registry, Resource
            from referencing.jsonschema import DRAFT202012
        except ImportError:
            pass
        else:
            resources = [(alias, Resource.from_contents(document)) for alias, document in store.items()]
            registry = Registry(retrieve=_deny_retrieval).with_resources(resources)
            # jsonschemaの自動初期化は組込みmeta-schemaのRegistryも合成します。
            # 許可集合をrepo schemaと入力schema自身だけに保つためresolverも渡します。
            root = Resource.from_contents(schema, default_specification=DRAFT202012)
            resolver = registry.resolver_with_root(root)
            return _LocalValidator(Draft202012Validator(schema, registry=registry, _resolver=resolver))

    from jsonschema import RefResolver

    class LocalResolver(RefResolver):
        def resolve_remote(self, uri):
            # RefResolver標準のrequests/urlopen/file読取へ到達させません。
            if uri in self.store:
                return self.store[uri]
            return _deny_retrieval(uri)

    resolver = LocalResolver.from_schema(schema, store=store)
    # RefResolverが自動登録する外部メタschemaは参照先の許可集合に加えません。
    resolver.store.clear()
    resolver.store.update(store)
    resolver.store[resolver.base_uri] = schema
    return _LocalValidator(Draft202012Validator(schema, resolver=resolver))
