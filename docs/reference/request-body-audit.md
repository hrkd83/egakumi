# APIの本文監査

## 提供するAPIの監査要件

本文監査の目標範囲は、初回構築のAPIとAzureポータルから後で追加するすべての業務API。現時点の採取対象は[初回構築](#初回構築)を参照する。調査では要求IDから、利用者が渡した業務データと実際に受け取った応答を追跡できることを受入条件とする。GETなど本文のない方向、資格情報を除外した方向、配信・記録が途中で終了した方向は区別して記録する。

本文監査の対象APIでは、APIMが認証済み主体と要求IDを引き継ぎ、HTTPの本文証跡を同じEvent Hubs、保存ワーカー、Blobへ送る。監査対象に追加するときは採取方式、接続先、除外する資格情報を決め、実効ポリシーと証跡の復元を公開前に確認する。追加対象がSSEやMCPの場合は逐次配信の維持も受け入れる。 [SSEの制約](https://learn.microsoft.com/en-us/azure/api-management/how-to-server-sent-events) · [MCPの制約](https://learn.microsoft.com/en-us/azure/api-management/export-rest-mcp-server)

### WebSocket・WebRTCの監査分担

| 記録元 | 証跡 |
| --- | --- |
| 基盤 | WebSocketではAPIMが観測した接続・切断・エラーと認証主体を関連付ける。WebRTCでは作成、認可、観測接続、停止要求など基盤を経由した制御操作と結果を残す。通話準備の成功と実際のメディア接続は別イベントにする。 [APIM接続ログの項目](https://learn.microsoft.com/en-us/azure/azure-monitor/reference/tables/apimanagementwebsocketconnectionlogs) |
| 業務システム | 送受信した業務テキスト・取得可能な文字起こし、方向、時刻、順序、終了状態を記録する。音声の実体は保管対象から外す。WebRTCの音声はクライアントから提供元へ流れるため、文字起こし等は観測元を記録する。 [WebRTCの音声経路](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/gpt-live-webrtc) |

PortalはAPIの試用画面として使う。接続の調査では、APIMの要求IDと接続ログを照合する。資格情報、Cookie、接続チケット、SDPの認証用データは証跡の対象外とする。業務システムは自分が記録した証跡へ、取得できた基盤の要求IDや接続IDを関連付ける。Azureでは合成セッションを使い、基盤が観測した接続・切断と、WebRTCの制御操作の応答を確認する。

## 初回構築

開発・テストはEvent Hubs Standard、本番はPremiumを使う。Core配備で本文監査用の非公開ネットワーク、マネージドID、Event Hubs、専用Blobを作る。Gateway配備で同じイメージから中継と保存ワーカーを起動し、準備状態を照合してから監査対象のAPIを公開する。両方とも常駐するContainer Appで、保存ワーカーにIngressは設けない。イメージは管理VMでビルドして同じ非公開ACRへ登録する。

APIMが付与する要求IDで、本文と従来のメタデータ監査を照合する。対象によって採取位置が異なる。

| 対象 | 採る内容と位置 |
| --- | --- |
| OpenAI Chat Completions・Responses | 中継で、APIMの制御を反映した提供元向け入力と、JSON／SSEの応答を採る |
| 埋め込み・リランク・画像・音声・意思決定・Graph・PII・Document | 認証・認可・利用枠を通過した要求では、APIMの変換前の入力と公開用の応答を同じ監査基盤へ送る。PIIとDocumentは、APIMの変換後の応答を採る。Documentの結果取得用チケットは除く |
| PIIの生成MCP | ツールが呼んだREST側でPIIの業務入出力を採る。外側のJSON-RPC／SSEを読むとストリーミングに影響するため、MCP接続の要求IDとREST処理の要求IDで照合する |
| 手動追加するClaude Messages・Count Tokens | Coreと同じFoundryに配備したモデルを使い、中継でJSON／SSEを採る。Claudeを追加する場合に中継IDへ対象Foundryの推論権限を付与する。 [Claude公開手順](../../apps/api-gateway/policies/portal-examples/claude-messages.md) |
| Realtime・Live制御 | 基盤では作成・認可・接続・停止等のメタデータを採る。業務テキスト・文字起こしは呼び出し側で扱う |

初回構築は標準HTTP 20操作のうち、OpenAI中継の2操作とAPIMが本文を提出する15操作が対象。残り3操作はRealtime／Liveの制御用である。APIMで停止した要求の本文は、認可後の本文送信より手前にあるためメタデータで確認する。外部MCPや独自ストリーミングAPIを追加する場合は、対応する業務本文の採取先と、セッション・逐次配信を維持する経路の確認を公開前に行う。 [MCPポリシーの制約](https://learn.microsoft.com/en-us/azure/api-management/export-rest-mcp-server#configure-policies-for-the-mcp-server)

既存のGateway APIのEntraアプリを使う。中継を有効にする場合、GatewayのEntra認証を有効にし、構築サブスクリプションと同じテナントでこのアプリのv2アクセストークンを発行する。テストと本番の標準設定では本文監査が有効で、別テナントの入力はCore開始前に拒否される。中継は自己のVNet内にある入口で、公開APIの委任トークンと区別し、APIMのマネージドIDのObject IDに限定する。テナントでアプリへの割当が必須の場合は、APIMのマネージドIDがapp-onlyトークンを取得できるよう、管理者がこのアプリのアプリケーション権限と割当を確認する。提供元・Event Hubs・Blobはそれぞれ中継または保存ワーカーのマネージドIDで認証する。

## 保存と障害時の動作

中継は認可済みの入力をEvent Hubsへ送って受領成功を確認してから提供元を呼ぶ。応答も受領の確認後に返す。SSEは監査対象のイベントを送信する単位で確認して逐次転送する。分割した記録は要求ID、方向、連番で照合する。入力の記録に失敗した場合は`503`で終了する。SSE配信開始後の障害は配信を途中で終了するため、最後まで記録できた要求と途中までの要求を区別する。提供元を一度呼び出した後の失敗では、既に実行された処理が残ることがある。

Event Hubsが受領済みのデータは、初期設定の7日間の保持内に再処理できる。保存ワーカーはBlobに保存した後に処理位置を更新し、再送時は同じ証跡IDを照合する。Event Hubsのスロットリング・サーバーエラーと、ワーカーの滞留・保存エラーをAzure Monitorで監視する。本文・キュー・チェックポイントの保存先はそれぞれアクセス権限を制限する。本文の閲覧・持ち出しには対象案件と期限を記録し、担当者本人に一時的な読み取り権限を付与する。

本番の本文専用Blobは、不変保管（WORM、Write Once, Read Many）を作成から2,557日間適用する。Core配備後にポリシーをロックし、初回の監査対象API公開前にロックと日数を読み戻す。保護期間中は保存済みの証跡の上書きと削除を防ぐ。ロック後の期間短縮はできない。期間満了後の廃棄は組織の手続で扱う。開発・テストの本文BlobはWORM無効とし、作成から30日を超えた本文を自動削除の対象とする。既存のLog Analyticsと診断ログの監査Storageは別の設定で管理する。[Azureのコンテナー単位の不変保管](https://learn.microsoft.com/en-us/azure/storage/blobs/immutable-policy-configure-container-scope)

業務上のテキスト・JSONと、取得できる文字起こしを残す。保存前に再利用できる資格情報を除く。画像・音声・添付の実体は保管せず、採取できる形式・サイズ・SHA-256で照合する。Multipartではテキスト欄と、添付ごとの照合情報を保存する。分割されたSSEツール引数は、復元と資格情報除去が保証できるまで欠落として記録する。自由記述には検出できない形式の機密情報が含まれ得るため、本文専用Blobは機密データとして扱う。

OpenAI／Claudeの中継はHTTP本文16 MiB、監査用の抽出結果は方向ごとに1 MiBを上限とする。中継経路で要求が上限を超える場合は`413`、提供元応答では配信前なら`502`、配信開始後なら中断して部分記録を残す。APIMからの本文提出では、既存APIのサイズ上限を維持するため、大きすぎる方向や採取できないストリームについて理由を監査へ残す。監査基盤が記録を受領できない場合はエラーを返す。

## Azure受入

- 合成データで、Chat Completions・Responses・ClaudeのJSON／SSEと、埋め込み・Graph・PII・Documentの入力と公開応答を同じ要求IDで復元する。画像・音声・Multipartでは、生成結果や添付の実体が保存されず、テキスト・形式・サイズ・ハッシュが得られることを確認する。
- 非公開の中継へ公開経路・別ID・委任トークンで接続できないことと、資格情報の合成マーカーが保存先・通常ログに出ないことを確認する。
- Event Hubsへの送信を試験環境で失敗させ、入力側の`503`と上流未呼出、SSE配信開始後の中断を確認する。
- Blob書き込みの一時失敗とワーカー再起動後に再開し、欠落・上書き・重複を確認する。Event Hubs上での滞留を保持期限の前に検知できることを確認する。
- 本文WORMの保護範囲、保持日数、ロックの読戻しを確認する。不変ポリシーを有効にした専用の検証用コンテナーで、ワーカーの新規書込みと再送が成立し、保護中のBlobの上書き・削除が拒否されることを確認する。
- SSEの初回応答時間、イベント間隔、長時間接続、通常JSONの応答時間を負荷時に確認する。対象プラン、非公開経路、本文の期限付き閲覧・削除も照合する。

## 調査時の照合

Log Analyticsの要求IDから証跡Blobの同じ要求IDの接頭辞を開く。調査者本人に`Storage Blob Data Reader`を本文コンテナーの範囲で一時付与し、管理経路から該当する記録だけを取得する。取得した記録は環境の保護領域に置き、Blobと同じ`<REQUEST_ID>/<CAPTURE_ID>/<連番>.json`の階層を維持する。ディレクトリは700、ファイルは600とする。

```bash
python3 -I -B scripts/audit/audit_body.py \
  --config '.local/<environment>/params.local.json' \
  --records '.local/<environment>/evidence/body-audit/<REQUEST_ID>/<CAPTURE_ID>'
```

同じ要求ID配下の`<CAPTURE_ID>.reconstructed.json`に、復元できた入出力、SSEのテキスト、欠番と終了状態を保存する。`complete`は記録の連番と終端が揃ったことを示し、`bodyOmissions`は各方向で本文の採取を省いた理由を示す。クライアントが表示まで完了したかを調べる場合は、クライアント側の証跡と照合する。
