"""APIごとの正式定義と、試験に必要な基盤設定を組み合わせる。"""
from copy import deepcopy
import json

from configuration_fixtures import CORE, fixture
from standard_apis import load_api_definitions


def catalog_overlay():
    """配備と同じ読み込み口で正式APIを集め、追加設定形式で返す。"""
    return {"apiDefinitions": load_api_definitions(CORE / "apps/gateway/apim/apis")}


def configured_base():
    """APIファイルへ複製せず、公開params/configからCM・モデル設定を用意する。"""
    value = fixture()
    public = json.loads((CORE / "params/test.example.json").read_text(encoding="utf-8"))
    value["credentialProviders"] = deepcopy(public["credentialProviders"])
    value["credentialProviders"][0]["clientId"] = "44444444-4444-4444-8444-444444444444"
    value["gateway"]["authentication"]["credentialLogin"] = deepcopy(
        public["gateway"]["authentication"]["credentialLogin"]
    )
    value["services"]["modelDeployments"] = json.loads((CORE / "config/test.json").read_text(encoding="utf-8"))["services"]["modelDeployments"]
    return value
