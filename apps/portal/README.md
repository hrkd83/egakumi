# egakumi AI Gateway Portal

Egakumi Coreに含まれる管理ポータルです。AIモデルとツールAPIの利用方法を確認し、実際のAPIを試し、組織のグループ単位でAPIキーを管理できます。Microsoft Entra IDでサインインし、日本語・英語の画面を利用します。

## できること

| 機能 | 利用できる内容 |
| --- | --- |
| Model Gateway | テキスト生成、埋め込み、画像生成、音声STT/TTS、リアルタイム音声、リランク、意思決定 |
| Tool Gateway・REST | Microsoft Graph /meによる本人プロフィール取得、PII Redaction、Document Analysis |
| Tool Gateway・MCP | PII Redactionの接続・ツール照会・実行 |
| APIキー管理 | グループ所有キーの発行、明示的なキー表示、停止・再開、棚卸確認、所有グループ移管、削除、利用制限の設定 |
| 基盤管理者向け機能 | 所有グループの健全性確認、会社情報管理、SQL台帳を基準としたAPIM購読の同期 |

モデル・ツールの詳細ページには「使い方ガイド」「API仕様」「試す」を用意しています。入力や選択に応じた要求例・組み込みコード例と、実APIの応答を確認できます。利用できるモデル・操作・入力上限は、配備したAPI定義と画面の案内に従います。

Model Gatewayのリアルタイム音声は、次の4シリーズで案内します。WebRTC対応モデルは初回にWebRTCを選び、WebSocketのみ対応するモデルはWebSocketを選びます。利用者が明示的に選んだモデル・接続方式はシリーズごとに保持し、各ページには公開APIが対応する選択肢だけを表示します。

| シリーズ | 詳細ページ（日本語） | 用途 |
| --- | --- | --- |
| GPT-Live | `/ja/models/realtime/gpt-live` | 聞きながら話す全二重の音声会話 |
| GPT Realtime | `/ja/models/realtime/gpt-realtime` | 音声を直接理解して応答。通常・miniを同じシリーズで選択 |
| Voice Live + GPT | `/ja/models/realtime/voice-live-gpt` | Azureの音声認識・GPT・音声合成を連携。現行の会話モデルは`gpt-6.1-sol` |
| GPT Live Transcribe | `/ja/models/realtime/gpt-live-transcribe` | マイクの音声を継続して文字起こし。AIの音声応答は返さない |

旧リアルタイム音声の`openai`詳細URLはGPT Realtimeへ、`azure-ai-speech`詳細URLはVoice Live + GPTへ案内します。シリーズの分類は表示・選択用であり、APIの提供元ID・接続契約・キーの利用権限は各APIの定義に従います。

Azure SpeechのTTSは「読み上げ（TTS）」→「試す」で、日本語の標準Neural音声7種から選べます。同じ文章で声を変えて生成し、返ったWAVを再生して比較します。音声を変えると以前の生成結果を消し、選択した音声名をSSMLとコード例にも反映します。[Microsoftの音声一覧](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts)

