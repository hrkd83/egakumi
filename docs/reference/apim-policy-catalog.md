# APIMポリシー一覧

2026年11月21日更新。Graphと文書解析を含む標準19 API、生成MCP、CM補助APIの登録資材を説明する。業務APIの同じURLで購読キーとEntraアクセストークンの両方を受け付ける。登録資材の構成と対象環境への反映・受入状況は分け、実際の適用と接続結果は環境ごとの保護された証跡で確認する。

## 要求が通る順序

1. APIMがAPI・Operationを特定する。購読キー付き要求はAPIM標準の購読検証を使い、キーなし要求は専用の購読不要Productを通して共通認証へ進む。
2. APIポリシーが固有の設定データを保存し、`core-api-settings`で監査・制御の2変数を準備してから、`<base/>`で全体ポリシーを継承する。WebSocketは接続開始の`onHandshake`で実行する。
3. 全体ポリシーが共通の要求情報と信頼した入口経路を確認する。設定済みキーヘッダーがあれば購読キー、なければ`Authorization: Bearer`のEntraアクセストークンを検証する。無効なキーをBearerで救済しない。
4. APIポリシーが`<base/>`直後に`core-api-request-preparation`で要求ヘッダーを整え、共通認証の完了を確認してから、Operation、モデル、入力形式・サイズ、生成上限を確認する。
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
| `core-api-settings` | `fragments/settings/core-api-settings/core-api-settings.xml` | `fragments/settings/core-api-settings/core-api-settings.cs` | 各APIの`base`前で設定を読み、`gatewayAuditSettings`と`gatewayControlSettings`を準備する。共通InboundのSTEPとは別のAPI側処理 |
| `core-api-request-preparation` | `fragments/request/core-api-request-preparation/core-api-request-preparation.xml` | なし（XMLのみ） | `gateway.xml`・`protocol.xml`・`mcp.xml`の`base`直後で内部IDヘッダー除去、APIM要求ID設定、共通認証の完了確認を順に行う |
| `core-query-observation` | `fragments/request/core-query-observation/core-query-observation.xml` | `fragments/request/core-query-observation/core-query-observation.cs` | 認証完了は先行する要求準備が確認する。HTTP操作またはMCPのメソッド・公開相対pathからラベルを選び、`gatewayQueryObservation`へ`route`・`httpMethod`・`presence`・`duplicates`だけを保存する。許可判定・mode・拒否応答は持たない |
| `core-api-input-validation` | `fragments/request/core-api-input-validation/core-api-input-validation.xml` | `fragments/request/core-api-input-validation/core-api-input-validation.cs` | 明示した対象操作でContent-Type宣言の存在・1値・非空MIME部分を確認し、`gatewayApiInputValidationPassed`へ真偽値を保存する。MIME一覧比較・メソッド再確認・query拒否は行わず、400応答と拒否監査は共有HTTP APIテンプレートの直後の分岐で行う |
| `core-api-operation-settings` | `fragments/request/core-api-operation-settings/core-api-operation-settings.xml` | `fragments/request/core-api-operation-settings/core-api-operation-settings.cs` | 操作の基本設定へ必要なモデル別差分を重ね、選択済み設定と任意の監査ラベルを`gatewayOperationSelection`へ保存する |
| `core-api-authentication-result` | `fragments/authentication/core-api-authentication-result/core-api-authentication-result.xml` | `fragments/authentication/core-api-authentication-result/core-api-authentication-result.cs` | HTTP APIで共通認証済みの購読または社員情報と監査ラベルを`gatewayDecision`へまとめる。内部情報を引き継げなければ、呼び出し元が監査と500応答で停止する |
| `core-model-limits` | `fragments/request/core-model-limits/core-model-limits.xml` | `fragments/request/core-model-limits/core-model-limits.cs` | 選択済みの操作設定で本文サイズと項目条件を確認し、`gatewayLimitDecision`へ結果を保存する。内部設定JSONの読取り失敗は500。以降の制限条件は続く棚卸の対象 |
| `core-speech-ssml-guard` | `fragments/request/core-speech-ssml-guard/core-speech-ssml-guard.xml` | `fragments/request/core-speech-ssml-guard/core-speech-ssml-guard.cs` | TTS本文を一度読み、選択済み上限との実サイズ比較と既存のDTD／ENTITY宣言検査を行い、`gatewaySpeechSsmlDecision`へ結果を保存する。監査・400／413応答はHTTP APIの呼び出し元が行う |
| `core-document-input-validation` | `fragments/request/core-document-input-validation/core-document-input-validation.xml` | `fragments/request/core-document-input-validation/core-document-input-validation.cs` | modelの必須・単一値・非空と`.`／`..`の拒否を本文読取りなしで確認する。モデル一覧照合・設定JSON再読取りは行わず、モデルの利用可否は提供元が判定する。内部モデル情報の引渡し失敗は500・OPERATION_SETTINGS_INVALID。監査・400／500応答はHTTP APIの呼び出し元が行う |
| `core-query-telemetry` | `fragments/audit/core-query-telemetry/core-query-telemetry.xml` | `fragments/audit/core-query-telemetry/core-query-telemetry.cs` | `route`を選択した観測ラベルとして記録し、要求情報・認証済み利用者・query概要・処理段階を関連付ける。⑥で件数区分を削除し、⑦でmode・拒否専用phaseの新規出力を撤去する。イベント名`query-guard`、schema 2とaudit schema 2は維持する |
| `core-request-context` | `fragments/audit/core-request-context/core-request-context.xml` | `fragments/audit/core-request-context/core-request-context.cs` | `gatewayRequestContext`：受信時刻、要求・API・操作のID、操作のひな形、接続元と相関候補 |
| `core-trusted-ingress` | `fragments/ingress/core-trusted-ingress/core-trusted-ingress.xml` | `fragments/ingress/core-trusted-ingress/core-trusted-ingress.cs` | 必須のHTTP入口ヘッダーを判定し、欠落・不一致・重複なら既存監査へ記録して403を返す。API固有の変数で免除しない |
| `core-credential-browser-exception` | `fragments/authentication/core-credential-browser-exception/core-credential-browser-exception.xml` | `fragments/authentication/core-credential-browser-exception/core-credential-browser-exception.cs` | `gatewayCredentialBrowserException`：接続ログインの開始・戻り先の受付への一致を判定する。本人の認証成功を示す値ではない |
| `core-common-authentication` | `fragments/authentication/core-common-authentication/core-common-authentication.xml` | `fragments/authentication/core-common-authentication/core-common-authentication.cs` | 通常要求のキー／Bearerを認証する。購読、トークン、本人、会社設定を確認し、成功を後続へ伝える |
| `core-entra-authentication-error` | `fragments/authentication/core-entra-authentication-error/core-entra-authentication-error.xml` | なし（短いXML式のみ） | 標準Entra検証の失敗を既存監査と401応答へつなぐ。共通`on-error`から呼び出す |
| `core-audit-authenticated-subject` | `fragments/audit/core-audit-authenticated-subject/core-audit-authenticated-subject.xml` | `fragments/audit/core-audit-authenticated-subject/core-audit-authenticated-subject.cs` | `gatewayAuthenticatedSubject`：認証で確認できた購読または社員。APIの利用許可とは区別する |
| `core-gateway-telemetry` | `fragments/audit/core-gateway-telemetry/core-gateway-telemetry.xml` | `fragments/audit/core-gateway-telemetry/core-gateway-telemetry.cs` | 要求・認証・利用許可等の監査ログを出力する。専用C#式は認証主体の読取と整合性確認を担当する |

