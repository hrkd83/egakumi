# APIMポリシー一覧

2026年11月18日更新。Graphと文書解析を含む標準19 API、生成MCP、CM補助APIの登録資材を説明する。業務APIの同じURLで購読キーとEntraアクセストークンの両方を受け付ける。登録資材の構成と対象環境への反映・受入状況は分け、実際の適用と接続結果は環境ごとの保護された証跡で確認する。

## 要求が通る順序

1. APIMがAPI・Operationを特定する。購読キー付き要求はAPIM標準の購読検証を使い、キーなし要求は専用の購読不要Productを通して共通認証へ進む。
2. APIポリシーがAPI固有の監査・制御設定を変数へ格納し、`<base/>`で全体ポリシーを継承する。
3. 全体ポリシーが共通の要求情報と信頼した入口経路を確認する。設定済みキーヘッダーがあれば購読キー、なければ`Authorization: Bearer`のEntraアクセストークンを検証する。無効なキーをBearerで救済しない。
4. APIポリシーがOperation、モデル、入力形式・サイズ、生成上限を確認する。
5. 検証済みの購読またはユーザーを呼出主体として回数・利用量を計数し、要求ID・API・Operation等の監査情報を確定する。
6. CMが必要なAPIで、同じ呼出主体に結び付いた本人接続セッションを確認する。
7. 入口のキー・ユーザートークン・接続セッション等を除去し、下流専用のMI・OAuth・登録済み資格情報で接続する。

Bearer認可は全業務API、生成MCP、保護されたCM共通APIを対象にする。認証用テナント、API audience、v2トークン、委任スコープ`Gateway.Invoke`を検証し、クライアントアプリIDによる許可リストは設けない。CMのブラウザー用サインイン開始・コールバックは、取引stateやCookieを照合する補助経路として別に扱う。

API登録処理は、APIの配備成功を確認してから共通ポリシーを適用し、読み直して照合する。`azd up`は共通基盤・モデルを準備し、続くGateway工程が必要なCM provider・認証窓口・名前付きの値・APIを設定してからPortalを配備する。

### 監査データの組み立てと共通フロー

共通Inboundは、次の連番で処理を説明する。見出しにA／Bの枝番は付けない。

| STEP | 処理 |
| --- | --- |
| 1 | 要求情報の準備 |
| 2 | AppGWのHTTP入口確認 |
| 3 | 接続ログインのブラウザー受付の判定 |
| 4 | 通常要求のキー／Bearer認証の選択と検証 |
| 5 | 認証済み利用者情報の準備 |

共通の`service.xml`は処理の順序を示し、STEP1からSTEP5をそれぞれleaf fragmentから呼び出す。要求情報・入口確認・受付の判定は認証前、確認済み利用者情報は認証後に1回ずつ処理する。STEP4の`core-common-authentication`が受付の判定結果を読み、購読キー／Bearerの認証分岐を選ぶ。標準Entra検証が失敗した場合は、共通`on-error`の`core-entra-authentication-error`が既存監査と401応答を処理する。

```xml
<inbound>
  <include-fragment id="core-request-context" fragment-id="core-request-context" />
  <!-- === -->
  <include-fragment id="core-trusted-ingress" fragment-id="core-trusted-ingress" />
  <!-- === -->
  <include-fragment id="core-credential-browser-exception" fragment-id="core-credential-browser-exception" />
  <!-- === -->
  <include-fragment id="core-common-authentication" fragment-id="core-common-authentication" />
  <!-- === -->
  <include-fragment id="core-audit-authenticated-subject" fragment-id="core-audit-authenticated-subject" />
</inbound>
<on-error>
  <include-fragment id="core-entra-authentication-error" fragment-id="core-entra-authentication-error" />
</on-error>
```

