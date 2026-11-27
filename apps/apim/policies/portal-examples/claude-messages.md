# Claude Code向けにFoundryのClaudeを公開する

Azureポータルから、Foundryに配備したClaudeモデルをGatewayのAnthropic Messages APIとして公開する例です。Egakumi Portalの試用カタログには初期構築のAPI定義を使います。`manual-claude-messages`、`manual-claude-foundry`、`models/claude`は公開用の例です。この例のIDと上限を使う場合は、[貼り付け用のXML](claude-messages.xml)を開き、全文をコピーして使えます。

## FoundryとAPIMバックエンド

1. **Microsoft Foundry → Model catalog**で対象Claudeを選び、提供リージョン、購入条件、割当、ネットワーク、使う機能を確認して配備します。配備名と表示されるターゲットURIを控えます。Messagesの標準形は`https://<FOUNDRY_RESOURCE>.services.ai.azure.com/anthropic/v1/messages`です。 [FoundryでClaudeを配備する](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-claude)
2. APIMのシステム割り当てマネージドIDへ、対象のFoundry資源で使う推論権限を割り当てます。`Cognitive Services User`または組織で承認した推論用カスタムロールを資源スコープで使います。APIMは`https://ai.azure.com`を対象とするEntraトークンで接続します。 [FoundryのEntra認証と権限](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/configure-entra-id) · [APIMのマネージドID](https://learn.microsoft.com/en-us/azure/api-management/authentication-managed-identity-policy)
3. **API Management → Backends → Add**で、ID`manual-claude-foundry`、URL`https://<FOUNDRY_RESOURCE>.services.ai.azure.com`を登録します。URLには`/anthropic/v1/messages`を含めず、TLSの名前・証明書検証を有効にします。APIMから解決したホストが対象のプライベートエンドポイントつきの宛先へ到達することを確認します。別Foundry資源を選んだときはPrivate DNS・既定ルート・NSG・該当時のFirewallルールをそれぞれ確認し、経路の変更を台帳へ記録します。

## API、操作、Product

1. **APIs → Add API → HTTP**でID`manual-claude-messages`と公開prefix`models/claude`を設定します。Web service URLは手順で登録したBackendと同じHTTPSのオリジンにします。APIのSubscription requiredを有効とし、Header nameを共通の`core-subscription-key-header`と一致させます。初期値は`api-key`です。
2. [貼り付け用のXML](claude-messages.xml)の全文を **All operations → Inbound processing → Code editor**へ貼り付けて保存します。**Add operation**で`POST /v1/messages`を追加し、Operation IDを`messages`に揃えます。表示名にも`messages`を使用し、保存後のIDを照合します。この例は60秒あたり60回の購読fallbackと、標準WAFに揃えた2,000 KiB（2,048,000バイト）のAPIM受信上限を設定します。ID・上限・操作を変える場合は、[SSE用JSON](claude-messages.example.json)を保護領域で編集し、[生成コマンド](README.md#ポリシーxmlを生成する)で出力します。公開`/v1/messages`は下流`/anthropic/v1/messages`へ書き換えます。Claude Codeが送る`?beta=true`は`copyUnmatchedParams: true`で転送します。`anthropic-version`と`anthropic-beta`は到達先まで渡し、JSON本文の`model`にはクライアントが実際のFoundry配備名を指定します。`anthropic-version`の例は`2023-06-01`です。 [Messagesリクエストとヘッダー](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-claude) · [Claude CodeのGateway互換性](https://code.claude.com/docs/en/llm-gateway-protocol)
3. 利用するClaude Codeと配備モデルで必要性・対応を確認した場合は、Operation ID`count-tokens`で`POST /v1/messages/count_tokens`を登録し、JSONにも`method`を`POST`、`backendPath`を`/anthropic/v1/messages/count_tokens`とする別操作を追加してXMLを再生成します。実応答と監査を確認してから利用者へ案内します。 [FoundryのClaude機能一覧](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/claude-models)
4. **Products**でキー用`manual-model-users`を購読必須・公開にし、手動APIを追加して専用購読を発行します。本人Bearerを使う場合は公開の購読不要Product`manual-entra-users`へ追加し、対象クライアントに組織のEntra設定で`Gateway.Invoke`の委任許可・同意を用意します。両方のProductのポリシーは共通処理を`<base />`で継承します。共通Entraの変更が反映されたことを実効ポリシーで確認します。 [共通のProduct設定](README.md#apiとproductを設定する)

入口で受け取った購読ヘッダー、`x-api-key`、Gateway用`Authorization`、内部制御用のヘッダーは下流へ渡す前に除去します。Foundry側のマネージドID認証はその後に付けます。対象Foundryの許可モデルを分離する場合は、公開対象のモデルだけを持つFoundry資源へ固定します。

## Application GatewayとWAF

1. **Application Gateway → Rules／URL path maps**で、Gateway用リスナーの`/models/claude/v1/messages`と追加した場合の`/models/claude/v1/messages/count_tokens`が、既存のAPIM向けHTTPSバックエンドで解決することを確認します。該当パスで現在のWAFポリシー、信頼済み入口の付与、Firewall検査経路を受け入れます。
2. **WAF policy**で`Prevention`と現在の保護ルールが有効なことを確認します。JSON本文の受入上限は、実効WAFポリシー、APIM、Foundry側の各上限のうち最も小さい値です。通常のJSON・`stream: true`および長い正規リクエストを試します。WAFで拒否された場合は時刻・公開パスとWAFのトランザクションIDから該当ルールとJSONフィールドを調べ、調整対象を限定します。パス専用WAFを関連付ける場合は元の保護と診断が揃っていることを確認します。 [Application GatewayのWAFポリシー](https://learn.microsoft.com/en-us/azure/web-application-firewall/ag/policy-overview) · [WAFの要求サイズ制限](https://learn.microsoft.com/en-us/azure/web-application-firewall/ag/application-gateway-waf-request-size-limits)
3. APIMの`buffer-response="false"`、App Gatewayの応答バッファ無効化、該当バックエンドのタイムアウトを確認します。一定時間継続するSSEで最初のイベント、逐次到着、最終イベント、クライアント切断後の終了を確認します。 [Application GatewayのSSE](https://learn.microsoft.com/en-us/azure/application-gateway/use-server-sent-events) · [APIMのSSE](https://learn.microsoft.com/en-us/azure/api-management/how-to-server-sent-events)

## クライアントと公開確認

| 経路 | クライアントの設定 |
| --- | --- |
| 本人Bearer | Claude Codeの`ANTHROPIC_BASE_URL`を`https://<GATEWAY_HOST>/models/claude`に設定します。egakumi-api宛の有効な本人トークンを保護された端末セッションの`ANTHROPIC_AUTH_TOKEN`へ渡します。配備名を`ANTHROPIC_MODEL`または`--model`へ指定し、期限後の再取得と再接続を確認します |
| 購読キー | Claude Code v2.1.227以降の対応版で、`CLAUDE_CODE_USE_FOUNDRY=1`、`CLAUDE_CODE_SKIP_FOUNDRY_AUTH=1`、同じGateway prefixを`ANTHROPIC_FOUNDRY_BASE_URL`へ設定し、`ANTHROPIC_CUSTOM_HEADERS`から実際の購読ヘッダーに値を渡します。`ANTHROPIC_FOUNDRY_AUTH_TOKEN`と`ANTHROPIC_FOUNDRY_API_KEY`は空にし、APIM側で下流認証を行います |

クライアントの環境変数へ秘密を渡す場合は、承認済みの取得方法から対象セッションへだけ設定します。既定モデル、サブエージェントやバックグラウンド操作用のモデルも、使う場合は配備名を確認します。端末上の認証方法と送信先は対象版の`/status`およびGatewayの要求記録で照合します。 [Claude Codeの環境変数](https://code.claude.com/docs/en/env-vars) · [Claude CodeとFoundry](https://code.claude.com/docs/en/microsoft-foundry)

- 専用キー、本人Bearer、JSON、SSEを順に確認します。未所属、期限切れ、scope不足、無効キーと有効Bearerを組み合わせた要求、別Productのキー、非公開操作を試し、拒否と下流未到達を確認します。
- 受入では無害な合成入力を使用します。`apim-request-id`、APIMの認証方式とAPI・操作、バックエンドの要求ID、Foundryの実モデル・利用量を照合します。Claude固有の使用量はFoundryのログやメトリクスで確認します。回数枠の429とFoundry側の429を区別します。
- 既存のWAF／Firewall設定と無関係な初期APIの受入結果も確認します。WAF関連付け、Product所属、作成したバックエンド、生成時の設定を記録し、更新時は同じ操作IDと保護設定で反映します。公開を停止するときは手動ProductからAPIを外し、購読と既存ストリームの状態を確認します。Productの公開状態は開発者ポータルの表示に使います。

この手順のXMLは合成入力で生成確認しています。Azureの実接続、Claude Codeの対象版、Foundryモデルごとの機能、WAFとTLS検査は新規環境で受け入れます。
