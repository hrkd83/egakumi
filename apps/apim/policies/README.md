# APIMポリシーの管理構成

共通の処理順序は `service.xml`、再利用する処理の実装は責務ごとのフォルダで管理する。
共通Inboundの見出しは、STEP1要求情報、STEP2入口確認、STEP3接続ログインの受付判定、STEP4キー／Bearer認証、STEP5認証済み利用者情報の連番で管理する。
現行登録資材はこの責務別の構成へ揃え、Azureの日本語説明とXML／同名C#の処理説明を管理する。XMLはフローと短式、同名C#は分離した実式を所有し、多行C#式のない部品へ空のC#を作らない。
一部の設定だけを整理した場合も配置・名前・説明は揃えるが、その部品の全制御を棚卸済みとは扱わない。
フォルダの移設だけでは、認証・モデル機能・監査出力・Azureの登録名を変更しない。

モデルAPIの内部設定はAPI・操作とモデル名から自動選択する。操作の基本設定と`modelSettings`の差分を正本とし、クライアントにprofileを指定させない。購読の利用枠profileとCMの接続`profileName`は別用途である。[設定部品の説明](fragments/settings/core-api-settings/README.md)を参照する。

本書は現在の管理仕様を説明する。変更ごとの判断は[工程1・2の棚卸報告](../../../docs/reference/apim-phase12-review.md)、環境別の反映・実接続・監査照合の状況は保護された作業記録で管理する。ソースの整理、合成検証、実環境検証を区別する。

初期APIの適用先は[ポリシー対応表](portal-examples/scope-map.md)、構築後の手動追加は[REST・SSEのポリシー例](portal-examples/README.md)を参照する。

