# APIMポリシーの管理構成

共通の処理順序は `service.xml`、再利用する処理の実装は責務ごとのフォルダで管理する。
共通Inboundの見出しは、STEP1要求情報、STEP2入口確認、STEP3接続ログインの受付判定、STEP4キー／Bearer認証、STEP5認証済み利用者情報の連番で管理する。
初めて棚卸または改修する部品は、その作業時にこの構成へ移設し、Azureの日本語説明とXML／C#の処理説明も揃える。未着手のファイルは現在の位置を保って順に確認する。
一部の設定だけを整理した場合も配置・名前・説明は揃えるが、その部品の全制御を棚卸済みとは扱わない。
フォルダの移設だけでは、認証・モデル機能・監査出力・Azureの登録名を変更しない。

モデルAPIの内部設定はAPI・操作とモデル名から自動選択し、クライアントにprofileを指定させない。段階2では旧`X-Egakumi-Profile`の付与・受付選択・値数/ID検査を撤去し、APIMの下流転送前削除だけを清掃として維持する。操作の基本設定＋モデル名をキーとした`modelSettings`差分へ整理し、`profiles`・`defaultProfile`・`modalities`を新正本から撤去する。モデル別の出力項目・上限・固定URIと既存受付条件は維持する。 残存制御は続く棚卸で個別に判断する。購読の利用枠profileやCMの接続`profileName`は変更対象外である。[設定部品の説明](fragments/settings/core-api-settings/README.md)を参照する。

実接続の限定受入では、キー／BearerによるResponsesのJSON・SSEとTTSの正常WAV応答、不正Bearerの401、空本文でContent-Type宣言がない要求の400、TTSのDTD宣言拒否の400を確認した。対応する認証済み利用者情報・応答・拒否理由とAppGW監査の相関も確認した。大本文の413、その他の経路・モデル、その他のContent-Type不備は未検証であり、受入全体は未完了である。

