# Azureポータルで追加するREST・SSE API

共通の入口認証・監査・回数制限がすでに配備されているAPIMに、手動管理のAPIを追加する手順です。[RESTの設定例](rest.example.json)と[Claude MessagesのSSE設定例](claude-messages.example.json)は同じ[APIスコープのひな型](rest.xml)を使います。生成XMLをポータルに保存して初めて反映されます。初期構築資材のAPIと適用スコープは[対応表](scope-map.md)、具体的な例は[Claudeの公開手順](claude-messages.md)を参照してください。

## APIとProductを設定する

1. **API Management → Backends → Add**でHTTPSのバックエンドを登録します。IDは`manual-`で始めます。ポリシーは固定のバックエンドIDへ送ります。使用する場合はAPIMのシステム割り当てマネージドIDに、対象先が要求する権限をその資源のスコープで付与します。Claude本文監査では配備済みの `core-audit-relay` を選びます。バックエンドの固定ホストと証明書の名前を照合します。 [Claudeの公開手順](claude-messages.md)
2. **APIs → Add API → HTTP**でIDを`manual-`で始め、未使用の公開prefixを設定します。**Subscription required**を有効にし、購読用ヘッダーは共通のNamed Value `core-subscription-key-header` と揃えます。初期値は`api-key`です。公開する操作のIDとメソッドを手順3のJSONへ記載します。ワイルドカードを使う汎用操作、コネクションを所有者に結び付ける処理、動的なバックエンド切替は別設計で扱います。 [APIMの手動API作成](https://learn.microsoft.com/en-us/azure/api-management/add-api-manually)
3. JSON例を保護領域へコピーし、API・Product・バックエンドのID、各操作の固定下流パス、入力サイズ、回数を選びます。**API → All operations → Inbound processing → Code editor**に生成XML全体を貼り付けます。保存してから個別操作を登録し、APIMで表示されるOperation IDを設定と照合します。**Calculate effective policy**で共通入口が一度だけ適用され、選択した固定の下流へ転送が一度だけ行われることを確認します。個別操作に追加ポリシーがある場合は各sectionの継承位置も確認します。 [APIMのポリシースコープと継承](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-policies)
4. **Products**でキー用の購読必須Productを公開し、このAPIを追加します。IDは`manual-`で始め、個別の利用者・サービスにそのProductの購読を発行します。Bearerも提供する場合は、IDが`manual-entra-users`の購読不要Productを公開し、追加する`manual-` APIだけを所属させます。両Productのポリシーは各sectionに`<base />`を置き、共通認証を継承します。API自体のSubscription requiredは維持します。開発者ポータルの公開状態は表示を切り替える設定です。アクセス停止にはAPIとProductの関連付け解除、購読の停止、または対象操作の削除を使います。 [APIMの購読](https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions)

初期構築が管理する`core-entra-model-users`、`released-apis`、既存の検証用Productは手動追加先から外します。このひな型のキー経路は指定した手動Productの購読を使います。Bearerには購読不要の`manual-entra-users`ひとつを使い、共通Entraのテナント・対象API・`Gateway.Invoke`で認証します。このProductを受け付ける共通認証が反映された後で関連付けます。 [APIMのアクセス制御](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-create-products)

## ポリシーXMLを生成する

次の例では、正式な準備手順で作成した環境の`generated/`を使います。`rest.example.json`をコピーし、公開例と異なるID・値があればコピー側を編集します。worktreeから主チェックアウトの保護領域を使う場合は、`--runtime-root`で主チェックアウトの絶対パスを指定します。

```sh
install -m 600 apps/api-gateway/policies/portal-examples/rest.example.json .local/test/generated/manual-api.json
python3 -B scripts/render_apim_portal_policy.py --environment test \
  --config .local/test/generated/manual-api.json \
  --output .local/test/generated/manual-api.xml
```

| 設定 | 動作 |
| --- | --- |
| `apiId`・`keyProductId`・`backendId` | Azureポータルで登録したID。表示名と区別する |
| `operations` | Operation IDとメソッドで許可する操作を選び、固定下流パスへ書き換える。`copyUnmatchedParams`で転送するクエリの扱いを選ぶ。URL内とヘッダーの入口資格情報を下流へ転送する前に除去する |
| `bodyFormat`・`maxRequestBytes` | `json`・`binary`・`multipart`・`text`で受信バイト数を制限する。`none`と`null`はこの検査の対象外。提供元の本文スキーマ・モデル名・機能可否は提供元が扱う |
| `backendAuthentication` | `none`は下流認証が不要な経路。`managedIdentity`は固定のHTTPS audienceを使う。`auditRelay`はClaude Messages／Count Tokens専用で、Gateway配備済みの本文監査中継を使う。マネージドIDのトークンを取得できない場合は転送を止める |
| `keyFallbackRate` | 共通の購読回数枠が有効なら共通の枠を使う。SQLで未管理の手動購読など、回数枠が適用されない場合に操作共通のAPI／購読別回数を適用する。SQL取得が異常終了した場合は既存の503で止まる |
| `responseTransport` | `rest`は通常HTTP、`sse`はAPIMの応答バッファを無効にする。App Gateway側も別途確認する |
| `bodyAudit` | 通常RESTの例では`true`で配備済みの本文監査部品を使う。認可・利用枠の確認後に入力と応答を提出する。個別操作で応答を書き換える場合は、操作ポリシーの最終変換後に応答の提出を置く。SSEは対応する監査中継を使う |

共通監査で受入・拒否・下流応答・ポリシー処理を記録します。モデル名や使用量は、対応する提供元のログと要求IDで照合します。ポータルの診断設定では本文の記録を0、許可ヘッダーを空または既存の安全な項目だけに設定し、キー・Bearer・トークンを記録対象から外します。Anthropicの本文やSSEを読み切って計数するポリシーは追加していません。 [APIMのSSE構成](https://learn.microsoft.com/en-us/azure/api-management/how-to-server-sent-events)

## 手動設定を維持する

運用台帳にAPI・操作・バックエンド・Product・ポリシーの保存版・App Gatewayの経路とWAF・担当者を記録します。構築資材側とID・公開prefixを共有しないようにします。初期構築後の再配備を試す際は、保護した以前の配備記録を使って計画を確認し、APIMの共通部品・Named Value・APIM以外のWAF／Firewallルールに対する変更もレビューします。手動APIが存在する環境で初回構築用の手順からやり直す場合は所有資源を確認します。APIMや含まれるリソースグループそのものを削除すると手動資源も失われます。
