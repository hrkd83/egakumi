# 第6回 外向き通信と出口IP — 制作記録

制作日：2026-11-02（JST）。状態：完成。完成MP4・自動検証・代表画面の目視確認・ブラウザー再生確認を完了。

## 到達点と範囲

Web依存先の許可、生成されるFQDN、SNAT、出口IPの期待値と実測の違いを説明できること。対応資料は01ネットワークの制御の31〜33節、36〜37節。

本教材は2026-11-01時点の教材・リポジトリ定義・公開設定例を根拠とする。稼働中Azureの設定取得や疎通の実測は行わず、構成からの期待値と観測結果を区別する。第5回の評価順の全解説、第7回の詳細ログ調査、応用編のハブ切替手順は扱わない。

## 共通仕様

Gemini 3.8 Flash TTS / MAKO=Aoede（Moderator）、MANABU=Charon（Product Manager）、IKUMI=Orus（Solution Architect）。全員通常速度1.0倍、生成後の速度・ピッチ変換なし。指定Google Cloud既存プロジェクトと既存ユーザー認証を使う。トークンを成果物へ保存しない。

1600×900、24fps、H.264 / AAC。共通の肖像、冒頭ロゴ4秒、本編BGMのクロスフェードとダッキング、最後の共通ロゴ8秒を使う。変更範囲はepisodes/06/とoutput/lesson-06-gemini/に限定。

## 準備

[事前検査](preproduction-verification.json)：passed。共通ファイル・素材のハッシュ、必要ツール、sharpを確認。空き容量4.78 GiB。担当回の一時ファイルだけを扱い、他回の出力・実行ロックへ触れない。

## 再生サーバー

共有serve-episode.pyのbyte range未対応によりチャプター移動ができない既知の事例がある。共有プログラムは変更せず、既存第5回の実績ある実装を第6回の[serve.py](serve.py)へ適用する。127.0.0.1の空きポートを自動選択する。

完成後の再起動：リポジトリルートで `python3 -u docs/study/video-sample/episodes/06/serve.py`。

## 台本・図・事前レビュー

87台詞、9章、表示本文5,651字、読み上げ6,051字。冒頭4台詞191字。Web依存先→FQDN生成→明示出口→SNAT→期待値/実測→確認3問→まとめ・第7回予告の構成。

| 章 | 教材の節 | 図・説明 |
| --- | --- | --- |
| 今回の問い・目的 | 31・36節 | 外部から見える送信元と、通信許可の分担 |
| Web依存先 | 31〜32節 | 送信元・用途・FQDN、APIMと管理VM、実行基盤とビルド |
| FQDN生成 | 33節 | 入力・設定生成・API公開・配備結果、追加と旧許可の保持 |
| 明示出口 | 36節 | 公開IPなしの管理VM、暗黙出口・UDR・許可・SNATの区別 |
| SNAT | 37節 | 公開宛て、Network/private、Application、PE直接接続 |
| 期待値と実測 | 37節 | 期待値の受渡し、確認先の許可、自動照合なし、対象からの測定 |
| 確認3問・まとめ | 31〜33・36〜37節 | 空配列、privateRanges、実測済みかの確認 |

[独立技術レビュー](source-review.md)：重大な技術矛盾・到達点欠落なし、3問の回答と出典5節リンクを確認済み。Microsoft公式のSNAT/private rangesとdefault outbound accessも照合。PaaSへのdefaultOutboundAccess効果を一般化せず、Bicepの属性と管理VM等の暗黙出口への依存回避として説明する。

[映像プレビューレビュー](visual-review.md)：32種類の図を確認。期待値から確認先への矢印を廃止し、自動実測があるように見える表現を避けた。APIM・ACAの図はネットワーク送信元を表し、NSG照合は本文でAPIMに限定する。英単語・日本語途中・句読点の行頭など33箇所の字幕改行を調整。最大2行、最大40文字／行、全文連結一致を確認。再プレビュー88枚を作成し、代表画面の文字と3人の顔・名前・役割を確認した。

## 音声生成準備

prepareで87件の設定を作成。dry-run正常（認証取得・送信なし）。通常実行はsandbox内でgcloud更新ができず停止、音声リクエスト0件。指定Google Cloudへのネットワーク実行は自動承認レビューを通過し、同じ設定・既存認証で継続した。新規プロジェクトや認証キー、IAM設定は作成していない。

