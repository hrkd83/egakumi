# 第12回 利用制限と下流認証

制作日：2026-11-02（JST）。状態：完成MP4書き出し・標準検証合格。完成尺 **15分54.625秒**。

- [完成MP4](../../output/lesson-12-gemini/egakumi-study-12.mp4)
- [再生ページ](http://127.0.0.1:60474/) / [保存済みページ](../../output/lesson-12-gemini/index.html)
- [45.708秒の連続プレビュー](../../output/lesson-12-gemini/conversation-preview.mp4) / [プレビュー再生](http://127.0.0.1:60474/conversation-preview.html)
- [字幕WebVTT](../../output/lesson-12-gemini/lesson-captions.vtt) / [公開台本](../../output/lesson-12-gemini/lesson-transcript.md)
- [標準検証](verification.json) / [画面・字幕の構造検証](structural-qa.json) / [ブラウザー再生記録](browser-replay.json)
- [生成使用量](../../output/lesson-12-gemini/speech-usage-summary.json)

出力ファイルが成果物の正本。localhostのURLは一時的な閲覧用で、127.0.0.1の自動空きポートを使い、他回のサーバーを変更していない。

## 到達点と根拠

回数・トークン量・1要求の生成上限の違いと、入口キーと下流認証の分担を、一つの要求を追って説明できることを目標にした。

[教材16節：回数制限](../../../02-external-protection.html#apim-rate)、[17節：トークン制御](../../../02-external-protection.html#apim-tokens)、[18節：下流認証](../../../02-external-protection.html#apim-backend-auth)を、関連XML・API定義・config・登録資材と照合した。詳細は[技術根拠](source-review.md)と[台本](script.json)のsourceReferencesに記録。

公開設定例とリポジトリ定義に基づく教材。稼働Azureから設定を取得・検証した結果ではない。SQLの実際の利用枠も取得していない。test / productionの共通60回/60秒はenabled:falseとして説明し、値だけで現在の制限を断定しない。

回数枠の単位、正常なnullと取得不能503、出力上限の入力名とdynamic例外、TPMと期間quotaの対象・拒否、入口値の除去、Managed Identity・専用キー・本人OAuthの分担を区別した。本人接続を追加してもサブスクリプションの利用枠は変わらない。設定・保存・反映の操作詳細は第17回、エラーの照合と変更は第13回、本人接続手順は第19回以降へ残す。

## 台本・画面と編集

3人の新しい掛け合いから始め、目的→回数→生成上限と利用量→下流認証→Chat要求の追跡→確認3問→まとめと第13回の問い、の8章。原稿85台詞から81台詞を採用し、採用読み上げ本文は5358字。

初回の通常速度での実長は16分39.667秒。認証準備と業務成功・409を補足する057〜060の問答を、まとまりごとに省略した。原則は064と084に残す。056から061へ自然につながることを執筆担当が再確認。直前文脈が変わる061だけ同じ本文・新ID `lesson12-turn-061r2-mako` で再録し、他80台詞は本文・声・style・波形のハッシュ一致で再利用した。旧原稿・設定・生成記録は `revisions/initial-recording/`、成功原音と要求は出力先へ保持する。[author.py](author.py)にも最終編集を反映した。

本編は `teaching-settings-v2`。採用19view、教材用の設定メニュー、固定カメラ。同じ問いと回答はviewId・表・構図・縮尺を保ち、話者や字幕chunkでリセットしない。赤枠は説明する対象へ切り替え、確認問題069・073・077の出題時は表示しない。noticeも中立。

[補助図スクリプト](render-diagrams.mjs)は、計数の実行位置と、入口から下流へ資格情報を切り替える流れの2図を生成する。各図の赤枠違い3状態、計6SVGは赤枠除去後に同じ構図となる。自動ズーム・パン・pulse・模擬ポインターは使わない。

初稿と確定短縮版の技術レビューは必須修正なし。静止画86枚・4コンタクトシートで設定表と図を確認し、英字識別子の字幕途中改行を修正した。完成MP4から3人の顔と肩書き、話者中央寄せ、出力パラメーター、入口値の除去、確認問題の出題、終了ロゴの6フレームを抽出して再確認した（出力先 `final-qa/`）。

## 音声・共通演出

Google Cloud `gemini-3.8-flash-tts`、MAKO=Aoede / MANABU=Charon / IKUMI=Orus。全員通常速度1.0。生成後の速度・ピッチ変更なし。既存認証、指定済みプロジェクト、workers=1。認証トークンは成果物へ保存しない。

共通ロゴ4秒、既存肖像のホログラムと滑らかな冒頭カメラ、本編BGMの2秒クロスフェード・ダッキング、共通8秒クロージングを共有ビルダーで合成。終曲はbrand-themeの63.8〜71.8秒。共通素材・共有ツール・他回は変更していない。

## 完成検証

標準 `verify-lesson.py` は **passed / warningsなし**。

| 項目 | 結果 |
| --- | --- |
| 映像 | 1600×900 / 24fps / H.264 yuv420p |
| 完成尺・フレーム | 954.625秒 / 22,911フレーム |
| 全編デコード | 映像・音声とも合格 |
| 台詞と字幕 | 採用81台詞 / 字幕174cue。本文・原音・送信要求・設定・字幕の整合合格 |
| 音声 | AACステレオ48kHz / 全員1.0倍 |
| 最終ミックス | −15.99 LUFS / True Peak −1.5 dBTP |
| 冒頭・BGM・終了 | カメラ5区間、クロスフェード2秒、ダッキング、共通8秒終了が合格 |

連続プレビューは **8:16.708〜9:02.417（45.708秒 / 1,097フレーム）**。045の問いから048の回答までを途中カットなし・速度変更なしで抽出。`lesson12-auth-flow`を保持し、説明中の対象だけを「入口値の除去」→「接続先資格情報」へ切り替える。声とBGMは連続。プレビューの全編デコードとフレーム数も合格。

ブラウザーでは完成MP4のduration=954.625、1600×900、playbackRate=1、再生進行9.758秒、media errorなしを確認。連続プレビューは先頭から34.412秒の回答中、最後の45.708秒まで、シーク・速度変更なしで再生し、ended=true、media errorなしを確認。完成ページを引き渡し用に開いている。

利用可能なツールでは音声内容の聴取・認識ができないため、発音、本文の欠落、演技指示の不発声、会話の自然さの聴感確認は未実施。機械検証とブラウザー再生を聴感検査の代わりとは記録しない。

MP4 SHA-256：`f206a92cef78820c7725e0ca0ba24e5b5e3f03fc0de4b6da9fa51f823de17fef`

## 生成使用量

初回85台詞＋文脈変更1台詞、計86件はすべてHTTP 200。自動再送なし。API返却使用量は全生成で入力28,388・音声出力24,237・合計52,625トークン。採用81台詞は入力26,750・音声出力22,864・合計49,614トークン、原音実長914.56秒。実請求額は未確認。

## 再生成

リポジトリルートで実行する。既存成功音声と履歴は保持する。

```sh
node docs/study/video-sample/episodes/12/render-diagrams.mjs
python3 docs/study/video-sample/prepare-gemini-lesson.py --script episodes/12/script.json --config episodes/12/generation.json --output lesson-12-gemini
python3 docs/study/video-sample/generate-gemini-lesson.py --config episodes/12/generation.json --workers 1
node docs/study/video-sample/build-lesson.mjs --script episodes/12/script.json --generation-config episodes/12/generation.json --cast podcast-cast-gemini.json --output lesson-12-gemini
python3 docs/study/video-sample/verify-lesson.py --script episodes/12/script.json --generation-config episodes/12/generation.json --cast podcast-cast-gemini.json --output lesson-12-gemini
python3 -u docs/study/video-sample/serve-episode.py --episode 12
```

表示のみの修正は制作手順のprepare --replaceと検証済みキャッシュの結び直しを行う。本文・声・styleの変更は新IDで再録し、成功音声や結果不明要求を自動再送しない。