表のパスは`apps/gateway/apim/policies/`を起点とする。共通フローの`service.xml`は直下に置き、初めて棚卸または改修する部品は、その作業時に`fragments/`の下で監査（`audit`）、入口確認（`ingress`）、認証（`authentication`）、API要求の準備・判定（`request`）へ分ける。Azureの部品名・部品フォルダ・XML・直接埋め込むC#式の名前を揃え、同じ部品フォルダにXMLとC#を置く。各C#の直接使用先は同名XMLひとつに限定する。XMLは部品全体のフローを、C#はそこから分離した式を所有する。`core-trusted-ingress.cs`は入口の判定条件、`core-gateway-telemetry.cs`は認証主体の読取部分であり、XMLの処理全体をC#へ移した意味ではない。短いXML式だけの部品には空のC#を作らない。フォルダ全体の役割と移設手順は[管理構成](../../apps/gateway/apim/policies/README.md)を参照する。

`core-common-authentication.cs`の5つの名前付き区画は、同名XMLの各式へ個別に埋め込む。キー検査からBearer検査までを全要求で一括実行する意味ではない。キー／Bearer共通の成功を`gatewayCommonAuthenticationValidated`、Bearerのトークン・本人・会社設定までの成功を`gatewayEntraAuthenticationValidated`で後続に伝える。2変数は要求ごとの検証結果であり、配備設定の有効／無効を示すスイッチではない。

未着手のCM・接続応答の監査は、既存XML名と`expressions/legacy/audit-subject-observation.cs`を移行期間だけ維持する。query監査は専用XML／C#へ整理し、共通利用者情報の式を生成時に説明付きで組み込む。初めて扱う部品は、全制御の判断が終わるまで待たず、その作業時に責務ごとの配置・専用の同名XML／C#・Azureの日本語説明・処理説明を揃える。専用C#式を別XMLへ直接流用しない。APIテンプレートは`api-templates/`、操作別ポリシーは`operation-policies/`、生成時に差し込むXML部品は`snippets/`へ順に移設する。棚卸完了までに旧資材・旧参照・旧構成専用の生成処理を撤去し、現行構成の新規構築・再配備・検証が成立することを確認する。`legacy`を別名で温存しない。既存のAPIM部品名やAPIの接続先は変更しない。

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

読み込み処理は`core-api-settings`へ集約する。各APIが持つBase64データは、復元するとAPI種類・監査JSON・制御JSONの3行になる。各JSONは生成時に1行へ整形し、内部の改行はエスケープする。二重のJSON化を避けて配備サイズを抑え、JSON文字列をそのまま渡す。通常HTTPの監査だけ従来と同じ操作別backendの選択を行う。

```xml
<set-variable name="gatewayApiSettingsSource" value="__API_SETTINGS_BASE64__" />
<include-fragment id="core-api-settings" fragment-id="core-api-settings" />
<!-- === -->
<base />
<!-- === -->
<include-fragment id="core-api-request-preparation" fragment-id="core-api-request-preparation" />
```

`__API_SETTINGS_BASE64__`は生成前の置換位置であり、AzureにはAPIごとの生成済みデータが入る。CM管理・ブラウザー受付のAPIは、このAIモデル用設定を渡さず、従来の継承を維持する。

| 変数 | 設定する場所 | 内容 |
| --- | --- | --- |
| `gatewayRequestContext` | All APIsのInbound先頭から`core-request-context`を実行 | 要求・API・操作ID、受信時刻、HTTPメソッド、操作のひな形、接続元と相関候補。[監査識別情報](audit-identification.md)の出力契約に従う |
| `gatewayAuthenticatedSubject` | 共通認証後に`core-audit-authenticated-subject`を実行 | 確認済みの購読または社員の識別情報。APIごとの利用許可とは別に監査へ渡す |
| `gatewayCredentialBrowserException` | STEP2直後、共通認証の分岐前に`core-credential-browser-exception`を実行 | CM設定の有無と専用API・操作・HTTPメソッド・元のパスから判定したtrue／false。共通認証を省略する受付かを示す |
| `gatewayApiSettingsSource` | 対象APIのInbound、`<base/>`の前 | API定義から生成したBase64設定データ。API種類と監査・制御のJSON文字列 |
| `gatewayApiSettings` | `core-api-settings` | Base64を一度だけ復元した3行の設定データ |
| `gatewayAuditSettings` | 対象APIの`<base/>`前で`core-api-settings`を実行 | サービス分類、接続先の種類と認証方式、操作別の接続情報、使用量計測の対象、WebSocketモデル、Agent、計測の役割 |
| `gatewayControlSettings` | 対象APIの`<base/>`前で`core-api-settings`を実行 | `operationId`、`operationConstraints`、`tokenLimitOperationIds`。操作・モデル設定の選択／入力の検査とトークン計数が参照するAPI固有設定 |
| 認証結果の変数 | All APIsの共通認証処理 | 検証済み購読・ユーザー・トークンと認証完了状態。API側の自己申告を認証結果として扱わない |

