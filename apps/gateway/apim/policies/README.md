# APIMポリシーの管理構成

共通の処理順序は `service.xml`、再利用する処理の実装は責務ごとのフォルダで管理する。
共通Inboundの見出しは、STEP1要求情報、STEP2入口確認、STEP3接続ログインの受付判定、STEP4キー／Bearer認証、STEP5認証済み利用者情報の連番で管理する。
棚卸済みの部品からこの構成へ移設し、未棚卸のファイルは現在の位置を保って順に確認する。
フォルダの移設だけでは、認証・モデル機能・監査出力・Azureの登録名を変更しない。

```text
policies/
├── service.xml
├── fragments/
│   ├── settings/
│   │   └── core-api-settings/
│   │       ├── core-api-settings.xml
│   │       ├── core-api-settings.cs
│   │       └── README.md
│   ├── audit/
│   │   ├── core-request-context/
│   │   │   ├── core-request-context.xml
│   │   │   └── core-request-context.cs
│   │   ├── core-audit-authenticated-subject/
│   │   │   ├── core-audit-authenticated-subject.xml
│   │   │   └── core-audit-authenticated-subject.cs
│   │   └── core-gateway-telemetry/
│   │       ├── core-gateway-telemetry.xml
│   │       └── core-gateway-telemetry.cs
│   ├── ingress/
│   │   └── core-trusted-ingress/
│   │       ├── core-trusted-ingress.xml
│   │       └── core-trusted-ingress.cs
│   └── authentication/
│       ├── core-credential-browser-exception/
│       │   ├── core-credential-browser-exception.xml
│       │   └── core-credential-browser-exception.cs
│       ├── core-common-authentication/
│       │   ├── core-common-authentication.xml
│       │   ├── core-common-authentication.cs
│       │   └── README.md
│       └── core-entra-authentication-error/
│           └── core-entra-authentication-error.xml
├── api-templates/
├── operation-policies/
└── snippets/
```

| 配置先 | 管理するもの |
| --- | --- |
| `service.xml` | AzureのAll APIsへ設定する共通ポリシー。STEP、認証方法の選択、部品を実行する順序を示す |
| `fragments/` | Azureへ登録するPolicy fragment。まず責務で分け、その下に部品ごとのフォルダを作る |
| `api-templates/` | APIの公開時に設定値を埋めて登録するポリシーテンプレート。複数APIが使う正本を共有する |
| `operation-policies/` | APIの特定操作に適用するポリシー。機能ごとにまとめる |
| `snippets/` | 生成時にポリシーへ差し込むXMLの部品。Azureへ登録するPolicy fragmentとは区別する |

## 部品の名前と所有関係

- AzureのフラグメントID、部品フォルダ、XMLのbasenameを一致させる。
- C#式を分離する場合は、同じフォルダに同名の `.cs` を置く。その式の直接使用先は同名XMLひとつにする。
- XMLは処理フロー、C#はXMLから分離した式を担当する。単純なXML処理のために空のC#ファイルは作らない。
- 処理の呼び出し順は `service.xml` 等の呼び出し元で示す。フォルダ名や並び順を実行順序として扱わない。
- C#を生成時にXMLへ埋め込み、Azureポータルでも説明コメントを読める構成を維持する。

ローカルのXMLは生成前のひな形であり、AzureではC#式や差し込み部品を組み込んだXMLが表示される。
同じフラグメント名でも、表示する段階が異なるため、ファイルの見た目がそのまま一致するとは限らない。
入口確認の具体的な差し込み内容は [core-trusted-ingressの説明](fragments/ingress/core-trusted-ingress/README.md) を参照する。
接続ログインの開始・戻り先の受付で共通認証を省略する理由は [core-credential-browser-exceptionの説明](fragments/authentication/core-credential-browser-exception/README.md) を参照する。

## 各APIの設定準備

