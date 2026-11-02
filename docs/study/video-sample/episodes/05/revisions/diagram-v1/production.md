# 第5回 Firewallは何を判断するか — 制作記録

制作日：2026-11-02（JST）。状態：完成。80台詞を生成し、完成MP4の最終自動検証と代表フレームの目視確認を完了。

## 到達点・範囲

Network／Applicationルールの違いと評価順、内部中継・基盤通信の許可を説明できること。80台詞、字幕本文5,465字、読み上げ用5,918字、9章。通常速度の生成音声から完成尺を測り、約15分を目安に判断する。

本教材と説明は2026-11-01時点のリポジトリ定義・公開設定例に基づく。稼働中Azureの取得値・疎通試験結果とは区別する。第6回の外部FQDN生成・SNAT、第7回の詳細なログ調査、第10回のIDPS詳細へ範囲を広げない。

## 台本と出典の対応

| 章 | 教材の節 | 主要な説明・図 |
| --- | --- | --- |
| 今回の問い・経路 | [26節](../../../01-network-control.html#fw-roles)、[34節](../../../01-network-control.html#fw-decisions) | ポリシー関連付け、FW経由とPE直接経路 |
| 2種類のルール | [26節](../../../01-network-control.html#fw-roles) | IP・ポート、HTTP Host / HTTPS SNI、Network FQDNのNTP、非復号TLS |
| 評価順 | [27節](../../../01-network-control.html#fw-evaluation) | 種類→グループ→コレクション、一致時終了、既定拒否と組み込み許可 |
| 内部中継 | [28節](../../../01-network-control.html#fw-internal)、[34節](../../../01-network-control.html#fw-decisions) | AppGW→APIM、gRPC条件、AppGW→ACA、状態追跡と復路UDR |
| APIM基盤通信 | [29節](../../../01-network-control.html#fw-apim-network) | StorageタグTCP 443、プラットフォームSQLと業務SQLの区別 |
| 管理・ACA・DNS | [30節](../../../01-network-control.html#fw-vm-aca-network)、[35節](../../../01-network-control.html#fw-maintenance) | NTP、管理・監視・認証、独自DNS、DNS proxy |
| 確認3問 | [34節](../../../01-network-control.html#fw-decisions) | StorageにFQDN許可を重ねるか、NTPの種別、SQL PEでFW許可を足すべきか |
| 保守・まとめ | [35節](../../../01-network-control.html#fw-maintenance) | 同じ通信の条件を照合、AlertとDenyの区別、次回の外向き通信 |

対応定義は `infra/modules/network.bicep`、`network-bindings.bicep`、`portal-network.bicep`、`credential-broker-network.bicep`、`apps/portal/deploy/gateway-egress.bicep`、公開configを参照。全ルール・全ポートの列挙は元資料へ残す。

Microsoft公式の[ルール処理](https://learn.microsoft.com/en-us/azure/firewall/rule-processing)、[FQDNフィルタリング](https://learn.microsoft.com/en-us/azure/firewall/domain-filtering-overview)、[NetworkルールのFQDN](https://learn.microsoft.com/en-us/azure/firewall/fqdn-filtering-network-rules)も照合した。教材との矛盾は見つからなかった。

## 編集とレビュー

- 91台詞の初稿から重複説明を削り、80台詞へ整理。3問と各到達点を維持した。
- Networkからという説明は今回の独自ルールに限定し、Threat Intelligence等を含めたFW全体の先頭判定と混同しない。
- 優先度400/350の図は「仮の比較」と明示。Networkが先という原理の説明であり、実グループ定義の一覧ではない。
- IP値は作らず、実内部VIP・実入口IP・タグと表記。AppGW→ACAの許可宛先CIDRとUDRの/32を区別。
- 字幕は最大2行、英文用語の途中や行頭の句読点を避ける改行へ調整。図の矢印ラベルはノードの外へ配置。
- 内容担当の独立レビューで、確定80台詞に重大な技術誤り・削除後の会話の参照切れ・到達点の欠落なし。全26種類の図のプレビューと代表フレームを目視確認。

## 共通仕様と準備結果

MAKO=Aoede / Moderator、MANABU=Charon / Product Manager、IKUMI=Orus / Solution Architect。音声はGoogle Cloud `gemini-3.8-flash-tts`、全員1.0倍、生成後の速度・ピッチ変換なし。共有の肖像・ロゴ・BGMを使用し、共有ファイルは変更しない。

`check-production.py --episode 5`：passed（必要ツール、sharp、共通ファイル・素材ハッシュ）。`prepare-gemini-lesson.py`：80件の生成設定を作成。`generate-gemini-lesson.py --dry-run`：正常、認証取得・ネットワーク送信なし。

## 初回の停止履歴（解消済み）

通常実行はgcloud認証更新時に停止。実行記録の `attemptedThisRun=0`、80件すべて `not_attempted`。Google Cloudへの音声生成リクエストは送っていない。

ネットワーク・認証更新を伴う再実行の自動承認レビューが、内部Azure構成を含む80台詞を指定Google Cloudへ送る明示的承認が必要という理由で拒否した。台詞と話し方を `egakumi-example` / `developer@example.com` / Gemini 3.8 Flash TTSへ送る承認を依頼し、ユーザーの「完成させてください」という続行指示を受領。指定先のまま再実行し、自動承認レビュー通過後に音声生成を開始した。

確認対象：[台本](script.json)、[送信内容と話し方](generation.json)、[人向け確認原稿](review.md)。

## 音声生成結果

80件すべてHTTP 200で成功。再試行0件、結果不明0件。発話905.44秒、冒頭の掛け合い32.56秒。API返却使用量は入力27,966・音声出力22,636・合計50,602トークン。実請求額は未確認。[使用量記録](../../output/lesson-05-gemini/speech-usage-summary.json)に保存。全員通常速度で、原音・送信本文・style・ハッシュを担当回の出力に保存した。認証トークンは保存していない。

## 最終表示の調整

初回MP4は全編自動検証に合格（警告なし、−16.0 LUFS / −1.5 dBTP）。完成フレームの独立目視で、確認問題の条件行と問い行がQ1/Q2に見える点を確認したため、第5回だけのvisualを「通信条件」「問い」の2カードへ変更した。本文・字幕・音声style・台詞IDは変更していない。prepareで新しい台本ハッシュを結び直し、80件すべて検証済み原音を再利用、API送信0件・認証取得なしで再合成した。初回の検証証跡は verification-initial.json に保持し、最終出力も再検証に合格した。

## 完成ファイル・最終検証

- 尺：15分47.958秒（約15分48秒）。22,751フレーム、1600×900、24fps、H.264 / yuv420p、48kHzステレオAAC。
- 音声：Gemini 3.8 Flash TTS、Aoede / Charon / Orus。全員通常速度1.0倍、速度・ピッチ変換なし。
- 構成：9章、80台詞、95字幕cue、冒頭ロゴ4秒、終了ロゴ8秒。BGMは指定のクロスフェードとダッキングを使用。
- [最終自動検証](verification.json)：passed、warningsなし。台本・設定・実送信・原音・最終MP4のハッシュ、全文字幕、カメラ、音源サンプル数、全編映像・音声デコード、共通クロージングを確認。
- 最終ミックスWAV：−16.0 LUFS / −1.5 dBTP。AAC化後の音量再測定は実施していない。
- 完成MP4から16場面を抽出。3人の名前・役割・中央寄せ、代表図、確認3問、まとめ、次回案内、ロゴ終了と暗転を目視確認。確認3問の表示修正後も文字切れ・重なりなし。
- 音声の聴感・発音確認は未実施。利用ツールでは音声内容の聴取・認識ができないため、自動検証やブラウザー再生確認を聴感確認とは扱わない。

MP4：45,501,636 bytes（43.39 MiB）。SHA-256：`8026f7ed4ac0a228cc0f472db58529e6c7661efef1c5bcee421a06fa2417dd38`。

[完成動画（MP4）](../../output/lesson-05-gemini/egakumi-study-05.mp4) / [再生ページファイル](../../output/lesson-05-gemini/index.html) / [WebVTT字幕](../../output/lesson-05-gemini/lesson-captions.vtt) / [完成台本](../../output/lesson-05-gemini/lesson-transcript.md) / [映像QAの時刻と根拠](../../output/lesson-05-gemini/qa-final/manifest.json)。

一時再生URL：[http://127.0.0.1:65190/](http://127.0.0.1:65190/)。再生ページの「動画を保存」「字幕を保存」から保存できる。localhostは起動中だけ有効で、成果物の正本は上記MP4。

## ローカル再生の補足

標準の共有 `serve-episode.py` では、この環境のブラウザーが `seekable=[0,0]` と判断し、後半チャプターへの移動が0秒へ戻ることを実際に確認した。共有プログラムは変更せず、第5回だけの [serve.py](serve.py) でHTTP byte range（206 / Content-Range / Accept-Ranges）に対応した。

新サーバーでは `seekable=[0,947.958333]`、12:19の確認問題ボタンから739.393秒へ移動、playbackRate=1、video.errorなしを確認した。MP4を変更する処理ではない。旧サーバーは今回起動したものだけ停止済み。他回のサーバーには触れていない。今後の共有ツール改善候補として、byte range対応を本記録に残す。

再起動：リポジトリルートで `python3 -u docs/study/video-sample/episodes/05/serve.py`。127.0.0.1の空きポートを自動選択し、外部へ公開しない。表示されたURLを使う。