```text
policies/
├── service.xml
├── README.md
├── fragments/
│   ├── audit/
│   │   ├── core-audit-authenticated-subject/
│   │   │   ├── README.md
│   │   │   ├── core-audit-authenticated-subject.cs
│   │   │   └── core-audit-authenticated-subject.xml
│   │   ├── core-audit-body-model/
│   │   │   ├── README.md
│   │   │   ├── core-audit-body-model.cs
│   │   │   └── core-audit-body-model.xml
│   │   ├── core-audit-context/
│   │   │   ├── README.md
│   │   │   ├── core-audit-context.cs
│   │   │   └── core-audit-context.xml
│   │   ├── core-backend-audit/
│   │   │   ├── README.md
│   │   │   ├── core-backend-audit.cs
│   │   │   └── core-backend-audit.xml
│   │   ├── core-gateway-telemetry/
│   │   │   ├── README.md
│   │   │   ├── core-gateway-telemetry.cs
│   │   │   └── core-gateway-telemetry.xml
│   │   ├── core-query-telemetry/
│   │   │   ├── README.md
│   │   │   ├── core-query-telemetry.cs
│   │   │   └── core-query-telemetry.xml
│   │   └── core-request-context/
│   │       ├── README.md
│   │       ├── core-request-context.cs
│   │       └── core-request-context.xml
│   ├── authentication/
│   │   ├── core-api-authentication-result/
│   │   │   ├── README.md
│   │   │   ├── core-api-authentication-result.cs
│   │   │   └── core-api-authentication-result.xml
│   │   ├── core-common-authentication/
│   │   │   ├── README.md
│   │   │   ├── core-common-authentication.cs
│   │   │   └── core-common-authentication.xml
│   │   ├── core-credential-browser-exception/
│   │   │   ├── README.md
│   │   │   ├── core-credential-browser-exception.cs
│   │   │   └── core-credential-browser-exception.xml
│   │   ├── core-credential-connection/
│   │   │   ├── README.md
│   │   │   ├── core-credential-connection.cs
│   │   │   └── core-credential-connection.xml
│   │   ├── core-credential-connection-outbound/
│   │   │   ├── README.md
│   │   │   ├── core-credential-connection-outbound.cs
│   │   │   └── core-credential-connection-outbound.xml
│   │   ├── core-credential-session-use/
│   │   │   ├── README.md
│   │   │   ├── core-credential-session-use.cs
│   │   │   └── core-credential-session-use.xml
│   │   ├── core-entra-authentication-error/
│   │   │   ├── README.md
│   │   │   └── core-entra-authentication-error.xml
│   │   └── core-realtime-observer/
│   │       ├── README.md
│   │       ├── core-realtime-observer.cs
│   │       └── core-realtime-observer.xml
│   ├── ingress/
│   │   └── core-trusted-ingress/
│   │       ├── README.md
│   │       ├── core-trusted-ingress.cs
│   │       └── core-trusted-ingress.xml
│   ├── quota/
│   │   ├── core-entra-user-profile/
│   │   │   ├── README.md
│   │   │   ├── core-entra-user-profile.cs
│   │   │   └── core-entra-user-profile.xml
│   │   ├── core-rate-limit/
│   │   │   ├── README.md
│   │   │   ├── core-rate-limit.cs
│   │   │   └── core-rate-limit.xml
│   │   └── core-token-limits/
│   │       ├── README.md
│   │       ├── core-token-limits.cs
│   │       └── core-token-limits.xml
│   ├── request/
│   │   ├── core-api-input-validation/
│   │   │   ├── README.md
│   │   │   ├── core-api-input-validation.cs
│   │   │   └── core-api-input-validation.xml
│   │   ├── core-api-operation-settings/
│   │   │   ├── README.md
│   │   │   ├── core-api-operation-settings.cs
│   │   │   └── core-api-operation-settings.xml
│   │   ├── core-api-request-preparation/
│   │   │   ├── README.md
│   │   │   └── core-api-request-preparation.xml
│   │   ├── core-api-target-validation/
│   │   │   ├── README.md
│   │   │   ├── core-api-target-validation.cs
│   │   │   └── core-api-target-validation.xml
│   │   ├── core-chat-store-disabled/
│   │   │   ├── README.md
│   │   │   ├── core-chat-store-disabled.cs
│   │   │   └── core-chat-store-disabled.xml
│   │   ├── core-document-input-validation/
│   │   │   ├── README.md
│   │   │   ├── core-document-input-validation.cs
│   │   │   └── core-document-input-validation.xml
│   │   ├── core-model-limits/
│   │   │   ├── README.md
│   │   │   ├── core-model-limits.cs
│   │   │   └── core-model-limits.xml
│   │   ├── core-query-observation/
│   │   │   ├── README.md
│   │   │   ├── core-query-observation.cs
│   │   │   └── core-query-observation.xml
│   │   └── core-speech-ssml-guard/
│   │       ├── README.md
│   │       ├── core-speech-ssml-guard.cs
│   │       └── core-speech-ssml-guard.xml
│   └── settings/
│       └── core-api-settings/
│           ├── README.md
│           ├── core-api-settings.cs
│           └── core-api-settings.xml
├── api-templates/
│   ├── README.md
│   ├── rest/
│   │   ├── rest.cs
│   │   └── rest.xml
│   ├── ws/
│   │   ├── ws.cs
│   │   └── ws.xml
│   ├── mcp/
│   │   ├── external.xml
│   │   ├── generated.cs
│   │   └── generated.xml
│   └── shared/
│       ├── stream.cs
│       └── stream.xml
├── operation-policies/
│   ├── credential-login/
│   │   ├── README.md
│   │   ├── callback.cs
│   │   ├── callback.xml
│   │   ├── disconnect.cs
│   │   ├── disconnect.xml
│   │   ├── identity-callback.xml
│   │   ├── launch-post.xml
│   │   ├── launch.xml
│   │   ├── logout-all.cs
│   │   ├── logout-all.xml
│   │   ├── logout.cs
│   │   ├── logout.xml
│   │   ├── session-commit.cs
│   │   ├── session-commit.xml
│   │   ├── start.cs
│   │   ├── start.xml
│   │   ├── status.cs
│   │   └── status.xml
│   ├── document-analysis/
│   │   ├── README.md
│   │   ├── document-analysis-status.cs
│   │   ├── document-analysis-status.xml
│   │   ├── document-analysis.cs
│   │   └── document-analysis.xml
│   ├── live/
│   │   ├── README.md
│   │   ├── live-create.cs
│   │   └── live-create.xml
│   ├── pii/
│   │   ├── README.md
│   │   ├── pii.cs
│   │   └── pii.xml
│   ├── realtime/
│   │   ├── README.md
│   │   ├── realtime-create.cs
│   │   ├── realtime-create.xml
│   │   ├── realtime-hangup.cs
│   │   └── realtime-hangup.xml
│   └── README.md
└── snippets/
    ├── audit/
    │   ├── audit-body-model-error/
    │   │   ├── README.md
    │   │   └── audit-body-model-error.xml
    │   ├── credential-telemetry/
    │   │   ├── README.md
    │   │   ├── credential-telemetry.cs
    │   │   └── credential-telemetry.xml
    │   └── query-observation-inbound/
    │       ├── README.md
    │       └── query-observation-inbound.xml
    ├── authentication/
    │   ├── credential-caller-error/
    │   │   ├── README.md
    │   │   └── credential-caller-error.xml
    │   ├── credential-challenge/
    │   │   ├── README.md
    │   │   ├── credential-challenge.cs
    │   │   └── credential-challenge.xml
    │   └── realtime-owner-context/
    │       ├── README.md
    │       ├── realtime-owner-context.cs
    │       └── realtime-owner-context.xml
    ├── quota/
    │   ├── mcp-rate-cleanup/
    │   │   ├── README.md
    │   │   ├── mcp-rate-cleanup.cs
    │   │   └── mcp-rate-cleanup.xml
    │   ├── rate-limit-error/
    │   │   ├── README.md
    │   │   ├── rate-limit-error.cs
    │   │   └── rate-limit-error.xml
    │   └── subscription-profile/
    │       ├── README.md
    │       ├── subscription-profile.cs
    │       └── subscription-profile.xml
    ├── request/
    │   └── mcp-metadata-guard/
    │       ├── README.md
    │       └── mcp-metadata-guard.xml
    └── README.md
```