監査情報と制御設定は、監査項目の追加・整形と、認可・入力制約・計数の判定を独立して扱うために別の変数へ分ける。`gatewayAuditSettings`を作る処理自体は入力を拒否したり本文を書き換えたりしない。制御設定を参照する後続ポリシーの動作は、それぞれ別に確認する。変数の分離だけで既存の許可条件、モデル機能、接続先、本文変換、計数対象を変更しない。

通常HTTPの`core-api-operation-settings`は、準備済みの`gatewayControlSettings`から操作IDに対応する基本設定を取得する。任意の`modelSettings`はモデル名をキーとした差分表で、選択したentryの項目を基本設定へ上書きする。入れ子の表は項目全体を置換し、部分的に混ぜない。結果を`gatewayOperationSelection`へ渡す。

モデル別設定も有効なaliasもない操作は、本文を読まず基本設定を返す。モデル名を使う操作では、操作直下の`maxRequestBytes`と元の本文バイト数を先に確認し、JSONの文字列`model`を読む。別名は操作直下の`modelAliases`で一意に解決する。モデルに個別entryがなければ基本設定を使う。

設定取得・表の型・本文サイズ設定の不備は500・`OPERATION_SETTINGS_INVALID`、JSON解析不備は400・`INVALID_REQUEST_BODY`、model欠落・非文字列は400・`INVALID_REQUEST_MODEL`に分ける。元の本文超過413を維持する。モデル選択部品では`allowedModels`を照合せず、差分がないモデルは基本設定を返す。未知・空文字のモデルを一覧にないという理由で先に403にせず、本文や資格情報も監査へ保存しない。

選択失敗後の監査・HTTP応答では、本文超過を`REQUEST_TOO_LARGE`・413・`Request too large`へ揃える。失敗結果だけに`status`と`code`を設定し、使われない初期403を撤去する。本文・modelの400と設定不備500の原因コード、要求ID、監査の出力項目と拒否条件は維持する。新規出力で本文超過をprofileの権限不足と取り違えないようにする。

次の`gatewayDecision`先頭では、API定義の`authorization.mode`確認を配備前の検査へ任せ、同じ値を要求ごとに復元・解析して確認する処理を撤去する。キー／Bearerの結果を引き継ぐ分岐は、APIMの検証済み購読`context.Subscription`の有無で選ぶ。購読があれば購読IDを使い、なければ共通認証済みのBearer本人情報を使う。キー優先と不正キーの拒否は共通ポリシーが決定し、後続でキー用ヘッダーを読み直して方式を決めない。後段の`gatewayTargetAllowed`に残るBase64復元・モデル／本文照合は別の棚卸対象である。

Bearer側に入った時点で購読なしは確定しており、共通認証の完了は先行する`core-api-request-preparation`が確認するため、この2条件の再確認を撤去する。社員情報を使う前の`gatewayEntraAuthenticationValidated`確認は維持する。共通認証の未設定・falseは先行部品で503となり、Bearer認証成功が確認できなければ本人情報の引き渡しを拒否する。

HTTP APIの`gatewayDecision`では、共通認証が作った社員情報JSONを読み、確認・整形済みの`tenantId`と`principalId`をそのまま引き継ぐ。`schemaVersion`とGUID形式・空GUIDの再検査、GUIDの再整形を撤去し、本人情報の検証を共通認証へ集約する。`callerId`は呼出者、`allocationId`は利用回数・トークン数を数える利用枠の帰属先を示す。共通認証が両方を同じ社員のIDとして確認しているため、HTTP側のID再構築・再照合も撤去し、それぞれの確認済み値を引き継ぐ。正常時の出力項目と値、社員単位の利用枠と監査は変えない。この整理はHTTPテンプレートを対象とし、protocol／mcp側の既存確認は続く棚卸で扱う。Azure反映・読戻しは完了した。キー／BearerのResponses JSON・SSE、無効Bearerの401と対応する利用者・応答・AppGW監査の照合を確認した。他モデル・transportや残る異常系の受入は未完である。

監査用ラベルの転記では、先行する操作・モデル設定の準備が成功した結果`gatewayOperationSelection`を直接読み、不要な存在確認の分岐を撤去する。`settingId`が空なら記録せず、値があれば既存の監査項目`profileId`へ転記する。これは管理側が付ける操作・モデル設定のラベルであり、クライアントのprofile選択や購読の利用枠設定を示す値ではない。監査の項目名と出力形式は維持する。

HTTP APIが認証結果を引き継げない場合は停止を維持し、500・`policy-error`・`AUTHENTICATION_PROCESSING_ERROR`として記録する。対象はBearer認証の完了記録の欠落・falseと、内部の社員情報JSONの読取りなどで発生する例外である。応答理由は`Gateway processing failed`とし、要求IDを返す。例外の詳細や生JSONは出力しない。正常時の認証結果と、共通認証の401、後段の`gatewayTargetAllowed`による403は変えない。操作・モデル設定の準備結果を読む処理の例外や、共通の`on-error`の扱いは、この失敗分岐と別に扱う。

棚卸済みの組み立てコードは`fragments/authentication/core-api-authentication-result/`の同名XML・C#へ分離する。HTTPテンプレートはその部品を呼び、短い失敗監査・500応答のフローは直後に残す。XML・C#・Azureフラグメントの名前を揃え、C#の説明コメントを生成XMLへ保持する。この部品へ他の監査ソースを展開せず、フラグメント同士の入れ子も作らない。[本体と入出力](../../apps/gateway/apim/policies/fragments/authentication/core-api-authentication-result/README.md)を参照する。

