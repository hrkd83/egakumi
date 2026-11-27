# Entraユーザーのトークン制限

`core-token-limits`は認証済みEntraユーザーの毎分・累積トークン枠をAPIMの`llm-token-limit`へ渡します。操作ごとの実効接続先がFoundry OpenAIであるHTTP Chat／Responsesを対象にし、`backendOverride`があれば反映します。購読キーにはこのトークン枠を適用しません。

| 入力・出力 | 用途 |
| --- | --- |
| `gatewayDecision` | 認証方式と確認済みテナント・ユーザーID |
| `gatewayUserLimits` | 共通認証で確認した毎分・累積トークン枠 |
| `gatewayControlSettings.tokenLimitOperationIds` | トークン枠を適用する操作 |
| `gatewayTokenCounterKey` | 毎分・累積枠は同じ`core-tokens-v1:user:<tid>:<oid>`を使う |

対象外の操作、認可済みの取消・切断、両方の枠が明示`null`の場合は計数を免除します。操作一覧を読めない場合は既存の`on-error`で処理します。Chat利用量の診断ログは別途生成した`nativeLlmOperationIds`で管理します。
