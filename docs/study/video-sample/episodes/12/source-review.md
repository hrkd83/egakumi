# 第12回の技術根拠

2026-11-02（JST）。教材16〜18節とリポジトリ定義の照合。稼働Azureの取得・実測は行っていない。

| 論点 | 根拠 |
| --- | --- |
| 回数枠は検証済みAPIMサブスクリプションIDで共有。主副キー・全APIにまたがる | `apps/gateway/apim/policies/rate-limit.xml:2–42` |
| test / production共通60回/60秒はenabled:false | `config/test.json:388–391`、`config/production.json:400–403` |
| SQL requestRate優先、nullは未設定、取得障害503、正常値30秒キャッシュ | `apps/gateway/apim/policies/subscription-profile.xml:3–47` |
| 共通制約後inbound加算。SSE完了を待たず、固有ガードでは加算後拒否がある | `apps/gateway/apim/policies/gateway.xml:116,242–249` |
| 回数超過429はRetry-AfterとRATE_LIMIT_EXCEEDED | `apps/gateway/apim/policies/rate-limit-error.xml:5–21` |
| 1要求の出力契約はChat通常max_completion_tokens / compatible max_tokens / Responses max_output_tokens | `apps/gateway/apim/apis/openai-inference-v1.json:68,97,133` |
| 別名混在、不正型、数値上限超過400。dynamic/nullは推測上限を追加しない | `apps/gateway/apim/policies/model-limits.xml:56–91` |
| TPM429 / 期間quota403、入力推定、streaming・並行計数の限界 | `apps/gateway/apim/policies/token-limit.xml:2–27` |
| トークン枠の対象はFoundry OpenAI HTTP Chat / Responses。全APIには適用されない | `apps/gateway/apim/api-publication.bicep:75–81` |
| 入口キー、受信Authorization、profileを除去後にbackend認証を作る | `apps/gateway/apim/policies/gateway.xml:266–289` |
| 登録audienceへのManaged Identity、専用キー、Key Vault参照Named Value | `apps/gateway/apim/api-publication.bicep:49–55,198–201` |
| 本人接続は接続証明をBroker照合後にCM token。任意UPNや接続名で本人扱いしない | `apps/gateway/apim/policies/credential-connection.xml:8–23,30–65,93–135` |
| CM managedはAPIM接続利用権であり利用者本人証明ではない | `apps/gateway/apim/policies/credential-session-use.xml:87–88` |

1要求の生成上限、tokensPerMinute、期間累積tokenQuotaは別の判定。追加の本人接続をしても利用枠のサブスクリプションIDは変わらない。数値例は教材用の仮定値として明示する。数値ログの対象がChatに限られる点と、Responsesのquota対応範囲を混同しない。