続く`core-model-limits`の資材は、初回棚卸時に`fragments/request/core-model-limits/`の同名XML・C#へ移し、直下の旧`model-limits.xml`を撤去する。Azure登録名は維持し、説明とコード内コメントを日本語へ揃える。冒頭の`gatewayOperationConstraint`の取得・JSON解析を独立した失敗処理へ分け、例外時は500・`LIMITS_NOT_CONFIGURED`を返す。上限設定`maxRequestBytes`は整数・1〜500 MiBの条件を維持し、`Int64.TryParse`で一度だけ`long`へ変換して使い回す。変換失敗や桁あふれ・範囲外も、本文を読む前に設定不備500へ揃える。先行処理が確認する操作共通上限と、ここで確認する選択後の上限は保証範囲が異なる。本文検査以降の例外400、本文超過413、モデルや本文項目の制限は変更しない。配置・名前・コメントの整備を、この部品の全制御の棚卸完了とは扱わない。[現在の処理と棚卸状態](../../apps/gateway/apim/policies/fragments/request/core-model-limits/README.md)を参照する。

本文形式`bodyFormat`の補完は、標準・追加APIが通る配備前の正規化処理へ統一する。実行時は文字列の`json`・`binary`・`multipart`・`text`・`none`を一度だけ確認し、欠落・不正値は本文を読む前に500・`LIMITS_NOT_CONFIGURED`で停止する。内部設定の検査であり、クライアントのContent-Typeの許可条件や本文パーサーは追加しない。内部JSONと形式の確認後、`none`なら本文と`maxRequestBytes`を読まず成功する。本文の存在禁止ではなく、使わない上限の検査を省く順序である。本文を扱う4形式の上限確認と、配備前の上限設定検査は維持する。

`core-model-limits`の`binary`・`multipart`・`text`では、MIME許可一覧比較と、それによる415を撤去する。提供元が対応する形式を基盤の一覧更新漏れで止めないため、受付形式は提供元が判断する。申告サイズの検査と対象操作の前段Content-Typeヘッダー検査を維持し、実受信サイズを確認するかは操作設定で指定する。`contentTypes`設定はAPI定義生成・SpeechのSSML検査対象選択などに使うため残す。Documentの個別形式検査は下記の入力棚卸で撤去する。SpeechのWAVヘッダー上書き、WAFの形式制御は後続の棚卸対象であり、任意形式の経路全体の受付は未確認である。

同じ3形式で、Content-Lengthを数値化できない・負数の場合に400にする独自の形式検査を撤去する。ヘッダーによる本文の区切り・構文の扱いはHTTPの受信処理に任せる。読み取れた申告サイズが上限を超える場合の早期413と、ヘッダー省略の許可を維持する。実受信サイズを確認するかは操作設定で指定する。ローカルのポリシー式試験は、HTTP受信層の不正要求の扱いを再現しない。

申告サイズによる早期413は、この部品での本文のバイト配列読取りと後続処理を省くため維持する。AppGWやAPIMが先に行った受信・保持処理まで省ける保証はない。省略・数値化できない値・上限以下は、操作設定に従って実受信サイズの確認か、本文を読み出さない成功へ進む。

汎用の操作設定`requestBodySizeCheck`は、非JSONの共有サイズ確認方法を指定する。未指定／`actual`は従来の実サイズ検査を維持し、`declared`は早期413の後にこの部品では本文を読み出さず成功する。標準のSpeech STT・音声ファイルの文字起こし・画像編集・Speech TTS・Document解析の5操作に`declared`を指定する。TTS以外の4操作はサイズ検査だけの全量読取りを撤去し、申告がない場合など、この部品による実サイズの413はなく、受付サイズは提供元が判断する。共有部品の成功codeは`DECLARED_SIZE_ONLY`とし、実サイズ確認後の`BOUNDED_UPLOAD`と区別する。TTSは直後の専用部品で実サイズも確認する。API名・操作名はサイズ検査のC#へ固定しない。ほかの本文読取りが共有テンプレートに残るため、完全な無バッファ転送や、Azure自身の技術制約を今回の変更だけで確認したとは扱わない。

TTSでは、従来のSSML検査対象から生成した`gatewayControlSettings.speechSsmlOperationIds`をHTTP APIが読み、`core-speech-ssml-guard`を共有サイズ検査直後、利用枠・下流認証準備・`accepted`監査より前で呼ぶ。旧Operation側の`<base />`後には重ねて呼ばない。同名XML／C#を正規フォルダへ置き、直下の旧`speech-ssml-guard.xml`は撤去する。部品は本文を一度だけ保持して読み、検証済みの`gatewayOperationConstraint.maxRequestBytes`との実サイズ比較を文字列化前に行い、通過後に既存のSSML宣言を走査する。結果は`gatewaySpeechSsmlDecision`の`allowed`・`status`・`code`へ保存する。成功`SPEECH_MARKUP_ALLOWED`、実サイズ超過`REQUEST_TOO_LARGE`／413、宣言拒否`INVALID_SPEECH_MARKUP`／400を区別し、監査・HTTP応答はAPI側へ残す。内部JSON読取り等の例外はAPIの既存`on-error`へ進め、同じ上限設定の検査や部品専用の設定500コードは追加しない。

この集約で、TTSの共有サイズ確認とSSML確認による本文の二重読取りを一度にする。UTF-8／16／32の判別、コメント・CDATA等を除外した宣言検査、公開400`InvalidSpeechMarkup`、元本文の保持を維持する。全文の文字列化・小文字化は残り、XMLパーサーや実体解決は追加しない。TTS集約時点ではDocument・JSONの未棚卸処理やWAFを変更していない。TTSのAzure反映・読戻しは完了した。キー／BearerのTTS正常WAV応答とDTD宣言の400、および対応する監査照合を確認した。実サイズ超過413など残る異常系は未実施である。[TTS部品の処理と対象](../../apps/gateway/apim/policies/fragments/request/core-speech-ssml-guard/README.md)を参照する。

