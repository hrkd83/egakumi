# APIMポリシー一覧

2026年10月31日更新。Graphと文書解析を含む標準19 APIの登録資材を説明する。登録資材の構成と対象環境への反映状況は分けて扱い、実APIM・Microsoftサインイン・各クライアントの疎通は配備後に受入する。

## 要求が通る順序

1. APIMが購読キーの状態と、API・Product等の利用範囲を検証する。
2. 全体ポリシーが信頼した入口経路、設定済みキーヘッダー、検証済み購読IDを確認する。
3. APIポリシーがOperation、モデル、入力形式・サイズ、生成上限を確認する。
4. 購読ID単位で回数を計数し、要求ID・API・Operation等の監査情報を確定する。
5. CMが必要なAPIで、本人接続セッションを確認する。
6. 入口のキー・接続セッション等を除去し、下流専用のMI・OAuth・登録済み資格情報で接続する。

API登録処理は、APIの配備成功を確認してから共通ポリシーを適用し、読み直して照合する。`azd up`は共通基盤・モデルを準備し、続くGateway工程が必要なCM provider・認証窓口・名前付きの値・APIを設定してからPortalを配備する。

## 全体で共通のポリシー

| 処理 | 実施内容 | 主なファイル |
| --- | --- | --- |
| 信頼した入口 | AppGW経由の識別値を確認。native gRPCは登録済みTCP経路を使用 | `service.xml` |
| 購読キー | 設定済みヘッダーが1個で空でないこと、`context.Subscription.Id`の存在を確認。URLのキーを拒否 | `service.xml` |
| 呼出主体 | `subscription:<購読ID>`。同じ購読のキーは同じ利用枠を使う | `gateway.xml`、`protocol.xml`、`mcp.xml` |
| 要求ID | 正常応答・例外応答へ監査と同じ`apim-request-id`を設定 | `service.xml` |
| CM補助 | 購読付き入口を内部brokerへ転送。個人APIではSQL台帳の本人セッションを照合 | `credential_login.py`、`credential-login/*.xml`、`credential-connection.xml` |

`core-subscription-key-header`をGateway配備が管理する名前付きの値として使用する。既定は`api-key`。`Ocp-Apim-Subscription-Key`を採用する環境では共通設定を変更する。APIごとの自動alias受入は行わない。`Authorization: Bearer <APIMキー>`の製品は、APIMの標準キー検証まで成立するかを別に確認する。

共通入口の失敗は401と要求IDを返す。APIM標準の購読検証でも、無効・停止・範囲外のキーは拒否する。信頼したHTTP入口の不一致は403。既存Open Product関連も公開計画で確認し、キーなし経路を残さない。

## APIごとのポリシー

| 処理 | 実施内容 |
| --- | --- |
| 共通処理の継承 | `<base/>`と完了マーカーを確認する。設定不足は503 |
| 操作・モデル | 公開Operationと固定モデル・profile・URI・queryを照合する |
| 入力・出力上限 | 本文サイズ、型、配列・文字列、生成トークン上限を検査する |
| 回数制限 | 検証済み購読IDをcounterに使用。追加の本人資格情報で利用枠を切り替えない |
| 購読プロファイル | `subscription-profile.xml`の固定処理をAPIへ展開し、PortalのSQL設定を内部APIから取得。正常な値を30秒キャッシュし、取得障害を無制限へ変換しない |
| LLMトークン上限 | `token-limit.xml`を対応HTTP APIだけから参照。プロファイルの毎分・期間内トークン数をAPIM標準ポリシーへ渡す。未設定は制限なし |
| 下流認証 | Foundry等はMI、外部サービスは登録済みの認証設定。キー・本人sessionを下流へ流さない |
| 監査 | 要求ID、購読、API、Operation、モデル、状態、使用量を記録。本文・token・keyを含めない |

## モデル・Operation固有のポリシー

| 対象 | 組み込む処理 | 初期設定の主な値・公開契約 |
|---|---|---|
| OpenAI Chat | profile内のモデル別生成上限を検査 | `max_completion_tokens`、各モデル128,000 |
| DeepSeek・Mistral・Grok Chat | `compatible`内のモデルごとの上限を検査 | `max_tokens`。DeepSeek 384,000／Grok 500,000。Mistralは未確認値を作らず自動値 |
| Model Router | `router` profileからRouter配備を呼ぶ | 振り分け先で変わるため、固定最大値を設定せず自動値 |
| Responses | モデル別の上限を検査し`store=false`へ設定 | `response`／`pdf`とも各モデル128,000。クライアントが会話入力を送る |
| FLUX 2 flex／pro | profileとモデルを照合し、profileの固定`backendUrlTemplate`へ書換え | 生成／編集の2 Operationを共用。既定flex、proは明示選択 |
| GPT Image | 本文またはURL配備名を照合しAzure用URIへ書換え | 生成3モデル、編集のアップロードは4 MiB以内 |
| Embedding・Rerank | モデル・入力配列・件数・サイズを検査 | 検索インデックス側のモデル・次元・版と合わせる |
| ファイル文字起こし | URLの配備名とmultipartサイズを検査 | `gpt-transcribe`、4 MiB以内 |
| Speech | SSML／WAVとサイズを検査し、Speech用URI・ヘッダー・Entra認証形式へ変換 | TTS 8 KiB、STT 1 MiB |
| Decision | 配備名と型付き要求を検査し、固定の`/systemone`へ転送 | `decision-1`、8 KiB |
| PII | `{text,language}`を検査しLanguage用要求・結果へ変換 | 固定のPIIモデル版、対応言語と警告コードを照合。生成MCPも同じREST処理を使う |
| Graph /me | CMの本人接続を検査し、本人のユーザー情報を固定`$select`で取得 | 氏名・所属・連絡先・アカウント状態など12属性。任意のGraph URLや属性追加は不可 |
| Document Analysis | ファイルの形式・サイズとRead／Layoutモデルを検査。上流の非同期解析を開始し、固定ホストの結果だけを照会 | 1 MiB、最大約60秒、上流結果4 MiB。詳細は[文書解析の契約](document-analysis.md) |