```text
policies/
├── service.xml
├── fragments/
│   ├── settings/
│   │   └── core-api-settings/
│   │       ├── core-api-settings.xml
│   │       ├── core-api-settings.cs
│   │       └── README.md
│   ├── request/
│   │   ├── core-api-request-preparation/
│   │   │   ├── core-api-request-preparation.xml
│   │   │   └── README.md
│   │   ├── core-api-input-validation/
│   │   │   ├── core-api-input-validation.xml
│   │   │   ├── core-api-input-validation.cs
│   │   │   └── README.md
│   │   ├── core-api-operation-settings/
│   │   │   ├── core-api-operation-settings.xml
│   │   │   ├── core-api-operation-settings.cs
│   │   │   └── README.md
│   │   ├── core-model-limits/
│   │   │   ├── core-model-limits.xml
│   │   │   ├── core-model-limits.cs
│   │   │   └── README.md
│   │   ├── core-speech-ssml-guard/
│   │   │   ├── core-speech-ssml-guard.xml
│   │   │   ├── core-speech-ssml-guard.cs
│   │   │   └── README.md
│   │   └── core-query-observation/
│   │       ├── core-query-observation.xml
│   │       ├── core-query-observation.cs
│   │       └── README.md
│   ├── audit/
│   │   ├── core-request-context/
│   │   │   ├── core-request-context.xml
│   │   │   └── core-request-context.cs
│   │   ├── core-audit-authenticated-subject/
│   │   │   ├── core-audit-authenticated-subject.xml
│   │   │   └── core-audit-authenticated-subject.cs
│   │   ├── core-gateway-telemetry/
│   │   │   ├── core-gateway-telemetry.xml
│   │   │   └── core-gateway-telemetry.cs
│   │   └── core-query-telemetry/
│   │       ├── core-query-telemetry.xml
│   │       ├── core-query-telemetry.cs
│   │       └── README.md
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
│       ├── core-entra-authentication-error/
│       │   └── core-entra-authentication-error.xml
│       └── core-api-authentication-result/
│           ├── core-api-authentication-result.xml
│           ├── core-api-authentication-result.cs
│           └── README.md
├── api-templates/
│   ├── gateway.xml
│   ├── mcp.xml
│   ├── protocol.xml
│   └── README.md
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

`service.xml`はAll APIsへ1つ登録する全体ポリシーである。`api-templates/gateway.xml`は各HTTP API、`api-templates/mcp.xml`は各生成MCP API、`api-templates/protocol.xml`は各WebSocket APIの`onHandshake`と外部MCP APIのAll operationsへ登録する共有テンプレートである。「複数APIで正本を共有する」ことと「All APIsへ全体ポリシーとして適用する」ことを区別する。[APIテンプレートの適用先](api-templates/README.md)を参照する。

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

## 各APIで共通認証後の要求を準備する

`gateway.xml`、`protocol.xml`、`mcp.xml`は、`<base />`直後に`core-api-request-preparation`を呼ぶ。
棚卸で承認した「内部用の本人・購読IDヘッダーを削除して続行」「要求IDをAPIMの値で上書き」「共通認証の完了が未設定またはfalseなら503」の3処理を、この順序で共有する。
認証の再実行ではなく、各APIの権限検査・下流接続へ進む前の準備である。CMのブラウザー受付へは追加しない。
単純なXMLだけのleaf fragmentとし、条件・応答と後段の未棚卸処理は保持する。詳しくは [要求準備の説明](fragments/request/core-api-request-preparation/README.md) を参照する。

## HTTP APIへ認証済み利用者情報を引き渡す

HTTPの`gateway.xml`は操作・モデル設定の準備後に`core-api-authentication-result`を呼び、共通認証済みの購読または社員情報と監査用ラベルを`gatewayDecision`へまとめる。
同名XML・C#を`fragments/authentication/core-api-authentication-result/`で管理し、組み立てる式とAPIのフローを分ける。
引き渡し失敗時の監査と500応答は、テンプレート内の直後の分岐に残す。他のフラグメントのソースをこの部品へ展開しない。
詳しくは [認証結果の引き渡し](fragments/authentication/core-api-authentication-result/README.md) を参照する。Protocol／MCP側は続く棚卸で扱う。

## 本文サイズと項目条件を確認する

`core-model-limits`は`fragments/request/core-model-limits/`の同名XML・C#で管理する。旧`model-limits.xml`は直下に残さない。
HTTPテンプレートが選択済みの設定を渡し、部品が`gatewayLimitDecision`へ結果を保存する。短い監査・応答フローはテンプレートに残す。
内部設定JSON・本文形式・本文を扱う形式の上限設定の不備は500・`LIMITS_NOT_CONFIGURED`へ分類する。`none`は上限を読まず成功する。共有部品のMIME許可一覧比較は撤去する。音声認識・音声ファイルの文字起こし・画像編集・音声合成の4操作は、汎用の操作設定`requestBodySizeCheck=declared`で、この共有部品の申告サイズ確認と早期413だけを使う。音声合成は次の専用部品で実サイズも確認し、前の3操作はサイズ検査だけの本文全量読取りを省く。ほかの操作の実サイズ検査は維持する。以降のモデル・本文項目の制限は、続く棚卸で個別に判断する。
移設とコメント整備を全制御の棚卸完了とは扱わない。[本体と棚卸状態](fragments/request/core-model-limits/README.md)を参照する。

## 音声合成の本文サイズと宣言を確認する

`core-speech-ssml-guard`を`fragments/request/core-speech-ssml-guard/`の同名XML・C#で管理し、直下の旧`speech-ssml-guard.xml`は残さない。既存のSSML検査対象から生成した`gatewayControlSettings.speechSsmlOperationIds`をHTTP APIが読み、共有サイズ検査直後、利用枠・下流認証準備・`accepted`監査より前で呼ぶ。Operationの`<base />`後には重ねて呼ばない。

この部品は本文を一度だけ読み、選択済みの`maxRequestBytes`との実サイズ比較を文字列化前に行い、通過後に既存のDTD／ENTITY宣言を検査する。元の本文を保持し、`gatewaySpeechSsmlDecision`へ結果だけを保存する。監査と400／413応答はHTTP APIの呼び出し元が担当し、部品内から別フラグメントを呼ばない。Document・JSONの未棚卸処理やWAFは変更せず、承認済みソースのAzure反映・読戻しは完了した。TTSはキー／Bearerの正常WAV応答とDTD宣言拒否の400、対応する認証済み利用者情報・応答・拒否理由とAppGW監査の相関を実接続で確認した。大本文413など未検証項目が残り、受入全体は未完了である。[本体と呼出順](fragments/request/core-speech-ssml-guard/README.md)を参照する。

## queryの観測対象と要求ごとの変数

棚卸⑦でquery部品を観測専用へ変更する。API定義のHTTP操作`queryObservation`、生成MCPの`mcp.queryObservation`、外部MCPの`mcpProxy.endpoints[].queryObservation`を、Bicepが`gatewayControlSettings`の操作ID辞書`queryObservations`と、メソッド・公開相対path辞書`mcpQueryObservations`へ生成する。`core-query-observation`はその参照だけから観測ラベルを選ぶ。サービス・モデル・入力制約kindの固定一覧は置かず、標準3ラベルと対象入口を維持する。同じラベルを複数APIで共有できる。

同名XMLが観測式を実行し、同名C#が`gatewayQueryObservation`へ`route`・`httpMethod`・`presence`・`duplicates`だけを保存する。`route`は公開URLではなく選択した観測ラベルで、ASCII英字で始まる1～64文字の英数字・`_`・`-`を使い、小文字の`none`／`unknown`を完全一致で予約する。未指定の入口は対象外であり、観測できない値は`unknown`として扱う。mode・許可判定・応答statusや、観測結果による400／500の分岐は持たない。観測・拒否を切り替える共有設定を廃止するため、この部品では名前付きの値を読み込まない。先行する要求準備の認証完了確認と503は変更しない。[観測対象の設定](../../../../docs/operations/apim.md)を参照する。

外部MCPの受付・応答・エラー処理は`protocol.xml`へ生成時に差し込み、WebSocketでは空にする。`core-query-telemetry`は専用フォルダの同名XML／C#で管理する。⑥で承認した有無・複数値の取得を維持し、件数区分`countBucket`の計算・出力を削除する。イベント名`query-guard`、`schemaVersion: 2`、`auditSchemaVersion: 2`は維持し、mode・`query-rejected`・`guard-error`は新規出力しない。他イベントのschema 1と旧query/schema 1のKQL読取り互換は別に保持する方針である。

観測専用化のローカル改修・検証と、承認済みソースのAzure反映・読戻しは完了した。APIM共通処理の限定受入は確認済みだが、query観測を含む全入口の受入は未完了である。旧`queryGuard`から新参照への移行は反映・読戻し済みである。旧`core-query-guard`fragment・`core-query-guard-modes` Named Valueは参照消滅を確認してAzureから退役し、不在の読戻しも完了した。保存した正本checkpointは現行生成元へ更新し、旧資材を含まない状態に整理済みである。所有・currentの一般照合は維持する。旧`gateway.queryGuards`の設定は現行入力に使わない。入力検査の条件は次節で扱い、URI変換・WAF・モデル本文検査は未棚卸のまま保持する。観測専用化だけでqueryの通過・転送やサービス互換性を確認したとは扱わない。[query観測部品](fragments/request/core-query-observation/README.md)と[query監査部品](fragments/audit/core-query-telemetry/README.md)を参照する。

## 操作定義から入力検査の対象を選ぶ

HTTP操作の`operations[].inputContract = {}`を配備前に検査し、`gatewayControlSettings.inputContracts`へ対象の操作IDと空の値`{}`を生成する。この表は条件一覧ではなく対象一覧であり、全対象でContent-Type宣言の存在・1値・非空MIME部分という共通条件を使う。`core-api-input-validation`はその判定結果を`gatewayApiInputValidationPassed`へ保存する。会社のMIME一覧比較、HTTPメソッドの再確認、API名・操作名・URL・`kind`の固定照合は行わず、未指定の操作には検査を追加しない。未指定の`true`は検査省略、対象操作の`true`は宣言条件の通過を表し、完全なMIME構文や要求全体の妥当性を保証しない。メソッドとURLの操作登録は維持し、APIMが照合する。

`gateway.xml`はquery観測の後、操作・モデル設定の選択や本文読取りより前に部品を呼ぶ。判定がfalseの場合は`INVALID_CONTENT_TYPE_HEADER`で拒否監査を記録し、理由`Invalid Content-Type header`の本文なし400を返す。APIM要求IDの`apim-request-id`と`Cache-Control: no-store`を維持し、短い拒否フローは共有HTTP APIテンプレートへ残す。同名XML／C#は`fragments/request/core-api-input-validation/`で管理し、日本語の説明を生成XMLへ組み込む。

対象選択と部品構成の整理、query制限・メソッド再確認の撤去、Content-Type取得の簡潔化、入口のMIME許可一覧比較の撤去を承認した。標準13操作の選択と宣言の存在・単一値・非空MIME部分の確認、不備時の400と監査を維持し、結果変数と拒否理由の改名、共有HTTP APIテンプレートに短い拒否フローを残す方針も承認済みである。入口検査と不備時の400監査はローカル整備済みで、承認済みソースのAzure反映・読戻しは完了した。空本文でContent-Type宣言がない要求の400と対応する拒否監査を実接続で確認した。その他のContent-Type不備は未検証であり、未知メソッドの実APIM照合も受入で確認する。後続の`core-model-limits`のMIME許可一覧比較と415も撤去した。`operationConstraints.contentTypes`は個別処理などで使うため残し、未棚卸の本文／モデル制約・URI書換え、Speech・Document・WAFの形式／転送設定は保持する。query観測・URL資格情報拒否は今回変更しない。[使い方と実行順](fragments/request/core-api-input-validation/README.md)を参照する。

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

1. 初めて扱う部品の目的・適用先を確認し、その作業時に責務別の配置、Azureの部品名と同名のXML／C#、日本語の説明を揃える。全項目の判断が終わるまで移設を待たない。
2. 対象ファイル、BicepとPythonの参照先、テスト、説明書を同時に更新し、判断済みと未判断の制御を明記する。
3. 移設前の正本から独立生成したポリシーと比較し、実行コード・条件・出力を保持したことと、追加した説明が処理を変えないことを確認する。
4. 現在の配備記録と実APIMのポリシーを照合する。生成内容が同じならAzureへの再書き込みは不要である。

`expressions/legacy/` と旧構成専用のXML・生成処理は移行期間だけの資材である。
各利用先の棚卸・移行を完了し、旧参照がなくなった段階で削除する。
棚卸完了の条件には、旧資材と旧構成専用の生成処理を撤去し、現行構成で新規構築・再配備・検証が成立することを含める。
名前だけを変えて別フォルダへ温存しない。実環境の復旧用バックアップは、既定の保護されたローカル証跡で扱う。

適用順序と各部品の出力は [APIMポリシー一覧](../../../../docs/reference/apim-policy-catalog.md) を参照する。