| 配置先 | 管理するもの |
| --- | --- |
| `service.xml` | AzureのAll APIsへ設定する共通ポリシー。STEP、認証方法の選択、部品を実行する順序を示す |
| `fragments/` | Azureへ登録するPolicy fragment。まず責務で分け、その下に部品ごとのフォルダを作る |
| `fragments/audit/core-backend-audit/` | 共有outboundのヘッダー上書き前に、既知の提供元要求IDを退避する。同名XML／C#と説明を管理し、Azureのcore-backend-audit IDと全検査条件を維持する |
| `fragments/quota/core-entra-user-profile/` | 共通認証が確認した社員利用枠を、user JSON解析・limits取得・JSON返却で引き渡す。同名XML／C#を管理し、Azureの`core-entra-user-profile` IDを維持する |
| `fragments/quota/core-rate-limit/` | 利用回数枠の選択・計数。同名XML／C#と説明を管理し、Azureの`core-rate-limit` IDを維持する |
| `fragments/audit/core-audit-body-model/` | 通常HTTPの監査用モデル名を取得する。同名XML／C#と日本語説明を管理し、Azure ID・モデル条件・本文保持を維持する |
| `fragments/audit/core-audit-context/` | 親要求IDの追跡候補と監査用モデル名を選ぶ。同名C#の固定trace／model区画を展開し、Azure IDと記録条件を維持する |
| `fragments/quota/core-token-limits/` | トークン利用枠の計数。同名XMLとenabled／counter固定2区画のC#を管理し、Azureの`core-token-limits` IDを維持する |
| `api-templates/` | APIの公開時に設定値を埋めて登録するポリシーテンプレート。複数APIが使う正本を共有する |
| `operation-policies/` | APIの特定操作に適用するポリシー。機能ごとにまとめる |
| `operation-policies/credential-login/` | CM保護6操作とブラウザー4操作を管理する。確認済みcallerと4操作の本人session要求を同名C#で管理し、ブラウザー3操作はXMLだけで管理する |
| `operation-policies/realtime/` | Realtime開始・終了の操作ポリシー。上流で確認済みの所有者を受け取り、署名チケットの発行と外部チケットの照合を行う |
| `fragments/authentication/core-realtime-observer/` | Realtime監視接続の所有者チケットを照合する。既存Azure IDを維持し、onHandshakeで本文を作らない |
| `snippets/` | 生成時にポリシーへ差し込むXMLの部品。Azureへ登録するPolicy fragmentとは区別する |
| `snippets/request/mcp-metadata-guard/` | MCPの予約メタデータパスだけを共通認証前に404で終了する。生成MCP・外部MCP・公開時の固定構造検査が同じXML正本を使う |
| `snippets/authentication/realtime-owner-context/` | 確認済みcallerとチケット表現、TTLの受取りを3経路へ生成時にinline展開する。Azure fragmentは追加しない |
| `snippets/authentication/credential-challenge/` | 本人再認証が必要なCM業務要求へ安全な一時URLを案内する。既存部品へinline展開し、外部応答の状態・秘密・URL・期限を検査する |
| `snippets/quota/subscription-profile/` | SQL購読profileを共有APIへinlineで差し込む。XMLはフロー全体、同名C#はgatewayFetchedProfileの受入検査式だけを管理する |
| `snippets/quota/rate-limit-error/` | `core-rate-limit`の上限超過だけを監査・本文なし429へ揃える。Retry-Afterの1式を同名C#で管理し、API・操作へinline展開する |
| `snippets/quota/mcp-rate-cleanup/` | 外部MCPの終了・取消形式を読み、利用回数枠の免除だけを選ぶ。同名XML／C#をinline展開し、本人・下流所有者の確認は維持する |
| `operation-policies/document-analysis/` | 開始4区画・状態取得3区画を同名C#から展開する。固定提供元・所有者チケット・公開結果・監査のフローと単式はXMLが所有する |
| `snippets/audit/audit-body-model-error/` | HTTP監査モデルの内部取得失敗を固定IDだけ500へ分ける。HTTP共通と独自on-errorの3操作へ認証／回数枠処理後にinline展開するXMLフロー |
| `snippets/audit/query-observation-inbound/` | 外部MCPの観測→received→監査を生成時inlineで呼ぶXMLフロー。入力の加工・拒否を行わず、独立Azure fragmentや空のC#を追加しない |
| `snippets/audit/credential-telemetry/` | CMの拒否監査の全messageを同名C#で管理する。既存主体投影をその式へ収め、CMへcompact説明でinline展開する |
| `fragments/authentication/core-credential-connection-outbound/` | 下流claims・失効の案内と検証済み接続更新ヘッダーを扱う。claims監査の全messageと主体投影を同名C#へ収め、Azure ID・応答条件を維持する |