| Azureの部品名 | XMLの正本 | C#式の正本 | 処理・出力 |
| --- | --- | --- | --- |
| `core-request-context` | `fragments/audit/core-request-context/core-request-context.xml` | `fragments/audit/core-request-context/core-request-context.cs` | `gatewayRequestContext`：受信時刻、要求・API・操作のID、操作のひな形、接続元と相関候補 |
| `core-trusted-ingress` | `fragments/ingress/core-trusted-ingress/core-trusted-ingress.xml` | `fragments/ingress/core-trusted-ingress/core-trusted-ingress.cs` | 必須のHTTP入口ヘッダーを判定し、欠落・不一致・重複なら既存監査へ記録して403を返す。API固有の変数で免除しない |
| `core-credential-browser-exception` | `fragments/authentication/core-credential-browser-exception/core-credential-browser-exception.xml` | `fragments/authentication/core-credential-browser-exception/core-credential-browser-exception.cs` | `gatewayCredentialBrowserException`：接続ログインの開始・戻り先の受付への一致を判定する。本人の認証成功を示す値ではない |
| `core-common-authentication` | `fragments/authentication/core-common-authentication/core-common-authentication.xml` | `fragments/authentication/core-common-authentication/core-common-authentication.cs` | 通常要求のキー／Bearerを認証する。購読、トークン、本人、会社設定を確認し、成功を後続へ伝える |
| `core-entra-authentication-error` | `fragments/authentication/core-entra-authentication-error/core-entra-authentication-error.xml` | なし（短いXML式のみ） | 標準Entra検証の失敗を既存監査と401応答へつなぐ。共通`on-error`から呼び出す |
| `core-audit-authenticated-subject` | `fragments/audit/core-audit-authenticated-subject/core-audit-authenticated-subject.xml` | `fragments/audit/core-audit-authenticated-subject/core-audit-authenticated-subject.cs` | `gatewayAuthenticatedSubject`：認証で確認できた購読または社員。APIの利用許可とは区別する |
| `core-gateway-telemetry` | `fragments/audit/core-gateway-telemetry/core-gateway-telemetry.xml` | `fragments/audit/core-gateway-telemetry/core-gateway-telemetry.cs` | 要求・認証・利用許可等の監査ログを出力する。専用C#式は認証主体の読取と整合性確認を担当する |

表のパスは`apps/gateway/apim/policies/`を起点とする。共通フローの`service.xml`は直下に置き、棚卸済みの部品は`fragments/`の下で監査（`audit`）、入口確認（`ingress`）、認証（`authentication`）へ分ける。Azureの部品名・部品フォルダ・XML・直接埋め込むC#式の名前を揃え、同じ部品フォルダにXMLとC#を置く。各C#の直接使用先は同名XMLひとつに限定する。XMLは部品全体のフローを、C#はそこから分離した式を所有する。`core-trusted-ingress.cs`は入口の判定条件、`core-gateway-telemetry.cs`は認証主体の読取部分であり、XMLの処理全体をC#へ移した意味ではない。短いXML式だけの部品には空のC#を作らない。フォルダ全体の役割と移設手順は[管理構成](../../apps/gateway/apim/policies/README.md)を参照する。

`core-common-authentication.cs`の5つの名前付き区画は、同名XMLの各式へ個別に埋め込む。キー検査からBearer検査までを全要求で一括実行する意味ではない。キー／Bearer共通の成功を`gatewayCommonAuthenticationValidated`、Bearerのトークン・本人・会社設定までの成功を`gatewayEntraAuthenticationValidated`で後続に伝える。2変数は要求ごとの検証結果であり、配備設定の有効／無効を示すスイッチではない。

まだ棚卸していないquery・CM・接続応答の監査は、既存XML名と`expressions/legacy/audit-subject-observation.cs`を移行期間だけ維持する。他の部品も棚卸の順に責務ごとの配置と専用の同名XML／C#へ揃え、この5部品のC#式を別XMLへ直接流用しない。APIテンプレートは`api-templates/`、操作別ポリシーは`operation-policies/`、生成時に差し込むXML部品は`snippets/`へ順に移設する。棚卸完了までに旧資材・旧参照・旧構成専用の生成処理を撤去し、現行構成の新規構築・再配備・検証が成立することを確認する。`legacy`を別名で温存しない。既存のAPIM部品名やAPIの接続先は変更しない。

C#ソースの行頭`//`説明は、生成時に閉じた`/* ... */`へ変換してAzureにも残す。XML属性では改行が空白へ正規化されるため、`//`をそのまま埋め込むと後続コードまでコメントになるおそれがある。実行コードと既存の`/* */`は保持し、説明の追加で処理や認証条件を変更しない。

要求情報の詳説は`core-request-context`、認証済み利用者情報の詳説は`core-audit-authenticated-subject`、監査出力側の読取・整合性確認の詳説は`core-gateway-telemetry`に全て残す。`core-trusted-ingress`にも、実際に組み込む監査コードの説明を保持する。同じ監査処理を組み込む共通拒否・CM・query等のポリシーには、`core-gateway-telemetry`を参照する案内を残す。生成途中のXMLの重複を抑えて配備サイズを守り、監査処理や出力項目は変更しない。

