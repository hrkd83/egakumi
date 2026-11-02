# Egakumi Coreの音声通信と遅延評価

本書は現行Coreの音声経路と、遅延・品質を切り分けて確認する方法をまとめます。公開経路と接続方式の正本は [API定義](../../apps/gateway/apim/apis/)、Portalの接続・音声処理は [apps/portal](../../apps/portal/)、モデル選択は [モデル一覧](foundry-model-catalog.md)を参照してください。過去の端末や一回の試行で観測した遅延・ポートを、全環境の保証値や必須構成として扱いません。

## 現行の通信経路

Coreは共通のApp Gateway・Firewall・Internal APIMと、FoundryへのPrivate Endpointを持ちます。PortalはCore内のContainer Appであり、サーバー側で購読キーを保持してGatewayへ要求します。

| 接続 | 制御・要求の経路 | 音声の経路 |
| --- | --- | --- |
| 音声ファイルSTT・Speech TTS | PortalサーバーまたはAPIクライアント→Gateway→APIM→Foundry/Speech | HTTP要求・応答の本文 |
| OpenAI Realtime・Live・文字起こしWSS | Portalサーバーまたはキーを保持するクライアント→GatewayのWSS→APIM→固定上流 | 同じWebSocket接続のイベント |
| OpenAI Realtime・Live WebRTC | PortalサーバーがGatewayのHTTP作成APIでSDPを交換 | ブラウザーと上流サービスがICEで確立した経路 |
| Azure Voice Live WebRTC | PortalサーバーがGatewayの専用WSSで`rtc.call.sdp.create`を送り、answerを受け取る | ブラウザーと上流サービスのWebRTCメディア経路 |

WebRTCメディアがApp Gateway→Firewall→APIMを音声frameごとに通過する構成ではありません。FoundryのPrivate EndpointはHTTP・接続準備側の名前解決と経路に適用し、別経路のICE接続まで同じものとして説明しません。

WebSocketでは、APIMはhandshakeの購読キー・固定上流・許可queryを検査します。接続後の音声frameやセッション設定はPortalと上流の契約で扱います。WAFが全通話内容を継続検査するものではありません。

## 公開する接続契約

表のパスはGatewayの公開originからの相対パスです。公開`/v1`と上流のAPI版は別に管理します。

| 契約 | 公開経路 |
| --- | --- |
| Realtime作成・終了 | `POST /models/openai-realtime/v1/calls`、`POST /models/openai-realtime/v1/calls/{callId}/hangup` |
| Realtime WSS・observer | `/models/openai-realtime/v1/ws/`、`/models/openai-realtime/v1/observer/` |
| Live作成・WSS | `POST /models/openai-live/v1/sessions`、`/models/openai-live/v1/ws/` |
| リアルタイム文字起こしWSS | `/models/openai-transcription/v1/ws/` |
| Voice Live通常WSS・WebRTC制御WSS | `/models/voice-live/v1/ws/`、`/models/voice-live/v1/webrtc/` |
| 音声ファイルSTT | `POST /models/openai-audio/v1/deployments/{deployment}/transcriptions`、`POST /models/azure-speech/v1/recognize` |
| Speech TTS | `POST /models/azure-speech/v1/synthesize` |

OpenAI Realtime・Live・文字起こし・Voice Liveは異なる開始設定、イベント、終了契約を持ちます。接続先・固定query・許可モデルを相互代用しません。Realtime miniの公開定義はWSSです。選択可能な方式はPortalカタログとAPI定義の双方で確認します。

## 認可・所有者・終了処理

Gatewayの共通認可はAPIM購読キーです。PortalへのEntraログインと、APIMから下流へのマネージドID認証はそれぞれ別に行います。

Realtimeの作成・終了・observerは、検証済み購読ID、署名した所有者ticket、call IDと期限を照合します。Portalはさらにログイン本人と実行状態を対応付け、別利用者の通話を操作させません。キーと所有者ticketはサーバー側で保持し、ブラウザーには必要なSDPや画面状態だけを渡します。