`service.xml`はAll APIsへ1つ登録する。`api-templates/rest/rest.xml`、`api-templates/mcp/external.xml`、`api-templates/mcp/generated.xml`は対象APIのAll operationsへ組み立てて登録する。`api-templates/ws/ws.xml`はWebSocketの`onHandshake`へ組み立てて登録する。WebSocketと外部MCPが共有する実行順序と認証式は`api-templates/shared/`に置く。 [APIテンプレートの適用先](api-templates/README.md)

図は現行正本の配置を示す。`policies`直下のファイルは`service.xml`と`README.md`だけである。旧認証互換XML・依存C#と旧形式の再生成処理を公開資材から撤去し、責務別の正本へ揃える。実際の旧fragment辞書に対する限定した説明差分の照合、current／desiredの厳密照合、保存した過去rawの完全照合、新規構築用のcanonical派生inline生成は維持する。ソースの整理と対象環境の反映・実受入は別に確認する。

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

REST、WebSocket、外部MCP、生成MCPの各ポリシーは、`<base />`直後に`core-api-request-preparation`を呼ぶ。
棚卸で承認した「内部用の本人・購読IDヘッダーを削除して続行」「要求IDをAPIMの値で上書き」「共通認証の完了が未設定またはfalseなら503」の3処理を、この順序で共有する。
認証の再実行ではなく、各APIの権限検査・下流接続へ進む前の準備である。CMのブラウザー受付へは追加しない。
単純なXMLだけのleaf fragmentとし、要求準備の条件・応答を所有する。詳しくは [要求準備の説明](fragments/request/core-api-request-preparation/README.md) を参照する。

## HTTP APIへ認証済み利用者情報を引き渡す

HTTPの`rest/rest.xml`は操作・モデル設定の準備後に`core-api-authentication-result`を呼び、共通認証済みの購読または社員情報と監査用ラベルを`gatewayDecision`へまとめる。
同名XML・C#を`fragments/authentication/core-api-authentication-result/`で管理し、組み立てる式とAPIのフローを分ける。
引き渡し失敗時の監査と500応答は、テンプレート内の直後の分岐に残す。他のフラグメントのソースをこの部品へ展開しない。
WebSocketと外部MCPは`shared/stream.cs`、生成MCPは`mcp/generated.cs`の固定区画で、同じ確認済み主体を引き継ぐ。 [認証結果の引き渡し](fragments/authentication/core-api-authentication-result/README.md)

