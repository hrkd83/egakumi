# APIMポリシー一覧

2026年11月23日更新。Graphと文書解析を含む標準19 API、生成MCP、CM補助APIの登録資材を説明する。業務APIの同じURLで購読キーとEntraアクセストークンの両方を受け付ける。登録資材の構成と対象環境への反映・受入状況は分け、実際の適用と接続結果は環境ごとの保護された証跡で確認する。

## 要求が通る順序

1. APIMがAPI・Operationを特定する。購読キー付き要求はAPIM標準の購読検証を使い、キーなし要求は専用の購読不要Productを通して共通認証へ進む。
2. APIポリシーが固有の設定データを保存し、`core-api-settings`で監査・制御の2変数を準備してから、`<base/>`で全体ポリシーを継承する。WebSocketは接続開始の`onHandshake`で実行する。
3. 全体ポリシーが共通の要求情報と信頼した入口経路を確認する。設定済みキーヘッダーがあれば購読キー、なければ`Authorization: Bearer`のEntraアクセストークンを検証する。無効なキーをBearerで救済しない。
4. APIポリシーが`<base/>`直後に`core-api-request-preparation`で要求ヘッダーを整え、共通認証の完了を確認してから、Operation、モデル、入力形式・サイズ、残存する本文項目条件を確認する。
5. 検証済みのユーザーにBearerの利用上限を適用し、要求ID・API・Operation等の監査情報を確定する。
6. CMが必要なAPIで、同じ呼出主体に結び付いた本人接続セッションを確認する。
7. 入口のキー・ユーザートークン・接続セッション等を除去し、下流専用のMI・OAuth・登録済み資格情報で接続する。

Bearer認可は全業務API、生成MCP、保護されたCM共通APIを対象にする。認証用テナント、API audience、v2トークン、委任スコープ`Gateway.Invoke`を検証し、クライアントアプリIDによる許可リストは設けない。CMのブラウザー用サインイン開始・コールバックは、取引stateやCookieを照合する補助経路として別に扱う。

API登録処理は、APIの配備成功を確認してから共通ポリシーを適用し、読み直して照合する。`azd up`で共通基盤・モデルを準備し、Entra登録後のGateway個別配備でCM provider・認証窓口・名前付きの値・APIを設定する。続いてPortalを個別配備する。

### 現行正本と棚卸履歴の境界

有限候補の13正本を、固定区画と全message所有を合わせた36の実処理へ結線した。短い固定組立て2式はXML維持と判断し、予定の29追加区画を実績に読み替えない。管理図・同名XML／C#・生成と保護source・説明・リンクは[現行の管理構成](../../apps/api-gateway/policies/README.md)へ揃えた。MOD-145ではBicepの認証失敗監査readerを現在のraw引用符へ揃え、Pythonと同じ失敗主体の参照を回復した。当該有限候補とMOD-145のローカル検証・独立レビューは閉じた。旧認証互換15 XML・依存C#と旧形式の再生成処理は公開資材から撤去し、現在の正本・canonical派生inlineと、実旧sourceの限定した説明差分照合を残す。対象環境の反映・実受入は保護記録で別に確認する。

以降のMODごとの維持条件と検証結果は、その時点の棚卸履歴である。Responsesの旧Item／ID加工・file ID拒否の結果を現在の透過契約へ転用しない。現在のChat／Responsesは対象操作へstore:falseを設定し、Responsesのinput／ID／typeを保持する。Hosted／ツールの目的別制限と、指定時conversation／backgroundの保存防止はそれぞれの現行説明に従う。

### 監査データの組み立てと共通フロー

CMの本文なし外部応答（MOD-95）は、Broker authorize／Graph本人ID／challengeの3検査式で「応答なし503→非200の状態分類→200の本文検査」の順へ整理しました。本文なしでもBroker403／409は同状態、Graph401は409・403は403、challenge403／429は同状態とし、その他非200と本文のない200は503です。元の本文検査以降は独立トークン比較でBroker568・Graph150・challenge478トークンが一致しました。既存のGraph403のclaims検査など、他処理の本文読取りは維持します。challengeを正規snippets/authentication/credential-challengeの同名XML／C#へ移し、長い応答検査だけをC#が所有します。旧XMLを撤去し、配備／保護source／Python、fixture・所有正本・Razor・設定案内を揃えました。Azure fragmentは追加しません。

MOD-95の関連47件は13.156sで成功（失敗・error・skipなし）しました。既存4試験へ真のBody=null、読取り禁止Bodyと回数、応答欠落、拒否後SQL非到達の35合成対照を追加し、新規試験メソッドは追加していません。初回は単体challengeの旧path読取り1error（24件、7.280s）を検出し、正規aliasとC#をorigin／settings置換前に展開する参照を修正しました。別実行の配備／保護source一致・容量1件は16.226sで成功しました。12provider完成fragmentの32KiB検査、session-use／outbound両方の生成等価も成功し、独立レビューの最終所見なしです。外部所有者・秘密・期限・失効・世代・URL・返却項目の条件と安全な監査は維持しました。実CM／Graph／SQL受入は未実施、工程1／2全体は未完了、Azure未反映です。

CMの固定設定検査（MOD-94）は、配備前に検査済みのproviderName形式・service接続名形式・personal方式の再確認3条件だけを撤去しました。正式構成の正規化が値と登録先の対応を検査し、HTTP／外部MCPへBicepが毎要求固定値を渡します。enabledの型・無効化403、内部caller欠落500、service／personalの実行分岐と外部Broker／CM／Graph／SQLの本人・秘密・期限・失効・世代条件は維持します。保存した変更前XMLから3行を除いた値が変更後と改行込みで完全一致しました。正規fragments/authentication/core-credential-connectionの配置とAzure名、同名caller C#は維持し、説明を更新しました。既存関連37件は13.851sで全件成功（失敗・error・skipなし）し、新規試験は追加していません。本文なし外部応答の分類と内部設定の読取り不備は別の続く棚卸で扱います。工程1／2全体は未完了、Azure未反映です。

Realtimeの開始・終了は[operation-policies/realtime](../../apps/api-gateway/policies/operation-policies/realtime/README.md)、補助接続は[core-realtime-observer](../../apps/api-gateway/policies/fragments/authentication/core-realtime-observer/README.md)の同名XML／C#を正本とする（MOD-93）。[owner受取りsnippet](../../apps/api-gateway/policies/snippets/authentication/realtime-owner-context/README.md)を生成時inlineし、確認済みcallerから既存v3／v4を引き継ぎ、社員JSON・認証印・GUID・購読との再照合を省く。外部チケットの署名・本人・方式・通話・期限・queryは維持する。内部owner／TTL受取り・ticket処理の限定IDだけ監査付き本文なし500に分け、別IDの既存分類を維持する。配備／保護source／Python rendererは同じ正本を使い、旧3XMLは撤去した。関連30件、別実行の配置等13件とsource／容量1件は成功し、独立レビュー所見なし。実行中の失敗と修正はテンプレート説明へ個別に記録した。Azure未反映である。

提供元の要求IDの退避は[core-backend-audit](../../apps/api-gateway/policies/fragments/audit/core-backend-audit/README.md)の同名XML／C#を正本とする。Azure名とgatewayBackendResponseIdsを維持し、各APIのoutbound/baseより前で既知ヘッダー3名・単一値・GUID／既存req_形式を確認する。これは外部応答の受入検査なので維持し、認証・所有者確認には使わない。本文を読まず、監査の出力は後続部品が行う。旧backend-audit.xmlは撤去し、BicepとPython renderer、配備と保護sourceの双方が同じ正本を参照する。

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
| `core-api-request-preparation` | `fragments/request/core-api-request-preparation/core-api-request-preparation.xml` | なし（XMLのみ） | 各APIテンプレートの`base`直後で内部IDヘッダー除去、APIM要求ID設定、共通認証の完了確認を順に行う |
| `core-query-observation` | `fragments/request/core-query-observation/core-query-observation.xml` | `fragments/request/core-query-observation/core-query-observation.cs` | 認証完了は先行する要求準備が確認する。HTTP操作またはMCPのメソッド・公開相対pathからラベルを選び、`gatewayQueryObservation`へ`route`・`httpMethod`・`presence`・`duplicates`だけを保存する。許可判定・mode・拒否応答は持たない |
| `core-api-input-validation` | `fragments/request/core-api-input-validation/core-api-input-validation.xml` | `fragments/request/core-api-input-validation/core-api-input-validation.cs` | 明示した対象操作でContent-Type宣言の存在・1値・非空MIME部分を確認し、`gatewayApiInputValidationPassed`へ真偽値を保存する。MIME一覧比較・メソッド再確認・query拒否は行わず、400応答と拒否監査は共有HTTP APIテンプレートの直後の分岐で行う |
| `core-api-operation-settings` | `fragments/request/core-api-operation-settings/core-api-operation-settings.xml` | `fragments/request/core-api-operation-settings/core-api-operation-settings.cs` | 操作の基本設定へ必要なモデル別差分を重ね、選択済み設定と任意の監査ラベルを`gatewayOperationSelection`へ保存する |
| `core-api-authentication-result` | `fragments/authentication/core-api-authentication-result/core-api-authentication-result.xml` | `fragments/authentication/core-api-authentication-result/core-api-authentication-result.cs` | HTTP APIで共通認証済みの購読または社員情報と監査ラベルを`gatewayDecision`へまとめる。内部情報を引き継げなければ、呼び出し元が監査と500応答で停止する |
| `core-credential-connection` | `fragments/authentication/core-credential-connection/core-credential-connection.xml` | `fragments/authentication/core-credential-connection/core-credential-connection.cs`（callerId取得式のみ） | 確認済みgatewayDecision.callerIdをCMへ渡し、接続所有者・本人セッション・Graph本人ID・期限・失効を照合する。内部caller読取り失敗は500を返し、APIへ展開したcredential-caller-errorが監査・応答で停止する。後続の使用直前確認を維持する |
| `core-credential-session-use` | `fragments/authentication/core-credential-session-use/core-credential-session-use.xml` | `fragments/authentication/core-credential-session-use/core-credential-session-use.cs`（使用結果検査式のみ） | 接続・Graph本人照合後、SQLで使用直前の期限・失効を確認し、世代・期限・rotationを検査する。Graph成功の重複確認を省き、更新・再認証と下流資格情報の準備を維持する |
| `core-entra-user-profile` | `fragments/quota/core-entra-user-profile/core-entra-user-profile.xml` | `fragments/quota/core-entra-user-profile/core-entra-user-profile.cs` | MOD-62～65はローカル検証済みで、この部品のInbound棚卸は一区切り。先行共通認証の全検査・不備500停止を維持し、user JSON解析・limitsのJObject取得・Formatting.None返却でgatewaySubscriptionProfileへ供給する。未判断の独自拒否なし。工程1／実SQL経路受入は未完了、Azure未反映 |
| `core-rate-limit` | `fragments/quota/core-rate-limit/core-rate-limit.xml` | `fragments/quota/core-rate-limit/core-rate-limit.cs` | 認証済み主体の利用回数枠を`gatewayRateDecision`へ準備し、有効な枠を計数する。MOD-49～55はローカル検証済みで、回数枠のInbound棚卸は一区切り。利用枠選択・Object／null分岐・計数キー・免除・標準ポリシーへの引渡しを維持する。工程1全体とトークン枠全体・社員profile・CM等の棚卸は未完了、標準429・on-error・実分散カウンターは未確認 |
| `core-token-limits` | `fragments/quota/core-token-limits/core-token-limits.xml` | `fragments/quota/core-token-limits/core-token-limits.cs`（計数キー組立て式のみ） | MOD-56～61はローカル検証済みで、この部品のInbound棚卸は一区切り。操作ID一覧の直接読取り・必要なparse／値抽出・標準ポリシー3分岐と正常枠・counterを維持し、説明整備済み。独自拒否の未判断残件はない。profile供給元・工程1全体は未完了、Azure未反映 |
| `core-model-limits` | `fragments/request/core-model-limits/core-model-limits.xml` | `fragments/request/core-model-limits/core-model-limits.cs` | 選択済みの操作設定で本文サイズを確認し、`gatewayLimitDecision`へ結果を保存する。内部設定JSONの読取り失敗は500。出力トークン専用・汎用整数・配列最大件数・汎用文字列・他提供元項目の存在拒否・Embedding入力形式と非空検査・次元検査と専用処理・Rerank入力と取得件数の検査・専用読取り処理の撤去はローカル実装・関連検証を完了、Azure未反映。想定外の例外を400へ変換する外側try/catchの撤去もローカル実装・関連検証を完了、Azure未反映。明示した設定500・本文413を維持し、例外は既存on-errorへ委ねる |
| `core-speech-ssml-guard` | `fragments/request/core-speech-ssml-guard/core-speech-ssml-guard.xml` | `fragments/request/core-speech-ssml-guard/core-speech-ssml-guard.cs` | TTS本文を一度読み、選択済み上限との実サイズ比較と既存のDTD／ENTITY宣言検査を行い、`gatewaySpeechSsmlDecision`へ結果を保存する。監査・400／413応答はHTTP APIの呼び出し元が行う |
| `core-api-target-validation` | `fragments/request/core-api-target-validation/core-api-target-validation.xml` | `fragments/request/core-api-target-validation/core-api-target-validation.cs` | HTTP APIの準備済み操作設定を読み、`gatewayTargetDecision`へallowed・status・codeを保存する。登録モデル一覧と本文model型の再確認を撤去し、genericのbodyModel・Embedding・Rerankは本文を読まず200を返す。Responsesと7操作（MOD-39）のメソッド・パス・操作IDは正式設定検査とAPIMの操作照合へ任せ、実行時の再検査を行わない。Responses・Hosted（MOD-37）・A2A（MOD-45）の本文読取り失敗・nullは400／`INVALID_REQUEST_BODY`、MOD-32のinput直下code_interpreter_callと残る本文・tools条件は403／`AUTHORIZATION_DENIED`、内部JSON取得・解析失敗は500／`OPERATION_SETTINGS_INVALID`。pathModelの空欄・dot segment、必要な先行モデル設定準備、alias・固定URI選択を維持する。includeの型・一覧403撤去（MOD-33）までのローカル実装・関連検証完了。tool_choiceのallowed_toolsによるfunction選択（MOD-34）もローカル実装・結合検証済み（MOD-34～36の最終104件）。Hosted本文読取り400分類（MOD-37）もローカル実装・関連検証済み（新しい105件）。7操作の重複確認撤去（MOD-39）もローカル実装・関連検証済み（最終147件）、Azure未反映 |
| `core-document-input-validation` | `fragments/request/core-document-input-validation/core-document-input-validation.xml` | `fragments/request/core-document-input-validation/core-document-input-validation.cs` | modelの必須・単一値・非空と`.`／`..`の拒否を本文読取りなしで確認する。モデル一覧照合・設定JSON再読取りは行わず、モデルの利用可否は提供元が判定する。内部モデル情報の引渡し失敗は500・OPERATION_SETTINGS_INVALID。監査・400／500応答はHTTP APIの呼び出し元が行う |
| `core-query-telemetry` | `fragments/audit/core-query-telemetry/core-query-telemetry.xml` | `fragments/audit/core-query-telemetry/core-query-telemetry.cs` | `route`を選択した観測ラベルとして記録し、要求情報・認証済み利用者・query概要・処理段階を関連付ける。⑥で件数区分を削除し、⑦でmode・拒否専用phaseの新規出力を撤去する。イベント名`query-guard`、schema 2とaudit schema 2は維持する |
| `core-request-context` | `fragments/audit/core-request-context/core-request-context.xml` | `fragments/audit/core-request-context/core-request-context.cs` | `gatewayRequestContext`：受信時刻、要求・API・操作のID、操作のひな形、接続元と相関候補 |
| `core-trusted-ingress` | `fragments/ingress/core-trusted-ingress/core-trusted-ingress.xml` | `fragments/ingress/core-trusted-ingress/core-trusted-ingress.cs` | 必須のHTTP入口ヘッダーを判定し、欠落・不一致・重複なら既存監査へ記録して403を返す。API固有の変数で免除しない |
| `core-credential-browser-exception` | `fragments/authentication/core-credential-browser-exception/core-credential-browser-exception.xml` | `fragments/authentication/core-credential-browser-exception/core-credential-browser-exception.cs` | `gatewayCredentialBrowserException`：接続ログインの開始・戻り先の受付への一致を判定する。本人の認証成功を示す値ではない |
| `core-common-authentication` | `fragments/authentication/core-common-authentication/core-common-authentication.xml` | `fragments/authentication/core-common-authentication/core-common-authentication.cs` | 通常要求のキー／Bearerを認証する。購読、トークン、本人、会社設定を確認し、成功を後続へ伝える |
| `core-entra-authentication-error` | `fragments/authentication/core-entra-authentication-error/core-entra-authentication-error.xml` | なし（短いXML式のみ） | 標準Entra検証の失敗を既存監査と401応答へつなぐ。Globalと、Entra有効時のDocument2・PII1・CM保護6操作のon-error先頭から呼び出す |
| `core-audit-authenticated-subject` | `fragments/audit/core-audit-authenticated-subject/core-audit-authenticated-subject.xml` | `fragments/audit/core-audit-authenticated-subject/core-audit-authenticated-subject.cs` | `gatewayAuthenticatedSubject`：認証で確認できた購読または社員。APIの利用許可とは区別する |
| `core-gateway-telemetry` | `fragments/audit/core-gateway-telemetry/core-gateway-telemetry.xml` | `fragments/audit/core-gateway-telemetry/core-gateway-telemetry.cs` | 要求・認証・利用許可等の監査ログを出力する。専用C#式は認証主体の読取と整合性確認を担当する |

SQL profile取得は、生成時snippetを共有APIへinlineで差し込む。独立したAzure fragmentは登録しない。

| 生成時の部品 | XMLの正本 | C#式の正本 | 処理・出力 |
| --- | --- | --- | --- |
| SQL購読profile | `snippets/quota/subscription-profile/subscription-profile.xml` | `snippets/quota/subscription-profile/subscription-profile.cs`（gatewayFetchedProfile式のみ） | MOD-66はローカル実装・関連検証済み。この取得snippetのローカルInboundは一区切り。別供給経路の受入検査・503監査／応答・30秒cache・社員分岐・明示null・inline方式を維持する。実SQL経路の受入と工程1全体は未完了、Azure未反映 |

表のパスは`apps/api-gateway/policies/`を起点とする。共通フローの`service.xml`は直下に置き、初めて棚卸または改修する部品は、その作業時に`fragments/`の下で監査（`audit`）、入口確認（`ingress`）、認証（`authentication`）、API要求の準備・判定（`request`）、利用枠の計数（`quota`）へ分ける。Azureの部品名・部品フォルダ・XML・直接埋め込むC#式の名前を揃え、同じ部品フォルダにXMLとC#を置く。各C#の直接使用先は同名XMLひとつに限定する。XMLは部品全体のフローを、C#はそこから分離した式を所有する。`core-trusted-ingress.cs`は入口の判定条件、`core-gateway-telemetry.cs`は認証主体の読取部分であり、XMLの処理全体をC#へ移した意味ではない。短いXML式だけの部品には空のC#を作らない。フォルダ全体の役割と移設手順は[管理構成](../../apps/api-gateway/policies/README.md)を参照する。