`core-gateway-telemetry.xml`のログ組み立てにも、処理段階・要求情報・認証主体・APIの利用許可・モデルと使用量ログの照合を説明する日本語の`/* 説明：... */`を置く。正本のフラグメントではAzureにも全説明を保持する。入口確認へ展開する場合も、残る処理の説明を保持する。共通拒否等の従来の複製では追加説明だけを省いて正本への案内を残し、既存のコードと説明を維持する。コメント追加は監査項目や認証条件を変更するものではない。

C#は生成時にXML属性用へ変換し、APIMは`include-fragment`の位置へ部品を展開する。拒否監査等で必要な生成済みXMLの再利用は維持し、C#ソースの直接使用先とは区別する。新しい外部通信や本文読み取りは追加しない。AzureポータルではSTEP見出しと共通フローをAll APIs、各処理の説明をPolicy fragmentsで確認する。個別fragmentにSTEP見出しは重ねて記載しない。`Calculate effective policy`で展開後も確認できる。fragment同士の入れ子、`base`、ポリシーsectionは部品へ入れない。

入口確認の部品は、共通の名前付きの値`core-l7-ingress-enabled`と`core-l7-ingress-secret`を参照する。拒否時の監査処理は部品内へ組み込み、他のfragmentを呼び出さない。条件、403応答、認証より前の実行位置は分離前と同じである。STEP見出し・目的・処理を独立したコメントにし、STEP間に`<!-- === -->`を置く。Azureで空行が除去されても区切りコメントは残る。

生成元照合は、参照名だけでなく部品の内容、配置、一度だけの実行、変数の追加書き込みを確認する。実式テストでは監査と入口確認の部品を展開してからC#を実行する。`service-inline-audit-v2.xml`は監査部品化直前、`service-inline-trusted-ingress.xml`は入口確認の分離直前の既知生成元を照合するための固定資材であり、現行の配備には使わない。

STEP3では、CMのログイン開始画面・受付と、認証後の戻り先（callback）用APIへのアクセスかを判定する。通常の画面遷移・フォーム送信・認証後のリダイレクトではAPI用のキー／Bearerヘッダーが送られないため、専用APIの起動画面、起動受付、本人認証の戻り先、接続完了の戻り先と完全一致する要求だけを例外にする。STEP4は判定結果がtrueの場合に従来の匿名フラグを設定し、共通のキー／Bearer検証を省略する。モデル・ツール等の通常APIは引き続きキー／Bearerを検証し、例外となる受付でもEntraへのサインインや各受付・Broker・CMによる取引・本人の確認を省略しない。詳しくは[受付の説明](../../apps/gateway/apim/policies/fragments/authentication/core-credential-browser-exception/README.md)を参照する。

OBOの採否は、共通STEP4の入口認証とSTEP5の認証済み利用者情報を確認した後、個別APIのCM本人接続・下流認証を棚卸する段階で判断する。この受付の部品化ではOBOを実装しない。

### 変数の責務と継承

標準APIは、APIごとの設定を変数へ格納してから`<base/>`で全体ポリシーを継承する。同じテンプレートでも、接続先や操作設定の値はAPIごとに異なる。全体ポリシーは要求から取得できる共通情報を作り、入口と認証を確認する。

| 変数 | 設定する場所 | 内容 |
| --- | --- | --- |
| `gatewayRequestContext` | All APIsのInbound先頭から`core-request-context`を実行 | 要求・API・操作ID、受信時刻、HTTPメソッド、操作のひな形、接続元と相関候補。[監査識別情報](audit-identification.md)の出力契約に従う |
| `gatewayAuthenticatedSubject` | 共通認証後に`core-audit-authenticated-subject`を実行 | 確認済みの購読または社員の識別情報。APIごとの利用許可とは別に監査へ渡す |
| `gatewayCredentialBrowserException` | STEP2直後、共通認証の分岐前に`core-credential-browser-exception`を実行 | CM設定の有無と専用API・操作・HTTPメソッド・元のパスから判定したtrue／false。共通認証を省略する受付かを示す |
| `gatewayAuditSettings` | 対象APIのInbound、`<base/>`の前 | サービス分類、接続先の種類と認証方式、操作別の接続情報、使用量計測の対象、WebSocketモデル、Agent、計測の役割 |
| `gatewayControlSettings` | 対象APIのInbound、`<base/>`の前 | `operationId`、`operationConstraints`、`tokenLimitOperationIds`。操作・入力の検査とトークン計数が参照するAPI固有設定 |
| 認証結果の変数 | All APIsの共通認証処理 | 検証済み購読・ユーザー・トークンと認証完了状態。API側の自己申告を認証結果として扱わない |