## 利用回数枠を選び計数する

[core-rate-limit](fragments/quota/core-rate-limit/README.md)の同名XML／C#が、確認済みの社員または購読IDと準備済みの利用枠を使い、既存の計数キーで回数を数える。本人情報・元設定の再読取りや再照合は行わない。SQLの回数枠を使うキー要求では、その値を直接選び、結果に使わない埋込み設定の復元・購読別検索を省く。

社員／購読ごとの枠、設定の優先順位、回数・期間、明示`null`による無制限、取消・切断の免除とAPIM標準429を維持する。SQL障害を無制限として扱わない。

## トークン利用枠を計数する

[core-token-limits](fragments/quota/core-token-limits/README.md)の同名XML／C#が、確認済み社員IDまたは購読IDから従来と同じカウンターIDを作る。準備済みの利用枠を使い、社員JSON・元設定・認証成功の重複検査は行わない。毎分／累積トークン枠、期間、明示`null`、免除と標準ポリシーへの値の引き渡しを維持する。

対象は操作の実効接続先で選び、`backendOverride`を含むFoundry OpenAI向けHTTP Chat／Responsesへ適用する。モデル一覧には依存せず、WebSocket・MCP・他形式へ広げない。内部対象一覧を読めない場合は、対象なしに補完せず既存on-errorへ渡す。Chatの利用量監査とAPI LLM診断も同じ実効接続先へ揃え、監査対象の現行範囲と本文収集量0を維持する。

## 利用枠の取得元

[core-entra-user-profile](fragments/quota/core-entra-user-profile/README.md)は、共通認証が検査済みの社員情報JSONから利用枠を取り出し、後段へ渡す。本人・元設定・項目・型・範囲の再検査は行わない。内部情報を取得できない場合は無制限へ補完せず、既存on-errorへ渡す。

[subscription-profile](snippets/quota/subscription-profile/README.md)はAPIへ生成時に埋め込む処理であり、SQL応答を独立した外部入力として検査する。購読ID・形式・項目・型・範囲、30秒キャッシュ、明示`null`と設定の優先順位を維持する。接続・読取り・検査に失敗した場合は、`policy-error`／`LIMIT_PROFILE_UNAVAILABLE`を監査へ渡し、503・`Retry-After: 5`・no-storeで停止する。

## CMの本人接続と下流認証

[core-credential-connection](fragments/authentication/core-credential-connection/README.md)は確認済み`callerId`を引き継ぎ、社員JSON・GUID整形・認証結果の再照合を行わない。内部IDの引き渡し不備は[credential-caller-error](snippets/authentication/credential-caller-error/README.md)が500・`AUTHENTICATION_PROCESSING_ERROR`・`policy-error`へ分類し、接続確認より先で停止する。

接続所有者、Broker本人セッション、取得トークンとGraph本人IDの照合は維持する。[core-credential-session-use](fragments/authentication/core-credential-session-use/README.md)は保持済みセッション値を使い、SQLによる使用直前の期限・失効・世代・rotation確認、更新・再認証を行う。未使用の所有者JSON、Graph照合成功の再確認、同じヘッダーの再読取りは行わない。

CM有無は生成時に選び、CMなしの空代入と実行時の重複分岐を除く。CMありでは接続確認・本人セッション確認・下流応答処理を直接呼ぶ。入口資格情報の除去、固定接続先、設定済みMI・CM等の下流専用認証を維持し、CMの拒否を別の方式で救済しない。[下流応答処理](fragments/authentication/core-credential-connection-outbound/README.md)を参照する。

## ResponsesとHosted Agentの入力

[core-api-target-validation](fragments/request/core-api-target-validation/README.md)が、要求本文と操作固有の機能境界を確認する。Responsesの通常の履歴ID・Item・type・ファイル参照、`previous_response_id`・`prompt`・`include`は原値を保持し、提供元に適否を任せる。本文`tools`はfunctionに限定し、functionだけを選ぶ`allowed_tools`を受け付ける。内蔵ツールを選ぶ指定と、input配列直下の明示的な`code_interpreter_call`は403で拒否する。