`core-common-authentication.cs`の5つの名前付き区画は、同名XMLの各式へ個別に埋め込む。キー検査からBearer検査までを全要求で一括実行する意味ではない。キー／Bearer共通の成功を`gatewayCommonAuthenticationValidated`、Bearerのトークン・本人・会社設定までの成功を`gatewayEntraAuthenticationValidated`で後続に伝える。2変数は要求ごとの検証結果であり、配備設定の有効／無効を示すスイッチではない。

CM・接続応答・queryの監査は、責務別の同名XML／C#へ整理し、それぞれの直接consumerが利用者情報の投影を所有する。未使用の`expressions/legacy/audit-subject-observation.cs`は撤去済みで、専用C#を別XMLへ直接流用しない。初めて扱う部品は、全制御の判断が終わるまで待たず、その作業時に責務ごとの配置・専用の同名XML／C#・Azureの日本語説明・処理説明を揃える。専用C#式を別XMLへ直接流用しない。APIテンプレートは`api-templates/`、操作別ポリシーは`operation-policies/`、生成時に差し込むXML部品は`snippets/`へ順に移設する。棚卸完了までに旧資材・旧参照・旧構成専用の生成処理を撤去し、現行構成の新規構築・再配備・検証が成立することを確認する。`legacy`を別名で温存しない。既存のAPIM部品名やAPIの接続先は変更しない。

C#ソースの行頭`//`説明は、生成時に閉じた`/* ... */`へ変換してAzureにも残す。XML属性では改行が空白へ正規化されるため、`//`をそのまま埋め込むと後続コードまでコメントになるおそれがある。実行コードと既存の`/* */`は保持し、説明の追加で処理や認証条件を変更しない。

要求情報の詳説は`core-request-context`、認証済み利用者情報の詳説は`core-audit-authenticated-subject`、監査出力側の読取・整合性確認の詳説は`core-gateway-telemetry`に全て残す。`core-trusted-ingress`にも、実際に組み込む監査コードの説明を保持する。同じ監査処理を組み込む共通拒否・CM・query等のポリシーには、`core-gateway-telemetry`を参照する案内を残す。生成途中のXMLの重複を抑えて配備サイズを守り、監査処理や出力項目は変更しない。

`core-gateway-telemetry.xml`のログ組み立てにも、処理段階・要求情報・認証主体・APIの利用許可・モデルと使用量ログの照合を説明する日本語の`/* 説明：... */`を置く。正本のフラグメントではAzureにも全説明を保持する。入口確認へ展開する場合も、残る処理の説明を保持する。共通拒否等の従来の複製では追加説明だけを省いて正本への案内を残し、既存のコードと説明を維持する。コメント追加は監査項目や認証条件を変更するものではない。

C#は生成時にXML属性用へ変換し、APIMは`include-fragment`の位置へ部品を展開する。拒否監査等で必要な生成済みXMLの再利用は維持し、C#ソースの直接使用先とは区別する。新しい外部通信や本文読み取りは追加しない。AzureポータルではSTEP見出しと共通フローをAll APIs、各処理の説明をPolicy fragmentsで確認する。個別fragmentにSTEP見出しは重ねて記載しない。`Calculate effective policy`で展開後も確認できる。fragment同士の入れ子、`base`、ポリシーsectionは部品へ入れない。

入口確認の部品は、共通の名前付きの値`core-l7-ingress-enabled`と`core-l7-ingress-secret`を参照する。拒否時の監査処理は部品内へ組み込み、他のfragmentを呼び出さない。条件、403応答、認証より前の実行位置は分離前と同じである。STEP見出し・目的・処理を独立したコメントにし、STEP間に`<!-- === -->`を置く。Azureで空行が除去されても区切りコメントは残る。

生成元照合は、参照名だけでなく部品の内容、配置、一度だけの実行、変数の追加書き込みを確認する。実式テストでは現在の監査と入口確認の部品を展開してからC#を実行する。旧テンプレートを再生成する照合方式は撤去し、保存した過去rawの完全照合と、検証済みcurrent scopeを分ける。

STEP3では、CMのログイン開始画面・受付と、認証後の戻り先（callback）用APIへのアクセスかを判定する。通常の画面遷移・フォーム送信・認証後のリダイレクトではAPI用のキー／Bearerヘッダーが送られないため、専用APIの起動画面、起動受付、本人認証の戻り先、接続完了の戻り先と完全一致する要求だけを例外にする。STEP4は判定結果がtrueの場合に従来の匿名フラグを設定し、共通のキー／Bearer検証を省略する。モデル・ツール等の通常APIは引き続きキー／Bearerを検証し、例外となる受付でもEntraへのサインインや各受付・Broker・CMによる取引・本人の確認を省略しない。詳しくは[受付の説明](../../apps/api-gateway/policies/fragments/authentication/core-credential-browser-exception/README.md)を参照する。

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

続く棚卸では、操作設定準備の失敗時にHTTP状態を取得してから監査の分類を選ぶ。内部設定不備500は`policy-error`、本文・modelの不備400と本文超過413は`rejected`として記録し、監査の前に状態・原因・分類を揃える。HTTP状態、理由文、本文なしの応答、要求ID、停止条件は変更しない。ローカル実装後、編集中の新旧資材が混在した初回試験の1件失敗を分け、ソース固定後に最新main上の関連104件を再実行してすべて成功、失敗・スキップなしを確認した。実XMLの監査分類式をC#で実行し、500と400・413の分類および監査・応答の順序を確認している。Azure未反映で、実経路の監査確認は工程1のまとめ反映時に行う。

HTTP共通Inboundの操作設定準備400／413／500、認証済み情報の引き渡し500、モデル・機能確認400／403／500、Speech入力検査400／413の失敗応答には、`Cache-Control: no-store`を追加する。4処理の5つの応答ブロックへHTTPヘッダーだけを追加し、状態・理由文・本文・要求ID・監査分類・停止条件は維持する。HTTPエラー応答のキャッシュ指定であり、Foundryへ送る`store`や履歴参照の処理は変更しない。ローカルの対象5応答の照合と、実ポリシー式・生成結果・Speech・正規ソースの関連13件はすべて成功、失敗・スキップなし。Azure未反映で、protocol／MCPの失敗応答はこの変更の対象ではない。

Chat Completionsの`store:false`強制はローカル実装・合成検証済みである。HTTP API、実効接続先`foundryOpenAi`、POST `/chat/completions`、操作制約`bodyModel`の組合せから、配備時に`chatStoreDisabledOperationIds`を生成する。操作の`backendOverride`があれば、その接続先を優先する。API名・登録モデル一覧・監査用`llmOperationIds`で対象を決めず、同じ宣言の追加APIや新しいGPTにも適用する。標準Chatのcompatibleモデル・model-routerも対象となる。WebSocket・外部MCP・生成MCP、別の操作・接続先へは広げない。

Chatの保存防止部品`core-chat-store-disabled`は、aliasと元の本文サイズ・利用枠を確認した後、下流送信前に本文を一度だけ消費して読む。`store`の未指定・true・false・null・別型をBoolean falseへ揃え、model・messages・stream等は保持したまま`gatewayChatRequestBody`へ文字列として準備する。正常時の`set-body`はその文字列を使い、本文を再読取りしない。JSONオブジェクトを読めない、非オブジェクト・null・本文なしの場合は400・`INVALID_REQUEST_BODY`と`rejected`監査、本文なし・要求ID・HTTPの`no-store`で終了する。先行する入力不備400・設定不備500・元の本文超過413と、Responsesのstore=false・指定時conversation=null／background=falseは維持する。Responsesの参照加工・file ID拒否の撤去はMOD-29に従う。Foundry側の設定は変更しておらず、Foundry全体の保存禁止を達成したとは扱わない。関連113件の固定ソース再実行では112件が成功し、生成MCPの合成宣言不足による対象1件を修正・再実行して成功した。途中のBicep構文・手書きARM式・生成XMLの字下げ差も修正済みで、未解決の失敗・スキップはない。Azure反映・compatibleモデル等の実受入と非保存動作は未確認で、工程1のまとめ反映時に確認する。

次の`gatewayDecision`先頭では、API定義の`authorization.mode`確認を配備前の検査へ任せ、同じ値を要求ごとに復元・解析して確認する処理を撤去する。キー／Bearerの結果を引き継ぐ分岐は、APIMの検証済み購読`context.Subscription`の有無で選ぶ。購読があれば購読IDを使い、なければ共通認証済みのBearer本人情報を使う。キー優先と不正キーの拒否は共通ポリシーが決定し、後続でキー用ヘッダーを読み直して方式を決めない。後段の判定部品の設定読取りは下記の共通部品で準備済み操作JSONへ統一し、モデル・本文条件と失敗分類は続く棚卸対象とする。

Bearer側に入った時点で購読なしは確定しており、共通認証の完了は先行する`core-api-request-preparation`が確認するため、この2条件の再確認を撤去する。社員情報を使う前の`gatewayEntraAuthenticationValidated`確認は維持する。共通認証の未設定・falseは先行部品で503となり、Bearer認証成功が確認できなければ本人情報の引き渡しを拒否する。

HTTP APIの`gatewayDecision`では、共通認証が作った社員情報JSONを読み、確認・整形済みの`tenantId`と`principalId`をそのまま引き継ぐ。`schemaVersion`とGUID形式・空GUIDの再検査、GUIDの再整形を撤去し、本人情報の検証を共通認証へ集約する。`callerId`は呼出者、`allocationId`は利用回数・トークン数を数える利用枠の帰属先を示す。共通認証が両方を同じ社員のIDとして確認しているため、HTTP側のID再構築・再照合も撤去し、それぞれの確認済み値を引き継ぐ。正常時の出力項目と値、社員単位の利用枠と監査は変えない。このHTTP整理のAzure反映・読戻しは完了した。protocol／生成MCPもMOD-43で確認済みIDの直接引渡しへ整理し、ローカル実装・関連検証済み、Azure未反映である。以下の実経路確認はHTTP側の結果である。キー／BearerのResponses JSON・SSE、無効Bearerの401と対応する利用者・応答・AppGW監査の照合を確認した。他モデル・transportや残る異常系の受入は未完である。

監査用ラベルの転記では、先行する操作・モデル設定の準備が成功した結果`gatewayOperationSelection`を直接読み、不要な存在確認の分岐を撤去する。`settingId`が空なら記録せず、値があれば既存の監査項目`profileId`へ転記する。これは管理側が付ける操作・モデル設定のラベルであり、クライアントのprofile選択や購読の利用枠設定を示す値ではない。監査の項目名と出力形式は維持する。

HTTP APIとprotocol／生成MCP（MOD-44）が認証結果を引き継げない場合は停止を維持し、500・`policy-error`・`AUTHENTICATION_PROCESSING_ERROR`として記録する。対象はBearer認証の完了記録の欠落・falseと、内部の社員情報JSONの読取りなどで発生する例外である。応答理由は`Gateway processing failed`とし、要求IDと`Cache-Control: no-store`を返し、応答本文は付けない。例外の詳細や生JSONは出力しない。正常時の認証結果と、共通認証の401、後段の`gatewayTargetDecision`による403は変えない。操作・モデル設定の準備結果を読む処理の例外や、共通の`on-error`の扱いは、この失敗分岐と別に扱う。

棚卸済みの組み立てコードは`fragments/authentication/core-api-authentication-result/`の同名XML・C#へ分離する。HTTPテンプレートはその部品を呼び、短い失敗監査・500応答のフローは直後に残す。XML・C#・Azureフラグメントの名前を揃え、C#の説明コメントを生成XMLへ保持する。この部品へ他の監査ソースを展開せず、フラグメント同士の入れ子も作らない。[本体と入出力](../../apps/api-gateway/policies/fragments/authentication/core-api-authentication-result/README.md)を参照する。

続く`core-model-limits`の資材は、初回棚卸時に`fragments/request/core-model-limits/`の同名XML・C#へ移し、直下の旧`model-limits.xml`を撤去する。Azure登録名は維持し、説明とコード内コメントを日本語へ揃える。冒頭の`gatewayOperationConstraint`の取得・JSON解析を独立した失敗処理へ分け、例外時は500・`LIMITS_NOT_CONFIGURED`を返す。上限設定`maxRequestBytes`は整数・1〜500 MiBの条件を維持し、`Int64.TryParse`で一度だけ`long`へ変換して使い回す。変換失敗や桁あふれ・範囲外も、本文を読む前に設定不備500へ揃える。先行処理が確認する操作共通上限と、ここで確認する選択後の上限は保証範囲が異なる。本文超過413を維持し、想定外の例外は既存`on-error`へ委ねる。配置・名前・コメントの整備を、この部品の全制御の棚卸完了とは扱わない。[現在の処理と棚卸状態](../../apps/api-gateway/policies/fragments/request/core-model-limits/README.md)を参照する。

続く棚卸で、HTTPの出力トークン項目名・整数型・最小値・最大値の独自検査と、そのための能力表照合・本文JSON読取りを撤去する。`integerLimits`による必須・整数型・上下限の400も撤去し、指定値を書き換えず提供元へ委ねる。OpenAI画像生成は整数検査だけのJSON読取りを省く。FLUXでも、続く汎用文字列検査の撤去により専用JSON読取りを省くが、必要な先行モデル選択の解析は残り、後段のmodel型確認だけの解析は撤去する。追加APIでも専用検査だけがJSONを読んでいた場合、その読取りはなくなり、入力不備も利用回数制限・受付記録を経て提供元へ進み得る。本文サイズと413、認証と利用枠は維持する。

出力項目名・能力表・整数設定は、Portalの表示・要求生成・入力検査やRealtime設定にも用途があるため残す。配備前の設定検査も変更しない。出力トークン専用検査と汎用整数検査の撤去はローカル実装・関連検証を完了し、Azure未反映である。旧範囲を超える指定値の提供元での実受入は未確認で、専用検査を通ることを提供元の受付保証とは扱わない。

`arrayLimits`による配列最大件数の独自400も撤去を承認した。現標準APIの基本・モデル別設定には非空の表がなく、主な影響はこの表を使う追加APIである。表の読取り・比較と、配列検査だけの本文JSON読取りを省き、件数の適否は提供元へ委ねる。Portalの`maxInputs`・`maxDocuments`、入力UI・件数検査・JSON schemaには用途があるため、設定と配備前検査は残す。本文サイズ413は維持する。配列検査撤去のローカル実装・関連検証を完了し、Azure未反映・提供元での実受入は未確認である。

`stringChoices`による汎用の文字列型・一覧一致の400も撤去を承認した。標準の対象はFLUX生成・編集の`output_format`とCohere Embeddingの`input_type`であり、型・null・一覧外の値の適否は提供元へ委ねる。専用の表読取り・比較・本文JSON読取りを省く。Cohereの次元検査だけの本文解析も、下記のEmbedding専用処理撤去に合わせて省く。必要な先行モデル設定準備の解析は残り、後段のmodel型確認だけの解析は撤去する。Portalの選択肢・入力検査・要求生成・JSON schemaと、配備前の設定検査は維持する。文字列検査撤去のローカル実装・関連検証を完了し、Azure未反映・提供元での実受入は未確認である。

Embeddingでは、別の提供元の項目が存在するだけで400にする`otherProviderFields`の確認も撤去を承認した。OpenAIへの`input_type`やCohereへの`dimensions`など、追加項目の有無と値だけで拒否せず、そのまま提供元へ渡す。本文413、必要な先行モデル設定準備は維持し、後段のmodel型確認と専用JSON読取りは撤去する。Portalの提供元別入力検査も変更しない。存在確認撤去のローカル実装・関連検証を完了し、Azure未反映・提供元の実受入は未確認である。

