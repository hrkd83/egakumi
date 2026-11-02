# 第10回 Firewallの許可とIDPSの検知 — 完成記録

2026-11-02（Asia/Tokyo）。状態：**完成MP4を書き出し、標準検証・表示確認を完了**。発音と聴感の確認は未実施（下記参照）。

## 完成ファイルと再生

- [完成動画 MP4](../../output/lesson-10-gemini/egakumi-study-10.mp4)：**16分15.25秒**、1600×900、24fps、H.264 / AAC、23,406フレーム。48,964,445 bytes（約46.70 MiB）。
- [再生ページ](../../output/lesson-10-gemini/index.html)、[日本語字幕 WebVTT](../../output/lesson-10-gemini/lesson-captions.vtt)、[表示用台本](../../output/lesson-10-gemini/lesson-transcript.md)。
- 一時再生URL：http://127.0.0.1:58948/ 。同じページの「動画を保存」でMP4を保存できる。localhostは一時閲覧用で、出力ファイルが正本。
- 一時サーバー再起動：リポジトリルートで `python3 -u docs/study/video-sample/episodes/10/serve.py`。空きポートを選び、127.0.0.1だけへ公開する。HTTP byte range対応で章移動を確認済み。共有サーバーの非対応を補うため回専用に実装し、共有プログラムは変更していない。
- MP4 SHA-256：`4dfd500fd583b8f67fffbcd1c88453df444fb2ad5e2dd518180c9c930d9fd139`。

## 到達点と出典

通常の通信規則Allow／DenyとIDPS Alert／Denyを分け、経路、TLS、IPによる方向分類から検査範囲を説明する。主範囲は [02の9〜11節](../../../02-external-protection.html#idps-settings)。経路の復習に2〜3節、ログの読み方に19節を補足参照した。

[source-review.md](source-review.md)にHTML・IaC・config/params例・Microsoft一次資料6件を対応付け、[content-review.md](content-review.md)に全90台詞の独立内容QAを記録した。設定値と図は現行リポジトリ定義・公開設定例に基づく教材で、稼働Azureの取得値・適用結果・実測ではない。

本番Premium/AlertとテストStandard/Off、Alertの並列検知とDenyのinline動作、規則Allow後のIDPS遮断、HTTP80とTLS非復号・PE直接経路、RFC1918/private間Internal、署名overrideと通信bypass、SNAT・DNS proxy・Threat Intelligence・通知の分担を扱った。次回予告は第11回のAPIMサブスクリプション認証・入力検査。

## 採用仕様と編集

90台詞、読み上げ5,999字、表示本文5,684字。MAKO=Aoede、MANABU=Charon、IKUMI=Orus、Gemini 3.8 Flash TTS。**全員通常速度1.0、生成後の速度・ピッチ変更なし**。音声実長934秒を測り、間と章・ロゴを加えて975.25秒に確定した。尺合わせの加速や無音追加はしていない。

共通 `study-series-v1`、本編 `teaching-settings-v2`。教材用設定ビュー11種類と補助図3種類の計14 viewIdを使用。90台詞を省略せず、最大2行・各34文字、204字幕chunk。質問と回答の構図を維持し、赤枠だけ説明対象に合わせる。確認問題73/77/81は正解枠なし、回答74/78/82から正解を強調する。

補助図は `idps-modes`（Alert/Deny）、`coverage-paths`（3経路）、`direction`（方向分類）。外部参照のないSVGで、設定メニューと図を同じ構図へ併記した。本編は固定カメラ、自動ズーム・パン・pulse・模擬ポインターなし。冒頭のみ共通仕様の話者カメラを使用する。

共通肖像・4秒ロゴ・BGM・8秒クロージングを維持。本編BGMは2秒クロスフェードとダッキング、終了はブランド曲63.8〜71.8秒。共有仕様・キャスト・素材・レンダラー・他回は変更していない。

## 生成証跡と使用量

Google Cloud `egakumi-example` / global / Agent Platform API、既存gcloud認証を使用。台本90台詞・5,999字と声/話し方指示の送信について、自動承認レビュー停止後にユーザーが「この台本を送信して制作を続ける」と明示許可した。

90/90要求がHTTP200で成功。workers=1、自動再送0、失敗要求の再送0。音声とリクエストのハッシュを検証して保存。認証トークンはメモリーのみで、成果物へ保存していない。使用量は promptTokenCount=29,059、candidatesTokenCount=23,350、totalTokenCount=52,409。金額は算定していない。

生成後の修正は英単語の改行と表示chunk分割だけ。本文・声・styleは不変で、90原音の検証済みキャッシュを再結合した。追加の音声生成要求は0。最終script SHA-256：`abdfe1ad917e58cac48f6a9691c27a7611445a3d10f44400cb3176ba5c3cc6c0`。

## 検証結果

- [標準検証](verification.json)：**passed、warningsなし**。全編23,406フレームと音声、クロージング192フレームを最後までデコード。原音/送信本文/声/style/音声ハッシュ、字幕204cue、通常速度、冒頭カメラ、BGM・終了ロゴ、尺・形式・音量を確認した。
- 最終ミックス：**−15.97 LUFS、−1.61 dBTP**。標準の音量・True Peak基準を満たす。
- [構造QA](structural-qa.json)：14 viewId内の構図保持、字幕全文一致、最大2行34字。全204chunkで英単語の語中改行0。
- [静止画QA](visual-review.md)：91プレビューと長い字幕、最終修正chunkを確認。完成MP4から冒頭の全員・各話者・確認問題・終了ロゴを抽出し、`qa/final-movie-contact.png`で実映像も確認した。
- [ブラウザー再生確認](browser-verification.json)：1600×900、全尺975.25秒、errorなし、playbackRate=1、全尺へのseekable範囲と章移動を確認。再生ページを成果物として保持した。

### 連続した質問・回答

[49.75秒の連続プレビュー](../../output/lesson-10-gemini/conversation-preview.html)（[MP4](../../output/lesson-10-gemini/conversation-preview.mp4)）は元動画の **157.791667〜207.541667秒（2:37.792〜3:27.542）**。turn017 MAKOの質問からturn022 MAKOの理解確認まで、1194フレームを内部カットなしで抽出した。3人の原音・BGM・通常速度をそのまま維持し、全編デコード合格。

ブラウザーで0秒から49.75秒のendedまで1.0倍で完走を確認し、開始・39.48秒・終了の画面を目視した。全6台詞で同じ `viewId=idps-modes` と同一のSVG構図・縮尺を保ち、質問から回答の間に本文画面の切替やカメラリセットがないことを[continuity-qa.json](continuity-qa.json)とも照合した。字幕と話者だけが進行する。

### 音声の確認範囲

[audio-audit.md](audio-audit.md)では原音90件のハッシュ・PCM形式・実尺・非ゼロ信号を確認し、全無音、異常尺、長い無音の顕著な兆候はなかった。一方、原音の短い飽和sampleが全体の約0.00475%にあり、最長同極性runは0.417msだったため、高感度の信号監査では注意として記録した。可聴な歪みとは断定していない。

利用できるツールでは音声の内容を聴取・認識できないため、**発音、演技指示の発声有無、対話の聴感・歪みの確認は未実施**。機械検証やブラウザーの等速完走を、聴感による合格とはしていない。完成動画と声のみ/BGM入り音声を再生ページから試聴できる。