Hostedは登録済みAgentの指示・ツールを使う。サンプル由来の本文4項目、user／input_text、件数・非空・stream型・metadataの制限を持たず、通常オプションと完全な入力Itemを原値で保持する。`instructions`・`tools`・`agent_reference`・`prompt`、本文直下の`previous_response_id`・`agent_session_id`と、input配列直下の明示的なCode Interpreter Itemは403で拒否する。A2AはJSON-RPC形式と宣言済みRPCメソッドの条件を維持する。

[HTTPテンプレート](api-templates/README.md)は、操作の実効接続先から選んだFoundry OpenAI向けChatと、`kind=statelessResponses`／`hostedAgentPost`の対象要求へBooleanの`store:false`を強制する。Responses・Hostedは、指定がある場合だけ`conversation:null`・`background:false`へ変更する。Hostedの親要求ID付与は固定Agentを指定した対象で継続する。Gatewayが保証するのは転送する保存指定であり、Hosted／A2Aサーバー・カスタムツールによる保存と参照先の所有者確認は、それぞれの契約で確認する。

## 本文サイズと項目条件を確認する

[core-model-limits](fragments/request/core-model-limits/README.md)は同名XML／C#で管理し、HTTPテンプレートが選択済みの設定を渡す。部品は本文形式と上限を確認して`gatewayLimitDecision`を保存し、短い監査・応答フローはテンプレートに残す。内部設定・本文形式・必要な上限設定の不備は500・`LIMITS_NOT_CONFIGURED`へ分類する。

音声認識・音声ファイルの文字起こし・画像編集・音声合成・Document解析の5操作は、`requestBodySizeCheck=declared`で共有の申告サイズ確認と早期413を使う。音声合成は専用部品で実サイズも確認し、ほかの4操作はサイズ検査だけの本文全量読取りを行わない。ほかの操作の実サイズ検査を維持し、共有のMIME許可一覧比較は行わない。

## 音声合成の本文サイズと宣言を確認する

[core-speech-ssml-guard](fragments/request/core-speech-ssml-guard/README.md)は同名XML／C#で管理する。HTTP APIが操作ID一覧から対象を選び、共有サイズ検査の直後、利用枠・下流認証・`accepted`監査より前で呼ぶ。操作の`<base />`後には重ねて呼ばない。

本文を一度だけ読み、文字列化前に実サイズを確認し、通過後にDTD／ENTITY宣言を検査する。元の本文を保持して判定結果だけを渡す。監査と400／413は呼び出し元が担当し、サイズ超過の理由文は共有検査と同じ`Request too large`を使う。

## Document解析の入力を確認する

[core-document-input-validation](fragments/request/core-document-input-validation/README.md)は、`kind=documentAnalysis`の操作でモデルの必須・単一・非空とURL階層を変える値を確認する。登録モデル一覧やMIME・先頭バイト・空本文による独自拒否は行わず、文書とモデルの利用可否を提供元へ任せる。申告サイズの413、固定接続先、対応API版とURL資格情報の共通拒否を維持する。

開始POSTはmodelをクエリから除いてpathを書き換え、pages・features等の追加クエリを保持する。api-versionは対応版へ上書きし、stringIndexTypeは指定値を保持して未指定時だけ補完する。結果GETは固定の結果取得URIへ送り、未使用の追加クエリを転送しない。

開始／状態取得の操作固有フローは`operation-policies/document-analysis/`で管理する。署名付きチケット・UUID・本人・期限・返却URIの照合と公開結果契約を維持する。本文の入力不備、認証情報の内部引き渡し不備、操作設定不備、提供元障害をそれぞれの400／500／502等へ分ける。[公開契約](../../../docs/reference/document-analysis.md)を参照する。Portalのアップロード・モデル選択・BFF制約はこの管理整理の対象外である。

## queryの観測対象と要求ごとの変数

HTTP操作の`queryObservation`、生成MCPと外部MCPの宣言から、Bicepが操作IDまたはメソッド・相対pathの参照表を生成する。[core-query-observation](fragments/request/core-query-observation/README.md)はその表から観測ラベルを選び、route・httpMethod・presence・duplicatesだけを保存する。未指定は対象外、取得できない値は`unknown`とし、観測結果から許可・拒否を決めない。

[core-query-telemetry](fragments/audit/core-query-telemetry/README.md)は同名XML／C#を正本とし、`query-guard`イベントのschema 2で有無・複数値を記録する。クエリ名・値・URL、mode、件数区分、旧拒否イベントを新規出力しない。他イベントと過去監査のKQL読取りは別に管理する。