⑬の版の正本は[Document公開Schema](../../apps/gateway/apim/schemas/document-analysis.json)のapiVersion.enumの単一値である。標準API定義の版2か所の重複を削除し、Python正規化で開始・状態取得へ補完する。明示不一致は配備前に拒否する。Bicepは正規化済み`entry.constraint.apiVersion`を`__DOCUMENT_API_VERSION__`から開始URI・受付URL照合・状態GETへ展開し、⑱では結果照合が、共通APIの準備済み`gatewayOperationConstraint`から`apiVersion`・`maxResponseBytes`を既存outbound try内で直接読む。Status専用`documentSettings`と埋込みBase64の復元・Bicep展開を撤去し、baseURL・API版markerと上限・版・チケット・結果の条件を維持する。⑲ではStatusの操作設定`gatewayOperationConstraint`の取得・JSON解析失敗だけを500へ分類し、応答前に既存`core-gateway-telemetry`で`policy-error`・`OPERATION_SETTINGS_INVALID`・500を1回記録する。㉙では開始・状態取得の認証結果`gatewayDecision`の取得・JSON解析と認証方式・owner引渡し不備だけを500・`policy-error`／`AUTHENTICATION_PROCESSING_ERROR`へ分け、⑲の操作設定不備と区別する。提供元本文・版・容量などの既存502は維持し、網羅的な再検査は追加しない。Portalの公開表示・結果検査も既存生成物を通じて同じ版を使う。対応版・公開仕様・チケット形式は維持し、実行時の追加読取りや検査は増やさない。

Document入力の棚卸①〜㉝では、MIME一覧・先頭バイト・独自の空本文・Content-Encodingの拒否と、サイズ確認だけの全量読取りを撤去する。共有の申告サイズ500,000,000 bytes超の413は維持し、共通Content-Type宣言検査には追加しない。原本と提供元JSONを本文変換なしで送り、文書とモデルの利用可否は提供元が判定する。modelの必須・単一値・非空と`.`／`..`の400を残し、2モデル一覧による拒否と、そのためだけの設定JSON再読取りは撤去する。model入力不備は400・`DocumentInvalidInput`、内部変数のモデル引渡し失敗は500・`OPERATION_SETTINGS_INVALID`／`DocumentAnalysisUnavailable`へ分け、利用回数制限・下流接続準備・`accepted`監査より前に止める。先行する操作設定・共有サイズ設定の検査と、固定接続先・チケット・返却URIとのモデル照合・応答の構造条件は維持する。開始と状態取得の操作ポリシーは`operation-policies/document-analysis/`へまとめる。他クエリの有無による一律400を撤去し、開始POSTではmodelをクエリから削除した後、pathだけの標準URI書換えと`copy-unmatched-params=true`でpages・featuresなどを転送した後、標準`set-query-parameter`でapi-versionは対応版へ上書きし、stringIndexTypeは`exists-action=skip`で指定値を保持して未指定時だけ`unicodeCodePoint`を補完する。API版は公開Schemaの単一値（現在`2024-11-30`）へ一元化し、⑭で文字位置の単位の指定を受け付け、不正値・複数値の適否は提供元へ任せる形へ整理する。㉑では状態取得GETの追加クエリを無視して同じジョブを取得し、クエリの有無だけで403にしない。チケット・UUID・本人・期限の照合、固定URI・対応版とURLへのAPIキー指定の共通拒否は維持する。㉒では`copy-unmatched-params=false`による未使用クエリの非転送を維持し、目的コメントを整える。対応版の結果取得GETはapi-versionのみを使う。将来正式なGET追加クエリが必要になれば、公開結果契約と合わせて見直す。WAF設定やクエリ名・値の監査出力を追加しない。①〜㉝の変更はAzure反映・読戻し照合を完了した。独立して作成した小文書の原本・Base64入力は、キー／Bearerで202受付から本文なしGETの200まで一部確認した。申告サイズ境界・内部障害500・期限切れ410など残る受入は未実施である。Portalの原本アップロード・2モデル選択・BFF制約は今回変更しない。[公開契約](document-analysis.md)を参照する。