Embeddingの`input`／`texts`をテキスト・トークンの固定形式と非空だけに限る400も撤去を承認した。欠落・null・空・空白・別型・別形式の適否は提供元へ委ね、本文を変更しない。本文413、必要な先行モデル設定準備と利用枠は維持し、後段のmodel型確認と専用JSON読取りは撤去する。Portalのテキスト入力UI・入力検査・部分Schemaは変更しない。[Cohere公式API](https://docs.cohere.com/v2/reference/embed)の`images`・複合`inputs`方式もAPIMの固定テキスト条件で止めないが、FoundryとこのGateway経路での受付は未検証である。入力形式検査撤去のローカル実装・関連検証を完了し、Azure未反映である。

Embeddingの次元指定をモデル別の最大値・許可一覧と比較する400と、そのためだけの`embeddingDimensionsByModel`の取得・構造・範囲検査による500も撤去した。旧最大値を超える正整数や許可一覧外の正整数を、この比較だけで拒否せず、指定値を保持して提供元へ委ねる。次元表はPortalの候補・既定値・入力検査・部分Schemaに必要なため残し、配備前の表検査も維持する。この撤去のローカル実装・関連検証を完了し、Azure未反映・提供元での実受入は未確認である。

次元指定の整数・1以上の400も撤去を承認した。その判定だけのEmbedding専用分岐をまとめて削除し、提供元別の項目選択、本文JSON読取り・取得不能時の400、`allowedModels`の配列確認500と登録モデル条件もこの部品から省く。有効な登録モデルのJSONでは、次元の0・負数・小数・文字列・nullも、この部品で拒否せず本文を保持して提供元へ委ねる。本文サイズ413、PII、認証と利用枠、Portal・配備前の次元表検査は維持する。サイズなどの先行確認を通った不正JSON・非オブジェクトは、この部品では200となる。続く共通Targetのmodel型確認と専用JSON読取りも撤去し、genericのEmbeddingは後段で本文を読まず200を返す。内部の`allowedModels:null`も後段の拒否理由にしない。モデル別設定やaliasを選ぶために先行処理が本文を読む場合のJSON・model不備400は維持する。これらの関連変更をまとめてAzureへ反映する。Embedding専用処理撤去はローカル実装・関連検証を完了し、Azure未反映である。Gateway全体のJSON読取り撤去や提供元の受付確認を意味しない。

Rerankの検索文`query`と対象文書`documents`の入力形式・非空条件による400も撤去を承認した。検索文の取得と型・空白除去後の非空判定、文書の必須配列・1件以上・各要素の文字列と非空判定、空白文字表を省き、入力の適否は提供元へ委ねて本文を変更しない。本文413、必要な先行モデル設定準備と利用枠、Portalのテキスト入力UI・入力検査・部分Schema・配備前検査は維持する。入力形式・非空検査撤去はローカル実装・関連検証を完了し、Azure未反映である。提供元での新入力の受付は未確認である。

続く`top_n`の整数・1以上・文書数以内による独自400と、その検査だけの読取り処理も撤去を承認した。取得件数と文書配列の取得、本文JSONオブジェクト読取り・取得不能時の400を含むRerank専用分岐をまとめて省く。0・負数・小数・文字列・null・文書数超過などの指定値を、この部品で拒否・補正せず、本文を保持して提供元へ委ねる。`core-model-limits`は本文サイズを確認し、クライアント本文をJSONとして再読取りしない。`kind`はPIIの受信枠計算に使うため残す。本文413、後段のJSON・model形式確認、認証と利用枠、Portalの入力・取得件数検査と配備前検査は維持する。Embeddingと同じく、Rerankの不正JSON・非オブジェクト・本文なしはサイズ確認を通ればこの部品では200となる。後段Targetのmodel型確認と専用JSON読取りも撤去し、genericのRerankは本文を読まず200を返す。必要な先行モデル設定準備のJSON・model不備400は維持し、関連変更をまとめてAzureへ反映する。取得件数と専用読取り処理の撤去はローカル実装・関連検証を完了し、Azure未反映である。提供元での実受入やGateway全体のJSON読取り撤去を意味しない。

本文JSONの専用検査がなくなったため、想定外の例外を一律400・`INVALID_REQUEST`へ変換する外側`try/catch`も撤去を承認した。本文バイト列の読取りや内部の種類指定等で発生する例外は、APIMと各APIの既存`on-error`へ委ねる。内部操作設定JSONと先行測定値JSONの読取り失敗を500・`LIMITS_NOT_CONFIGURED`へ分ける2つの小さい`catch`、形式・上限設定の明示500、本文超過413は維持する。通常HTTPの`on-error`は`POLICY_ERROR`を監査へ渡し、既存応答が400以上ならそのstatus、それ以外は監査statusを0とする。一般例外のHTTP応答はAPIM標準へ委ね、500へ固定する処理は追加しない。PII・Documentは専用`on-error`の一般例外502を維持し、監査経路を含むこの差は後工程で棚卸する。外側400変換撤去はローカル実装・関連検証を完了し、Azure未反映である。ローカル式試験の例外伝播とXML参照の確認を、実APIMの応答・監査確認とは扱わない。

HTTP APIの`core-model-limits`呼出し元では、失敗結果のstatusを監査phaseの設定より前に取得し、内部設定不備500・`LIMITS_NOT_CONFIGURED`を`policy-error`、本文超過413・`REQUEST_TOO_LARGE`を`rejected`へ分ける整理を承認した。既存の監査部品が受け付けるphaseを使い、拒否条件・code・HTTP status・本文・要求IDを維持する。PII専用413・`PiiInputTooLarge`の公開契約も変更しない。新しい変数表・部品・監査schemaは追加しない。この監査分類整理はローカル実装・関連検証を完了し、Azure未反映である。

同じ呼出し元の一般失敗応答は、500の理由文を`Gateway processing failed`、413を`Request too large`へ分け、`return-response`内で`Cache-Control: no-store`を設定する整理を承認した。公開JSONの理由code・HTTP status・`apim-request-id`・本文内の`requestId`と監査分類を維持し、PII専用413も変更しない。要求ID付きのエラーを保存・再利用しない意図を当該応答で明示し、上位や操作側のoutboundによる補完には依存しない。この段階の整理は一般Limits失敗枝だけを対象とし、当時のSpeech SSML等の別枝は未変更であった。現行のSpeech実本文超過413の理由文はMOD-30に従う。理由文と`no-store`の整理はローカル実装・関連検証を完了し、Azure未反映である。実応答の確認はまとめ反映後に行う。

本文形式`bodyFormat`の補完は、標準・追加APIが通る配備前の正規化処理へ統一する。実行時は文字列の`json`・`binary`・`multipart`・`text`・`none`を一度だけ確認し、欠落・不正値は本文を読む前に500・`LIMITS_NOT_CONFIGURED`で停止する。内部設定の検査であり、クライアントのContent-Typeの許可条件や本文パーサーは追加しない。内部JSONと形式の確認後、`none`なら本文と`maxRequestBytes`を読まず成功する。本文の存在禁止ではなく、使わない上限の検査を省く順序である。本文を扱う4形式の上限確認と、配備前の上限設定検査は維持する。

`core-model-limits`の`binary`・`multipart`・`text`では、MIME許可一覧比較と、それによる415を撤去する。提供元が対応する形式を基盤の一覧更新漏れで止めないため、受付形式は提供元が判断する。申告サイズの検査と対象操作の前段Content-Typeヘッダー検査を維持し、実受信サイズを確認するかは操作設定で指定する。`contentTypes`設定はAPI定義生成・SpeechのSSML検査対象選択などに使うため残す。Documentの個別形式検査は下記の入力棚卸で撤去する。SpeechのWAVヘッダー上書き、WAFの形式制御は後続の棚卸対象であり、任意形式の経路全体の受付は未確認である。

同じ3形式で、Content-Lengthを数値化できない・負数の場合に400にする独自の形式検査を撤去する。ヘッダーによる本文の区切り・構文の扱いはHTTPの受信処理に任せる。読み取れた申告サイズが上限を超える場合の早期413と、ヘッダー省略の許可を維持する。実受信サイズを確認するかは操作設定で指定する。ローカルのポリシー式試験は、HTTP受信層の不正要求の扱いを再現しない。

申告サイズによる早期413は、この部品での本文のバイト配列読取りと後続処理を省くため維持する。AppGWやAPIMが先に行った受信・保持処理まで省ける保証はない。省略・数値化できない値・上限以下は、操作設定に従って実受信サイズの確認か、本文を読み出さない成功へ進む。

汎用の操作設定`requestBodySizeCheck`は、非JSONの共有サイズ確認方法を指定する。未指定／`actual`は従来の実サイズ検査を維持し、`declared`は早期413の後にこの部品では本文を読み出さず成功する。標準のSpeech STT・音声ファイルの文字起こし・画像編集・Speech TTS・Document解析の5操作に`declared`を指定する。TTS以外の4操作はサイズ検査だけの全量読取りを撤去し、申告がない場合など、この部品による実サイズの413はなく、受付サイズは提供元が判断する。共有部品の成功codeは`DECLARED_SIZE_ONLY`とし、実サイズ確認後の`BOUNDED_UPLOAD`と区別する。TTSは直後の専用部品で実サイズも確認する。API名・操作名はサイズ検査のC#へ固定しない。ほかの本文読取りが共有テンプレートに残るため、完全な無バッファ転送や、Azure自身の技術制約を今回の変更だけで確認したとは扱わない。

TTSでは、従来のSSML検査対象から生成した`gatewayControlSettings.speechSsmlOperationIds`をHTTP APIが読み、`core-speech-ssml-guard`を共有サイズ検査直後、利用枠・下流認証準備・`accepted`監査より前で呼ぶ。旧Operation側の`<base />`後には重ねて呼ばない。同名XML／C#を正規フォルダへ置き、直下の旧`speech-ssml-guard.xml`は撤去する。部品は本文を一度だけ保持して読み、検証済みの`gatewayOperationConstraint.maxRequestBytes`との実サイズ比較を文字列化前に行い、通過後に既存のSSML宣言を走査する。結果は`gatewaySpeechSsmlDecision`の`allowed`・`status`・`code`へ保存する。成功`SPEECH_MARKUP_ALLOWED`、実サイズ超過`REQUEST_TOO_LARGE`／413、宣言拒否`INVALID_SPEECH_MARKUP`／400を区別し、監査・HTTP応答はAPI側へ残す。内部JSON読取り等の例外はAPIの既存`on-error`へ進め、同じ上限設定の検査や部品専用の設定500コードは追加しない。

この集約で、TTSの共有サイズ確認とSSML確認による本文の二重読取りを一度にする。UTF-8／16／32の判別、コメント・CDATA等を除外した宣言検査、公開400`InvalidSpeechMarkup`、元本文の保持を維持する。全文の文字列化・小文字化は残り、XMLパーサーや実体解決は追加しない。TTS集約時点ではDocument・JSONの未棚卸処理やWAFを変更していない。TTSのAzure反映・読戻しは完了した。キー／BearerのTTS正常WAV応答とDTD宣言の400、および対応する監査照合を確認した。実サイズ超過413など残る異常系は未実施である。[TTS部品の処理と対象](../../apps/api-gateway/policies/fragments/request/core-speech-ssml-guard/README.md)を参照する。

Speech実本文超過413の理由文整理（MOD-30）はローカル実装済みである。HTTP APIが`core-speech-ssml-guard`の実受信サイズ超過を返す枝の理由文だけを、`Request limit validation failed`から共有サイズ応答と同じ`Request too large`へ揃える。HTTP状態413、公開JSONの`REQUEST_TOO_LARGE`とrequestId、`apim-request-id`、監査の`rejected`／`REQUEST_TOO_LARGE`／413、`Cache-Control: no-store`、上限と拒否条件は維持する。DTD／ENTITYによる400応答は変更しない。差分確認済み、Azure未反映であり、現行理由文の実HTTP応答と実サイズ超過413の受入は未確認である。

HTTP APIの後段判定は、最初の棚卸で`fragments/request/core-api-target-validation/`の同名XML・C#へ分離する。部品は準備済みの`gatewayOperationConstraint`を直接JSONとして読み、API定義のBase64復元、変数の存在確認と操作定義へのfallback、直後のnull確認を省く。先行する操作設定準備と共有サイズ確認が内部JSONを取得できた場合だけここへ進み、この間に設定を書き換えない。モデル・本文・tools・操作の判定結果は部品が返し、短い拒否監査とHTTP応答はAPIテンプレートへ残す。XML・C#・Azure登録名を揃え、日本語の説明コメントを整える。この分離と読取り集約はローカル実装・関連検証完了、Azure未反映である。protocol・MCPが使うBase64生成処理は維持する。[後段判定部品の現在の契約](../../apps/api-gateway/policies/fragments/request/core-api-target-validation/README.md)を参照する。

続く棚卸で、内部操作設定JSONの取得・解析失敗だけを500・`OPERATION_SETTINGS_INVALID`へ分ける整理を承認した。読取りの小さい`catch`を独立させ、結果は`gatewayTargetDecision`の内部JSONにallowed・status・codeで保存する。この分類整理の時点では、設定読取り後の残る本文条件の拒否やHosted／A2A等の本文例外は403・`AUTHORIZATION_DENIED`、正常は200・`TARGET_ALLOWED`とした。Responsesの本文読取り失敗は続く棚卸で400へ分け、Hostedの読取り不備も現在はMOD-37に従い400へ分ける。API側は実結果のstatus・codeを使い、500を`policy-error`／`Gateway processing failed`、403を`rejected`／`API target is not authorized`として記録・応答する。本文なしと`apim-request-id`を維持し、新しい結果形式の再検査や設定は追加しない。この内部JSONの分類整理では各判定条件を追加せず、その他の設定項目や本文側の拒否分類は続く棚卸対象である。この内部JSON読取り分類と結果引渡しはローカル実装・関連検証完了、Azure未反映である。

`pathModel`では、URLで指定したモデルを`allowedModels`と照合して403にする処理を撤去し、空・空白のみ・`.`・`..`の拒否は維持する。モデルの利用可否は提供元へ委ね、標準の画像編集・ファイル文字起こしと追加APIの`pathModel`操作に同じ構成を使う。`backendUrlTemplate`を明示した操作では、配備時の生成処理が全placeholderの値を`Uri.EscapeDataString`でURIの一要素へ符号化し、固定パス・queryを維持する。template未指定が正式に可能な追加APIは従来のAPIM標準URL・query転送を保ち、新しいC#のURL組立ては加えない。標準2操作は明示templateを持つ。固定host・API版、認証・利用枠・サイズ枠と本文の別条件を維持する。`allowedModels`はPortalの候補・正式設定の用途に残し、監査モデル名の採否には使わない（MOD-35）。パラメーター設定も維持する。この一覧拒否撤去とURI符号化はローカル実装・関連検証完了、Azure未反映であり、提供元の実受入と実経路のURI処理は未確認である。

APIMの登録モデル一覧による実行時拒否を完全に撤去する方針を承認した。HTTP共通の`bodyModel`・`textEmbeddings`・`rerank`・`statelessResponses`の一覧403と、比較専用の`gatewayEffectiveModel`再取得を削除した。同じ固定接続先・同じAPI形式の新モデルは、モデル別差分がなければ操作の基本設定、共通認証・利用回数制限・本文サイズ枠を使う。OpenAI Chat／Responsesの将来モデルを、一覧・差分表に追加せず通し、基本設定と本文保持をローカルで確認した。aliasとFLUX等の異なる接続パスに必要な固定URI選択は維持し、提供元のモデル利用可否と別に扱う。`protocol.xml`の動的WebSocket選択も一覧照合を撤去し、指定modelまたは既存のdefaultModelを使う。固定接続先用query契約とquery単一値の別条件は維持する。Portalの候補・能力表示と配備前検査用のモデル一覧を残し、要求拒否や監査モデル名の採否には使わない（MOD-35）。HTTP・動的WebSocketの撤去はローカル実装・実C#検証を完了し、Azure未反映・実受入は未確認である。

続く棚卸で、このHTTP共通部品のmodel型再確認と、そのためだけの本文JSON読取りも撤去した。`bodyModel`・`textEmbeddings`・`rerank`は本文を読まず200の内部成功結果を返す。部品単独ではmodel欠落・null・数値・配列・オブジェクト、不正JSON・scalar・本文なし・読取り例外を理由に拒否しない。Responsesはmodel型だけを再確認せず、この段階で残っていた状態参照・input・background・tools・tool_choice・includeのための本文読取りと`body == null`拒否を維持した。現行の参照処理とツール選択はMOD-29・MOD-32・MOD-33・MOD-34に従い、tools・tool_choiceとCode Interpreter呼出しItemの条件のために本文読取りを残す。includeの一覧・型による403はMOD-33で撤去し、原値で提供元へ渡す。他条件が正常ならmodelの型だけで403にしない。続く棚卸で、ResponsesのJSON読取り失敗と`body == null`だけを400・`INVALID_REQUEST_BODY`へ分け、監査は`rejected`、応答理由は`Invalid request body`とした。この段階の状態・入力・tools条件とHosted／A2Aの読取り不備403、内部設定500を維持し、本文なし・要求IDの応答契約を変えない。必要な先行モデル設定準備のJSON・model不備400、設定不備500・サイズ超過413、aliasと固定URI選択は維持する。model型撤去後の87件が成功した後、Responsesの読取り400分類後にも対象87件を実行してすべて成功した。実C#、公開設定のsource契約、実Bicep生成でgenericの読取り0回、Responsesの実readerと監査・応答400、先行確認・本文保持を確認した。当時のローカル実装・検証完了、Azure未反映・提供元の実受入は未確認である。

Responsesのメソッド・パス再確認を撤去し、正式な設定検査とAPIMの操作照合へ任せた。正式登録は引き続き`statelessResponses`・POST `/responses`だけであり、部品単独でGETや別URLを評価して本文条件が成功することは、その経路を公開する意味ではない。経路再確認撤去後は、実C#・source契約・実Bicep生成と正式な設定検査3件を含む90件がすべて成功し、失敗・スキップはない。直接評価のGET／別URL各200・reader400・状態参照403と読取り1回、実フローの本文保持、正式な正常登録とkind偽装・GET／別経路の登録拒否を確認した。ローカル実装・検証完了、Azure未反映・実経路の応答は未確認である。

Responses簡素化（MOD-29）はローカル実装・合成検証済みである。`store`だけは未指定・指定値にかかわらずBoolean falseへ揃え、指定された`conversation`をnull、`background`をfalseへ上書きする。未指定のconversation・backgroundは追加せず、streamを保持する。Conversationへの入出力追加はResponseのstoreとは別の保存なので、conversationの無効化を維持する。現在はMOD-32の拒否条件に該当せず受け付けた要求について、`previous_response_id`・`prompt`・`conversation_id`とinput全体を変更せず、明示item_reference、type未指定／nullの参照、完全Itemsのid・type・call_id・順序・内容を保持する。file_id・file_ids・vector_store_idsの存在・値を理由にした再帰的403を撤去し、入力ファイル指定と引用注釈をそのまま提供元へ渡す。引用注釈の個別例外は不要になる。toolsのfunction限定、tool_choiceのfunction選択（MOD-34）とincludeの原値転送（MOD-33）、本文JSON読取り400・内部設定500、aliasと先行する入力・サイズ・利用枠の条件は維持する。MOD-29時点ではChatと合わせた関連113件の確認を完了した（112件成功後、合成宣言不足の対象1件を修正・再実行して成功）。この結果を続くMOD-32の検証結果へ転用しない。Azure反映・実受入は未確認である。

ChatとResponsesのstore=falseは、Gateway経由の今回の応答を保存しない要求指定である。Foundry側の設定は変更しておらず、Foundry全体の保存禁止や保存済みIDの参照権限無効化を達成したとは扱わない。

続くツール棚卸（MOD-31）では、利用者別に隔離できない保存資源は基本的に蓄積させない方針を採用した。内蔵Code Interpreterは応答履歴とは別にコンテナ・ファイルを保持し、同project内で利用者別に隔離されないため、toolsのfunction限定を維持して一律開放は進めない。Microsoftの[Responses手順](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/responses)はinput内のcode_interpreter_callからも既存コンテナを参照できると説明しており、function限定とstore=falseだけで全資源の保存・参照防止を達成したとは扱わない。MOD-31時点ではinputからの参照は未棚卸であった。現在のcode_interpreter_callの拒否はMOD-32に従い、その他の既存資源の参照と所有者確認は継続棚卸の対象である。

カスタムツールは、アプリがfunction呼出しを受け、個人接続付きMCPで実行して結果をFoundryへ返す構成を候補とする。追加APIの外部MCPプロキシはCM連携の正式設定・生成に対応するが、標準の個人接続はGraph向けである。MCP用のOAuth接続先・scope・同じresource hostの固定本人確認先と、MCP側の本人認証・資源隔離を別途設計する。購読キー単独を個人識別として扱わず、[本人接続セッション](credential-manager-login.md)と検証済み利用者を対応付ける。Foundryからremote MCPへCMの個人接続を自動で引き継ぐ処理は未実装である。MOD-31は公式仕様と既存正本の読取りによる方針整理で、新しい拒否条件・本文加工・MCP資源・Azure変更や実受入を実施した記録ではない。

Code Interpreter呼出しItemの拒否（MOD-32）は、Responsesのinput配列直下のオブジェクトで、typeが文字列code_interpreter_callの場合だけ要求全体を403にする。共有コンテナとファイルの参照を、この種類のItemで渡さないために追加した。該当要素を削除して続行せず、既存のrejected／AUTHORIZATION_DENIED／403監査、本文なし、要求ID付きのapim-request-idとCache-Control: no-storeを使う。再帰走査は行わず、深い位置の同名type、typeの欠落・null・別型・大小文字の違いはこの条件では拒否しない。通常のmessage・reasoning・function、一般のid・call_id・引用注釈と、受け付けたinputの順序・内容・参照値を保持する。store=false・指定時conversation=null／background=false、その他のtools・tool_choice、本文JSON不備400・内部設定500と先行条件は維持する。ローカル実装・合成検証済みである。MOD-32時点の関連71件が初回からすべて成功した（17.266s、失敗・スキップなし）。追加の実C#24対照と親フロー45対照で、共有21条件の403が5条件・200が16条件と他kind3条件を確認した。本文保持・既存の応答／監査、実BicepとPythonの生成XMLの一致、正本所有先とRazor構文も確認した。当時のinclude制限は続くMOD-33で撤去し、この71件を新しいinclude条件の検証成功へ転用しない。Azure未反映・実Foundryでの新条件の受入は未確認である。

Responsesのincludeの型・一覧による403撤去（MOD-33）では、2種類への一覧制限と配列・文字列型の独自判定を撤去し、指定値・配列の順序や重複・未指定状態をそのまま提供元へ渡す。includeは応答に追加情報を含める指定であり、保存やツール実行の指定ではないため、値の受付判断を提供元へ任せる。null・文字列・数値・オブジェクトなどもこの条件では403にしない。toolsのfunction限定、tool_choiceの既存条件とinput直下のcode_interpreter_call拒否、store=false・指定時conversation=null／background=falseを維持する。残る判定に本文を使うため本文読取りの回数は減らさず、JSON不備400・内部設定500・本文サイズ・利用枠と既存監査も維持する。MOD-33時点では、tool_choiceのオブジェクトをfunctionに限定する既存条件の目的コメントだけを整え、条件を変えていない。その後のallowed_toolsによるfunction選択はMOD-34に従い、この73件を新しい選択条件の検証結果へ転用しない。ローカル実装・合成検証済みで、MOD-33時点の関連73件が初回からすべて成功した（12.376s、失敗・スキップなし）。実C#22対照と親フロー41対照でincludeの共有19条件（透過16・別条件による拒否3）を確認した。先行するCode Interpreter拒否の実C#24対照・親フロー45対照、store・alias・JSON400・設定500・本文413、実Bicep／Python生成XMLの一致、正本所有先とRazor構文も成功した。Azure未反映・実Foundryでの新しい指定の受入は未確認である。MOD-32の71件は当時の拒否・透過条件に対する別結果であり、MOD-33の検証成功へ転用しない。

Responsesのtool_choiceでallowed_toolsによるfunction選択（MOD-34）はローカル実装・結合検証済みである。オブジェクト指定はtypeがfunction、またはtypeがallowed_toolsで選択先toolsが配列かつ全要素がオブジェクト・文字列のtype: functionである場合を通す。空の選択先配列は許可し、mode・name・他項目と配列の順序・重複を保持して提供元へ渡す。mode・名前の型や値、関数宣言との一致は再検査しない。allowed_tools内のtoolsの欠落・null・非配列、要素が非オブジェクト、typeの欠落・null・非文字列・function以外、内蔵ツールやMCPとの混在は既存の403／AUTHORIZATION_DENIEDで拒否する。オブジェクト以外のtool_choiceは既存の扱いを維持する。要求全体のtoolsのfunction限定とinput直下code_interpreter_call拒否、store=false・指定時conversation=null／background=false、include透過、JSON400・設定500・本文サイズ・利用枠・監査を維持し、本文読取りを増やさない。MCP実行を開放する変更ではない。Azure未反映・実Foundryでの新しい選択指定の受入は未確認である。MOD-33の73件は当時の条件に対する結果で、新しい選択条件の検証成功へ転用しない。

監査モデル名の登録一覧照合撤去（MOD-35）は、利用するモデルをAPIMの登録一覧で制限しない既承認方針に合わせた変更である。audit-body-model.xmlとaudit-context.xmlからallowedModelsとの比較だけを撤去し、新しく配備したモデルも一覧を更新せずtargetModelへ記録する。既存の正規表現`^[A-Za-z0-9_.-]{1,160}$`、空値・形式不一致・読取り例外時の空文字を維持する。共通部品はWebSocket設定から始め、準備済みHTTP本文変数、pathModelのパス、操作の固定modelで順に上書きする既存の取得順序を保つ。HTTP専用の本文読取りではpreserveContentを維持し、共通部品にはBody.Asを含めない。aliasは監査前に解決・書換えた配備名を記録する。allowedModelsの設定はPortalなどの用途に残し、認証・親要求の追跡候補・資格情報の処理と監査項目を変更しない。ローカル実装・結合検証済みで、Azure未反映・新しい監査モデル条件の実経路ログは未確認である。

Bearerの取消・切断の回数計数免除（MOD-36）は、既存の取消を止めない意図に合わせ、rate-limit.xmlのuser分岐も購読キーと同じgatewayRateLimitExemptを使うようにする。既に免除と判定した要求ではgatewayRateDecision.enabledをfalseとし、回数制限を実行しない。HTTPのrealtimeHangup／A2A tasks/cancelとMCPの既存DELETE／cancelの選択条件を保ち、新しい免除対象は追加しない。社員本人・利用枠プロファイルの確認、設定済みcalls・期間・counterKey、トークン制限と429応答は維持する。ローカル実装・結合検証済みで、Azure未反映・実カウンターや枠超過後の取消・切断の実経路動作は未確認である。

MOD-34～36の検証は、最終の結合選択104件がすべて成功した一つの結果である（14.853s、失敗・スキップなし）。初回の87件選択では、XML属性内の行コメントが生成時の1行化で後続式を覆い、回数式の準備とRazor検証に失敗した。コメントをブロック形式へ修正し、既存監査対照を新条件へ揃えた固定ソースで最終選択を実行した。function選択の共有69条件（通過29・拒否40）を実target72対照・親フロー141対照、監査専用116対照と既存監査40対照、取消免除の追加16実式対照で確認した。個別の全スイートをそれぞれ実行した結果とは扱わず、Azure反映・実Foundry受入・実監査ログとカウンターは未確認である。

Hostedの本文読取り不備400への分類（MOD-37）は、hostedAgentPostでJSONオブジェクトの読取りが例外になる場合とbodyがnullの場合だけを400・INVALID_REQUEST_BODYへ分ける。共通の呼び出し元でrejected監査・理由Invalid request body、本文なし・apim-request-id・Cache-Control: no-storeを使う。MOD-37時点では、読めたHosted本文のメソッド・項目・stream・store・metadata・inputによる403、A2Aの読取り不備403、内部操作設定JSON不備500を維持し、本文・読取り回数と残る拒否条件は変えていない。現在のメソッド再確認の撤去はMOD-39に従う。ローカル実装・関連検証済みで、MOD-37は前回の結合選択へ新しい1メソッドを加えた105件が初回からすべて成功した（13.811s、失敗・スキップなし）。Hostedの共有46条件（200が11・読取り400が12・残る403が23）を実targetのHosted48対照と親フロー73対照で確認し、A2A読取り403の11対照、Responses・generic・設定500と監査分類・理由文・no-store・要求ID・本文なしも確認した。既存のfunction選択69条件、監査・回数免除も同じ選択で成功した。Azure未反映・実経路の新しい400応答と監査は未確認である。MOD-34～36の104件は前段の結合結果で、この分類変更の検証成功へ転用しない。

親要求の追跡候補で確認済み呼出者IDを使う整理（MOD-38）は、audit-context.xmlのgatewayAgentTraceがgatewayDecision.callerIdをactorIdへ引き継ぎ、allowed・認証方式・購読情報・社員情報JSONの再読取りと本人IDの再照合を省く。HTTP・protocol・MCPの上位処理が追跡候補の準備前に認証結果を確認し、その間にgatewayDecisionを上書きしない引渡し経路を使う。X-Core-Agent-Parent-Idの有無・単一値・空GUIDでないことと、その後のヘッダー削除は維持する。ヘッダーなしと呼出者IDが空白の場合はnone、ヘッダー不備と読取り例外はinvalid、有効な申告はcandidate・ハイフン付きclaimedParentId・確認済みactorIdの既存形式を使う。監査の相関候補であり、認可や実際の親子関係の証明には使わない。ローカル実装・関連検証済みで、前回の105件選択へ追跡候補の7メソッドを加えたMOD-38の112件が初回からすべて成功した（17.637s、失敗・スキップなし）。実式6メソッドの76対照は先行する認証・引渡し不備36件のtrace前停止、両認証のHTTP／protocol／MCP正常6件、親ヘッダー24件と内部fallback10件で、配置確認1メソッドも成功した。関係のない内部変数・購読情報・本文の読取りは0回。既存の監査8メソッド36対照も今回の選択で確認し、MOD-35時点の40対照は当時の履歴として保持する。Azure未反映・実経路の追跡監査は未確認で、MOD-37の105件を今回の整理の検証成功へ転用しない。

7操作のメソッド・パス・操作IDの重複確認撤去（MOD-39）は、MOD-38の検証結果を記録した後に進めた。対象はHostedのPOST、LiveのPOST /sessions、Realtime作成と切断のPOST、Document開始のPOST／analyze_document、結果取得のGET／get_document_operation、PIIのPOST／pii_redact_textである。正式設定検査が各kindに必要なメソッド・パス・操作名の組合せを限定し、APIMが登録済み操作を照合するため、共通Targetで同じ条件を再確認しない。Hostedは残る本文条件だけを確認し、他の6操作はこの部品で本文を読まず内部成功を返す。Hosted読取り400と本文項目403、SDP・セッション／Documentチケット・modelクエリ・PII入力等の専用処理、認証・本文サイズ・利用枠・本文読取り回数を維持する。Azure Portal等で正式定義と異なる操作へ手動変更した場合の、この部品による二重拒否はなくなる。新しい操作やWAF範囲を追加せず、未登録メソッド・パスはAPIMの操作照合に従う。APIMのkind設定検査と、既存WAFの正規経路が求める操作名一致は別条件として維持する。Hosted／A2Aのサーバー実行基盤・保存・所有者分離は、連携先の構築・統合時に設計・検証する。ローカル実装・関連検証済みで、MOD-39の最終選択147件がすべて成功した（39.102s、失敗・スキップなし）。初回147件（38.603s）の2エラーは、Live／Realtime作成の操作名変更が維持したWAF整合条件で拒否される対照の期待値不備で、WAF条件を変えず期待値を修正して再実行した。正式設定39対照は全設定28（許可9・拒否19［API17・WAF2］）、APIのみの操作名許可4とkind上書き拒否7である。6kindの実target42／親フロー42対照、Hosted共有46（200が12・読取り400が12・残る403が22）と専用Document・Realtime所有者／SDP・LiveSDP・PII schemaも確認した。Azure未反映・実APIMの新条件の操作照合は未確認で、MOD-38の112件を今回の検証成功へ転用しない。

protocol／生成MCPの認証結果準備で未使用の設定復元とmode確認を省く整理（MOD-40）は、MOD-39の最終検証後に進めた。protocol.xmlとmcp.xmlのgatewayDecisionだけからAPI設定のBase64復元と認証方式ラベルmodeの再確認による403を撤去する。MOD-40時点ではキー用ヘッダーの有無でキー／Bearerを選ぶ分岐を維持し、現在の分岐整理はMOD-41に従う。共通認証の完了、Bearer側の社員JSON・schemaVersion・非空GUID・callerId／allocationId一致確認、拒否403と監査・要求ID・成功時の出力を維持する。先頭のcore-api-settings、WebSocketのモデル／query設定、MCPのtools/callとPII引数検査で必要な専用設定復元は残す。本文読取り・転送・利用枠・親要求追跡の扱いは変えない。ローカル実装・関連検証済みで、前回147件選択へ標準認証の5メソッドと追跡の2メソッドを加えたMOD-40の154件が初回からすべて成功した（51.063s、失敗・スキップなし）。HTTP・protocol・MCPの標準認証33対照と正式設定の未知／欠落mode拒否、追跡96対照（従来76と、単独式へ渡した未使用設定の5不備×protocol／MCP×キー／userの20）を確認した。配置の2メソッドではMCP引数検査用の別設定復元も維持されていることを確認した。Azure未反映・実経路の新しい認証結果準備は未確認。MOD-39の最終147件（39.102s）は前段の別実行として保持し、今回の検証成功へ転用しない。

protocol／生成MCPのキー／Bearer選択に検証済み購読を使う整理（MOD-41）は、MOD-40の検証後にローカル実装・関連検証を完了した。protocol.xmlとmcp.xmlのgatewayDecisionはcontext.Subscriptionがあればキー結果、なければBearer結果を準備し、この選択だけのgatewayKeyHeader読取りを削除する。キー用ヘッダーの形式確認とキー優先は先行する共通認証へ任せる。Bearer側の社員本人・IDの照合と不備403、profileId・回数制限・接続先・要求ID・監査と転送用ヘッダーの必要な削除を維持する。今回の選択50件がすべて成功した（13.167s、失敗・スキップなし）。初回50件（15.256s）は、人工的に購読を差し替えた単独式の対照で、HTTPだけが差替え購読を選ぶという旧期待値1件に不備があった。ソースを変更せず、protocol・MCPも同じ選択となる期待値へ揃えて再実行した。標準認証5・追跡10メソッドと拡張実式・Entra実式・Bicep／Razor生成・PII2件の選択で、追跡102対照（従来96とキー優先・ヘッダー削除の6）を確認した。引渡し式内のキー用ヘッダーとgatewayKeyHeaderの読取りは0回で、先行する共通認証と親要求ヘッダーの検査は維持する。Azure未反映・実経路の認証結果引渡しは未確認。MOD-40の154件を再実行した結果とは扱わない。

protocol／生成MCPのBearer分岐で購読なしと共通認証完了の再確認を省く整理（MOD-42）は、MOD-41の検証後にローカル実装を完了し、MOD-43の固定ソースで関連検証済みである。protocol.xmlとmcp.xmlのgatewayDecisionから、elseへ進む前に確定したcontext.Subscriptionの再確認と、gatewayCommonAuthenticationValidatedの存在・trueの再確認だけを削除する。先行するcore-api-request-preparationは共通認証の記録が欠落またはfalseなら503で止め、要求IDを返す。MOD-42時点ではBearer固有のgatewayEntraAuthenticationValidated、社員JSON・schemaVersion・非空GUID・callerId／allocationIdの照合と不備403を維持し、現在の社員IDの引渡しはMOD-43に従う。キー／Bearerの選択・優先、profileId・回数制限・接続先・監査・要求IDとヘッダー削除は変えない。前段のMOD-42選択50件全成功は引継ぎ文章でのみ受領し、実行時間と実行証跡は未確認である。今回のMOD-43の54件全成功（11.190s、失敗・スキップなし）には、先行準備の503と共通認証完了の再確認撤去の確認も含む。Azure未反映・実経路の引渡しは未確認。MOD-41の50件をこの変更の検証成功へ転用しない。

protocol／生成MCPで社員本人の確認済みIDをそのまま引き継ぐ整理（MOD-43）はローカル実装・関連検証済みである。protocol.xmlとmcp.xmlのgatewayDecisionは、gatewayUserAuthenticationをJSONオブジェクトへ読み、共通認証で確認・整形済みのtenantId・principalId・callerId・allocationIdをそのまま既存の出力項目へ引き継ぐ。この引渡しだけのschemaVersion確認・GUIDの解析と再整形・callerIdとallocationIdの作り直しと再照合を削除する。gatewayEntraAuthenticationValidatedの存在・true、JSON読取りと読取り例外の不許可結果、および既存の403・監査・要求IDは維持する。先行共通認証による本人確認、キー／Bearerの選択・優先、profileId・回数制限・接続先とヘッダー削除は変えない。今回の固定ソースで選択54件がすべて成功した（11.190s、失敗・スキップなし）。Razorを検証し、今回Bicepコンパイルは実施していない。Azure未反映・実経路の引渡しは未確認。先行変更の試験結果をこの変更の検証成功へ転用しない。

protocol／生成MCPの認証結果引渡し不備の応答整理（MOD-44）はローカル実装・関連検証済みである。gatewayDecision直後の不許可応答だけを、HTTPと同じ500・policy-error／AUTHENTICATION_PROCESSING_ERROR・理由Gateway processing failedへ揃え、Cache-Control: no-storeを追加する。判定条件・要求ID・本文なしを維持し、先行する資格情報不備の拒否、共通認証の準備503と操作固有403は変えない。固定ソースの関連54件が初回からすべて成功した（9.661s、失敗・スキップなし）。実XMLの3面12失敗対照で500・監査1回・要求ID／no-store・後段停止を確認し、正式応答の本文なし・理由文と先行拒否も維持した。今回Bicepコンパイルは未実施、Azure未反映・実経路未確認である。MOD-43の54件（11.190s）は前段の別実行である。

A2A本文JSON読取り不備の分類（MOD-45）はローカル実装・関連検証済みである。追加HTTP APIのkind=a2aJsonRpcでBody.As<JObject>の例外・nullだけを400／INVALID_REQUEST_BODYへ分け、読めた本文のjsonrpc=2.0、idの文字列／整数型、methodとallowedMethodsの条件403は維持する。既存のrejected監査・本文なし・要求ID・no-store、設定500・本文サイズ・利用枠を維持し、JSON-RPC errorオブジェクトの対応は別項目とする。標準APIにA2Aはなく、Card GET・WebSocket・MCPは対象外である。固定ソースの関連22件が初回からすべて成功（15.057s、失敗・スキップなし）し、400／403と本文・監査・応答の保持を確認した。今回Bicepコンパイルは未実施、Azure未反映・実A2Aサーバー受入未確認である。過去のA2A読取り403は当時の記録である。

Hosted metadataのサンプル制限撤去（MOD-46）はローカル実装・関連検証済みである。追加HTTP APIのkind=hostedAgentPostで、metadataの欠落・型・項目・値を理由にした403を撤去する。本文直下のinput／stream／store／metadataの4項目限定、storeがBoolean falseであること、stream／inputの既存条件、JSON400・内部設定500を維持する。親要求IDを注入する対象APIの既存処理は、object metadataの他値を保持し、非object metadataをobjectへ置換してcore_parent_request_idを上書きする。固定ソースの関連23件が初回からすべて成功した（14.447s、失敗・スキップなし）。標準APIは対象外、過去のmetadata403は当時の記録である。今回Bicepコンパイルは未実施、Azure未反映・実Hostedサーバー受入未確認である。

Hostedのstream型判定撤去（MOD-47）はローカル実装・関連検証済みである。追加HTTP APIのkind=hostedAgentPostでstreamのBoolean型限定403を撤去し、値を保持して型変換・補完は行わない。metadata制限撤去、store Boolean false必須・input・本文直下4項目限定、JSON400・設定500と親要求ID注入を維持する。固定ソースの関連20件が初回からすべて成功した（7.360s、失敗・スキップなし）。MOD-46の23件（14.447s）とstream検査維持は当時の別記録である。標準APIは対象外、今回Bicepコンパイルは未実施、Azure未反映・実Hostedの型受付／SSE未確認である。

Hostedのinput／text非空判定撤去（MOD-48）はローカル実装・関連検証済みである。文字列inputと、既存の1 user／1 input_text内のtextについて、空文字・空白だけを理由にした403を撤去し、値を保持する。inputの欠落・型、配列件数・role/type・許可項目とtext文字列、store Boolean false・本文4項目の条件は維持する。metadata／streamの承認済み条件と保存参照形式の受付範囲は変えない。固定ソースの関連20件が初回からすべて成功した（7.757s、失敗・スキップなし）。MOD-47の20件（7.360s）と非空検査維持は当時の別記録である。今回Bicepコンパイルは未実施、Azure未反映・実Hosted受入未確認である。

利用回数枠の本人情報再照合撤去（MOD-49）はローカル実装・関連検証済みである。社員JSONの再読取り・ID再照合だけを省き、gatewayDecisionの確認済み社員IDから同じ計数キーを生成する。正本をfragments/quota/core-rate-limitの同名XML・C#へ移し、Azure IDを維持する。Entra成功印・allowed／callerの基本検査と、profile・上限・DeepEquals・null・免除・キーの条件は未判断のまま維持し、回数制限全体の棚卸完了とは扱わない。実BicepコンパイルとPython／Bicep生成一致を含む関連59件が全件成功した（19.435s、失敗・スキップなし）。Azure未反映・実カウンター未確認である。

計数部品の社員認証成功印の再確認撤去（MOD-50）はローカル実装・関連検証済みである。成功印は共通認証・引渡し・計数直前の社員profileへ任せ、不正Bearerの先行拒否とallowed／caller・profile／DeepEquals／範囲・null無制限・免除・キー／counterを維持する。実Bicepコンパイル・生成一致を含む関連65件が全件成功した（15.836s、失敗・スキップなし）。MOD-49の59件と成功印維持は当時の別記録である。Azure未反映・実カウンター未確認である。

計数部品のallowed／caller非空の再確認撤去（MOD-51）はローカル実装・関連検証済みである。先行するHTTP／Protocolの引渡し失敗500、本人確認・社員profileと、キー側method／購読ID／caller一致を維持する。profile・DeepEquals・範囲・null無制限・免除・counter等は未判断のまま残す。実Bicepコンパイル・生成一致を含む関連65件が全件成功した（21.645s、失敗・スキップなし）。MOD-49／50の基本検査維持と結果は当時の別記録である。Azure未反映・実カウンター未確認である。

社員profile／requestRateの再検査撤去（MOD-52）はローカル実装・関連検証済みである。計数式のDeepEqualsと必須・型・範囲の再検査を直前UserProfileへ集約し、先行共通認証／UserProfileの同じ不備拒否を維持する。追加API・免除でもUserProfileを通し、profile／requestRate読取り・値適用、null無制限・counter・免除・キー側処理を維持する。前段とは別実行の関連65件が全件成功した（18.122s、失敗・スキップなし）。実Bicepコンパイル・生成一致も成功している。他の未判断条件は残り、Azure未反映・実カウンター未確認である。

キー側の認証結果再確認撤去（MOD-53）はローカル実装・関連検証済みである。認証方式・購読ID非空・caller一致の3条件を撤去し、共通認証済みcontext.Subscription.Idを直接使う。キー用設定のBase64復元・JObject解析を社員側のreturn後へ移し、社員要求では復元しない。先行共通認証・引渡しの不正キー拒否と失敗停止、override／SQL profile・null無制限・回数期間・counter・免除は維持する。関連66件が全件成功した（15.960s、失敗・スキップなし）。社員の非復元、先行401後の未実行と残る枠条件を確認し、実Bicepコンパイル・生成一致も成功した。MOD-49～52の維持条件と結果は各変更時点の別記録で、他のキー側処理は残件である。Azure未反映・実カウンター未確認である。

キー側のprofile存在再確認撤去（MOD-54）はローカル実装・関連検証済みである。profile存在確認・有効時の欠落throw・空JObject fallback・requestRate存在再確認を撤去し、準備済みprofileを直接読む。全入口で回数枠を含む初期値を用意する先行subscription-profileと、そのSQL不備503停止、社員UserProfileの不備停止を維持する。profileEnabled切替・優先順位・明示null無制限・値・counter・免除は維持する。関連73件が全件成功した（17.597s、失敗・スキップなし）。SQL契約不備503とrate未実行、正規null無制限・counter、全入口の準備を確認し、実Bicepコンパイル・生成一致も成功した。MOD-53の66件は別時点の結果で、他のキー側処理は残件である。Azure未反映・実カウンター未確認である。

SQL回数枠の直接選択（MOD-55）はローカル実装・関連検証済みである。SQL有効かつrequestRateがObjectのキー要求ではvaluesにprofileRateを直接選び、埋込みBase64設定の復元とoverride探索を省く。それ以外は従来の設定選択を維持し、制限なし時を含む内部calls／periodも同値を保つ。公開設定・APIM変数フラグを追加せず、profile切替・優先順位・明示null無制限・counter・免除・先行不備停止は維持する。関連74件が全件成功した（20.304s、失敗・スキップなし）。SQL Objectの非復元と免除、それ以外の内部値・counterと先行拒否を確認し、実Bicepコンパイル・生成一致も成功した。MOD-54の73件と残件は当時の別記録である。

回数枠のInbound棚卸・ローカル検証はここで一区切りである。この部品に未判断の独自拒否・設定再検査は残らず、枠選択・Object／null分岐・免除・APIM標準ポリシーへの引渡しは必要な処理として維持する。工程1全体は未完了で、トークン枠全体・社員profile・CM等の棚卸は未完了、APIM標準429・on-error・実分散カウンターは未確認、Azure未反映である。

トークン枠の社員counter重複確認撤去（MOD-56）はローカル実装・関連検証済みである。社員情報JSON再読取り・ID再照合・allowedと認証成功印の再確認を省き、準備済みverifiedTenantId／verifiedPrincipalIdから従来と同じcore-tokens-v1:user:tid:oidのキーを生成する。正本をfragments/quota/core-token-limitsの同名XML・C#へ移し、Azure IDを維持する。C#は計数キー組立て式だけとし、有効化等の他の式はXML内に残す。有効化式の成功印・allowed・設定一致・必須検査、TPM／quota・免除・キーcounter・対象操作は維持する。関連78件が全件成功した（21.162s、失敗・スキップなし）。実C#のID引継ぎと先行停止、数値型・5種類の期間、実Bicepコンパイル・生成一致・同名所有・Razor・説明保持を確認した。トークン制御全体と工程1全体の棚卸は未完了で、MOD-55の74件は別部品・別時点の結果である。Azure未反映・実カウンター未確認である。

トークン有効化式の認証成功再確認撤去（MOD-57）はローカル実装・関連検証済みである。社員側のallowedと認証成功印の存在・trueの再確認だけを撤去し、直前社員profileの先行停止を維持する。gatewayEntraUserLimitsの存在・復元・DeepEquals、TPM／quotaの必須・上限・null、免除・キー側・対象操作・counterは維持する。初回の正本移設と計数キーだけのC#所有は変更しない。関連79件が全件成功した（24.220s、失敗・スキップなし）。実C#の成功印非アクセス、孤立対照と正式な先行停止、残る設定不備停止・counter等を確認し、実Bicepコンパイル・生成一致・同名所有・Razor・説明保持も成功した。MOD-56の維持条件と78件（21.162s）は変更前の別実行である。トークン制御全体・工程1全体は未完了、Azure未反映・実カウンター未確認である。

トークン有効化式の社員設定再確認集約（MOD-58）はローカル実装・関連検証済みである。元設定存在・復元・DeepEquals・TPM／quota必須項目の再確認を先行社員profileへ集約する。準備済みprofileの読取りとnullによる有効化判断、先行profileの欠落・型・範囲・一致検査、上限・キー側・counter・対象操作・免除は維持する。関連79件が全件成功した（26.658s、失敗・スキップなし）。実有効化式の元設定・成功印非アクセス、先行UserProfileの不備停止、有限／nullの組合せ・counter・免除・数値型・5種類の期間を確認し、実Bicepコンパイル・生成一致・同名所有・Razor・説明保持も成功した。backendOverride等の対象選択とprofile読取り・標準ポリシー向けの値選択は未棚卸である。MOD-57の79件（24.220s）と当時の維持条件は別実行の記録である。トークン制御全体・工程1全体は未完了、Azure未反映・実カウンター未確認である。

トークン枠の実効接続先による対象選択（MOD-59）はローカル実装・関連検証済みである。tokenLimitOperationsをAPI既定接続先からbackendOverrideを反映した操作の実効接続先へ揃える。Foundry以外既定からFoundry Chat／Responsesへのoverrideは対象、Foundry既定からexternal Chatへのoverrideは対象外となり、標準openai-inference-v1は同じである。HTTP・Chatのパスとkind・Responsesのkind、モデル一覧非依存、枠値・null・免除・counter・標準ポリシー分岐を維持し、WebSocket／MCP／他形式へ広げない。関連85件が全件成功した（21.181s、失敗・スキップなし）。正規化overrideの対象と処理順・標準／対象外境界、既存転送先選択を確認し、実Bicepコンパイル・生成一致・同名所有・Razor・説明保持も成功した。nativeLlmOperationsの利用量監査対象選択は変更せず、profile読取り・標準ポリシー向け値選択とともに別残件である。MOD-58の79件（26.658s）は変更前の別実行である。トークン制御全体・工程1全体は未完了、Azure未反映・実カウンター未確認である。

Chat利用量監査の実効接続先による対象選択（MOD-60）はローカル実装・関連検証済みである。nativeLlmOperationsもbackendOverrideを反映した実効接続先へ揃える。非Foundry既定→Foundry Chatは監査対象・API LLM診断有効、Foundry既定→external Chatだけなら対象外・診断無効、他にFoundry Chatがある混在APIは診断有効を維持する。ResponsesはChat限定の対象選択に含めない。新しいemit-token-metricは追加せず、既存の監査照合案内とnative LLMログの対象だけを変える。body.bytes=0、global／生成MCP診断無効、store・認証・利用枠・counterは維持する。関連85件が全件成功した（26.796s、失敗・スキップなし）。実Bicepのoverride監査ID・診断・store対象と混在／対象外境界、本文非記録・既存枠条件を確認し、コンパイル・生成一致・同名所有・Razor・説明保持も成功した。MOD-59の85件（21.181s）は変更前の別実行である。トークン値取得・標準ポリシー分岐は読取りレビュー済みで説明整備予定、operations!=nullによる内部不備時の無計数続行は次の棚卸候補である。profile供給元等にも残件があり、トークン全体・工程1全体は未完了、Azure未反映・実native記録／counter未確認である。

トークン有効化式の操作ID一覧直接読取り（MOD-61）はローカル実装・関連検証済みである。JArray直読みとし、operations!=nullによる内部不備時の無計数続行を撤去する。欠落・null・別型は免除時も既存on-errorへ渡し、正常空配列・対象外・免除の枠値とcounterは維持する。一覧はモデル名ではなく操作IDである。必要値取得・0／Monthly内部値・標準ポリシー3分岐・Int32→Int64の目的コメントを整え、条件・値を維持する。C#は計数キー式だけ、他式はXML内のままで、準備JSON・設定・応答・ログを追加しない。関連88件が全件成功した（31.368s、失敗・スキップなし）。実C#34対照で一覧不備の例外伝播・正常値・先行停止を確認し、実Bicepコンパイル・生成一致・同名所有・Razor・説明保持と既存枠・override・診断・転送先選択も成功した。MOD-60の85件（26.796s）は変更前の別実行である。

core-token-limitsのローカルInbound棚卸はここで一区切りである。parse・値抽出・Int32→Int64・標準ポリシー3分岐は必要な処理として維持し、説明整備済みで、独自拒否の未判断の残件はない。profile供給元・工程1全体は未完了、Azure未反映・実native計数・429・on-error応答分類（工程2）は未確認である。

社員profileの認証成功再確認撤去（MOD-62）はローカル実装・関連検証済みである。先頭のallowedとgatewayEntraAuthenticationValidatedの存在・trueの再確認だけを撤去し、先行共通認証・引渡しの停止を維持する。正本をfragments/quota/core-entra-user-profileの同名XML・C#へ移し、Azure IDは維持する。社員ID一致、元limits再解析・DeepEquals、3項目の存在・型・範囲・期間と明示null、SQL側の処理は維持する。関連121件が全件成功した（36.106s、失敗・スキップなし）。実C#の孤立対照・成功印非アクセス、正式な先行停止と残る不備停止を確認し、実Bicepコンパイル・生成一致・同名所有・Razor対象・説明保持と既存counter・override・診断も成功した。この部品全体と工程1全体の棚卸は未完了、Azure未反映である。MOD-61の88件（31.368s）は別部品・別時点の結果として保持する。

社員profileのID再照合撤去（MOD-63）はローカル実装・関連検証済みである。callerId／allocationId再照合と、そのためだけのgatewayDecision JSON解析を撤去する。社員user JSON読取りと元limits再解析・DeepEquals、3項目・型・範囲・期間・null、SQL側・先行認証停止は維持する。配置とAzure IDはMOD-62から不変である。関連122件が全件成功した（28.680s、失敗・スキップなし）。decisionを直前に変更・直後に復元する孤立4対照と全profile呼出しで非アクセス・同じ出力を確認し、先行停止・残る枠条件・counter／override／診断、実Bicepコンパイル・生成一致・同名所有・Razor・説明保持も成功した。MOD-62のID一致維持と121件（36.106s）は当時の別実行として保持する。この部品全体と工程1全体は未完了、Azure未反映である。

社員profileの元設定再照合撤去（MOD-64）はローカル実装・関連検証済みである。元gatewayEntraUserLimits存在確認・JSON再解析・DeepEqualsだけを撤去する。先行共通認証の元設定検査と不備500、user JSONからのlimits取得・返却、limits非null／3項目・必須・型・範囲・期間・明示null、SQL側は維持する。関連123件が全件成功した（25.556s、失敗・スキップなし）。元設定を直前に変更・直後に復元する対照で非アクセス・同じcounter／tokenKey／監査本人、構造・型・範囲と先行認証の不備停止を確認し、実Bicepコンパイル・生成一致・同名所有・Razor・説明保持も成功した。MOD-62／63の元設定一致確認と121件（36.106s）／122件（28.680s）は当時の別実行として保持する。部品全体と工程1全体は未完了、Azure未反映である。

社員profileの設定検査集約（MOD-65）はローカル実装・関連検証済みである。残る構造・3項目限定・必須・型・範囲・期間の検査と専用localsを撤去し、user JSON解析→limitsのJObject取得→Formatting.Noneでの返却の3処理へ絞る。同じ設定を全検査する先行共通認証と不備500停止は維持し、読取り不能・missing・null・nonObjectは補完やcatchを追加せず既存on-errorへ渡す。SQL側・明示null・counter・免除・Azure ID・配置は維持する。関連125件が全件成功した（29.737s、失敗・スキップなし）。単独profileの人工設定12対照は直後に通常値へ復元し、同じ不備の正式共通認証500・監査と後段未実行、取得失敗7対照の例外伝播・補完なしを確認した。実Bicepコンパイル・生成一致・同名所有・Razor・説明保持と既存枠・override・診断も成功した。MOD-64の123件（25.556s）等は当時の別実行として保持する。

core-entra-user-profileのローカルInbound棚卸はここで一区切りである。必要な供給3処理を維持し、未判断の独自拒否は残らない。工程1全体と実SQL経路の受入は未完了、Azure未反映・実native動作／on-error応答分類は未確認である。

SQL購読profileの503監査追加（MOD-66）はローカル実装・関連検証済みである。正本をsnippets/quota/subscription-profileのXML・同名C#へ移し、C#はgatewayFetchedProfile式だけを所有する。503応答の直前に既存telemetryへpolicy-error／LIMIT_PROFILE_UNAVAILABLE／503を1回記録する。受入検査・503応答・Retry-After: 5・no-store・30秒cache・社員分岐・明示null・共有APIへの生成時inline方式は維持し、新しいAzure fragmentは登録しない。関連164件が全件成功した（33.446s、失敗・スキップなし）。実取得式・503監査・cache／bypass、全HTTP／WebSocket／外部MCPの生成・実行順と実Bicepコンパイル・生成一致・同名所有・Razor・日本語説明を確認した。独立レビューでも取得式と監査追加以外の旧フローが同一だった。MOD-65の社員profileの125件（29.737s）とローカルInbound一区切りは当時の別部品記録である。

SQL応答は別供給経路の受入検査で、撤去すると不備を後段で制限なしと解釈する場合があるため維持する。読取り棚卸で削除推奨のAPIM内重複はなく、この取得snippetのローカルInboundは一区切りである。実SQL経路の受入と工程1全体は未完了、Azure未反映である。

HTTP InboundのCM有無再判定撤去（MOD-67）はローカル実装・関連検証済みである。CM有無はBicep生成時に決定するため、`gatewayCredentialConnection`が非空かを実行時に再確認する`choose`だけを撤去する。CMありでは`core-credential-connection`、`core-credential-session-use`をこの順で直接呼び、CMなしでは従来の生成時置換で宣言済みの通常の下流認証を使う。接続設定のsetterと生成区画marker、本人・期限・失効／無効の確認と拒否、資格情報削除・接続先・利用枠・監査を維持する。Outbound・protocol・MCPは変更しない。関連73件が全件成功した（35.159s、失敗・スキップなし）。実Bicep生成・正本一致と配備容量の余裕、標準Graph・追加HTTPのCM有効／無効と通常の下流認証・固定接続先・資格情報削除・操作override、実CM C#の既存拒否・障害・期限・失効条件、Razorと処理順を確認した。MOD-66の164件（33.446s）は別部品・別実行の結果として保持する。CMなしの空setterも今回は維持し、CM全体の棚卸と工程1全体は未完了、Azure未反映である。

CMなしのHTTP空代入省略（MOD-68）はローカル実装・関連検証済みである。`gatewayCredentialConnection`のsetterを既存のCM生成区画へ移し、接続設定がないAPIでは未使用の空代入を生成しない。接続設定があるAPIは無効設定も含め、設定値の代入と`core-credential-connection`→`core-credential-session-use`の順序を維持する。既存のBicep生成分岐、Outbound・protocol・MCP、CM本体の本人・期限・失効／無効の拒否、認証・接続先・利用枠・監査は変更しない。関連40件が全件成功した（26.027s、失敗・スキップなし）。CMなしHTTPの全生成結果で空setterと変数参照がないこと、追加の外部keyVaultHeader／Foundry HTTP API、宣言済みMI audience・固定接続先・キー秘密設定・入口資格情報の削除順・操作override、実Bicep生成・正本一致と配備容量の余裕、CMあり／無効設定の代入値と2include順序、Razorを確認した。CM内部C#は未変更で、MOD-67の73件（35.159s）とMOD-66の164件（33.446s）は別実行として保持する。CM全体の棚卸と工程1全体は未完了、Azure未反映である。

HTTP共通末尾の要求ID再上書き撤去（MOD-69）はローカル実装・関連検証済みである。末尾の`x-ms-client-request-id`再上書き1行だけを撤去し、`<base />`直後の`core-api-request-preparation`が同じAPIM `RequestId`を上書きする処理は維持する。protocol・MCPのsetter、資格情報とCM専用ヘッダーの削除、固定接続先、下流認証、受付監査も維持する。関連56件が全件成功した（26.665s、失敗・スキップなし）。正規要求準備の実XML内C#式の25合成対照で旧処理との同値・ヘッダー除去・認証完了印不備503・例外伝播、全HTTPの先行fragment呼出しと共通固定setterの重複なし、宣言済み下流認証／ヘッダーとoverride優先、CM有無・無効設定・追加API、実Bicep生成・正本一致・配備容量の余裕、Razor・診断・経路を確認した。HTTP共通末尾のローカル棚卸・検証はここで一区切りである。MOD-68の40件（26.027s）とMOD-67／66の個別結果は別実行として保持する。追加Hosted／A2Aの本文契約、CM本体・protocol・横断確認と工程1全体は未完了、Azure未反映である。

Hostedのinput配列の最大件数撤去（MOD-70）はローカル実装・関連検証済みである。追加HTTP APIの`kind=hostedAgentPost`で最大1件という条件だけを撤去し、1件以上の全要素に既存の`role=user`・`role`／`content`限定、content1件・`type=input_text`・`type`／`text`限定・text文字列の検査を適用する。配列の順序・内容を保持し、文字列input、空配列403、本文直下4項目限定、store Boolean false必須、JSON400、保存参照形式の拒否、metadata・stream・本文サイズ・利用枠を維持する。他の件数・入力構造は変更しない。fixture表記差を補正した後の最終120件が全件成功した（35.950s、失敗・スキップなし）。共有Hosted85条件の実C#・親フロー評価で、新規複数35条件（通過3・拒否32）、後半の参照拒否、本文の順序・値と読取り1回、既存監査・標準受付境界、実Bicep生成・正本一致・配備容量の余裕を確認した。MOD-69の56件（26.665s）とHTTP共通末尾の一区切りは別の結果として保持する。工程1全体は未完了、Azure未反映・実Hosted受入未確認である。

Hostedのcontent配列の最大件数撤去（MOD-71）はローカル実装・関連検証済みである。各messageのcontentを1件以上の配列とし、最大1件という条件だけを撤去する。全partへ既存のオブジェクト・`type`／`text`限定・`type=input_text`・text文字列の検査を適用し、順序・値を保持して結合・削除は行わない。input全要素のuser／role-content限定、空input／content403、文字列input、store Boolean false必須、本文直下4項目限定、JSON400、保存参照形式の拒否、本文サイズ・metadata・利用枠を維持する。関連120件が初回から全件成功した（32.205s、失敗・スキップなし）。共有Hosted116条件の新part31条件（通過3・拒否28）と、前回content件数だけで拒否した2条件の200への変更を実C#・親フローで確認した。親raw本文の新4対照で、Unicode・空白を含む3messages各2partの完全保持と後続ID／file_id／input_file拒否を確認した。全要素の型・空配列境界、読取り1回・監査・標準受付境界、実Bicep生成・正本一致・容量・正本所有・Razor・説明も成功した。MOD-70の最終120件（35.950s）とfixture表記補正を含む記録、MOD-69のHTTP共通末尾の一区切りは別結果として保持する。工程1全体は未完了、Azure未反映・実Hosted受入と保存仕様は未確認である。

Hostedの空input／content配列による403撤去（MOD-72）はローカル実装・関連検証済みである。`input:[]`と`content:[]`だけを理由にした拒否を撤去する。文字列inputは従来どおり扱い、それ以外のinputと各contentで配列を取得できない欠落・null・別型の拒否は維持する。全message／partのrole・型・許可属性・参照形式の検査を維持し、空contentがあっても後続要素を検査する。本文を編集せず、store Boolean false必須、本文直下4項目限定、JSON400・本文サイズ・metadata・利用枠・受付監査を維持する。関連120件が初回から全件成功した（37.063s、失敗・スキップなし）。共有Hosted135条件の実C#・親フロー評価で空配列とUnicode原本文の完全保持、空content後のID／ファイル参照・型／null不備・後続part拒否を確認した。親raw本文の追加5対照、読取り1回・既存監査・正規化・標準操作境界と実Bicep生成・正本一致・容量・正本所有・Razor・説明も成功した。MOD-71の120件（32.205s）とMOD-70等の個別結果は当時の別実行として保持する。工程1全体は未完了、Azure未反映・実Hosted受入未確認である。

Hostedのstore false強制（MOD-73）はローカル実装・関連検証済みである。store Boolean false必須の403だけを撤去し、転送前に全`hostedAgentPost`の本文へ必ずBooleanの`store:false`を書く。省略・true・null・別型もfalseへ揃え、kindで選ぶため`targetAgent`の有無には依存しない。既存metadata注入と同じ本文読取り・set-bodyにまとめ、metadataは従来どおりtargetAgent非空のときだけ処理する。非Hostedにはstoreを追加せず、既存metadata処理を保持する。input／streamの値、本文直下4項目限定・参照形式拒否・JSON400・元本文サイズ・利用枠を維持する。関連123件が初回から全件成功した（42.035s、失敗・スキップなし）。共有Hosted139条件と親の追加76条件でfalse強制・metadata・input／stream保持・非Hosted除外・先行拒否を確認した。target直接は原本文を保持する1読取り、Hosted親はtarget1＋編集1の2読取りで、サイズ測定は別である。合法追加HTTPの実Bicep生成XMLと正本読戻しでsize→target→編集→rate順、先行拒否時の後段未到達・設定引渡し、容量・正本所有・Razor・説明を確認した。利用枠の実カウンター検証ではない。MOD-72等とparams統合結果は別実行として保持する。工程1全体は未完了・Azure未反映で、反映は工程1の区切りでまとめる。実Hosted受入と提供元のfalse遵守・保存防止は未確認である。

MOD-40～42の本人再照合と、MOD-43までのgatewayDecision直後の不許可403は各変更時点の契約である。現在のprotocol／MCPはMOD-43の確認済みIDの引渡しとMOD-44の内部不備500へ整理し、新しい条件の検証結果は別に記録する。

以下のMOD-17～25は、参照のnull化・Itemやidの削除・file ID拒否を段階的に検証した過去の記録である。各段落の「残る」「最新」や次段階への参照は当時の契約を指す。現在はMOD-29で参照加工・file ID拒否を撤去するため、旧条件の成功を新条件の成功へ転用しない。

**履歴: MOD-17／18** `previous_response_id`と`conversation`は一律403の対象から外し、それぞれ指定されていれば既存の`store=false`本文編集内でnullへ上書きして要求を続ける。未指定の項目は追加せず、両方指定されていても本文読取りを増やさない。この2項目の参照指定を取り消し、継続に必要な内容はクライアントが完全なItemsとしてinputへ渡す。モデルalias・input・他の項目と、file参照・tools等の残る条件は維持する。previous_response_id上書き時点の91件に続き、conversation上書き後も関連91件がすべて成功し、失敗・スキップはない。実targetで両項目それぞれ指定値7条件の通過、実gateway本文編集式で単独指定・未指定・両指定・両null・片方nullとalias／直接モデルの40対照によりnull化・未指定維持・store=false・他項目保持・編集読取り1回を確認した。Responses読取り400、残る403・内部設定500、先行400／500／413、正式登録の境界にも回帰はない。ローカル実装・検証完了、Azure未反映・実受入は未確認であり、旧参照拒否の実機結果を新条件の成功へ転用しない。

**履歴: MOD-19** `conversation_id`が存在するだけの独自403も撤去し、値を補正せず提供元へ転送する。未指定なら項目を追加しない。受付の適否は提供元が判定し、APIMが同項目を解釈する新しい処理は追加しない。previous_response_id／conversationの指定時null化とstore=false、alias・input・他値保持と残る条件は維持する。この拒否撤去後は関連92件がすべて成功し、失敗・スキップはない。実targetで3項目各8条件の通過、実gateway本文編集でconversation_idの8条件×alias／直接モデル16対照の原値保持と既存の両項目40対照のnull化・未指定保持・編集読取り1回を確認した。残る400／403／500、先行400／500／413と正式登録の境界にも回帰はない。ローカル実装・検証完了、Azure未反映・同項目を含む提供元の受付は未確認である。

**履歴: MOD-20** `prompt`は提供元の保存済みテンプレートを指定する項目であり、idを必須、version・variablesを任意とする。variablesには文字列や画像・ファイル入力を含められる。本Gatewayはpromptの存在による独自403を撤去し、指定されていれば既存のstore=false本文編集でprompt全体をnullへ上書きする。variables内の保存済みファイルIDも残さず、未指定なら項目を追加しない。保存テンプレートを使う指定は取り消され、必要な内容はクライアントがinputへ渡す。previous_response_id／conversationのnull化、conversation_id原値、alias・input・他値、file参照・tools等の残る条件を維持する。prompt上書き後の関連93件はすべて成功し、失敗・スキップはない。実targetで4項目各8条件と保存ファイル変数のpromptを通し、実本文編集26対照でprompt全体null・変数と原ID除去・未指定維持・4項目の組合せ・編集読取り1回を確認した。既存の両項目40対照とconversation_id原値保持16対照、残る400／403／500・先行400／500／413・正式登録境界にも回帰はない。ローカル実装・検証完了、Azure未反映・prompt:nullを含む実提供元の受付は未確認である。

**履歴: MOD-21** `background`の型やtrueによる独自403も撤去し、指定されていれば既存のstore=false本文編集内でfalseへ上書きする。未指定なら項目を追加せず、streamはそのまま保持する。背景処理の指定を取り消し、通常のJSON／SSE方式で処理を続ける。本文読取りを増やさず、3参照null・conversation_id原値・alias・input・他値と残るfile参照／tools等の条件を維持する。background上書き後の関連94件はすべて成功し、失敗・スキップはない。実targetの8条件、実本文編集8条件×stream true／false×alias／直接モデル32対照で指定時bool false・未指定維持・stream値保持・編集読取り1回を確認した。既存prompt26対照・両項目40対照・conversation_id16対照、残る400／403／500・先行400／500／413・正式登録境界にも回帰はない。ローカル実装・検証完了、Azure未反映・新条件の実受入とJSON／SSE応答は未確認である。

**履歴: MOD-22** `input`の欠落と文字列／配列以外による独自403も撤去し、未指定は未指定として保持し、入力形式の受付可否は提供元が判定する。この段階では配列の場合だけ、従来の範囲でfile_id・file_ids・vector_store_idsの403を維持した。明示item_referenceとfile_idのnullへの後続整理は下記に従う。新しい本文解析や型検査、配列以外のオブジェクトへの走査は追加しない。input形式撤去後の関連95件はすべて成功し、失敗・スキップはない。実targetの入力10条件と当時の保存参照4条件、実本文編集の10条件×alias／直接モデル20対照で値・未指定・既存補正・他項目・編集読取り1回を確認した。既存32＋26＋40＋16対照、残る403・reader400・内部500・先行400／500／413・正式登録境界にも回帰はない。ローカル実装・検証完了、Azure未反映・各input形式の実提供元受付とJSON／SSE応答は未確認である。

**履歴: MOD-23／24** 配列inputの再帰的なtype=item_referenceによる403も撤去し、既存の本文編集で配列直下のオブジェクトが文字列type=item_referenceを明示する場合は要素を取り除く。さらにtypeが未指定またはnullで小文字のidプロパティがある場合、idだけ、またはidとtype:nullだけの要素は取り除く。ほかのプロパティがあれば内容を保持してidプロパティだけを削除し、nullへの上書きは行わない。idの値の型・空値では判定しない。残る順序・内容・call_id、別のtypeを持つ完全ItemsのID、深いデータ、非配列は変更しない。全要素が該当する場合は空配列となり、後続の受付可否は提供元が判定する。file_id・file_ids・vector_store_idsとtools等の残る条件、既存補正と本文読取り回数を維持し、新しい本文読取りや配列以外への走査は追加しない。

**履歴: MOD-24の結果** type未指定／nullを整理した段階の関連97件はすべて成功し、失敗・スキップはない。実targetの追加43条件と実本文編集43条件×alias／直接モデル86対照で、type未指定／null×IDの8値種別×IDだけ／内容あり、未知・null値のプロパティ、大文字ID、ID欠落、隣接・完全Items・深い値・非配列を確認した。targetによる入力保持、IDだけの要素削除と内容がある要素のIDプロパティ削除、順序／暗号化内容／phase／call_id、既存補正・編集読取り1回を確認した。既存16条件×2の対照、保存file系4条件403、既存20＋32＋26＋40＋16対照・reader400・内部500・先行400／500／413・正式登録にも回帰はない。前段の明示item_reference整理時の96件は、その段階の別結果である。file_idのnullへの最新整理は下記に従う。ローカル実装・検証完了、Azure未反映・新条件の実提供元受付とJSON／SSE応答は未確認である。

**履歴: MOD-25** inputが配列の場合の既存の再帰走査で、file_idのnullだけを理由にした403を撤去する。入力ファイル・画像・深いunknownや引用情報などのfile_id:nullは原値を保持し、非nullは空文字・数値・false・配列・オブジェクトも従来どおり403となる。file_ids・vector_store_idsはnullや空配列でも項目の存在による403を維持する。非配列のinput、本文トップレベルやほかの領域へ検査を広げず、本文編集や読取りを追加しない。最新の関連98件はすべて成功し、失敗・スキップはない。共有39条件（許可18・拒否21）と他kind3条件の実target42条件、39条件×alias／直接モデル78対照と他kind3対照の実gateway81対照で、null原値と入力保持、9種の非null・混在・他資源の拒否、既存参照整理とtools／include、走査範囲を確認した。Responsesのtarget読取り1回、許可時の既存編集読取り1回、拒否時の編集なし、他kindのtarget読取り0回を保持し、既存43条件／86対照・16条件／32対照・20＋32＋26＋40＋16対照、reader400・内部500・先行400／500／413・正式登録にも回帰はない。MOD-24の97件は前段の別結果である。ローカル実装・検証完了、Azure未反映・file_id:nullを含む実提供元の受付とJSON／SSE応答は未確認である。

画像編集と文字起こしのWAFについても、登録モデル名を完全パスへ展開する方式から、公開操作の署名に従いモデル名に依存しない同じ用途別WAFを適用する方針を承認した。共通の署名設定からPythonとIaCが適用パスを生成し、既存の画像共有経路・本文形式設定を保持するローカル実装を行う。別操作やMCPを同じ範囲へ追加する場合の配備前確認は維持する。現Azureは旧方式のため、新モデルに同じWAFが適用されることはまとめ反映・読戻し後に確認する。提供元の成功や全入力の受付を保証しない。追加APIの非モデル変数の`.`／`..`によるURL正規化は既存設計の棚卸候補として分け、新しいAPIM拒否条件は追加しない。

標準の対象操作と転送先のパスは次のとおりである。モデル値の表記を保持して符号化し、別の接続先やAPI版を指定する入力には使わない。

| 操作 | 公開パス | 下流パス |
| --- | --- | --- |
| 画像編集 | `/models/openai-images/v1/deployments/{deployment}/edits` | `/openai/deployments/{deployment}/images/edits` |
| ファイル文字起こし | `/models/openai-audio/v1/deployments/{deployment}/transcriptions` | `/openai/deployments/{deployment}/audio/transcriptions` |

⑬の版の正本は[Document公開Schema](../../apps/api-gateway/schemas/document-analysis.json)のapiVersion.enumの単一値である。標準API定義の版2か所の重複を削除し、Python正規化で開始・状態取得へ補完する。明示不一致は配備前に拒否する。Bicepは正規化済み`entry.constraint.apiVersion`を`__DOCUMENT_API_VERSION__`から開始URI・受付URL照合・状態GETへ展開し、⑱では結果照合が、共通APIの準備済み`gatewayOperationConstraint`から`apiVersion`・`maxResponseBytes`を既存outbound try内で直接読む。Status専用`documentSettings`と埋込みBase64の復元・Bicep展開を撤去し、baseURL・API版markerと上限・版・チケット・結果の条件を維持する。⑲ではStatusの操作設定`gatewayOperationConstraint`の取得・JSON解析失敗だけを500へ分類し、応答前に既存`core-gateway-telemetry`で`policy-error`・`OPERATION_SETTINGS_INVALID`・500を1回記録する。㉙では開始・状態取得の認証結果`gatewayDecision`の取得・JSON解析と認証方式・owner引渡し不備だけを500・`policy-error`／`AUTHENTICATION_PROCESSING_ERROR`へ分け、⑲の操作設定不備と区別する。提供元本文・版・容量などの既存502は維持し、網羅的な再検査は追加しない。Portalの公開表示・結果検査も既存生成物を通じて同じ版を使う。対応版・公開仕様・チケット形式は維持し、実行時の追加読取りや検査は増やさない。

Document入力の棚卸①〜㉝では、MIME一覧・先頭バイト・独自の空本文・Content-Encodingの拒否と、サイズ確認だけの全量読取りを撤去する。共有の申告サイズ500,000,000 bytes超の413は維持し、共通Content-Type宣言検査には追加しない。原本と提供元JSONを本文変換なしで送り、文書とモデルの利用可否は提供元が判定する。modelの必須・単一値・非空と`.`／`..`の400を残し、2モデル一覧による拒否と、そのためだけの設定JSON再読取りは撤去する。model入力不備は400・`DocumentInvalidInput`、内部変数のモデル引渡し失敗は500・`OPERATION_SETTINGS_INVALID`／`DocumentAnalysisUnavailable`へ分け、利用回数制限・下流接続準備・`accepted`監査より前に止める。先行する操作設定・共有サイズ設定の検査と、固定接続先・チケット・返却URIとのモデル照合・応答の構造条件は維持する。開始と状態取得の操作ポリシーは`operation-policies/document-analysis/`へまとめる。他クエリの有無による一律400を撤去し、開始POSTではmodelをクエリから削除した後、pathだけの標準URI書換えと`copy-unmatched-params=true`でpages・featuresなどを転送した後、標準`set-query-parameter`でapi-versionは対応版へ上書きし、stringIndexTypeは`exists-action=skip`で指定値を保持して未指定時だけ`unicodeCodePoint`を補完する。API版は公開Schemaの単一値（現在`2024-11-30`）へ一元化し、⑭で文字位置の単位の指定を受け付け、不正値・複数値の適否は提供元へ任せる形へ整理する。㉑では状態取得GETの追加クエリを無視して同じジョブを取得し、クエリの有無だけで403にしない。チケット・UUID・本人・期限の照合、固定URI・対応版とURLへのAPIキー指定の共通拒否は維持する。㉒では`copy-unmatched-params=false`による未使用クエリの非転送を維持し、目的コメントを整える。対応版の結果取得GETはapi-versionのみを使う。将来正式なGET追加クエリが必要になれば、公開結果契約と合わせて見直す。WAF設定やクエリ名・値の監査出力を追加しない。①〜㉝の変更はAzure反映・読戻し照合を完了した。独立して作成した小文書の原本・Base64入力は、キー／Bearerで202受付から本文なしGETの200まで一部確認した。申告サイズ境界・内部障害500・期限切れ410など残る受入は未実施である。Portalの原本アップロード・2モデル選択・BFF制約は今回変更しない。[公開契約](document-analysis.md)を参照する。

モデル選択前のサイズは配備時に操作上限へ集約し、要求ごとの候補一覧集計を省く。既存のモデル別上限は差分設定へ保持し、[APIM ClassicのBuffered payload上限](https://learn.microsoft.com/en-us/azure/api-management/api-management-gateways-overview)の500 MiBを維持する。モデル選択で測定した元本文バイト数を後段の共有部品へ引き継ぎ、サイズだけの再読取りを省く。選択後の上限比較と超過413を維持し、未測定の操作は従来どおり測定する。この測定値再利用はローカル実装・関連検証を完了し、Azure未反映である。

クライアントはprofileを選ばず、API・操作とモデル名を指定する。旧ヘッダーは下流転送前に削除する。内部profiles・defaultProfile・modalitiesも撤去し、一つのモデルから一つの設定を取得する。FLUXの固定URIを維持し、出力token項目とモデル上限の設定はPortal表示・要求生成などの用途へ残す。任意の`settingId`を既存監査の`decision.profileId`へ明示転記する。購読利用枠profileやCMの`profileName`は変更対象外である。

構造整理後の棚卸で、モデル選択部品の登録一覧による先行403と、出力上限のための`core-model-limits`の能力表照合をローカルで撤去した。続く完全撤廃の承認で、`core-api-target-validation`の本文モデル一覧403も撤去する。同じ接続先・API形式の登録外モデルは、個別差分がなくても操作の基本設定を使う。FLUX等の異なる接続パスに必要な固定URI選択、本文・toolsの別条件は維持する。提供元のモデル追加だけで同じ経路を使うローカル対照と、Azure反映・読戻し・実疎通は区別する。[操作設定の部品](../../apps/api-gateway/policies/fragments/request/core-api-operation-settings/README.md)を参照する。

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

Agentのinput透過（MOD-74）はローカル実装・関連検証済みである。`hostedAgentPost`のinput必須・型・user／input_text・許可属性というサンプル検査を撤去し、完全Items・一般ID・file_id・引用情報・未指定を保持する。配列直下の明示的なcode_interpreter_callだけは通常Responsesと同じ条件で403である。MOD-74時点では本文4項目・JSON400・サイズ・認証／枠・store:falseと既存metadataを維持した。関連123件が初回から全件成功した（35.467s、失敗・スキップなし）。共有Hosted174条件と親の追加22条件、読取り・先行停止、実Bicep生成・配備／読戻しsource・正本所有・容量・Razorを確認した。参照転送は所有者隔離や保存停止の確認と別であり、本文直下の受付は別の残件である。MOD-73とは別実行、工程1全体は未完了・Azure未反映である。

Agentの通常オプションと保存防止（MOD-75）はローカル実装・関連検証済みである。本文4項目制限を撤去し、instructions・tools・agent_reference・promptとprevious_response_id・agent_session_idの従来403を目的別に明示する。その他の本文項目は原値で、既存Hosted本文編集でstore:falseと指定時conversation:null／background:falseへ揃える。未指定・非Hostedへ追加せず、metadata・input透過・CI直下拒否・JSON400・サイズ・認証／枠は維持する。関連123件が初回から全件成功した（38.115s、失敗・スキップなし）。共有Hosted264条件と親の追加70条件、読取り・先行停止、実Bicep生成・配備／読戻しsource・正本所有・容量・Razorを確認した。本文modelは原値であり、Agentモデルとの優先順位や全保存停止の保証とは別である。共通Agent入口のローカル棚卸は一区切りで、runtime／所有者／正式A2A・工程1全体は未完了、Azure未反映である。MOD-74とは別実行として扱う。

## 生成元出力と資源配備の分離

`api-publication.bicep`は単一の正本を保ち、正式CLIの[publication_templates.py](../../apps/api-gateway/deploy/publication_templates.py)がコンパイル結果を二つのARM要求へ分ける。生成専用は既存APIMを参照して完成XML・設定を出力し、資源配備は同じAPI・操作・部品を登録する。実配備には完成XMLを再度出力せず、`publicationSourceGenerationId`だけを出力して使った生成元を示す。

大きなJSONを出力から省くことと、生成元の完全照合を省くことは別である。保存コードのexport、入力、生成時のManaged Identity、成功した実配備との結び付きを照合し、監査・認証・ポリシー本文の確認は維持する。生成が失敗した段階ではAPIM資源を更新しない。新規構築・更新・復旧の順序は[登録用資材の説明](../../apps/api-gateway/README.md)を参照する。

所属を1件ずつ変更する際の中間読取りでは、全API本体・全APIの操作／Product／revision／schema／tool一覧、共有ポリシー・fragment・Named Value・backend・Product、外部の事前条件を再照合する。個別定義とXMLは、変更対象APIとMCPの操作参照でつながるAPIを両方向・推移的に照合し、無関係APIの個別子資源への反復読取りを省く。保存した全snapshotは保持し、staging前後・共通認証切替後の生成元照合と、全所属変更後の全件照合を続ける。無関係な個別子資源の変更は次の当該API確認または最終全件照合で拒否し、今回追加した所属だけを解除して、正常完了にはしない。未知の構造・未完の生成元は全件読取りに戻し、回復処理と旧構成の明示的整合も従来の全件読取りを使う。この選択は内部処理で行い、params／configの切替フラグは追加しない。

## 過去の生成元と現在の正本の照合

旧認証互換XML・依存C#と旧形式の再生成処理は公開資材から撤去する。過去の保護された生成元のraw・参照先・ETagを、現在のXMLへ読み替えない。履歴照合は、同じ完全なAPI構成を持つ独立に検証済みのcurrent scopeと、保存済みの過去rawをそれぞれ厳密に照合する。

現在の正本から派生する共通認証inline生成は、閉じたGlobalの初期構築に使用する。実fragment辞書から読む既知の旧説明差と、有限のBroker/cache移行候補も維持する。これらを、旧literal設定blob・廃止済みgRPC免除・旧audit版の再生成へ広げない。

## 全体で共通のポリシー

現行の公開資材はHTTP・WebSocket・MCPを対象とする。gRPCの専用入口・API・入口確認の免除は廃止した。

| 処理 | 実施内容 | 主なファイル |
| --- | --- | --- |
| 信頼した入口 | AppGW経由の識別値を通常HTTP(S)で常設確認。gRPC専用の入口免除は廃止 | `fragments/ingress/core-trusted-ingress/core-trusted-ingress.xml`、`service.xml` |
| 購読キー | 設定済みヘッダーが1個で空でないこと、`context.Subscription.Id`の存在を確認。URLのキーを拒否 | `service.xml` |
| Entraアクセストークン | キーヘッダーがない要求で署名・期限、認証用テナント、API audience、v2、`Gateway.Invoke`を検証。クライアントアプリIDは認可の制限に使わない | `fragments/authentication/core-common-authentication/core-common-authentication.xml`、`service.xml` |
| 呼出主体 | キーは`subscription:<購読ID>`、Bearerは`user:<テナントID>:<ユーザーObject ID>`。同じ購読または同じユーザーの利用枠を共有 | [各APIテンプレート](../../apps/api-gateway/policies/api-templates/README.md) |
| 要求ID | 正常応答・例外応答へ監査と同じ`apim-request-id`を設定 | `service.xml` |
| CM補助 | キーまたはBearerで検証した主体を内部brokerへ渡す。個人APIではSQL台帳の本人セッションと同じ主体を照合 | `credential_login.py`、`credential-login/*.xml`、`fragments/authentication/core-credential-connection/` |

`core-subscription-key-header`をGateway配備が管理する名前付きの値として使用する。既定は`api-key`。`Ocp-Apim-Subscription-Key`を採用する環境では共通設定を変更する。APIごとの自動alias受入は行わない。`Authorization: Bearer`にはEntraアクセストークンを送り、購読キーをBearerトークンとして扱わない。両ヘッダーを送った要求は購読キー経路で検証する。

通常APIの購読キーは、設定済みヘッダーでの送信だけを受け付ける。解析済みqueryに`subscription-key`または`api-key`があれば、正しいキーやBearerをヘッダーへ送っていても拒否する。この確認は`api-version`等の通常のquery項目を対象にせず、STEP3で確認したCMのブラウザー受付の例外も変更しない。URL指定のキーを受け付けるには認証方法の選択も変更する必要があり、拒否条件の削除だけでは対応できない。

ヘッダーでの送信に限定する目的は、URL経由でキーが履歴・アクセスログへ残る機会を減らすことである。APIMでの拒否や診断の既知query項目の非表示設定は、ブラウザー履歴や前段のAppGW・プロキシログからキーを消す処理ではない。query名の大小文字・エンコードの扱いは実APIMの解析に依存するため、全表記を検出・非表示にできるとは扱わない。

キー用ヘッダーがない通常要求は先にBearer側へ進むため、キー検証側には独立したヘッダー欠落の拒否分岐を置かない。既存の不正キー判定で空値を先に確認し、ヘッダーがなくても配列参照を実行せず拒否する。初回配備の途中で使うポリシーもこの検査で401を返せるため、初回専用の欠落分岐は追加しない。キー検証側の拒否の監査コードは`INVALID_AUTHENTICATION`に統一する。

共通入口の認証失敗は401と要求IDを返す。無効・停止・範囲外のキーは拒否し、同時に有効なBearerがあっても救済しない。キーも有効なBearerもない要求を業務APIへ通さない。信頼したHTTP入口の不一致は403。専用の購読不要ProductはBearer要求を共通認証へ届けるために使い、既存の別のOpen Productや未認証の迂回経路がないことを公開計画で確認する。

基盤が提供するすべてのAPIは、キー／Bearerの両認証に標準対応する。STEP3でCMのブラウザー受付の例外を判定した後、STEP4はキー用ヘッダーの有無だけで認証方法を選ぶ。Bearer分岐の条件へAPI名の一覧を埋め込まない。APIごとにBearer対応をtrue／falseで宣言する設定や実行時の分岐は設けない。配備前に、生成した全API・操作のポリシーが共通認証を継承し、検証済みの購読または社員を扱う構成であることを照合する。構成が不足していれば配備を停止し、要求受付後にAPI別の対応フラグで拒否する構成にはしない。共通認証の完了状態やEntraトークンの検証結果を確認する処理は引き続き残す。専用Productへ所属させるAPI集合は、配備処理が正本から自動生成して照合するために保持する。要求ごとの認証方法を選ぶ条件には使わない。

Bearer要求のProductが存在し、専用Productであることは共通認証で一度だけ確認する。各APIテンプレートはProduct確認を重複させず、共通認証とEntra検証の完了、検証済み本人情報の整合性を確認して`gatewayDecision`へ引き渡す。

キー要求の検証済み購読の存在と、購読IDが空でないこと、キー用ヘッダーが空・空白だけでなく値の数が1つであることは、共通認証で一度だけ確認する。各APIテンプレートは共通認証の完了を確認した後、この購読IDを呼出主体へ引き継ぎ、同じ購読・ヘッダー検査を繰り返さない。値の数はAPIMが認識した値配列の個数であり、生のHTTPヘッダー行数やカンマを含む1文字列と同一視しない。Bearer側の購読混在と本人情報の整合確認は維持する。MCPからRESTへの転送は別要求なので、REST側の共通認証も実行する。

キー用ヘッダーには専用の名前を設定する。APIが要求IDへ書き換える`x-ms-client-request-id`と、AppGWの相関用の`x-appgw-trace-id`は使用できず、既存の認証・接続用ヘッダーと同様に配備前の設定検査で拒否する。これにより、共通認証から各APIへ引き継ぐ途中で内部ヘッダーの処理がキーを書き換える構成を防ぐ。

## APIごとのポリシー

APIと操作のスコープに登録するテンプレートは、[REST、WebSocket、外部MCP、生成MCP](../../apps/api-gateway/policies/api-templates/README.md)に分かれる。全体スコープには`service.xml`を登録する。

| 処理 | 実施内容 |
| --- | --- |
| 共通処理の継承と要求準備 | `<base/>`直後に`core-api-request-preparation`を呼び、内部ID除去・APIM要求ID設定・完了マーカー確認を行う。未設定またはfalseは503 |
| 操作・モデル | 公開Operationと固定モデル・profile・URI・queryを照合する |
| 入力サイズ・項目条件 | APIMの技術上限と残存する型・必須構造を検査。HTTPの出力トークン数・汎用整数条件は提供元へ委ね、Portalの入力検査と区別する。試験用の小容量・固定件数は標準AI APIへ追加しない |
| 回数制限 | キーは検証済み購読、Bearerは検証済みユーザーのcounterを使用。CMの追加資格情報で入口の利用枠を切り替えない |
| queryの観測 | `core-query-observation`がAPIの明示した参照から観測ラベルを選び、有無・複数値だけを記録用JSONへまとめる。標準3ラベルと対象を維持し、modeや共有Named Valueは使わない |
| 操作の入力契約 | query観測の後、本文読取りより前に`core-api-input-validation`が対象表`inputContracts`を選び、共通のContent-Type宣言条件を確認する。対象13操作は`inputContract={}`、生成表の対象値も`{}`。会社のMIME一覧比較・メソッド再確認・query拒否は撤去する |
| 利用枠の設定 | Bearerは共通認証で検証済みの利用者に設定済みの上限を適用する。購読キーの共通上限は標準資材で設定しない。[購読運用](../operations/access-and-keys.md) |
| LLMトークン上限 | `token-limit.xml`を対応HTTP APIだけから参照。検証済み利用枠の毎分・期間内トークン数をAPIM標準ポリシーへ渡す。明示した制限なしの設定と設定欠落を区別する |
| 下流認証 | Foundry等はMI、外部サービスは登録済みの認証設定。キー・入口のユーザートークン・本人sessionを下流へ流さない |
| 監査 | 要求ID、呼出主体、認証方式、購読またはユーザー・テナント・クライアント、API、Operation、モデル、状態、使用量を記録。本文・token・keyを含めない |

各APIの先頭で棚卸・承認した3処理は、内部用の`X-Core-Caller-Id`／`X-Core-Subscription-Id`を削除して続行、`x-ms-client-request-id`をAPIM要求IDで上書き、共通認証の完了を確認する方針で維持する。XMLだけのleaf fragmentへまとめ、通常HTTP・WebSocketの接続開始・MCPの3テンプレートから呼び出す。認証の再実行やCMブラウザー受付の変更は含めず、後段の要求ID再設定とBearer側の再確認は別の棚卸で扱う。[要求準備の説明](../../apps/api-gateway/policies/fragments/request/core-api-request-preparation/README.md)を参照する。

棚卸⑦ではquery部品を観測専用にする方針を承認した。HTTP操作の`queryObservation`、生成MCPの`mcp.queryObservation`、外部MCPの`mcpProxy.endpoints[].queryObservation`から、Bicepが`gatewayControlSettings`の操作ID辞書`queryObservations`と、メソッド・公開相対path辞書`mcpQueryObservations`を作る。サービス・モデル形式・入力制約kindに依存せず、未指定の入口は対象外である。標準対象はLive・Realtimeの作成とPIIのMCP表口、ラベルは`liveCreate`・`realtimeCreate`・`piiMcp`を保持する。外部MCPでは公開pathと下流の`uriTemplate`を区別し、API登録と同じ公開path関数を使う。

`core-query-observation`は観測対象を選び、要求ごとの`gatewayQueryObservation`へ`route`・`httpMethod`・`presence`・`duplicates`だけを保存する。`route`は公開URLではなく観測ラベルで、ASCII英字で始まる1～64文字の英数字・`_`・`-`を使い、小文字の`none`／`unknown`を完全一致で予約する。mode、許可判定、応答status、観測結果による400／500の停止分岐は撤去する。観測できない値は`unknown`として扱う。認証完了の確認は先行する`core-api-request-preparation`が担当し、認証完了マーカー未設定／falseの503は維持する。

⑥で承認した有無・複数値の取得を保持し、件数区分`countBucket`の計算・出力を削除する。`core-query-telemetry`は観測JSONへ要求情報・認証済み主体・`phase`・`statusClass`を合わせる。イベント名`query-guard`、`schemaVersion: 2`、`auditSchemaVersion: 2`を維持し、mode・`query-rejected`・`guard-error`は新規出力しない。記録段階は`received`・`backend-response`・`policy-error`で、旧ログは移行前のphase・modeとして区別する。他イベントのschema 1と旧query/schema 1のKQL読取り互換は別に保持する方針である。

旧`gateway.queryGuards`、APIの`queryGuard`参照、`core-query-guard`fragment、`core-query-guard-modes` Named Valueは廃止対象であり、現行設定として使わない。④・⑤までのmode補完・設定不備で停止する方式は移行前の設計となる。新fragmentと参照APIのAzure反映・全graph照合・読戻し、正本checkpoint更新は完了した。キー／BearerのResponses JSON・SSEと対応する監査照合を確認した。旧fragment・旧Named Valueは参照不在を確認して退役し、読戻しで不在を確認した。現行生成元・構築入力・正本checkpoint・通常配備の期待値にも旧資材を含まない。所有・currentの一般照合と過去の保護証跡は維持する。query観測・転送や他の経路の受入は未完である。

外部MCPの観測用フローは`api-templates/mcp/external.xml`が所有する。WebSocketの受信queryと固定queryの制御は`api-templates/ws/ws.xml`で管理する。各設定の確認は[query観測部品](../../apps/api-gateway/policies/fragments/request/core-query-observation/README.md)と[運用時の確認](../operations/apim.md)を参照する。

その後に承認したPIIの除外分割は、App GatewayのWAF設定として別に管理する。現行構成はRESTの`g-pii-rest`で`text`、MCPの`g-pii-mcp`で`params.arguments.text`だけを`RequestArgValues / Equals`から除外し、`p-pii-redact`は維持する。このWAF変更はAPIMの入力検査とは独立し、認証・モデル機能・OBO・URI制約・他のWAF規則を変更しない。Azure反映・読戻しと正常REST／MCPの通過・マスキング確認を完了し、旧`g-pii-redact`は参照不在を確認して退役した。未実施の異常系と同名queryへの除外の挙動は残件である。通常reconcileによる旧資源の自動削除へは変更せず、移行中の旧binding保持・復旧と成功後の退役を区別する。[WAF側の設定・移行手順](../testing/waf-compatibility.md#pii入口の除外をrestとmcpへ分ける)を参照する。

HTTPの`operations[].inputContract = {}`で検査対象を明示し、Bicepが`gatewayControlSettings.inputContracts`へ対象操作IDと空の値`{}`を生成する。この空の値は条件不足ではなく、対象だけを記録し、全対象に共通のContent-Type宣言条件を使う意味である。MIME一覧を表へ持ち込まず、`operations[].method`とURLのARM登録は維持してAPIMに照合を任せる。従来の13操作だけを明示し、未指定のLive・Realtime・PII・MCP・WebSocket等へこの検査を追加しない。`gatewayApiInputValidationPassed`のtrueは対象操作での宣言条件の通過、または未指定による検査省略を表す。完全なMIME構文や要求全体の妥当性を保証せず、認証や後続の本文・モデル検査は省略しない。

Content-Typeの存在・単一値確認は`TryGetValue`による一度の取得で行い、唯一の値を`;`で分けた先頭のMIME部分が、空白除去後も空でないことを確認する。空値・空MIME部分は拒否し、会社の許可一覧とは比較しない。新しい構文パーサーを追加せず、HTTP／WAFの既存構文保護を維持する。単一値はAPIM解析後の配列の確認であり、元のHTTPに重複ヘッダー行がなかった証明ではない。

対象選択と部品構成の整理、query制限・メソッド再確認の撤去、取得処理の簡潔化、入口のMIME許可一覧比較の撤去を承認した。不備時の監査と400応答を維持し、結果変数を`gatewayApiInputValidationPassed`、監査理由を`INVALID_CONTENT_TYPE_HEADER`、応答理由を`Invalid Content-Type header`へ改名する方針も承認済みである。短い拒否フローは共有HTTP APIテンプレートに残し、本文なし400、APIM要求IDを持つ`apim-request-id`、`Cache-Control: no-store`を維持する。入口検査と不備時の400監査のAzure反映・読戻しは完了した。空本文でContent-Type未指定の400と対応する拒否監査を確認した。空値・空MIME部分・重複値など追加異常系は未実施である。未知メソッドの実APIM照合はローカルC#検証と分けて確認する。query観測・URL資格情報拒否は今回変更しない。後続の`core-model-limits`のMIME許可一覧比較と415も撤去した。`operationConstraints.contentTypes`は個別処理などで使うため残し、未棚卸の本文／モデル制約・URI書換え、Speech転送・WAFの形式制御は保持する。Document入力の現在の整理は上記の専用部品を参照する。残存制御は後の適用順で棚卸し、関連受入を別に整理する。共有部品の比較撤去だけで任意形式の下流受付やquery転送が保証されたとは扱わない。まとめ反映・読戻しとキー／BearerのResponses JSON・SSE、および対応する監査照合は完了した。他モデル・transportや残る異常系の受入は未完である。[入力検査の使い方と13操作の範囲](../../apps/api-gateway/policies/fragments/request/core-api-input-validation/README.md)を参照する。

## モデル・Operation固有のポリシー

| 対象 | 組み込む処理 | 初期設定の主な値・公開契約 |
|---|---|---|
| OpenAI Chat | モデル別の操作設定を選ぶ | Portalの出力設定は`max_completion_tokens`、各モデル128,000。出力指定の適否は提供元が判定 |
| DeepSeek・Mistral・Grok Chat | モデル別の操作設定を選ぶ | Portalの出力設定は`max_tokens`。DeepSeek 384,000／Grok 500,000。Mistralは未確認値を作らず自動値 |
| Model Router | 本文のモデル名からRouter配備を呼ぶ | 振り分け先で変わるため、固定最大値を設定せず自動値 |
| Responses | モデル別の操作設定を選び`store=false`へ設定 | Portalの出力設定は各モデル128,000。クライアントが会話入力を送る |
| FLUX 2 flex／pro | モデルから内部flex／pro設定を選び、その固定`backendUrlTemplate`へ書換え | クライアントはモデル名を指定する。`n`・`num_images`の1は設定・Portal用途へ残し、APIMの汎用整数拒否は撤去。受付件数は提供元が判定 |
| GPT Image | 本文またはURL配備名を照合しAzure用URIへ書換え | 生成3モデル、`n`の1〜10はPortal表示・要求生成用の設定。APIMの汎用整数拒否は撤去。Portalの件数・寸法・品質・形式の入力検査は維持 |
| Embedding・Rerank | 本文サイズと後段のmodel形式確認を検査 | Embeddingの入力形式・非空・次元と、Rerankの検索文・文書の入力形式・非空・任意の取得件数`top_n`の判定は提供元へ委ね、専用JSON解析も撤去する。Portal・配備前検査を維持し、検索側のモデル・次元・版を合わせる |
| ファイル文字起こし | URLの配備名とmultipart形式を検査 | `gpt-transcribe`。Gatewayの4 MiB枠を撤廃し、提供元のファイル制約に従う |
| Speech | SSML／WAVを検査し、Speech用URI・ヘッダー・Entra認証形式へ変換 | TTS 8 KiB・STT 1 MiBの独自枠を撤廃。DTD／ENTITY拒否、対応形式・提供元の時間制約は維持 |
| Decision | 配備名と型付き要求を検査し、固定の`/systemone`へ転送 | `decision-1`。8 KiBの独自枠を撤廃 |
| PII | `{text,language}`を検査しLanguage用要求・結果へ変換 | 固定のPIIモデル版、対応言語と警告コードを照合。生成MCPも同じREST処理を使う |
| Graph /me | キー利用時はCMの本人接続、Bearer利用時はOBOで、本人のユーザー情報を固定`$select`で取得 | 氏名・所属・連絡先・アカウント状態など12属性。任意のGraph URLや属性追加は不可 |
| Document Analysis | raw binaryの開始POSTと、署名チケット・同じ呼出主体での状態GETを提供 | 入力500,000,000 bytes、結果500 MiB、受付202→状態GET→完了200。[文書解析の契約](document-analysis.md) |

通常のAI APIの`maxRequestBytes`設定はAPIM Classicのバッファー上限**500 MiB（524,288,000 bytes）**に揃える。本文を読み出す検査では、JSONはBase64や構造を含むUTF-8本文全体、multipartはファイル以外の境界・項目も含む実サイズを比較する。音声認識・音声ファイルの文字起こし・画像編集・音声合成は`requestBodySizeCheck=declared`とし、共有部品による申告サイズの早期413を残す。前の3操作は共有部品による実サイズの読取り・比較を行わず、音声合成は専用SSML部品で実サイズも比較する。提供元のモデル・形式・料金プランによる上限が小さい場合は提供元が拒否するため、500 MiBは各モデルの受付保証ではない。Content-Lengthは必須化しない。`actual`または未指定の操作とTTSの専用部品は、chunked入力も実受信サイズで検査する。申告のみの操作でAzureが適用する技術制約は、この部品の実サイズ検査とは区別する。[APIMの技術上限](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits#api-management-gateway-runtime-limits)

出力能力値の正本は [model-capabilities.json](../../apps/api-gateway/model-capabilities.json)で、Portalの表示・要求生成・入力検査などに使う。HTTPの要求では出力トークン項目を省略でき、`core-model-limits`は指定された項目名・値・最大値を独自に検査せず、本文を書き換えずに提供元へ委ねるローカル実装へ整理した。Azureへの反映と提供元での実受入は未確認である。不明な固定上限を作らない。Realtime WebRTCの作成時の512は初期設定であり、モデル上限4,096とは区別する。WebSocket確立後やWebRTCのDataChannelはAPIMポリシーで検査しないため、Portalが送信する設定と、Gatewayが全クライアントへ強制する制限は同一ではない。

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

Live作成は共通の500 MiB枠と型・SDP形式を検査し、instructionsの独自文字数枠を設けない。初期voiceは`marin`。失敗入力は400、形式が不正な上流の成功応答は502へ整える。[Live作成のXML](../../apps/api-gateway/policies/operation-policies/live/live-create.xml)

WSSの`websocket.session.models`は接続後イベントで使うモデルのクライアント契約を表す。APIMが検査するのは接続開始時の要求である。frame内のモデル・音声・設定とセッション継続時間は、アプリと上流サービスで管理・検査する。HTTP要求回数と音声セッションの利用量は別に計測する。[Microsoft公式のWebSocket制約](https://learn.microsoft.com/en-us/azure/api-management/websocket-api)

WSSは接続時に設定済みの購読キーヘッダー、または`Authorization: Bearer`を送れるSDK・アプリサーバーから利用する。ブラウザは必要な認証ヘッダーを送れるBFF等を経由する。ブラウザのWebRTCは、アプリのサーバーがGatewayの作成APIを呼び、SDP answerをブラウザへ返す。

WebRTCの接続制御はAPIMを通り、音声メディアは端末とサービスのICE経路を通る。受入ではその宛先・ポート・端末ネットワークの到達性を確認する。[Azure Live WebRTC](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/gpt-live-webrtc)

## Credential Managerとの接合

CMは、必要なAPIが利用する共通の下流OAuth機能である。`graph-profile-v1`のキー経路と、CMが必要なMCPに適用する。GraphのBearer経路では、Gateway用BearerをBrokerでOBO交換し、Graphの委任トークンを固定の本人取得に使う。

本人接続は、CM共通APIでサインインを開始し、確認済みの呼出主体・本人・provider・接続・期限をセッションに結び付ける。キーでは同じ購読、Bearerでは同じテナント・ユーザーを照合し、両方式の間でセッションの所有者を移さない。対象APIへ`X-Core-Connection-Session`を送る。任意のUPNや接続名の指定を本人確認の代わりに使わない。

サービス共有接続は、サーバー設定のproviderとconnectionを使う。どちらもCMの`get-authorization-context identity-type=managed`がAPIM自身の接続利用権限を確認し、CMが下流tokenを取得・更新する。本人セッションの検査は、その前のCoreポリシーで行う。

初回のログインURL提示、サインイン、状態照会、元要求の再実行は[CM接続手順](credential-manager-login.md)を参照する。API応答だけで利用者のブラウザを開くとは扱わない。Dify等は対応ツール／プラグインで連携する。

接続再認証は409、下流の権限不足は403として区別し、下流の生エラー・token・Cookie・認証URLを業務応答へ流さない。

## ポータルで追加するAPI

初期構築では[API定義](../../apps/api-gateway/apis/README.md)から、操作、固定バックエンド、個別制御を宣言します。[Bicep](../../apps/api-gateway/api-publication.bicep)がXMLとC#を組み立て、同じ生成元を公開後の読戻しに使います。[適用先一覧](../../apps/api-gateway/policies/portal-examples/scope-map.md)では、標準APIの公開パス、操作ID、Azureのスコープ、個別XMLを確認できます。

| 資材 | Azureポータルでの適用先 | 対象 |
| --- | --- | --- |
| `service.xml` | All APIs | 信頼済み入口、共通認証、要求情報と監査用の主体 |
| `fragments/` | Policy fragments | 認証、監査、利用枠、要求準備などの共通処理 |
| `api-templates/rest/rest.xml` と `rest.cs` | REST APIのAll operations | Model REST、Tool REST、RESTのSSE、WebRTCのHTTP通話制御 |
| `api-templates/ws/ws.xml` と `ws.cs` | WebSocket APIの `onHandshake` | Realtime、文字起こし、Voice Live。WebRTC用のWebSocketシグナリングを含む |
| `api-templates/mcp/external.xml` | 外部MCP APIのAll operations | 外部MCPサーバーへの固定転送、必要な上流OAuth、セッションとSSE |
| `api-templates/mcp/generated.xml` と `generated.cs` | 生成MCP APIのAll operations | PIIなどの既存REST操作をMCPツールとして公開 |
| `api-templates/shared/stream.xml` と `stream.cs` | WebSocketと外部MCPの生成時に展開 | 両者で共通の認証結果、利用枠、監査、固定転送 |
| `operation-policies/` | 各Operation | Document、PII、Realtime、Liveなどに必要な固有制御 |

新しい操作は、下表の通信方式と必要な制御から選びます。バックエンドへ固定経路で転送し、入力項目やモデル機能の妥当性は提供元に任せます。RESTではJSON、SSE、multipart、バイナリを同じ分類で扱い、転送と受信上限を操作に合わせます。

| APIの分類 | 再利用するフロー | API・操作で設定する差分 |
| --- | --- | --- |
| テキスト生成 | REST JSON／SSE | 固定パス、下流認証、利用枠、SSEの転送 |
| 埋め込み、リランク、意思決定 | REST JSON | 固定パス、下流認証、利用枠 |
| 画像生成、音声STT／TTS | REST JSON／multipart／バイナリ | 受信上限、必要なヘッダー、SSMLで必要な外部参照対策 |
| リアルタイム音声 | WebSocketの接続開始、WebRTCのREST制御 | 固定接続先、モデルquery、通話の所有者と終了操作 |
| Tool REST API | REST | 下流権限、必要な操作の所有者、非同期結果の取得契約 |
| MCP | 外部MCP、生成MCP | 公開するtool、セッション、固定接続先、下流認証。生成MCPは元のREST操作の認可を再利用 |

### 初期構築用ポリシーの変更

RESTの `bodyModel`、`textEmbeddings`、`rerank` は、適用済みの操作設定を再度読み直して許可を返す処理を省きます。埋め込みやリランク、意思決定の一部と、同じ種類の制約を使う生成APIが対象です。モデルの上書き・本文加工・転送が必要な既存処理は適用操作の定義で確認できます。固定バックエンド、本文上限、共有資源の保護、本人・所有者、監査、利用枠はゲートウェイの責務です。

`inputContract` は、対象のContent-Typeヘッダーが1値で空でないことを確認します。WAFの型別ルールはヘッダーを基に分岐するため、このAPIMの確認を含めて保護します。Document・Realtimeの所有者照合、SSMLの外部参照対策、Responsesの保存・共有資源対策、生成MCPの引数とREST側の再認証については、[適用操作と根拠](apim-phase12-review.md)を参照します。

### 構築後のAPIMとWAF

手動追加APIは[REST／SSEのポリシー例](../../apps/api-gateway/policies/portal-examples/README.md)から、専用API ID、固定バックエンド、Operation IDとパス、利用枠を設定します。購読キーは専用の購読必須Product、本人Bearerを提供する場合は `manual-` で始まるAPIを購読不要の `manual-entra-users` に関連付けます。共通認証は同じテナント・audience・委任scope・本人を確認します。管理対象Productのメンバーシップには標準構築時の照合があるため、手動追加には独立したProductを使います。各Operationで `Calculate effective policy` を開き、入口・認証・利用枠・監査・認証情報の除去・固定転送先を確認します。 [APIMでのポリシー継承](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-policies)

AppGWでは新しいパスに実際に効くWAFとAPIMへのHTTPS経路を確認します。標準ではGateway側の既定経路と共通WAFを使います。パス別WAFを追加する際は、既定ポリシーに代わってそのパスで有効になる設定と、信頼済み入口ヘッダーの書換えを照合します。本文形式に固有の例外が必要な場合は、実際に遮断した正常要求からパス、項目、管理ルールを特定します。 [WAFの関連付けと優先順位](https://learn.microsoft.com/en-us/azure/web-application-firewall/ag/policy-overview)

監査は要求ID、認証済み主体、API・操作、転送先、結果を記録し、対応する提供元の利用量を照合します。現行の診断は入出力本文0バイトです。新しいSSE APIでは転送時に本文を読み込まず、提供元の診断とGateway要求を照合します。 [APIMのSSEと診断](https://learn.microsoft.com/en-us/azure/api-management/how-to-server-sent-events)

API、Product、固定バックエンド、診断、WAF関連付けを変更台帳に記録し、Core再配備前の差分確認に使います。具体的なGUI操作は[Claudeの手動追加手順](../operations.md#構築後にclaudeをguiで追加する)にまとめています。

## GUIで確認する場所

1. APIMの「名前付きの値」でキーヘッダー、上流audience、入口経路、Realtime署名鍵の参照を確認する。
2. APIs → All APIsのコードエディターで共通の購読キー検証とEntraアクセストークン検証を確認する。認証用テナント・API audience・`Gateway.Invoke`は適用された`validate-azure-ad-token`を見る。
3. 対象API → All operationsで操作制約・下流認証を確認する。CMを使うAPIは接続セッションの照合処理も確認する。
4. Operationのコードエディターで固定URI・Live／Realtime／PII等の固有処理を確認する。WSSは`onHandshake`を見る。
5. Policy fragmentsで`core-api-settings`のAPI設定準備、`core-api-request-preparation`の共通認証後の要求準備、`core-request-context`・`core-audit-authenticated-subject`・`core-gateway-telemetry`のC#説明、`core-trusted-ingress`の入口確認を読む。`core-rate-limit`・`core-credential-connection`等の個別処理もここで確認する。
6. Credential managerでprovider、connection、本人同意、APIM MIのaccess policyを確認する。

XMLには処理の目的・入力・失敗時の動作を説明する日本語コメントを記載する。登録前に計画を確認し、適用後のXMLを読み直して照合する。

監査出力の認証主体、要求context、AppGWログとの相関とqueryの採用規則は[監査の識別情報](audit-identification.md)を参照する。監査出力の追加と保存設計、資材の実装と対象環境の適用・受入を分けて扱う。


CM呼出者IDの整理（MOD-76）はローカル実装・関連検証済みです。[接続部品](../../apps/api-gateway/policies/fragments/authentication/core-credential-connection/README.md)は確認済みgatewayDecision.callerIdを使い、社員JSON・GUID整形・認証結果の再照合を省きます。Azure名とCM固有の所有者・session・Graph本人・期限・失効照合、内部空callerの既存403を維持します。実式・Python／実Bicep生成・保護source・構文と独立レビューを確認しました。追加raw XML Razorクラスは公式DLL未指定でスキップし、実行別の結果を部品READMEへ記録しています。内部不備分類・CM全体と工程1全体は未完了、Azure未反映です。


CMの内部callerエラー分類（MOD-77）はローカル実装・関連検証済みです。既存の空ID停止条件だけを500へ分け、[API展開用snippet](../../apps/api-gateway/policies/snippets/authentication/credential-caller-error/README.md)でpolicy-error／AUTHENTICATION_PROCESSING_ERROR／500の監査と要求ID・no-store付き本文なし応答を返します。監査全文をCMfragmentへ複製せず、本人接続の拒否と資格情報の取得・所有者照合を維持します。関連164件が初回から全件成功（39.283s、失敗・スキップなし）。初期容量precheck失敗と調整後の確認は部品READMEで別記録とします。工程1／2全体は未完了、Azure未反映です。

MOD-78は承認済みのCM重複3点をローカル実装・関連検証済みです。正規配置・維持する照合と処理・検証結果は[本人セッション部品](../../apps/api-gateway/policies/fragments/authentication/core-credential-session-use/README.md)へまとめます。CM全体・工程1／2全体は未完了、Azure未反映です。

MOD-79はローカル実装・関連検証済みです。9操作で共通トークン検証の失敗だけを既存401へ渡し、その他のエラー契約を維持します。有効／無効の生成・正本一致・限定したscope・容量と実式を確認しました。初回fixture不備とskipの補完は[部品README](../../apps/api-gateway/policies/fragments/authentication/core-entra-authentication-error/README.md)へ別実行として記録しています。工程1／2全体は未完了、Azure未反映です。