外部MCPの受付・応答・エラー時のquery観測は、`mcp/external.xml`が適用区画を指定する。query観測と入力検査は別の設定で管理する。 [観測対象の設定](../../../docs/operations/apim.md)

## 操作定義から入力検査の対象を選ぶ

HTTP操作の`inputContract = {}`は検査対象の宣言である。Bicepが操作IDの参照表を生成し、[core-api-input-validation](fragments/request/core-api-input-validation/README.md)はContent-Type宣言の存在・単一値・非空MIME部分という共通条件を確認する。MIME許可一覧、HTTPメソッド、API名・操作名・path・kindによる再照合は行わない。未指定操作には検査を追加せず、メソッドとURLはAPIMの操作登録が照合する。

`rest/rest.xml`はquery観測の後、操作・モデル設定の選択と本文読取りより前に呼ぶ。不備は`INVALID_CONTENT_TYPE_HEADER`の拒否監査、本文なし400・要求ID・no-storeへ分類する。判定通過は完全なMIME構文や要求全体の妥当性を保証しない。Document固有入力、本文サイズ、Speech宣言、WAFはそれぞれの契約で確認する。

## STEP4の認証部品

`core-common-authentication`は、STEP3で判定した接続ログインのブラウザー受付を除き、通常の要求を認証する。
設定したキー用ヘッダーがあればAPIMが検証した購読を確認し、なければEntraのBearerトークンを検証する。
不正なキーの要求は401で終了する。Bearerは初期構築の購読不要Productを使う。手動追加用の購読不要Productは`manual-`で始まるAPIに限り共通認証が受け付け、手動APIのポリシーで専用の所属を照合する。Productの確認後は同じテナント・audience・委任scope・本人を検証する。

同名のC#ファイルには、キーの確認、Bearer形式の確認、本人情報の取得、会社設定と認証結果の組立、失敗監査の主体準備という5つの名前付き区画を置く。
各区画をXMLの対応する式へ生成時に埋め込むため、C#ファイル全体を一度に実行する構成ではない。
標準のトークン検証、分岐、応答、認証成功を示す2変数はXMLで処理順を確認できる。
詳細は [認証部品の説明](fragments/authentication/core-common-authentication/README.md) を参照する。

`gatewayCommonAuthenticationValidated`はキー／Bearer共通の検証完了、`gatewayEntraAuthenticationValidated`はBearerのトークン・本人・会社設定の確認完了を示す。
要求ごとの処理結果であり、APIの利用可否を切り替える配備設定ではない。

`core-entra-authentication-error`は、標準のEntraトークン検証が中断した場合に、`service.xml`の`on-error`から呼び出す。
既存の失敗監査と401応答を担当し、短いXML式だけで構成するため空のC#ファイルは作らない。
両認証部品の失敗監査は、生成時に監査部品のソースを展開する。フラグメント内から別フラグメントを呼び出さない。
Document開始／結果取得・PII・CM保護6操作も、Entra有効時は個別on-errorの先頭で共通検証の失敗を引き継ぎ、401・拒否監査・Bearer challengeへ揃える。提供元障害・利用枠・CMセッション等の個別エラー分類を維持する。

## 変更時の管理手順

1. 部品の目的・適用先を確認し、責務別の配置、Azure名と同名のXML／必要なC#、日本語説明を揃える。
2. 生成元、Bicep／Pythonの参照、テスト、説明書を合わせて変更し、条件を変える判断と配置だけの整理を区別する。
3. 独立生成と差分で、意図した変更、実行コード・条件・出力、式区画・文字列・説明の対応を確認する。
4. 配備記録と実APIMを正式な読戻しで照合する。生成内容が同じなら、その整理のための再書き込みは不要である。

旧構成専用のXML・依存C#・再生成処理は現行正本へ温存しない。復旧用の過去rawと実環境の証跡は、既定の保護領域で管理する。新規構築・再配備・読戻しは現在の正本から行う。

適用順序と各部品の出力は[APIMポリシー一覧](../../../docs/reference/apim-policy-catalog.md)、判断内容は[工程1・2の棚卸報告](../../../docs/reference/apim-phase12-review.md)を参照する。
