# Model Gatewayの受入試験

[Model GatewayのAPI契約](../reference/gateway/api-contracts.md)、配備済みモデル、[用途別ポリシー](../reference/gateway/apim-policy-catalog.md)と照合します。採用するカテゴリと方式について、正常応答、認証拒否、容量や未対応入力の応答を確認します。上限・形式エラーはAppGW、APIM、提供元のどこで発生したかをログで判定します。

| カテゴリ | 確認する内容 |
| --- | --- |
| テキスト生成 | Chat Completions、Responses、採用するMessages等を呼ぶ。JSONは本文と完了情報、SSEは増分の受信・正常な終了・途中切断を確認する。ツール呼び出しや画像・文書入力は採用モデルごとに確認する |
| 埋め込み | OpenAI、採用するCohereの代表入力で件数・次元・順序と、次元指定がAPI契約に合うことを確認する |
| 画像 | 生成と編集を分け、採用する配備名・本文形式・応答の画像または参照を確認する |
| STT／TTS | 採用する文字起こし・音声入力と音声生成を試す。正しい音声形式、文字起こし、合成音声、SSMLの構文拒否を確認する |
| リアルタイム | WebSocket Upgrade、イベント、音声出力、正常終了を確認する。WebRTCはセッション作成、実音声、通話停止・観測の所有者照合を確認する |
| リランク | 基準文書とノイズを含む合成文書で、順位・件数・元データとの対応を確認する |
| 意思決定 | 合成入力で使用するモデル、構造化された結果、状態の引継ぎ、API契約に沿ったエラーを確認する |

## ストリーミングと音声

SSEは最初のイベントが全応答終了前に届くかを測り、受信した増分と保護された監査を照合します。WebSocket・WebRTCは基盤で接続・制御の記録を照合します。業務システムは送受信した業務テキストと取得できる文字起こしを記録し、利用できる基盤の接続IDと関連付けます。試用用のPortalは業務側の記録対象から外します。 [リアルタイム音声の契約](../reference/gateway/realtime-audio.md) · [本文監査](../reference/audit/request-body-audit.md)

## 確認に使うクライアント

- [統合クライアント](../../scripts/clients/integration_client.py)で採用したHTTP／SSEの方式を選ぶ。
- [音声クライアント](../../scripts/clients/voice_client.py)と[ブラウザー検証入口](../../scripts/clients/realtime_browser.py)で採用した音声の方式を確認する。
- [本文監査の確認クライアント](../../scripts/validation/coverage_client.py)で監査対象の方式を選ぶ。

実行するコマンドの `--help` で対応方式と必要な入力を確認し、ファイル出力先を環境の保護領域に指定します。