終了時はマイクtrack、再生、peer connection、制御WSSを契約に従って閉じます。Realtimeのcallを作成済みならhangup対象として保持し、SDP適用・ICE・画面操作が途中で失敗した場合も回収します。端末の切断や停止ボタンの表示だけで、上流処理の停止・課金終了まで確認したとしません。

## 遅延の測定と切り分け

測定では同じモデル・接続方式・voice・発話終了設定・端末・接続経路を固定します。接続確立、接続済みの通常ターン、tool等を使うターン、割込みは分けて記録します。

| 区間・指標 | 確認すること |
| --- | --- |
| 接続開始→利用可能 | サインイン・Portal準備、DNS/TLS、Gateway handshake、上流セッション、ICE確立を区別する |
| 発話終了→最初の応答音声受信 | VADの待ち時間、上流生成、ネットワーク転送を切り分ける |
| 応答音声受信→可聴再生 | decode、再生キュー、先読み、音声デバイスの遅延を確認する |
| 割込み→旧応答の停止 | ローカル再生停止、上流取消、未聴部分の履歴調整、次ターン開始を別々に確認する |
| 継続会話 | jitter・損失・再送、受信量と再生量、キュー増加、長音声、次の発話への復帰を確認する |
| 正常終了・途中切断 | 各transportと上流処理の終了、残る実行・利用量を確認する |

Portalの受信PCMキュー・先読み、ミュート・割込み・履歴処理は [音声処理](../../apps/portal/lib/)の実装と実機結果の双方を参照します。未接続の実装や修正を保存しただけの状態を、配備済み・再試験合格と読み替えません。

中央値・p95等は同じ条件の複数試行から算出し、要求件数・失敗・途中切断も併記します。単一の最短値やp95の平均を代表性能にしません。利用者が聞く品質は端末再生まで含めて確認し、字幕到着やSDP交換の成功だけで音声往復を合格にしません。

## ネットワーク・配置を変えるとき

端末の社内proxy、VPN、Wi-Fi、NAT、UDP許可と実際のICE候補を確認します。サービスごとの公式条件と実測を使い、過去に見えたポートを別製品や別リージョンへ共用しません。FirewallやWAFを一律に無効化することを検証の前提にしません。

Coreの共通`foundryLocation`はモデル・Speech等の接続先へ影響します。地域を変更するときは、選んだ全モデルの版・SKU・利用枠・提供容量、Voice Live/Speechの対応条件、PE・DNS・MI権限を確認します。PEを日本に作ることと、上流モデルの処理地域を日本へ移すことを混同しません。

電話中継やサーバー発WebRTCは現行Portalとは別の追加設計です。採用する場合は、電話側の音声形式・codec・sample rate、WebRTCライブラリー、利用者と実行の対応、取消・正常終了・再接続を新たに検証します。管理VMは証明書・構築・運用処理のための資源であり、電話中継を提供する現行Coreの業務ホストとして説明しません。

## 実施結果と受入

動作テストは [動作テスト仕様書](../testing/integration-tests.md)、資材・構築・設定切替は [構築テスト仕様書](../testing/construction-tests.md)で確認します。実環境の進捗・残件・再開記録はリポジトリ外の非公開運用用ワークスペースで管理します。詳細な受入条件を本書へ重複掲載しません。

実機の音声、割込み、長音声、正常終了・途中切断・再接続、認可・所有者、利用量は対象モデル・方式ごとに記録します。過去のローカル試験件数や一回の音声成功を、別revision・別環境・未確認方式の合格へ転記しません。

## 整理前の記録

変更前の調査・設計・検証記録は [2026-11-01整理前のスナップショット](../history/pre-20261101-realtime-latency-review.md)に保存しています。現在の仕様・手順・受入判定と区別して参照してください。