リアルタイムの「試す」では、GPT Realtimeは10音声、Voice Live + GPTは同じ日本語7音声から選べます。声は接続前に選び、変更するときは接続を終了してから再接続します。GPT-Liveは現在の公開契約で確認したMarinを使用し、文字起こし専用シリーズには声の選択を表示しません。UIで選択できることと、実サービスで音声を生成・聴取できたことは分けて確認します。[Realtimeの音声](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/realtime-audio-webrtc)、[Voice Liveの音声合成](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-how-to#audio-output-through-azure-text-to-speech)

Hosted Agent APIとA2Aは準備中です。画像編集はGateway APIで提供し、Portalの実行パネルには含めていません。金額に基づく利用上限とGraph認証完了のプッシュ通知は未対応です。

## 使い始める

1. 配備先の`https://portal.<domain>`を開き、許可されたEntraテナントのアカウントでサインインします。
2. 「管理」のAPIキー管理で、所属グループが所有するキーを確認します。発行可能なグループでは、会社・用途・連絡先を指定して新しいキーを発行できます。
3. Model GatewayまたはTool Gatewayで対象を選び、使い方とAPI仕様を確認します。「試す」で利用するキーを選び、入力して実行します。
4. Microsoft Graphの本人接続を求められた場合は、案内されたMicrosoft認証をPortalと同じアカウントで完了します。完了確認後、元の操作へ戻ります。

音声会話は利用者が開始し、マイクの許可・ミュート・停止を操作します。ファイルを使う機能は、画面に表示された対応形式とサイズ内のファイルを選びます。APIの実行には利用料金が発生する場合があります。

ルート`/`はブラウザーの優先言語に応じて表示し、一致しなければ日本語へ進みます。主な画面は`/ja/models`・`/ja/tools`で、英語では`/en/…`を使います。画面右上でも言語を切り替えられます。

## 認証・権限とデータの扱い

| 利用者 | 操作範囲 |
| --- | --- |
| サインインした利用者 | カタログ・ガイドの参照と、現在所属するグループが所有する有効キーによるAPI実行 |
| 所有グループのメンバー | そのグループのキーの発行・管理。候補グループと発行条件は環境設定に従う |
| 基盤管理グループのメンバー | グループ健全性、会社情報、購読同期などの管理画面 |

Portalのログインと、Graph等の下流API用の本人接続は別に管理します。本人接続はPortalが確認した利用者と購読に結び付け、Microsoftのトークン管理はCredential Managerが担当します。PortalのAPI実行処理はキーと接続セッションをサーバー側で扱います。

通常の新規キーは、リリース済みAPIをまとめた`released-apis`製品を使います。利用範囲はAPIMの製品・APIの関連付けと購読状態で決まり、Portalは操作直前にも現在のグループ所属と所有権を確認します。回数・対応LLMのトークン制限はキー詳細で設定できます。

SQLへ保存するのは、利用者の識別情報・ログインセッション・購読メタデータ・監査・暗号化した本人接続状態です。APIキーの実値はAPIM、MicrosoftのOAuthトークンはCredential Managerが管理し、PortalのSQLへは保存しません。生成内容、アップロードした文書、解析結果、マイク音声をPortalのSQLへ保存する機能はありません。ローカルSQLとAzure SQLのデータは同期しません。

購読同期はSQL台帳にAPIMを合わせる管理操作で、購読の再作成・有効化・削除を伴います。差分と確認画面を確認してから実行してください。[配備・権限・同期の詳細](../../docs/setup/portal.md)を参照してください。

## Azureへ導入・更新する

新規導入は[Coreの導入手順](../../README.md)に従います。`azd up`が共通基盤とGatewayの準備後にPortalを配備します。Entra、DNS、秘密情報、paramsの準備は[Portal配備手順](../../docs/setup/portal.md)で確認します。

構築済み環境のPortalだけを更新・再試行する場合は、構築に使用したparamsとstateを使い、リポジトリ直下で計画を確認してから実行します。

```sh
python3 scripts/deploy_portal.py --config "$DEPLOY_PARAMS_FILE"
python3 scripts/deploy_portal.py --config "$DEPLOY_PARAMS_FILE" --execute
```

配備コマンドが管理VMでのイメージ作成、DB初期化、アプリ更新、公開ヘルス確認を行います。手元のDockerや、Portal更新のための基盤再構築は不要です。更新・失敗時の再試行・撤去は[運用手順](../../docs/operations.md)を使います。

## ローカルで起動する

Node.js 24、Docker Engine・Compose、開発用Entraの戻り先と資格情報を用意します。初回は[ローカル認証テストの準備](../../docs/testing/portal-local-auth.md)に従って、`.local/<environment>/portal/runtime.env`と同じ環境のSQL管理用設定を作成してください。アプリ直下に`.env.local`を作らず、launcherから保護した設定を子プロセスへ渡します。Gatewayを実行する場合は、構築済みGatewayへの接続設定と、ローカル台帳のグループ・購読設定も必要です。

準備後、`DEPLOY_PARAMS_FILE`と絶対パスの`DEPLOY_LOCAL_DIRECTORY`を設定して、リポジトリ直下から実行します。

```sh
PORTAL_LOCAL_SQL_ENV_FILE="$DEPLOY_LOCAL_DIRECTORY/secrets/portal-local-sql-admin.env" \
  docker compose --env-file "$DEPLOY_LOCAL_DIRECTORY/secrets/portal-local-sql-admin.env" \
  -f apps/portal/compose.local.yml up -d --wait
npm --prefix apps/portal ci
python3 scripts/run_portal_local.py --config "$DEPLOY_PARAMS_FILE" \
  --admin -- npm run db:bootstrap-local
python3 scripts/run_portal_local.py --config "$DEPLOY_PARAMS_FILE" -- npm run dev
```

[日本語画面](http://localhost:3000/ja)または[英語画面](http://localhost:3000/en)を開きます。`db:bootstrap-local`は開発専用のローカルDBと実行用権限を用意し、Azure SQLを変更しません。

アプリは起動した端末で`Ctrl+C`、SQLは上のComposeコマンドの`up -d --wait`を`stop`へ置き換えて停止します。停止してもローカルDBのデータは保持されます。開発用設定やローカルSQLの資格情報はGitへ登録せず、Azureへコピーしません。

## 開発・テスト・保守

コーディング規約・TSDoc・テストの共通ルールは[DEVELOPMENT.md](../../DEVELOPMENT.md)、Portal固有の開発ルールは[AGENTS.md](AGENTS.md)を参照してください。`apps/portal`でローカル検査を実行します。

```sh
npm test
npm run lint
npx tsc --noEmit
npm run build
npm audit
```

SQL統合試験には専用のローカルSQLと追加設定が必要です。[共通の実行方法](../../DEVELOPMENT.md#テストの実行方法)を参照してください。実環境の受入条件・結果は[動作テスト仕様書](../../docs/testing/integration-tests.md)、資材の構築・更新・撤去の確認は[構築テスト仕様書](../../docs/testing/construction-tests.md)で管理します。

| 確認したいこと | 参照先 |
| --- | --- |
| 公開API、モデル・音声の契約 | [プラットフォーム仕様](../../docs/reference/platform.md)、[API定義](../gateway/apim/apis/)、[出力上限](../gateway/apim/model-capabilities.json) |
| 本人接続の保存・更新・失効 | [共通クライアント契約](../../docs/reference/connection-session-clients.md)、[接続セッション実装](lib/connection-session/) |
| 文書解析の対応形式・制約 | [Document Intelligence仕様](../../docs/reference/document-analysis.md) |
| 生成応答・SSEの保護上限 | [応答処理](lib/model-response.ts)、[ストリーム処理](lib/model-stream.ts)、[関連テスト](tests/model-stream.test.ts) |
| 音声の送信・再生とHTTP／WebSocket窓口 | [音声クライアント](lib/realtime-client.ts)、[中継処理](lib/realtime-relay.ts)、[Portalサーバー](scripts/serve-portal.ts)、[関連テスト](tests/realtime-relay.test.ts) |
| 入力上限・WAV形式 | [モデル入力](lib/model-request.ts)、[音声入力](lib/speech-request.ts)、[音声の関連テスト](tests/speech-gateway.test.ts) |
| 依存・表示素材 | [package.json](package.json)・[lockfile](package-lock.json)、[CSSの出典](styles/README.md)、[提供元ロゴ](public/brand/providers/README.md) |