監査情報と制御設定は、監査項目の追加・整形と、認可・入力制約・計数の判定を独立して扱うために別の変数へ分ける。`gatewayAuditSettings`を作る処理自体は入力を拒否したり本文を書き換えたりしない。制御設定を参照する後続ポリシーの動作は、それぞれ別に確認する。変数の分離だけで既存の許可条件、モデル機能、接続先、本文変換、計数対象を変更しない。

共有fragmentとAPIポリシーを同じ資材の構成にそろえて配備する。現行の共有処理は`gatewayControlSettings`を直接参照する。更新中に旧APIと新しい共有処理が混在すると、一時的に要求が失敗する場合がある。全APIの更新後に、分離した設定を使うことと、キー・Bearer両方式の接続を確認する。監査設定には制御項目を格納しない。

名前付きの値（Named Values）は、APIMが保存し、複数のポリシーから`{{name}}`で参照する固定設定である。共通キーヘッダー名、入口の有効設定、Brokerの接続先、API別の下流audienceは既に名前付きの値で管理し、秘密情報はKey Vault参照を使う。API固有の値も保存できるが、要求IDや認証結果のように要求ごとに決まる情報は実行時変数へ保持する。

Entraの共通認証には、次の4つの非秘密の名前付きの値を使う。構築入力を正本として生成し、API公開前に実値の型と内容を照合する。認証用テナントはAzure基盤のテナントとは別の設定である。

| 名前付きの値 | 内容 |
| --- | --- |
| `core-entra-tenant-id` | トークンを発行する認証用EntraテナントID |
| `core-entra-api-audience` | APIMで公開するAPI用アプリのApplication ID |
| `core-entra-required-scope` | 必須の委任スコープ名。既定は`Gateway.Invoke` |
| `core-entra-user-limits` | 社員の`requestRate`、`tokensPerMinute`、`tokenQuota`を含むJSON文字列。明示的な`null`は該当する制限なしを表す |

トークン検証のtenant・audience・scopeは名前付きの値を直接参照する。利用枠JSONは`set-variable`の文字列値として取得し、検証後の要求内で解析・照合する。JSONをC#式の引用文字列へ直接挿入しない。これらの値はAPIM側の認証・利用枠であり、Foundryのモデル容量を設定するものではない。

既存の埋め込み設定から移行する場合は、現在と同じ4値を先に追加・読み戻し確認し、APIとfragmentの実行時変数が互換であることを確認した後に共通ポリシーを切り替える。APIやProduct所属の変更を伴わない配置変更を、API全体の再公開と混同しない。切り替え前後のキー・Bearer・SSEを確認し、旧XMLと生成元checkpointを保護して失敗時に戻せる状態を保つ。旧literalの固定テンプレートは以前の構成の照合に使い、新規構築のdesiredには使用しない。

`gatewayAuditSettings`と`gatewayControlSettings`の初期値はAPI契約から生成する。実行時にはOperation別の接続情報を選び、選択済みprofileの操作制約を反映するため、要求内の加工結果を実行時変数へ保持する。固定JSONを名前付きの値へ移しても、この変数は必要となる。現在はAPI契約とポリシーを同じ版で照合する設定として生成資材で管理し、ポリシー本文と独立して運用する共通固定値には名前付きの値を使う。

実行時変数の定義は、GUIのAll APIsまたは対象APIのAll operationsのXMLで確認する。配備時には置換用文字列が設定JSONのBase64文字列に置き換わるが、画面に実行後の変数値が表示されるわけではない。登録資材の変更と実環境への反映は別に検証する。

旧生成元が一部のAPIしか含まない既存環境では、読み取ったXMLを生成元の証明として代用しない。明示した全APIの整合工程では、既知の旧生成元を独立に照合し、残りのAPI・操作・schema・tool・fragment・接続先をバックアップと競合検出の証跡として読み取る。社員Productへの所属を一時解除して現在の資材を配備し、完全なv2生成元と実設定が一致した後に新しい生成元を保護領域へ記録する。共通認証の切替と社員Productへの所属復帰は最後に行う。既存キーのscope・状態は事前確認し、Foundryの容量とFirewallルールをこの整合工程で変更しない。

