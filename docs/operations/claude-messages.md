# Claude Messages APIの追加

[運用手順](README.md) · [APIMの運用](apim.md)

## 構築後にClaudeをGUIで追加する

FoundryでClaudeモデルをデプロイし、Claude Codeから公開GatewayのAnthropic Messages APIを呼びます。初期構築後の作業です。ポータルの入力値と貼り付けるポリシーは[Claude Messagesの設定例](../../apps/api-gateway/policies/portal-examples/claude-messages.md)にあります。

1. **Microsoft Foundry → モデルカタログ → Claude → デプロイ**で、購入条件、リージョン、バージョン、使用枠とPrivate Link対応を確認し、デプロイ名とFoundryリソースのエンドポイントを控えます。 [FoundryでClaudeモデルを使う](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-claude)
2. **Foundryリソース → Access control (IAM)**で、監査中継のContainer AppのマネージドIDへ推論権限を割り当てます。**API Management → Backends**でGateway配備済みの `core-audit-relay` を確認します。中継からCoreのFoundryへの名前解決とHTTPS到達性を確認します。 [FoundryのEntra認証](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/configure-entra-id) · [監査中継の設定](../../apps/api-gateway/policies/portal-examples/claude-messages.md)
3. **API Management → APIs → Add API → HTTP**でID `manual-claude-messages`、サフィックス`models/claude`を設定し、購読を必須にします。 [HTTP APIの手動作成](https://learn.microsoft.com/en-us/azure/api-management/add-api-manually)
4. **API → All operations → Inbound processing → Code editor**に[Claudeの貼り付け用XML](../../apps/api-gateway/policies/portal-examples/claude-messages.xml)を保存してから、操作ID `messages`、`POST /v1/messages`を追加します。固定の下流パスは`/anthropic/v1/messages`です。`Calculate effective policy`で入口、認証、利用枠、監査、資格情報の除去、固定転送先を確認します。SSEではAPIMの`forward-request`を応答バッファなしに設定します。`count_tokens`は利用するモデルとクライアントで対応を確認した場合に追加します。 [Claudeで使えるAPI](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/claude-models)
5. **Products**で購読必須の`manual-model-users`を作成し、キーで提供するAPIを関連付けます。本人Bearerを提供する場合は、購読不要の`manual-entra-users`に対象APIを関連付けます。トークンはGateway APIの `Gateway.Invoke` 委任scopeを使います。期限切れ、scope不足、無効キーと有効Bearerの併送、許可外操作の拒否を確認します。公開状態は開発者ポータルへの表示設定です。実際のアクセス停止にはAPIとの関連付け解除や購読の停止を使います。 [APIMの購読とアクセス制御](https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions)
6. **Application Gateway → Rules／Path map**で、Gateway用リスナーが新しいパスをAPIMへHTTPSで転送し、有効なWAFがPreventionで動作することを確認します。共通WAFで開始し、正常要求の誤検知を確認した場合は、対象のパス・項目・ルールを絞って見直します。パス別WAFを追加する際は、そのパスで有効になる設定全体と入口ヘッダーの書換えを照合します。SSE用の応答バッファ設定も確認します。 [WAFの関連付け](https://learn.microsoft.com/en-us/azure/web-application-firewall/ag/policy-overview) · [Application GatewayのSSE設定](https://learn.microsoft.com/en-us/azure/application-gateway/use-server-sent-events)
7. **Claude Code**では`https://<GATEWAY_HOST>/models/claude`をGatewayのベースURLにし、購読キーまたはBearerとデプロイ名を設定します。MessagesのJSONとSSEを試し、`apim-request-id`から認証主体・操作・結果と、保存した入力・応答を照合します。Claudeの利用量はFoundry側の診断で確認します。 [Claude Codeの設定例](../../apps/api-gateway/policies/portal-examples/claude-messages.md) · [本文監査の対象](../reference/audit/request-body-audit.md) · [Claude CodeのGateway設定](https://code.claude.com/docs/en/llm-gateway)

本文監査を必須とする提供範囲では、Claude本文の採取と復元結果を受け入れてから公開します。手動追加の所有者と変更履歴を運用台帳に記録します。Coreの再配備前は、API・Product・WAF・経路の差分を確認します。手動追加したAPI／モデルも、所有APIM・Foundry・RGを削除する`azd down`では撤去対象です。