出力上限の正本は [model-capabilities.json](../../apps/gateway/apim/model-capabilities.json)。HTTPでは選択したモデルに対応する上限を検査する。Realtime WebRTCの作成時の512は初期設定であり、モデル上限4,096とは区別する。WebSocket確立後やWebRTCのDataChannelはAPIMポリシーで検査しないため、Portalが送信する設定と、Gatewayが全クライアントへ強制する制限は同一ではない。

### Live・Realtime・文字起こしWSS

| 接続契約 | ポリシーが実施する内容 | 接続後に扱う内容 |
|---|---|---|
| Realtime WebRTC | SDP検査、短期資格情報を使う作成、署名した所有者ticket、終了時の所有者・期限・call ID照合 | WebRTC音声・Realtimeイベント |
| Realtime WSS | 購読キー検証、許可model query、固定上流、接続回数上限 | Realtime会話イベント |
| Realtime Observer | 署名ticketとcall所有者の照合後、既存callへ接続 | sidebandイベント |
| **Live WebRTC** | `{sdp,instructions?,voice?}`を検査し、固定モデルと`delegation.type=client`で上流sessionを作成 | WebRTCメディアとLiveイベント。応答は`{sessionId,sdp}` |
| **Live WSS** | 購読キー検証・固定`/openai/v1/live/sessions`・接続回数上限 | `session.start`でモデルを指定、PCM音声・transcript・`session.close`／`session.closed` |
| **文字起こしWSS** | 購読キー検証・固定上流・`intent=transcription`・接続回数上限 | `session.update`でモデルを指定、音声入力・途中／確定テキスト |
| Voice Live | Speechサービス用の固定接続先・query・MI認証 | Voice Live固有の音声・WebRTC制御 |

Live作成の入力上限はJSON 128 KiB、SDP 65,536文字、instructions 8,192文字。初期voiceは`marin`。失敗入力は400、形式が不正な上流の成功応答は502へ整える。[Live作成のXML](../../apps/gateway/apim/policies/live-create.xml)

WSSの`websocket.session.models`は接続後イベントで使うモデルのクライアント契約を表す。APIMが検査するのは接続開始時の要求である。frame内のモデル・音声・設定とセッション継続時間は、アプリと上流サービスで管理・検査する。HTTP要求回数と音声セッションの利用量は別に計測する。[Microsoft公式のWebSocket制約](https://learn.microsoft.com/en-us/azure/api-management/websocket-api)

WSSは接続時に購読キーヘッダーを送れるSDK・アプリサーバーから利用する。ブラウザはキーを保持するBFF等を経由する。ブラウザのWebRTCは、アプリのサーバーがGatewayの作成APIを呼び、SDP answerをブラウザへ返す。

WebRTCの接続制御はAPIMを通り、音声メディアは端末とサービスのICE経路を通る。受入ではその宛先・ポート・端末ネットワークの到達性を確認する。[Azure Live WebRTC](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/gpt-live-webrtc)

## Credential Managerとの接合

CMは、必要なAPIが利用する共通の下流OAuth機能とする。`graph-profile-v1`のGraph APIと、CMが必要なMCPに適用できる。入口の購読キーと下流の本人OAuth接続を分ける。

本人接続は、CM共通APIでサインインを開始し、確認済み購読・本人・provider・接続・期限をセッションに結び付ける。対象APIへ`X-Core-Connection-Session`を送る。任意のUPNや接続名の指定を本人確認の代わりに使わない。

サービス共有接続は、サーバー設定のproviderとconnectionを使う。どちらもCMの`get-authorization-context identity-type=managed`がAPIM自身の接続利用権限を確認し、CMが下流tokenを取得・更新する。本人セッションの検査は、その前のCoreポリシーで行う。

初回のログインURL提示、サインイン、状態照会、元要求の再実行は[CM接続手順](../setup/credential-manager-login.md)を参照する。API応答だけで利用者のブラウザを開くとは扱わない。Dify等は対応ツール／プラグインで連携する。

接続再認証は409、下流の権限不足は403として区別し、下流の生エラー・token・Cookie・認証URLを業務応答へ流さない。

## GUIで確認する場所

1. APIMの「名前付きの値」でキーヘッダー、上流audience、入口経路、Realtime署名鍵の参照を確認する。
2. APIs → All APIsのコードエディターで共通キー検証を確認する。
3. 対象API → All operationsで操作制約・下流認証を確認する。CMを使うAPIは接続セッションの照合処理も確認する。
4. Operationのコードエディターで固定URI・Live／Realtime／PII等の固有処理を確認する。WSSは`onHandshake`を見る。
5. Policy fragmentsで`core-rate-limit`・`core-credential-connection`等を確認する。
6. Credential managerでprovider、connection、本人同意、APIM MIのaccess policyを確認する。

XMLには処理の目的・入力・失敗時の動作を説明する日本語コメントを記載する。登録前に計画を確認し、適用後のXMLを読み直して照合する。