この明示的な工程には[reconcile_apim_source.py](../../scripts/reconcile_apim_source.py)を使う。`--config .local/<environment>/params.local.json`だけではローカル計画を作り、`--execute`を追加すると事前確認後にAPIMを反映する。Gatewayの通常配備とは入口を分け、Broker・SQL・Portalの配備を自動で含めない。必要なBrokerやDBの準備が未完了なら、API更新前に停止する。保護されたバックアップは`.local/<environment>/evidence/`、照合済みの新生成元は`.local/<environment>/state/gateway-api-source.json`へ保存する。

## 全体で共通のポリシー

| 処理 | 実施内容 | 主なファイル |
| --- | --- | --- |
| 信頼した入口 | AppGW経由の識別値を通常HTTP(S)で常設確認。gRPC専用の入口免除は廃止 | `fragments/ingress/core-trusted-ingress/core-trusted-ingress.xml`、`service.xml` |
| 購読キー | 設定済みヘッダーが1個で空でないこと、`context.Subscription.Id`の存在を確認。URLのキーを拒否 | `service.xml` |
| Entraアクセストークン | キーヘッダーがない要求で署名・期限、認証用テナント、API audience、v2、`Gateway.Invoke`を検証。クライアントアプリIDは認可の制限に使わない | `fragments/authentication/core-common-authentication/core-common-authentication.xml`、`service.xml` |
| 呼出主体 | キーは`subscription:<購読ID>`、Bearerは`user:<テナントID>:<ユーザーObject ID>`。同じ購読または同じユーザーの利用枠を共有 | `gateway.xml`、`protocol.xml`、`mcp.xml` |
| 要求ID | 正常応答・例外応答へ監査と同じ`apim-request-id`を設定 | `service.xml` |
| CM補助 | キーまたはBearerで検証した主体を内部brokerへ渡す。個人APIではSQL台帳の本人セッションと同じ主体を照合 | `credential_login.py`、`credential-login/*.xml`、`credential-connection.xml` |

`core-subscription-key-header`をGateway配備が管理する名前付きの値として使用する。既定は`api-key`。`Ocp-Apim-Subscription-Key`を採用する環境では共通設定を変更する。APIごとの自動alias受入は行わない。`Authorization: Bearer`にはEntraアクセストークンを送り、購読キーをBearerトークンとして扱わない。両ヘッダーを送った要求は購読キー経路で検証する。

通常APIの購読キーは、設定済みヘッダーでの送信だけを受け付ける。解析済みqueryに`subscription-key`または`api-key`があれば、正しいキーやBearerをヘッダーへ送っていても拒否する。この確認は`api-version`等の通常のquery項目を対象にせず、STEP3で確認したCMのブラウザー受付の例外も変更しない。URL指定のキーを受け付けるには認証方法の選択も変更する必要があり、拒否条件の削除だけでは対応できない。

ヘッダーでの送信に限定する目的は、URL経由でキーが履歴・アクセスログへ残る機会を減らすことである。APIMでの拒否や診断の既知query項目の非表示設定は、ブラウザー履歴や前段のAppGW・プロキシログからキーを消す処理ではない。query名の大小文字・エンコードの扱いは実APIMの解析に依存するため、全表記を検出・非表示にできるとは扱わない。

キー用ヘッダーがない通常要求は先にBearer側へ進むため、キー検証側には独立したヘッダー欠落の拒否分岐を置かない。既存の不正キー判定で空値を先に確認し、ヘッダーがなくても配列参照を実行せず拒否する。初回配備の途中で使うポリシーもこの検査で401を返せるため、初回専用の欠落分岐は追加しない。キー検証側の拒否の監査コードは`INVALID_AUTHENTICATION`に統一する。

共通入口の認証失敗は401と要求IDを返す。無効・停止・範囲外のキーは拒否し、同時に有効なBearerがあっても救済しない。キーも有効なBearerもない要求を業務APIへ通さない。信頼したHTTP入口の不一致は403。専用の購読不要ProductはBearer要求を共通認証へ届けるために使い、既存の別のOpen Productや未認証の迂回経路がないことを公開計画で確認する。

基盤が提供するすべてのAPIは、キー／Bearerの両認証に標準対応する。STEP3でCMのブラウザー受付の例外を判定した後、STEP4はキー用ヘッダーの有無だけで認証方法を選ぶ。Bearer分岐の条件へAPI名の一覧を埋め込まない。APIごとにBearer対応をtrue／falseで宣言する設定や実行時の分岐は設けない。配備前に、生成した全API・操作のポリシーが共通認証を継承し、検証済みの購読または社員を扱う構成であることを照合する。構成が不足していれば配備を停止し、要求受付後にAPI別の対応フラグで拒否する構成にはしない。共通認証の完了状態やEntraトークンの検証結果を確認する処理は引き続き残す。専用Productへ所属させるAPI集合は、配備処理が正本から自動生成して照合するために保持する。要求ごとの認証方法を選ぶ条件には使わない。