モデル選択前のサイズは配備時に操作上限へ集約し、要求ごとの候補一覧集計を省く。既存のモデル別上限は差分設定へ保持する。[APIM ClassicのBuffered payload上限](https://learn.microsoft.com/en-us/azure/api-management/api-management-gateways-overview)の500 MiBは維持し、後段の重複検査は別に棚卸する。

クライアントはprofileを選ばず、API・操作とモデル名を指定する。旧ヘッダーは下流転送前に削除する。内部profiles・defaultProfile・modalitiesも撤去し、一つのモデルから一つの設定を取得する。FLUXの固定URI、出力token項目とモデル上限を維持し、任意の`settingId`を既存監査の`decision.profileId`へ明示転記する。購読利用枠profileやCMの`profileName`は変更対象外である。

構造整理後の棚卸で、モデル選択部品の登録一覧による先行403を撤去する方針を採用した。後段の`core-model-limits`には能力表がないモデルの拒否、`gatewayTargetAllowed`にはモデル一覧の照合、FLUXには固定URIの選択が残る。これらの棚卸と関連する整備をまとめて反映するまで、登録外モデルが下流へ届くとは扱わない。本文・toolsの制約も続く棚卸で個別に判断する。ローカルの整備・検証とAzure反映・読戻し・実疎通は区別する。[操作設定の部品](../../apps/gateway/apim/policies/fragments/request/core-api-operation-settings/README.md)を参照する。

共有fragmentとAPIポリシーを同じ資材の構成にそろえて配備する。今回の移行は新しい設定fragmentを先に登録し、次に各APIの初期化を切り替える。後続が参照する2変数と共通認証は変えず、移行途中の旧APIも同じ2変数を準備する。全APIの更新後に、分離した設定を使うことと、キー・Bearer両方式の接続を確認する。監査設定には制御項目を格納しない。

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

`gatewayAuditSettings`と`gatewayControlSettings`の初期値はAPI契約から生成する。実行時にはOperation別の接続情報を選び、選択済みの操作・モデル制約を反映するため、要求内の加工結果を実行時変数へ保持する。固定JSONを名前付きの値へ移しても、この変数は必要となる。現在はAPI契約とポリシーを同じ版で照合する設定として生成資材で管理し、ポリシー本文と独立して運用する共通固定値には名前付きの値を使う。

実行時変数の定義は、GUIのAll APIsまたは対象APIのAll operationsのXMLで確認する。配備時には置換用文字列が設定データのBase64文字列に置き換わるが、画面に実行後の変数値が表示されるわけではない。登録資材の変更と実環境への反映は別に検証する。

旧生成元が一部のAPIしか含まない既存環境では、読み取ったXMLを生成元の証明として代用しない。明示した全APIの整合工程では、既知の旧生成元を独立に照合し、残りのAPI・操作・schema・tool・fragment・接続先をバックアップと競合検出の証跡として読み取る。社員Productへの所属を一時解除して現在の資材を配備し、完全なv2生成元と実設定が一致した後に新しい生成元を保護領域へ記録する。共通認証の切替と社員Productへの所属復帰は最後に行う。既存キーのscope・状態は事前確認し、Foundryの容量とFirewallルールをこの整合工程で変更しない。

この明示的な工程には[reconcile_apim_source.py](../../scripts/reconcile_apim_source.py)を使う。`--config .local/<environment>/params.local.json`だけではローカル計画を作り、`--execute`を追加すると事前確認後にAPIMを反映する。Gatewayの通常配備とは入口を分け、Broker・SQL・Portalの配備を自動で含めない。必要なBrokerやDBの準備が未完了なら、API更新前に停止する。保護されたバックアップは`.local/<environment>/evidence/`、照合済みの新生成元は`.local/<environment>/state/gateway-api-source.json`へ保存する。

## 生成元出力と資源配備の分離

`api-publication.bicep`は単一の正本を保ち、正式CLIの[publication_templates.py](../../apps/gateway/deploy/publication_templates.py)がコンパイル結果を二つのARM要求へ分ける。生成専用は既存APIMを参照して完成XML・設定を出力し、資源配備は同じAPI・操作・部品を登録する。実配備には完成XMLを再度出力せず、`publicationSourceGenerationId`だけを出力して使った生成元を示す。

大きなJSONを出力から省くことと、生成元の完全照合を省くことは別である。保存コードのexport、入力、生成時のManaged Identity、成功した実配備との結び付きを照合し、監査・認証・ポリシー本文の確認は維持する。生成が失敗した段階ではAPIM資源を更新しない。新規構築・更新・復旧の順序は[登録用資材の説明](../../apps/gateway/apim/README.md)を参照する。

所属を1件ずつ変更する際の中間読取りでは、全API本体・全APIの操作／Product／revision／schema／tool一覧、共有ポリシー・fragment・Named Value・backend・Product、外部の事前条件を再照合する。個別定義とXMLは、変更対象APIとMCPの操作参照でつながるAPIを両方向・推移的に照合し、無関係APIの個別子資源への反復読取りを省く。保存した全snapshotは保持し、staging前後・共通認証切替後の生成元照合と、全所属変更後の全件照合を続ける。無関係な個別子資源の変更は次の当該API確認または最終全件照合で拒否し、今回追加した所属だけを解除して、正常完了にはしない。未知の構造・未完の生成元は全件読取りに戻し、回復処理と旧構成の明示的整合も従来の全件読取りを使う。この選択は内部処理で行い、params／configの切替フラグは追加しない。

## 廃止前の生成元を照合するXML

次のファイルは、過去の保護された生成元と実ポリシーを厳密に照合するために保持する固定テンプレートで、新規構築の配備対象ではない。ファイル内のgRPC経路・入口免除の記述は当時の仕様であり、現在の提供通信を示さない。所有照合を壊さないよう、旧XMLの本文とコメントは保持する。

| 旧生成元 | 照合対象 |
| --- | --- |
| `core-trusted-ingress-legacy-grpc.xml` | 廃止前のgRPC入口免除を含むleaf fragment |
| `service-inline-trusted-ingress.xml` | 入口確認を全体ポリシーへ埋め込んでいた版 |
| `service-inline-audit-v2.xml`、`service-audit-v1.xml` | 監査・認証を全体ポリシーへ埋め込んでいた版 |
| `service-legacy-entra.xml`、`service-literal-entra.xml` | 以前のEntra設定を全体ポリシーへ埋め込んでいた版 |

## 全体で共通のポリシー

現行の公開資材はHTTP・WebSocket・MCPを対象とする。gRPCの専用入口・API・入口確認の免除は廃止した。

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

各APIに登録する共有テンプレートは`api-templates/gateway.xml`（HTTP API）、`api-templates/mcp.xml`（生成MCP API）、`api-templates/protocol.xml`（WebSocketの`onHandshake`と外部MCP API）である。All APIsへ登録する`service.xml`とは適用先が異なる。3テンプレートの配置変更ではXMLの内容を変えず、全制御の棚卸完了とは扱わない。[配置と適用先](../../apps/gateway/apim/policies/api-templates/README.md)を参照する。

| 処理 | 実施内容 |
| --- | --- |
| 共通処理の継承と要求準備 | `<base/>`直後に`core-api-request-preparation`を呼び、内部ID除去・APIM要求ID設定・完了マーカー確認を行う。未設定またはfalseは503 |
| 操作・モデル | 公開Operationと固定モデル・profile・URI・queryを照合する |
| 入力・出力上限 | APIMの技術上限、型・必須構造、指定時のモデル別出力上限を検査。試験用の小容量・固定件数は標準AI APIへ追加しない |
| 回数制限 | キーは検証済み購読、Bearerは検証済みユーザーのcounterを使用。CMの追加資格情報で入口の利用枠を切り替えない |
| queryの観測 | `core-query-observation`がAPIの明示した参照から観測ラベルを選び、有無・複数値だけを記録用JSONへまとめる。標準3ラベルと対象を維持し、modeや共有Named Valueは使わない |
| 操作の入力契約 | query観測の後、本文読取りより前に`core-api-input-validation`が対象表`inputContracts`を選び、共通のContent-Type宣言条件を確認する。対象13操作は`inputContract={}`、生成表の対象値も`{}`。会社のMIME一覧比較・メソッド再確認・query拒否は撤去する |
| 利用枠の設定 | キーは`subscription-profile.xml`でPortalのSQL設定を内部APIから取得し、正常な値を30秒キャッシュする。Bearerは`entra-user-profile.xml`で共通認証が保持した社員利用枠を確認する。取得障害や不完全な設定を無制限へ変換しない |
| LLMトークン上限 | `token-limit.xml`を対応HTTP APIだけから参照。検証済み利用枠の毎分・期間内トークン数をAPIM標準ポリシーへ渡す。明示した制限なしの設定と設定欠落を区別する |
| 下流認証 | Foundry等はMI、外部サービスは登録済みの認証設定。キー・入口のユーザートークン・本人sessionを下流へ流さない |
| 監査 | 要求ID、呼出主体、認証方式、購読またはユーザー・テナント・クライアント、API、Operation、モデル、状態、使用量を記録。本文・token・keyを含めない |

各APIの先頭で棚卸・承認した3処理は、内部用の`X-Core-Caller-Id`／`X-Core-Subscription-Id`を削除して続行、`x-ms-client-request-id`をAPIM要求IDで上書き、共通認証の完了を確認する方針で維持する。XMLだけのleaf fragmentへまとめ、通常HTTP・WebSocketの接続開始・MCPの3テンプレートから呼び出す。認証の再実行やCMブラウザー受付の変更は含めず、後段の要求ID再設定とBearer側の再確認は別の棚卸で扱う。[要求準備の説明](../../apps/gateway/apim/policies/fragments/request/core-api-request-preparation/README.md)を参照する。

棚卸⑦ではquery部品を観測専用にする方針を承認した。HTTP操作の`queryObservation`、生成MCPの`mcp.queryObservation`、外部MCPの`mcpProxy.endpoints[].queryObservation`から、Bicepが`gatewayControlSettings`の操作ID辞書`queryObservations`と、メソッド・公開相対path辞書`mcpQueryObservations`を作る。サービス・モデル形式・入力制約kindに依存せず、未指定の入口は対象外である。標準対象はLive・Realtimeの作成とPIIのMCP表口、ラベルは`liveCreate`・`realtimeCreate`・`piiMcp`を保持する。外部MCPでは公開pathと下流の`uriTemplate`を区別し、API登録と同じ公開path関数を使う。

`core-query-observation`は観測対象を選び、要求ごとの`gatewayQueryObservation`へ`route`・`httpMethod`・`presence`・`duplicates`だけを保存する。`route`は公開URLではなく観測ラベルで、ASCII英字で始まる1～64文字の英数字・`_`・`-`を使い、小文字の`none`／`unknown`を完全一致で予約する。mode、許可判定、応答status、観測結果による400／500の停止分岐は撤去する。観測できない値は`unknown`として扱う。認証完了の確認は先行する`core-api-request-preparation`が担当し、認証完了マーカー未設定／falseの503は維持する。

⑥で承認した有無・複数値の取得を保持し、件数区分`countBucket`の計算・出力を削除する。`core-query-telemetry`は観測JSONへ要求情報・認証済み主体・`phase`・`statusClass`を合わせる。イベント名`query-guard`、`schemaVersion: 2`、`auditSchemaVersion: 2`を維持し、mode・`query-rejected`・`guard-error`は新規出力しない。記録段階は`received`・`backend-response`・`policy-error`で、旧ログは移行前のphase・modeとして区別する。他イベントのschema 1と旧query/schema 1のKQL読取り互換は別に保持する方針である。

旧`gateway.queryGuards`、APIの`queryGuard`参照、`core-query-guard`fragment、`core-query-guard-modes` Named Valueは廃止対象であり、現行設定として使わない。④・⑤までのmode補完・設定不備で停止する方式は移行前の設計となる。新fragmentと参照APIのAzure反映・全graph照合・読戻し、正本checkpoint更新は完了した。キー／BearerのResponses JSON・SSEと対応する監査照合を確認した。旧fragment・旧Named Valueは参照不在を確認して退役し、読戻しで不在を確認した。現行生成元・構築入力・正本checkpoint・通常配備の期待値にも旧資材を含まない。所有・currentの一般照合と過去の保護証跡は維持する。query観測・転送や他の経路の受入は未完である。

外部MCP用フローは`api-templates/protocol.xml`へ生成時に差し込み、WebSocketでは空にする。入力検査の条件は次段落で扱い、URI変換・モデル本文検査・生成MCPのPII向け制約は未棚卸のまま保持する。観測専用化だけでqueryの通過・転送や各モデル・ツールの互換性が整ったとは扱わない。[query観測部品](../../apps/gateway/apim/policies/fragments/request/core-query-observation/README.md)と[運用時の確認](../operations/apim.md)を参照する。残る処理は必要性・効果・負荷・複雑さを比較して個別に採否を判断する。

その後に承認したPIIの除外分割は、App GatewayのWAF設定として別に管理する。現行構成はRESTの`g-pii-rest`で`text`、MCPの`g-pii-mcp`で`params.arguments.text`だけを`RequestArgValues / Equals`から除外し、`p-pii-redact`は維持する。このWAF変更はAPIMの入力検査とは独立し、認証・モデル機能・OBO・URI制約・他のWAF規則を変更しない。Azure反映・読戻しと正常REST／MCPの通過・マスキング確認を完了し、旧`g-pii-redact`は参照不在を確認して退役した。未実施の異常系と同名queryへの除外の挙動は残件である。通常reconcileによる旧資源の自動削除へは変更せず、移行中の旧binding保持・復旧と成功後の退役を区別する。[WAF側の設定・移行手順](../testing/waf-compatibility.md#pii入口の除外をrestとmcpへ分ける)を参照する。

HTTPの`operations[].inputContract = {}`で検査対象を明示し、Bicepが`gatewayControlSettings.inputContracts`へ対象操作IDと空の値`{}`を生成する。この空の値は条件不足ではなく、対象だけを記録し、全対象に共通のContent-Type宣言条件を使う意味である。MIME一覧を表へ持ち込まず、`operations[].method`とURLのARM登録は維持してAPIMに照合を任せる。従来の13操作だけを明示し、未指定のLive・Realtime・PII・MCP・WebSocket等へこの検査を追加しない。`gatewayApiInputValidationPassed`のtrueは対象操作での宣言条件の通過、または未指定による検査省略を表す。完全なMIME構文や要求全体の妥当性を保証せず、認証や後続の本文・モデル検査は省略しない。

Content-Typeの存在・単一値確認は`TryGetValue`による一度の取得で行い、唯一の値を`;`で分けた先頭のMIME部分が、空白除去後も空でないことを確認する。空値・空MIME部分は拒否し、会社の許可一覧とは比較しない。新しい構文パーサーを追加せず、HTTP／WAFの既存構文保護を維持する。単一値はAPIM解析後の配列の確認であり、元のHTTPに重複ヘッダー行がなかった証明ではない。

対象選択と部品構成の整理、query制限・メソッド再確認の撤去、取得処理の簡潔化、入口のMIME許可一覧比較の撤去を承認した。不備時の監査と400応答を維持し、結果変数を`gatewayApiInputValidationPassed`、監査理由を`INVALID_CONTENT_TYPE_HEADER`、応答理由を`Invalid Content-Type header`へ改名する方針も承認済みである。短い拒否フローは共有HTTP APIテンプレートに残し、本文なし400、APIM要求IDを持つ`apim-request-id`、`Cache-Control: no-store`を維持する。入口検査と不備時の400監査のAzure反映・読戻しは完了した。空本文でContent-Type未指定の400と対応する拒否監査を確認した。空値・空MIME部分・重複値など追加異常系は未実施である。未知メソッドの実APIM照合はローカルC#検証と分けて確認する。query観測・URL資格情報拒否は今回変更しない。後続の`core-model-limits`のMIME許可一覧比較と415も撤去した。`operationConstraints.contentTypes`は個別処理などで使うため残し、未棚卸の本文／モデル制約・URI書換え、Speech転送・WAFの形式制御は保持する。Document入力の現在の整理は上記の専用部品を参照する。残存制御は後の適用順で棚卸し、関連受入を別に整理する。共有部品の比較撤去だけで任意形式の下流受付やquery転送が保証されたとは扱わない。まとめ反映・読戻しとキー／BearerのResponses JSON・SSE、および対応する監査照合は完了した。他モデル・transportや残る異常系の受入は未完である。[入力検査の使い方と13操作の範囲](../../apps/gateway/apim/policies/fragments/request/core-api-input-validation/README.md)を参照する。

## モデル・Operation固有のポリシー

| 対象 | 組み込む処理 | 初期設定の主な値・公開契約 |
|---|---|---|
| OpenAI Chat | 操作設定のモデル別生成上限を検査 | `max_completion_tokens`、各モデル128,000 |
| DeepSeek・Mistral・Grok Chat | `compatible`内のモデルごとの上限を検査 | `max_tokens`。DeepSeek 384,000／Grok 500,000。Mistralは未確認値を作らず自動値 |
| Model Router | 本文のモデル名からRouter配備を呼ぶ | 振り分け先で変わるため、固定最大値を設定せず自動値 |
| Responses | モデル別の上限を検査し`store=false`へ設定 | 各モデル128,000。クライアントが会話入力を送る |
| FLUX 2 flex／pro | モデルから内部flex／pro設定を選び、その固定`backendUrlTemplate`へ書換え | クライアントはモデル名を指定する。提供元の出力1枚は維持し、1024固定を撤廃 |
| GPT Image | 本文またはURL配備名を照合しAzure用URIへ書換え | 生成3モデル、指定時の`n`は1〜10。1024・低品質固定を撤廃し、寸法・品質・編集入力は提供元の制約に従う |
| Embedding・Rerank | モデル・型・非空入力・次元を検査 | Embeddingの16件固定を撤廃。`top_n`は任意、指定時は文書数以内。検索側のモデル・次元・版を合わせる |
| ファイル文字起こし | URLの配備名とmultipart形式を検査 | `gpt-transcribe`。Gatewayの4 MiB枠を撤廃し、提供元のファイル制約に従う |
| Speech | SSML／WAVを検査し、Speech用URI・ヘッダー・Entra認証形式へ変換 | TTS 8 KiB・STT 1 MiBの独自枠を撤廃。DTD／ENTITY拒否、対応形式・提供元の時間制約は維持 |
| Decision | 配備名と型付き要求を検査し、固定の`/systemone`へ転送 | `decision-1`。8 KiBの独自枠を撤廃 |
| PII | `{text,language}`を検査しLanguage用要求・結果へ変換 | 固定のPIIモデル版、対応言語と警告コードを照合。生成MCPも同じREST処理を使う |
| Graph /me | CMの本人接続を検査し、本人のユーザー情報を固定`$select`で取得 | 氏名・所属・連絡先・アカウント状態など12属性。任意のGraph URLや属性追加は不可 |
| Document Analysis | raw binaryの開始POSTと、署名チケット・同じ呼出主体での状態GETを提供 | 入力500,000,000 bytes、結果500 MiB、受付202→状態GET→完了200。[文書解析の契約](document-analysis.md) |

通常のAI APIの`maxRequestBytes`設定はAPIM Classicのバッファー上限**500 MiB（524,288,000 bytes）**に揃える。本文を読み出す検査では、JSONはBase64や構造を含むUTF-8本文全体、multipartはファイル以外の境界・項目も含む実サイズを比較する。音声認識・音声ファイルの文字起こし・画像編集・音声合成は`requestBodySizeCheck=declared`とし、共有部品による申告サイズの早期413を残す。前の3操作は共有部品による実サイズの読取り・比較を行わず、音声合成は専用SSML部品で実サイズも比較する。提供元のモデル・形式・料金プランによる上限が小さい場合は提供元が拒否するため、500 MiBは各モデルの受付保証ではない。Content-Lengthは必須化しない。`actual`または未指定の操作とTTSの専用部品は、chunked入力も実受信サイズで検査する。申告のみの操作でAzureが適用する技術制約は、この部品の実サイズ検査とは区別する。[APIMの技術上限](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits#api-management-gateway-runtime-limits)

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
5. Policy fragmentsで`core-api-settings`のAPI設定準備、`core-api-request-preparation`の共通認証後の要求準備、`core-request-context`・`core-audit-authenticated-subject`・`core-gateway-telemetry`のC#説明、`core-trusted-ingress`の入口確認を読む。`core-rate-limit`・`core-credential-connection`等の個別処理もここで確認する。
6. Credential managerでprovider、connection、本人同意、APIM MIのaccess policyを確認する。

XMLには処理の目的・入力・失敗時の動作を説明する日本語コメントを記載する。登録前に計画を確認し、適用後のXMLを読み直して照合する。

監査出力の認証主体、要求context、AppGWログとの相関とqueryの採用規則は[監査の識別情報](audit-identification.md)を参照する。監査出力の追加と保存設計、資材の実装と対象環境の適用・受入を分けて扱う。