## 音声生成結果

87件すべてHTTP 200で成功。再試行0件・結果不明0件。発話950.64秒、冒頭の掛け合い32.56秒。API返却使用量は入力29,410・音声出力23,766・合計53,176トークン。実請求額は未確認。[使用量と原音監査](generation-audit.json)に保存。全員通常速度1.0倍、生成後の速度・ピッチ変換なし。原音と実際の送信本文・style・ハッシュを担当回の出力に保存し、認証トークンは保存していない。

## 完成ファイルと最終検証

- 完成尺：16分34.917秒（約16分35秒）。23,878フレーム、1600×900、24fps、H.264 / yuv420p、48kHzステレオAAC。
- 9章、87台詞、131字幕cue。冒頭ロゴ4秒、ホログラムの3人引きと中央寄せ、章ごとの図、字幕、最後の共通ロゴ8秒を収録。
- [自動検証](verification.json)：passed、warningsなし。台本・設定・実送信・原音・完成MP4のハッシュ、通常速度、全文字幕、カメラ、音源サンプル数、全編映像・音声デコード、BGMのクロスフェードとダッキング、共通クロージングを確認。
- 最終ミックスWAV：−16.03 LUFS / −1.5 dBTP。AAC化後の音量再測定は未実施。
- [独立映像QA](visual-review.md)：完成MP4から代表22枚と字幕改行確認4枚を抽出し目視。3人の顔・名前・役割と中央寄せ、代表図、確認3問、まとめ、第7回予告、ロゴ終了と暗転に問題なし。英語の技術語・環境変数・ホスト名の字幕も原寸で確認。
- [ブラウザー再生](browser-verification.json)：duration=994.916667、playbackRate=1、seekable=[0,994.916667]、video.errorなし。13:38の確認問題チャプターから後半へ移動し、映像表示と再生の進行を確認。確認後は冒頭の停止状態へ戻し、再生ページを残した。
- 音声の聴感・発音確認は未実施。利用可能なツールでは音声内容を聴取・認識できないため、自動検証やブラウザー再生を聴感確認とは扱わない。全編を人が視聴したとは記録しない。

MP4：47,104,768 bytes（44.92 MiB）。SHA-256：`ce7f6a6f6f5b991f23c04af2e59d3ab129cceb0f0a30d17f618a2d675b7f0ee8`。

[完成動画（MP4）](../../output/lesson-06-gemini/egakumi-study-06.mp4) / [保存済み再生ページ](../../output/lesson-06-gemini/index.html) / [WebVTT字幕](../../output/lesson-06-gemini/lesson-captions.vtt) / [完成台本](../../output/lesson-06-gemini/lesson-transcript.md) / [タイムライン](../../output/lesson-06-gemini/lesson-timeline.json) / [最終QA時刻](../../output/lesson-06-gemini/qa-final/manifest.json) / [字幕補足QA](../../output/lesson-06-gemini/qa-final/supplemental-caption-manifest.json)。

一時再生URL：[http://127.0.0.1:51321/](http://127.0.0.1:51321/)。再生ページの「動画を保存」「字幕を保存」から保存できる。localhostはサーバー起動中のみ有効。成果物の正本は上記MP4。ローカルサーバーは自動承認レビュー通過後に起動し、127.0.0.1へ限定した。他回のサーバー・ロック・出力には触れていない。

## 再生成

リポジトリルートで以下を実行する。成功済みの音声と証跡は保持して再利用する。

```sh
python3 docs/study/video-sample/generate-gemini-lesson.py --config episodes/06/generation.json --workers 1
node docs/study/video-sample/build-lesson.mjs --script episodes/06/script.json --generation-config episodes/06/generation.json --cast podcast-cast-gemini.json --output lesson-06-gemini --reuse-audio true
python3 docs/study/video-sample/verify-lesson.py --script episodes/06/script.json --generation-config episodes/06/generation.json --cast podcast-cast-gemini.json --output lesson-06-gemini
python3 -u docs/study/video-sample/episodes/06/serve.py
```

完成状況は本記録で管理し、共有の計画・README・プロファイル・キャスト・素材・レンダラーは変更していない。