Bearer要求のProductが存在し、専用Productであることは共通認証で一度だけ確認する。`gateway.xml`、`protocol.xml`、`mcp.xml`はProduct確認を重複させず、共通認証とEntra検証の完了、検証済み本人情報の整合性を確認して`gatewayDecision`へ引き渡す。

キー要求の検証済み購読の存在と、購読IDが空でないこと、キー用ヘッダーが空・空白だけでなく値の数が1つであることは、共通認証で一度だけ確認する。`gateway.xml`、`protocol.xml`、`mcp.xml`は共通認証の完了を確認した後、この購読IDを呼出主体へ引き継ぎ、同じ購読・ヘッダー検査を繰り返さない。値の数はAPIMが認識した値配列の個数であり、生のHTTPヘッダー行数やカンマを含む1文字列と同一視しない。Bearer側の購読混在と本人情報の整合確認は維持する。MCPからRESTへの転送は別要求なので、REST側の共通認証も実行する。

キー用ヘッダーには専用の名前を設定する。APIが要求IDへ書き換える`x-ms-client-request-id`と、AppGWの相関用の`x-appgw-trace-id`は使用できず、既存の認証・接続用ヘッダーと同様に配備前の設定検査で拒否する。これにより、共通認証から各APIへ引き継ぐ途中で内部ヘッダーの処理がキーを書き換える構成を防ぐ。

## APIごとのポリシー

| 処理 | 実施内容 |
| --- | --- |
| 共通処理の継承 | `<base/>`と完了マーカーを確認する。設定不足は503 |
| 操作・モデル | 公開Operationと固定モデル・profile・URI・queryを照合する |
| 入力・出力上限 | APIMの技術上限、型・必須構造、指定時のモデル別出力上限を検査。試験用の小容量・固定件数は標準AI APIへ追加しない |
| 回数制限 | キーは検証済み購読、Bearerは検証済みユーザーのcounterを使用。CMの追加資格情報で入口の利用枠を切り替えない |
| 利用枠の設定 | キーは`subscription-profile.xml`でPortalのSQL設定を内部APIから取得し、正常な値を30秒キャッシュする。Bearerは`entra-user-profile.xml`で共通認証が保持した社員利用枠を確認する。取得障害や不完全な設定を無制限へ変換しない |
| LLMトークン上限 | `token-limit.xml`を対応HTTP APIだけから参照。検証済み利用枠の毎分・期間内トークン数をAPIM標準ポリシーへ渡す。明示した制限なしの設定と設定欠落を区別する |
| 下流認証 | Foundry等はMI、外部サービスは登録済みの認証設定。キー・入口のユーザートークン・本人sessionを下流へ流さない |
| 監査 | 要求ID、呼出主体、認証方式、購読またはユーザー・テナント・クライアント、API、Operation、モデル、状態、使用量を記録。本文・token・keyを含めない |

## モデル・Operation固有のポリシー