通常HTTP・WebSocketの接続開始・MCPでは、API固有の設定データを`gatewayApiSettingsSource`へ保存し、`<base />`より前で`core-api-settings`を呼ぶ。
この部品が`gatewayAuditSettings`と`gatewayControlSettings`を準備してから、共通Inboundへ進む。
設定値の正本はAPI定義に残し、監査と制御の用途も分けたままにする。CMの管理・ブラウザー受付へAIモデル用設定を追加しない。
入力・出力とHTTPの操作別選択は [設定部品の説明](fragments/settings/core-api-settings/README.md) を参照する。

各APIの`<base />`は「共通ポリシーの適用」という独立した見出し・目的・処理のブロックで示す。
上位のInboundをその位置で実行し、拒否されなければ後続のAPI固有処理へ戻る。WebSocketでは`onHandshake`の操作ポリシーから上位を継承する。
HTTPの操作制約・下流接続、WebSocketのモデル・固定query、生成MCPからREST操作への入力検査・下流認証の引継ぎは、それぞれ後続の処理で確認する。
`__...__`はBicepから埋め込むAPI契約、`{{...}}`は名前付きの値への参照を示す。監査では本文・資格情報を記録せず、SSEは読み切らず中継する。
テンプレートの冒頭は種類の短い説明だけにし、各ブロックと重複する全体説明をAPIごとに繰り返して埋め込まない。

## STEP4の認証部品

`core-common-authentication`は、STEP3で判定した接続ログインのブラウザー受付を除き、通常の要求を認証する。
設定したキー用ヘッダーがあればAPIMが検証した購読を確認し、なければEntraのBearerトークンを検証する。
不正なキーを、有効なBearerトークンで救済しない。API名やクライアントアプリの許可リストでBearer受付を選別しない。

同名のC#ファイルには、キーの確認、Bearer形式の確認、本人情報の取得、会社設定と認証結果の組立、失敗監査の主体準備という5つの名前付き区画を置く。
各区画をXMLの対応する式へ生成時に埋め込むため、C#ファイル全体を一度に実行する構成ではない。
標準のトークン検証、分岐、応答、認証成功を示す2変数はXMLで処理順を確認できる。
詳細は [認証部品の説明](fragments/authentication/core-common-authentication/README.md) を参照する。

`gatewayCommonAuthenticationValidated`はキー／Bearer共通の検証完了、`gatewayEntraAuthenticationValidated`はBearerのトークン・本人・会社設定の確認完了を示す。
要求ごとの処理結果であり、APIの利用可否を切り替える配備設定ではない。

`core-entra-authentication-error`は、標準のEntraトークン検証が中断した場合に、`service.xml`の`on-error`から呼び出す。
既存の失敗監査と401応答を担当し、短いXML式だけで構成するため空のC#ファイルは作らない。
両認証部品の失敗監査は、生成時に監査部品のソースを展開する。フラグメント内から別フラグメントを呼び出さない。

## 棚卸に合わせた移設

1. 処理の目的・適用先を確認し、配置先を決める。
2. 対象ファイル、BicepとPythonの参照先、テスト、説明書を同時に更新する。
3. 移設前の正本から独立生成したポリシーと比較し、処理・コメントが変わらないことを確認する。
4. 現在の配備記録と実APIMのポリシーを照合する。生成内容が同じならAzureへの再書き込みは不要である。

`expressions/legacy/` と旧構成専用のXML・生成処理は移行期間だけの資材である。
各利用先の棚卸・移行を完了し、旧参照がなくなった段階で削除する。
棚卸完了の条件には、旧資材と旧構成専用の生成処理を撤去し、現行構成で新規構築・再配備・検証が成立することを含める。
名前だけを変えて別フォルダへ温存しない。実環境の復旧用バックアップは、既定の保護されたローカル証跡で扱う。

適用順序と各部品の出力は [APIMポリシー一覧](../../../../docs/reference/apim-policy-catalog.md) を参照する。
