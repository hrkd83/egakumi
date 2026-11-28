# APIMポリシー一覧

Graphと文書解析を含む標準19 API、生成MCP、CM補助APIの登録資材を説明する。業務APIの同じURLで購読キーとEntraアクセストークンの両方を受け付ける。登録資材の構成と対象環境への反映・受入状況は分け、実際の適用と接続結果は環境ごとの保護された証跡で確認する。

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

## 監査と診断

全体ポリシーは要求ID、信頼済み入口の識別情報、認証済み主体、API・操作・ルート、応答結果を関連付ける。購読IDやBearerの主体を、未検証の入力ヘッダーで上書きできないようにする。 [監査の識別情報](../audit/audit-identification.md)

APIM診断の入出力本文設定は0バイトとする。APIのルーティング、認可で止まった要求、Realtime・Liveの接続制御をメタデータで追跡する。MCPからのREST実行は接続ログとREST側の要求を照合する。 [監査で取得する項目](../audit/request-body-audit.md#初回構築)

## 生成元出力と資源配備の分離

`api-publication.bicep`は単一の正本を保ち、正式CLIの[publication_templates.py](../../../apps/api-gateway/deploy/publication_templates.py)がコンパイル結果を二つのARM要求へ分ける。生成専用は既存APIMを参照して完成XML・設定を出力し、資源配備は同じAPI・操作・部品を登録する。実配備には完成XMLを再度出力せず、`publicationSourceGenerationId`だけを出力して使った生成元を示す。

大きなJSONを出力から省くことと、生成元の完全照合を省くことは別である。保存コードのexport、入力、生成時のManaged Identity、成功した実配備との結び付きを照合し、監査・認証・ポリシー本文の確認は維持する。生成が失敗した段階ではAPIM資源を更新しない。新規構築・更新・復旧の順序は[登録用資材の説明](../../../apps/api-gateway/README.md)を参照する。

所属を1件ずつ変更する際の中間読取りでは、全API本体・全APIの操作／Product／revision／schema／tool一覧、共有ポリシー・fragment・Named Value・backend・Product、外部の事前条件を再照合する。個別定義とXMLは、変更対象APIとMCPの操作参照でつながるAPIを両方向・推移的に照合し、無関係APIの個別子資源への反復読取りを省く。保存した全snapshotは保持し、staging前後・共通認証切替後の生成元照合と、全所属変更後の全件照合を続ける。無関係な個別子資源の変更は次の当該API確認または最終全件照合で拒否し、今回追加した所属だけを解除して、正常完了にはしない。未知の構造・未完の生成元は全件読取りに戻し、回復処理も全件読取りを使う。この選択は内部処理で行い、params／configの切替フラグは追加しない。

## 保存した生成元の確認

現在の資材で配備したAPIを更新するときは、配備と対応する生成元を照合する。保存した記録と実資源が一致しない場合は更新前に停止する。 [Gateway配備](../../setup/11-deploy-gateway.md)

## 全体で共通のポリシー

現行の公開資材はHTTP・WebSocket・MCPを対象とする。gRPCの専用入口・API・入口確認の免除は廃止した。

| 処理 | 実施内容 | 主なファイル |
| --- | --- | --- |
| 信頼した入口 | AppGW経由の識別値を通常HTTP(S)で常設確認。gRPC専用の入口免除は廃止 | `fragments/ingress/core-trusted-ingress/core-trusted-ingress.xml`、`service.xml` |
| 購読キー | 設定済みヘッダーが1個で空でないこと、`context.Subscription.Id`の存在を確認。URLのキーを拒否 | `service.xml` |
| Entraアクセストークン | キーヘッダーがない要求で署名・期限、認証用テナント、API audience、v2、`Gateway.Invoke`を検証。クライアントアプリIDは認可の制限に使わない | `fragments/authentication/core-common-authentication/core-common-authentication.xml`、`service.xml` |
| 呼出主体 | キーは`subscription:<購読ID>`、Bearerは`user:<テナントID>:<ユーザーObject ID>`。同じ購読または同じユーザーの利用枠を共有 | [各APIテンプレート](../../../apps/api-gateway/policies/api-templates/README.md) |
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

APIと操作のスコープに登録するテンプレートは、[REST、WebSocket、外部MCP、生成MCP](../../../apps/api-gateway/policies/api-templates/README.md)に分かれる。全体スコープには`service.xml`を登録する。

| 処理 | 実施内容 |
| --- | --- |
| 共通処理の継承と要求準備 | `<base/>`直後に`core-api-request-preparation`を呼び、内部ID除去・APIM要求ID設定・完了マーカー確認を行う。未設定またはfalseは503 |
| 操作・モデル | 公開Operationと固定モデル・profile・URI・queryを照合する |
| 入力サイズ・項目条件 | APIMの技術上限と残存する型・必須構造を検査。HTTPの出力トークン数・汎用整数条件は提供元へ委ね、Portalの入力検査と区別する。試験用の小容量・固定件数は標準AI APIへ追加しない |
| 回数制限 | キーは検証済み購読、Bearerは検証済みユーザーのcounterを使用。CMの追加資格情報で入口の利用枠を切り替えない |
| queryの観測 | `core-query-observation`がAPIの明示した参照から観測ラベルを選び、有無・複数値だけを記録用JSONへまとめる。標準3ラベルと対象を維持し、modeや共有Named Valueは使わない |
| 操作の入力契約 | query観測の後、本文読取りより前に`core-api-input-validation`が対象表`inputContracts`を選び、共通のContent-Type宣言条件を確認する。対象13操作は`inputContract={}`、生成表の対象値も`{}`。会社のMIME一覧比較・メソッド再確認・query拒否は撤去する |
| 利用枠の設定 | Bearerは共通認証で検証済みの利用者に設定済みの上限を適用する。購読キーの共通上限は標準資材で設定しない。[購読運用](../../operations/access-and-keys.md) |
| LLMトークン上限 | `token-limit.xml`を対応HTTP APIだけから参照。検証済み利用枠の毎分・期間内トークン数をAPIM標準ポリシーへ渡す。明示した制限なしの設定と設定欠落を区別する |
| 下流認証 | Foundry等はMI、外部サービスは登録済みの認証設定。キー・入口のユーザートークン・本人sessionを下流へ流さない |
| 監査 | 要求ID、呼出主体、認証方式、購読またはユーザー・テナント・クライアント、API、Operation、モデル、状態、使用量を記録。本文は専用監査経路で採取する |

HTTP・WebSocketの接続開始・MCPの3テンプレートは、`core-api-request-preparation` で内部用の `X-Core-Caller-Id`／`X-Core-Subscription-Id` を除去し、`x-ms-client-request-id` をAPIM要求IDへ設定する。共通認証の完了を確認してから後続処理に進む。 [要求準備の説明](../../../apps/api-gateway/policies/fragments/request/core-api-request-preparation/README.md)

queryの観測対象は、HTTP操作の `queryObservation`、生成MCPの `mcp.queryObservation`、外部MCPの `mcpProxy.endpoints[].queryObservation` で指定する。観測イベント `query-guard` の `schemaVersion: 2` は、値を除いて項目の有無・複数値を記録する。記録段階は `received`、`backend-response`、`policy-error`。

外部MCPの観測用フローは`api-templates/mcp/external.xml`が所有する。WebSocketの受信queryと固定queryの制御は`api-templates/ws/ws.xml`で管理する。各設定の確認は[query観測部品](../../../apps/api-gateway/policies/fragments/request/core-query-observation/README.md)と[運用時の確認](../../operations/apim.md)を参照する。

PIIのWAFは、RESTの `g-pii-rest` では `text`、MCPの `g-pii-mcp` では `params.arguments.text` を対象に、フィールド単位の除外を適用する。PortalのPII試用は `p-pii-redact` を使う。 [WAF側の設定と受入](../../testing/waf-compatibility.md)

HTTPの`operations[].inputContract = {}`で検査対象を明示し、Bicepが`gatewayControlSettings.inputContracts`へ対象操作IDと空の値`{}`を生成する。この空の値は条件不足ではなく、対象だけを記録し、全対象に共通のContent-Type宣言条件を使う意味である。MIME一覧を表へ持ち込まず、`operations[].method`とURLのARM登録は維持してAPIMに照合を任せる。従来の13操作だけを明示し、未指定のLive・Realtime・PII・MCP・WebSocket等へこの検査を追加しない。`gatewayApiInputValidationPassed`のtrueは対象操作での宣言条件の通過、または未指定による検査省略を表す。完全なMIME構文や要求全体の妥当性を保証せず、認証や後続の本文・モデル検査は省略しない。

Content-Typeの存在・単一値確認は`TryGetValue`による一度の取得で行い、唯一の値を`;`で分けた先頭のMIME部分が、空白除去後も空でないことを確認する。空値・空MIME部分は拒否し、会社の許可一覧とは比較しない。新しい構文パーサーを追加せず、HTTP／WAFの既存構文保護を維持する。単一値はAPIM解析後の配列の確認であり、元のHTTPに重複ヘッダー行がなかった証明ではない。

Content-Type宣言の条件を満たさない要求は、共有HTTPテンプレートが本文なしの400と `apim-request-id`、`Cache-Control: no-store` を返す。監査では `INVALID_CONTENT_TYPE_HEADER` を使う。Document固有の要件は別途確認する。 [文書解析の公開契約](document-analysis.md)

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

出力能力値の正本は [model-capabilities.json](../../../apps/api-gateway/model-capabilities.json)で、Portalの表示・要求生成・入力検査などに使う。HTTPの要求では出力トークン項目を省略でき、`core-model-limits`は指定された項目名・値・最大値を独自に検査せず、本文を書き換えずに提供元へ委ねる。Realtime WebRTCの作成時の512は初期設定であり、モデル上限4,096とは区別する。WebSocket確立後やWebRTCのDataChannelはAPIMポリシーで検査しないため、Portalが送信する設定と、Gatewayが全クライアントへ強制する制限は同一ではない。

WAFは対象AI完全パスだけ2000 KiBまで本文を検査し、サイズ超過だけでは拒否しない。認証・管理系の既定防御は維持する。AppGWのバックエンド待機時間は設定値を確認する。[WAFの範囲](../../testing/waf-compatibility.md)

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

Live作成は共通の500 MiB枠と型・SDP形式を検査し、instructionsの独自文字数枠を設けない。初期voiceは`marin`。失敗入力は400、形式が不正な上流の成功応答は502へ整える。[Live作成のXML](../../../apps/api-gateway/policies/operation-policies/live/live-create.xml)

WSSの`websocket.session.models`は接続後イベントで使うモデルのクライアント契約を表す。APIMが検査するのは接続開始時の要求である。frame内のモデル・音声・設定とセッション継続時間は、アプリと上流サービスで管理・検査する。HTTP要求回数と音声セッションの利用量は別に計測する。[Microsoft公式のWebSocket制約](https://learn.microsoft.com/en-us/azure/api-management/websocket-api)

WSSは接続時に設定済みの購読キーヘッダー、または`Authorization: Bearer`を送れるSDK・アプリサーバーから利用する。ブラウザは必要な認証ヘッダーを送れるBFF等を経由する。ブラウザのWebRTCは、アプリのサーバーがGatewayの作成APIを呼び、SDP answerをブラウザへ返す。

WebRTCの接続制御はAPIMを通り、音声メディアは端末とサービスのICE経路を通る。受入ではその宛先・ポート・端末ネットワークの到達性を確認する。[Azure Live WebRTC](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/gpt-live-webrtc)

## Credential Managerとの接合

CMは、必要なAPIが利用する共通の下流OAuth機能である。`graph-profile-v1`のキー経路と、CMが必要なMCPに適用する。GraphのBearer経路では、Gateway用BearerをBrokerでOBO交換し、Graphの委任トークンを固定の本人取得に使う。

本人接続は、CM共通APIでサインインを開始し、確認済みの呼出主体・本人・provider・接続・期限をセッションに結び付ける。キーでは同じ購読、Bearerでは同じテナント・ユーザーを照合し、両方式の間でセッションの所有者を移さない。対象APIへ`X-Core-Connection-Session`を送る。任意のUPNや接続名の指定を本人確認の代わりに使わない。

サービス共有接続は、サーバー設定のproviderとconnectionを使う。どちらもCMの`get-authorization-context identity-type=managed`がAPIM自身の接続利用権限を確認し、CMが下流tokenを取得・更新する。本人セッションの検査は、その前のGatewayの共通ポリシーで行う。

初回のログインURL提示、サインイン、状態照会、元要求の再実行は[CM接続手順](../identity/credential-manager-login.md)を参照する。API応答だけで利用者のブラウザを開くとは扱わない。Dify等は対応ツール／プラグインで連携する。

接続再認証は409、下流の権限不足は403として区別し、下流の生エラー・token・Cookie・認証URLを業務応答へ流さない。

## ポータルで追加するAPI

初期構築では[API定義](../../../apps/api-gateway/apis/README.md)から、操作、固定バックエンド、個別制御を宣言します。[Bicep](../../../apps/api-gateway/api-publication.bicep)がXMLとC#を組み立て、同じ生成元を公開後の読戻しに使います。[適用先一覧](../../../apps/api-gateway/policies/portal-examples/scope-map.md)では、標準APIの公開パス、操作ID、Azureのスコープ、個別XMLを確認できます。

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

`inputContract` は、対象のContent-Typeヘッダーが1値で空でないことを確認します。WAFの型別ルールはヘッダーを基に分岐するため、このAPIMの確認を含めて保護します。Document・Realtimeの所有者照合、SSMLの外部参照対策、Responsesの保存・共有資源対策、生成MCPの引数とREST側の再認証については、[ポリシーの適用先一覧](../../../apps/api-gateway/policies/portal-examples/scope-map.md)を参照します。

### 構築後のAPIMとWAF

手動追加APIは[REST／SSEのポリシー例](../../../apps/api-gateway/policies/portal-examples/README.md)から、専用API ID、固定バックエンド、Operation IDとパス、利用枠を設定します。購読キーは専用の購読必須Product、本人Bearerを提供する場合は `manual-` で始まるAPIを購読不要の `manual-entra-users` に関連付けます。共通認証は同じテナント・audience・委任scope・本人を確認します。管理対象Productのメンバーシップには標準構築時の照合があるため、手動追加には独立したProductを使います。各Operationで `Calculate effective policy` を開き、入口・認証・利用枠・監査・認証情報の除去・固定転送先を確認します。 [APIMでのポリシー継承](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-policies)

AppGWでは新しいパスに実際に効くWAFとAPIMへのHTTPS経路を確認します。標準ではGateway側の既定経路と共通WAFを使います。パス別WAFを追加する際は、既定ポリシーに代わってそのパスで有効になる設定と、信頼済み入口ヘッダーの書換えを照合します。本文形式に固有の例外が必要な場合は、実際に遮断した正常要求からパス、項目、管理ルールを特定します。 [WAFの関連付けと優先順位](https://learn.microsoft.com/en-us/azure/web-application-firewall/ag/policy-overview)

追加したAPIでは、要求IDによるメタデータの照合と、SSEの逐次配信を受け入れる。エージェントの本文はFoundry側で着信を確認する。 [監査の受入](../audit/request-body-audit.md) · [APIMのSSEと診断](https://learn.microsoft.com/en-us/azure/api-management/how-to-server-sent-events)

API、Product、固定バックエンド、診断、WAF関連付けを変更台帳に記録し、基盤再配備前の差分確認に使います。具体的なGUI操作は[Claudeの手動追加手順](../../operations/claude-messages.md#構築後にclaudeをguiで追加する)にまとめています。

## GUIで確認する場所

1. APIMの「名前付きの値」でキーヘッダー、上流audience、入口経路、Realtime署名鍵の参照を確認する。
2. APIs → All APIsのコードエディターで共通の購読キー検証とEntraアクセストークン検証を確認する。認証用テナント・API audience・`Gateway.Invoke`は適用された`validate-azure-ad-token`を見る。
3. 対象API → All operationsで操作制約・下流認証を確認する。CMを使うAPIは接続セッションの照合処理も確認する。
4. Operationのコードエディターで固定URI・Live／Realtime／PII等の固有処理を確認する。WSSは`onHandshake`を見る。
5. Policy fragmentsで`core-api-settings`のAPI設定準備、`core-api-request-preparation`の共通認証後の要求準備、`core-request-context`・`core-audit-authenticated-subject`・`core-gateway-telemetry`のC#説明、`core-trusted-ingress`の入口確認を読む。`core-rate-limit`・`core-credential-connection`等の個別処理もここで確認する。
6. Credential managerでprovider、connection、本人同意、APIM MIのaccess policyを確認する。

XMLには処理の目的・入力・失敗時の動作を説明する日本語コメントを記載する。登録前に計画を確認し、適用後のXMLを読み直して照合する。

監査出力の認証主体、要求context、AppGWログとの相関とqueryの採用規則は[監査の識別情報](../audit/audit-identification.md)を参照する。監査出力の追加と保存設計、資材の実装と対象環境の適用・受入を分けて扱う。