| 対象 | 組み込む処理 | 初期設定の主な値・公開契約 |
|---|---|---|
| OpenAI Chat | profile内のモデル別生成上限を検査 | `max_completion_tokens`、各モデル128,000 |
| DeepSeek・Mistral・Grok Chat | `compatible`内のモデルごとの上限を検査 | `max_tokens`。DeepSeek 384,000／Grok 500,000。Mistralは未確認値を作らず自動値 |
| Model Router | `router` profileからRouter配備を呼ぶ | 振り分け先で変わるため、固定最大値を設定せず自動値 |
| Responses | モデル別の上限を検査し`store=false`へ設定 | `response`／`pdf`とも各モデル128,000。クライアントが会話入力を送る |
| FLUX 2 flex／pro | profileとモデルを照合し、profileの固定`backendUrlTemplate`へ書換え | 既定flex、proは明示選択。提供元の出力1枚は維持し、1024固定を撤廃 |
| GPT Image | 本文またはURL配備名を照合しAzure用URIへ書換え | 生成3モデル、指定時の`n`は1〜10。1024・低品質固定を撤廃し、寸法・品質・編集入力は提供元の制約に従う |
| Embedding・Rerank | モデル・型・非空入力・次元を検査 | Embeddingの16件固定を撤廃。`top_n`は任意、指定時は文書数以内。検索側のモデル・次元・版を合わせる |
| ファイル文字起こし | URLの配備名とmultipart形式を検査 | `gpt-transcribe`。Gatewayの4 MiB枠を撤廃し、提供元のファイル制約に従う |
| Speech | SSML／WAVを検査し、Speech用URI・ヘッダー・Entra認証形式へ変換 | TTS 8 KiB・STT 1 MiBの独自枠を撤廃。DTD／ENTITY拒否、対応形式・提供元の時間制約は維持 |
| Decision | 配備名と型付き要求を検査し、固定の`/systemone`へ転送 | `decision-1`。8 KiBの独自枠を撤廃 |
| PII | `{text,language}`を検査しLanguage用要求・結果へ変換 | 固定のPIIモデル版、対応言語と警告コードを照合。生成MCPも同じREST処理を使う |
| Graph /me | CMの本人接続を検査し、本人のユーザー情報を固定`$select`で取得 | 氏名・所属・連絡先・アカウント状態など12属性。任意のGraph URLや属性追加は不可 |
| Document Analysis | raw binaryの開始POSTと、署名チケット・同じ呼出主体での状態GETを提供 | 入力500,000,000 bytes、結果500 MiB、受付202→状態GET→完了200。[文書解析の契約](document-analysis.md) |

