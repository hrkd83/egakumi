# Claude Code向けにFoundryのClaudeを公開する

Azureポータルから、Coreと同じFoundryに配備したClaudeモデルをGatewayのAnthropic Messages APIとして公開する例です。事前にCoreとGatewayを配備します。Egakumi Portalの試用カタログには初期構築のAPI定義を使います。`manual-claude-messages`と`models/claude`は公開用の例です。この例のIDと上限を使う場合は、[貼り付け用のXML](claude-messages.xml)を開き、全文をコピーして使えます。

## FoundryとAPIMバックエンド

1. **Microsoft Foundry → Model catalog**で対象Claudeを選び、提供リージョン、購入条件、割当、ネットワーク、使う機能を確認して配備します。配備名と表示されるターゲットURIを控えます。Messagesの標準形は`https://<FOUNDRY_RESOURCE>.services.ai.azure.com/anthropic/v1/messages`です。 [FoundryでClaudeを配備する](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-claude)
2. **Foundry → アクセス制御（IAM）**で、APIMのシステム割り当てマネージドIDの推論権限を確認します。対象のFoundry資源のスコープで `Cognitive Services User` または組織が承認したMaaS推論用ロールを使います。APIMのポリシーが `https://ai.azure.com` 宛てのトークンを取得します。 [FoundryのEntra認証と権限](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/configure-entra-id)
3. **API Management → Backends → Add**でID `manual-claude-messages-backend` を登録します。URLは `https://<FOUNDRY_RESOURCE>.services.ai.azure.com` とし、パスはポリシーの固定URIで追加します。TLSの証明書名とチェーンの検査を有効にし、APIMから対象ホストがFoundryのPrivate Endpointへ解決して接続できることを確認します。 [APIMのバックエンド](https://learn.microsoft.com/en-us/azure/api-management/backends) · [APIMのマネージドID](https://learn.microsoft.com/en-us/azure/api-management/authentication-managed-identity-policy)


## API、操作、Product

1. **APIs → Add API → HTTP**でID`manual-claude-messages`と公開prefix`models/claude`を設定します。Web service URLは手順で登録したBackendと同じHTTPSのオリジンにします。APIのSubscription requiredを有効とし、Header nameを共通の`core-subscription-key-header`と一致させます。初期値は`api-key`です。
2. [貼り付け用のXML](claude-messages.xml)の全文を **All operations → Inbound processing → Code editor**へ貼り付けて保存します。**Add operation**で`POST /v1/messages`を追加し、Operation IDを`messages`に揃えます。表示名にも`messages`を使用し、保存後のIDを照合します。この例は標準WAFに揃えた2,000 KiB（2,048,000バイト）のAPIM受信上限を設定します。BearerはEntraユーザーの回数枠に従い、購読キーの共通回数枠はありません。ID・上限・操作を変える場合は、[SSE用JSON](claude-messages.example.json)を保護領域で編集し、[生成コマンド](README.md#ポリシーxmlを生成する)で出力します。公開`/v1/messages`は下流`/anthropic/v1/messages`へ書き換えます。Claude Codeが送る`?beta=true`は`copyUnmatchedParams: true`で転送します。`anthropic-version`と`anthropic-beta`は到達先まで渡し、JSON本文の`model`にはクライアントが実際のFoundry配備名を指定します。`anthropic-version`の例は`2023-06-01`です。 [Messagesリクエストとヘッダー](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-claude) · [Claude CodeのGateway互換性](https://code.claude.com/docs/en/llm-gateway-protocol)
3. 利用するClaude Codeと配備モデルで必要性・対応を確認した場合は、Operation ID`count-tokens`で`POST /v1/messages/count_tokens`を登録し、JSONにも`method`を`POST`、`backendPath`を`/anthropic/v1/messages/count_tokens`とする別操作を追加してXMLを再生成します。実応答と監査を確認してから利用者へ案内します。 [FoundryのClaude機能一覧](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/claude-models)
4. **Products**でキー用`manual-model-users`を購読必須・未公開にし、手動APIを追加します。キーが必要な場合に基盤管理者が専用購読を発行します。本人Bearerを使う場合は公開の購読不要Product`manual-entra-users`へ追加し、対象クライアントに組織のEntra設定で`Gateway.Invoke`の委任許可・同意を用意します。両方のProductのポリシーは共通処理を`<base />`で継承します。共通Entraの変更が反映されたことを実効ポリシーで確認します。 [共通のProduct設定](README.md#apiとproductを設定する)

入口で受け取った購読ヘッダー、`x-api-key`、Gateway用`Authorization`、内部制御用のヘッダーを除去し、APIM自身のマネージドIDでCoreのFoundryへ接続します。別のFoundry資源を公開する場合は、その資源専用の固定経路と権限を設計します。

## Application GatewayとWAF

1. **Application Gateway → Rules／URL path maps**で、Gateway用リスナーの`/models/claude/v1/messages`と追加した場合の`/models/claude/v1/messages/count_tokens`が、既存のAPIM向けHTTPSバックエンドで解決することを確認します。該当パスで現在のWAFポリシー、信頼済み入口の付与、Firewall検査経路を受け入れます。
2. **WAF policy**で`Prevention`と現在の保護ルールを確認します。WAF・APIM・Foundryの受入上限を確認します。通常のJSON・`stream: true`と長い正規リクエストを試します。WAFで拒否された場合は時刻・公開パス・トランザクションIDから該当ルールとJSONフィールドを確認し、調整対象を限定します。パス専用WAFを関連付ける場合は元の保護と診断を揃えます。 [WAFの要求サイズ制限](https://learn.microsoft.com/en-us/azure/web-application-firewall/ag/application-gateway-waf-request-size-limits)
3. APIMの`buffer-response="false"`、App Gatewayの応答バッファ無効化、該当バックエンドのタイムアウトを確認します。一定時間継続するSSEで最初のイベント、逐次到着、最終イベント、クライアント切断後の終了を確認します。 [Application GatewayのSSE](https://learn.microsoft.com/en-us/azure/application-gateway/use-server-sent-events) · [APIMのSSE](https://learn.microsoft.com/en-us/azure/api-management/how-to-server-sent-events)

## クライアントと公開確認

| 経路 | クライアントの設定 |
| --- | --- |
| 本人Bearer | Claude Codeの`ANTHROPIC_BASE_URL`を`https://<GATEWAY_HOST>/models/claude`に設定します。egakumi-api宛の有効な本人トークンを保護された端末セッションの`ANTHROPIC_AUTH_TOKEN`へ渡します。配備名を`ANTHROPIC_MODEL`または`--model`へ指定し、期限後の再取得と再接続を確認します |
| 購読キー | Claude Code v2.1.227以降の対応版で、`CLAUDE_CODE_USE_FOUNDRY=1`、`CLAUDE_CODE_SKIP_FOUNDRY_AUTH=1`、同じGateway prefixを`ANTHROPIC_FOUNDRY_BASE_URL`へ設定し、`ANTHROPIC_CUSTOM_HEADERS`から実際の購読ヘッダーに値を渡します。`ANTHROPIC_FOUNDRY_AUTH_TOKEN`と`ANTHROPIC_FOUNDRY_API_KEY`は空にし、APIM側で下流認証を行います |

クライアントの環境変数へ秘密を渡す場合は、承認済みの取得方法から対象セッションへだけ設定します。既定モデル、サブエージェントやバックグラウンド操作用のモデルも、使う場合は配備名を確認します。端末上の認証方法と送信先は対象版の`/status`およびGatewayの要求記録で照合します。 [Claude Codeの環境変数](https://code.claude.com/docs/en/env-vars) · [Claude CodeとFoundry](https://code.claude.com/docs/en/microsoft-foundry)

- 専用キー、本人Bearer、JSON、SSEを順に確認します。未所属、期限切れ、scope不足、無効キーと有効Bearerを組み合わせた要求、別Productのキー、非公開操作を試し、拒否と下流未到達を確認します。
- 受入では無害な合成入力を使用します。`apim-request-id`からAPIMの認証主体・操作・結果を調べ、取得できたFoundryの要求IDと照合します。通常JSONとSSEの応答、資格情報の除去、診断ログの着信を確認します。Claude固有の使用量はFoundryで確認します。この直接REST経路の監査はメタデータを対象とします。 [標準診断と監査ログの保管](../../../../docs/reference/audit/audit-log-design.md)
- 既存のWAF／Firewall設定と無関係な初期APIの受入結果も確認します。WAF関連付け、Product所属、作成したバックエンド、生成時の設定を記録し、更新時は同じ操作IDと保護設定で反映します。公開を停止するときは手動ProductからAPIを外し、購読と既存ストリームの状態を確認します。Productの公開状態は開発者ポータルの表示に使います。

この手順のXMLは合成入力で生成確認しています。Azureの実接続、Claude Codeの対象版、Foundryモデルごとの機能、WAFとTLS検査は新規環境で受け入れます。