通常のAI APIの`maxRequestBytes`はAPIM Classicのバッファー上限**500 MiB（524,288,000 bytes）**に揃える。JSONはBase64や構造を含むUTF-8本文全体、multipartはファイル以外の境界・項目も含む。提供元のモデル・形式・料金プランによる上限が小さい場合は提供元が拒否するため、500 MiBは各モデルの受付保証ではない。Content-Lengthは必須化せず、chunked入力も実際の受信容量で検査する。[APIMの技術上限](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits#api-management-gateway-runtime-limits)

出力上限の正本は [model-capabilities.json](../../apps/gateway/apim/model-capabilities.json)。HTTPの出力トークン項目は省略でき、指定時は選択モデルの公表上限を検査する。不明な固定上限を作らない。Realtime WebRTCの作成時の512は初期設定であり、モデル上限4,096とは区別する。WebSocket確立後やWebRTCのDataChannelはAPIMポリシーで検査しないため、Portalが送信する設定と、Gatewayが全クライアントへ強制する制限は同一ではない。

WAFは対象AI完全パスだけ2000 KiBまで本文を検査し、サイズ超過だけでは拒否しない。認証・管理系の既定防御は維持する。AppGWは既存の設定可能な600秒、FWの通信先・ポート・IDPSは変更しない。Chat／Responsesの同期・SSEも維持する。[WAFの範囲](../testing/waf-compatibility.md)

APIMの`forward-request`は引き続き`timeout="240"`・`buffer-response="false"`。240秒はバックエンドの応答ヘッダー待機であり、SSE全体の処理時間上限とは区別する。[forward-request仕様](https://learn.microsoft.com/en-us/azure/api-management/forward-request-policy)

Portalは通常HTTP／SSEに独自の総処理時間枠を設けない。Node HTTPサーバーのヘッダー受信60秒・本文受信300秒は低速送信への通信保護として維持する。後者はアップロード完了までの期限であり、受信後のモデル処理やSSEの総時間には適用しない。

購読キーとBearerの認可、明示された回数・トークン枠、本人接続の有効期限と更新リースは維持する。容量の緩和で、停止キー・無効なトークン・無権限の操作・失効済み接続を許可しない。

### Live・Realtime・文字起こしWSS

| 接続契約 | ポリシーが実施する内容 | 接続後に扱う内容 |
|---|---|---|
| Realtime WebRTC | SDP検査、短期資格情報を使う作成、署名した所有者ticket、終了時の所有者・期限・call ID照合 | WebRTC音声・Realtimeイベント |
| Realtime WSS | 購読キーまたはBearer検証、許可model query、固定上流、接続回数上限 | Realtime会話イベント |
| Realtime Observer | 署名ticketとcall所有者の照合後、既存callへ接続 | sidebandイベント |
| **Live WebRTC** | `{sdp,instructions?,voice?}`を検査し、固定モデルと`delegation.type=client`で上流sessionを作成 | WebRTCメディアとLiveイベント。応答は`{sessionId,sdp}` |
| **Live WSS** | 購読キーまたはBearer検証・固定`/openai/v1/live/sessions`・接続回数上限 | `session.start`でモデルを指定、PCM音声・transcript・`session.close`／`session.closed` |
| **文字起こしWSS** | 購読キーまたはBearer検証・固定上流・`intent=transcription`・接続回数上限 | `session.update`でモデルを指定、音声入力・途中／確定テキスト |
| Voice Live | Speechサービス用の固定接続先・query・MI認証 | Voice Live固有の音声・WebRTC制御 |

Live作成は共通の500 MiB枠と型・SDP形式を検査し、instructionsの独自文字数枠を設けない。初期voiceは`marin`。失敗入力は400、形式が不正な上流の成功応答は502へ整える。[Live作成のXML](../../apps/gateway/apim/policies/live-create.xml)

WSSの`websocket.session.models`は接続後イベントで使うモデルのクライアント契約を表す。APIMが検査するのは接続開始時の要求である。frame内のモデル・音声・設定とセッション継続時間は、アプリと上流サービスで管理・検査する。HTTP要求回数と音声セッションの利用量は別に計測する。[Microsoft公式のWebSocket制約](https://learn.microsoft.com/en-us/azure/api-management/websocket-api)

WSSは接続時に設定済みの購読キーヘッダー、または`Authorization: Bearer`を送れるSDK・アプリサーバーから利用する。ブラウザは必要な認証ヘッダーを送れるBFF等を経由する。ブラウザのWebRTCは、アプリのサーバーがGatewayの作成APIを呼び、SDP answerをブラウザへ返す。

WebRTCの接続制御はAPIMを通り、音声メディアは端末とサービスのICE経路を通る。受入ではその宛先・ポート・端末ネットワークの到達性を確認する。[Azure Live WebRTC](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/gpt-live-webrtc)

## Credential Managerとの接合

CMは、必要なAPIが利用する共通の下流OAuth機能とする。`graph-profile-v1`のGraph APIと、CMが必要なMCPに適用できる。入口の購読キーまたはEntraアクセストークンによる認可と、下流の本人OAuth接続を分ける。

本人接続は、CM共通APIでサインインを開始し、確認済みの呼出主体・本人・provider・接続・期限をセッションに結び付ける。キーでは同じ購読、Bearerでは同じテナント・ユーザーを照合し、両方式の間でセッションの所有者を移さない。対象APIへ`X-Core-Connection-Session`を送る。任意のUPNや接続名の指定を本人確認の代わりに使わない。

サービス共有接続は、サーバー設定のproviderとconnectionを使う。どちらもCMの`get-authorization-context identity-type=managed`がAPIM自身の接続利用権限を確認し、CMが下流tokenを取得・更新する。本人セッションの検査は、その前のCoreポリシーで行う。

初回のログインURL提示、サインイン、状態照会、元要求の再実行は[CM接続手順](../setup/credential-manager-login.md)を参照する。API応答だけで利用者のブラウザを開くとは扱わない。Dify等は対応ツール／プラグインで連携する。

接続再認証は409、下流の権限不足は403として区別し、下流の生エラー・token・Cookie・認証URLを業務応答へ流さない。

## GUIで確認する場所

1. APIMの「名前付きの値」でキーヘッダー、上流audience、入口経路、Realtime署名鍵の参照を確認する。
2. APIs → All APIsのコードエディターで共通の購読キー検証とEntraアクセストークン検証を確認する。認証用テナント・API audience・`Gateway.Invoke`は適用された`validate-azure-ad-token`を見る。
3. 対象API → All operationsで操作制約・下流認証を確認する。CMを使うAPIは接続セッションの照合処理も確認する。
4. Operationのコードエディターで固定URI・Live／Realtime／PII等の固有処理を確認する。WSSは`onHandshake`を見る。
5. Policy fragmentsで`core-request-context`・`core-audit-authenticated-subject`・`core-gateway-telemetry`のC#説明と、`core-trusted-ingress`の入口確認を読む。`core-rate-limit`・`core-credential-connection`等の個別処理もここで確認する。
6. Credential managerでprovider、connection、本人同意、APIM MIのaccess policyを確認する。

XMLには処理の目的・入力・失敗時の動作を説明する日本語コメントを記載する。登録前に計画を確認し、適用後のXMLを読み直して照合する。

監査出力の認証主体、要求context、AppGWログとの相関とqueryの採用規則は[監査の識別情報](audit-identification.md)を参照する。監査出力の追加と保存設計、資材の実装と対象環境の適用・受入を分けて扱う。
