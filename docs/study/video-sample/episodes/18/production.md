# 第18回 組織・SQL・APIMの状態を整える

完成：2026-11-02T17:13:40+09:00（JST）。**採用版の完成動画・字幕・再生ページを制作し、最終検証と連続問答の再生確認を完了。**

- 完成尺：16分34.833秒（約16分35秒）
- 映像：1600×900、24fps、H.264 / AAC、23,876フレーム
- 採用83台詞、字幕141区間、全員通常速度1.0。生成後の速度・ピッチ変更なし。
- [採用MP4 egakumi-study-18.mp4](../../output/lesson-18-gemini/egakumi-study-18.mp4)
- [再生ページ](../../output/lesson-18-gemini/index.html) / [一時再生URL](http://127.0.0.1:62619/)
- [字幕](../../output/lesson-18-gemini/lesson-captions.vtt) / [対話原稿](../../output/lesson-18-gemini/lesson-transcript.md)
- [53.375秒の連続問答](../../output/lesson-18-gemini/conversation-preview.html)

採用MP4 SHA-256：`a77bd91111e89e7082b48f20b628f62fd979db726c5d4bb5ee8863e3040cd1ad`。

localhostは実行中サーバーの一時閲覧先。正本は回別の出力ファイル。以下は整理前の完成・検証記録で、整理の実施結果は末尾へ追記する。

## 到達点と出典

CONTENT-PLAN.md第18回の到達点は、所有グループの観測と承認、SQL台帳とAPIM実体を区別し、不一致・取得不能時の確認先を選べること。

元資料は03-subscription-management.htmlのgroup-observation / subscription-sync / maintenance。Graph照会、所有グループ観測Job、同期の差分分類・確認・実行・監査、保守時の確認範囲を対応コードと照合した。参照開始時HEADは69832cd。詳しい確認先はscript.jsonのsourceReferencesとcontent-review.md。稼働Azureの取得・変更・実機検証は行っていない。

9章、採用本文5,442字。承認と観測、Graphの四状態、15分Job、SQLとAPIMの比較、同期の最大10件・5分署名・直前再確認・開始監査・再照会、不一致の具体例、変更時の確認先、3問の確認問題、まとめと第19回への接続を含む。

## 編集と原音の再利用

最初の94台詞版は18分14.958秒で、共通verifyが14〜17分の目安外を警告した。機構・具体例・確認問題は残し、重複する反応と要約11台詞をfirstCut.omitTurnIdsで省略。独立レビューで必須論点と対話の接続を確認し、83台詞・16分34.833秒へ整えた。詳細はeditorial-cut.json。

元の94台詞の台本・生成設定・生成結果・検証記録はhistory/full-94/、長尺MP4・タイムライン等はoutput側history/full-94/へ保持。全94件の原音と送信要求も保持する。

採用83台詞は送信済み本文・声・styleを一切変えず、元の録音時の文脈指示を含む要求と原音のハッシュを維持して選択した。prepare-editorial-cut.pyで元設定から採用対象を選び、共通generatorが原音83件を検証済みキャッシュとして再利用（attemptedThisRun=0）。編集のための追加API送信・再録は0件。音量・BGM・字幕・章時刻は編集後の長さで再合成した。

## 音声生成と承認

Gemini 3.8 Flash TTS / egakumi-example / global / 指定済みgcloudユーザー認証。MAKO=Aoede、MANABU=Charon、IKUMI=Orus、全員通常速度。

最初の拡張実行は自動承認レビューが台本文の外部送信への明示承認不足として拒否。ユーザーへ94台詞6372字・話し方指定・Google Cloudの指定プロジェクト・従量課金を示し、「承認して完成動画まで進める」との明示承認を取得した。既存認証の更新を確認後、94件すべてHTTP 200で生成完了。再送0件。トークンを制作ファイル・ログへ保存していない。

実際の生成原音合計1047.16秒、採用原音949.88秒。全94件のAPI使用量はpromptTokenCount=31,643、candidatesTokenCount=26,179、totalTokenCount=57,822。応答に金額がないため金額は未算定。

## 映像と画面保持

本編teaching-settings-v2、20固定view。質問から回答までviewId・表・構図・縮尺を保持し、赤枠のみ説明対象に合わせる。自動ズーム・パン・pulse・模擬ポインターなし。3確認問題の081/084/087は無枠、回答082/085/088から正解を強調。既存ロゴ4秒、肖像、冒頭の約0.85秒の話者カメラ、本編BGMの2秒クロスフェード・ダッキング、ブランド曲63.8〜71.8秒の共通8秒クロージングを使用。

初回の長尺版では一時PNGがENOSPCで停止したため、当該回の失敗したrender-workだけを整理し、回専用build-lossless.mjsを使った。共有レンダラー・音源・時刻・最終形式は維持し、中間画像をlossless WebPへ変換。代表画像の復号pixel一致、48フレームの連結試験、独立コードレビューを記録した。共有ファイルは変更していない。採用83台詞版は十分な容量を確認後、通常の共有build-lesson.mjsで完成した。

## 最終検証と確認範囲

verification.jsonはpassed、warningsなし。本文・声・通常速度・送信証跡・原音ハッシュ・字幕・冒頭カメラ・BGM・共通クロージング・全編の映像音声デコード・長さ・フレーム数を確認。

- 最終ミックス：-16.03 LUFS、True Peak -3.62 dBTP。
- 原音の構造検査は83件合格。無音・長さ異常のフラグなし。微小PCMピーク到達に関する要聴取フラグ43件はaudio-audit.jsonに保持し、可聴歪みを確認済みとは扱わない。
- 最終MP4から52枚を抽出し、全20view、字幕、設問の枠、3人の顔・名前・役割、話者中央寄せ、閉幕を目視。final-visual-review.mdへ記録。
- 発音、本文の脱落、演技、会話の自然さ、可聴歪みの聴感評価は未実施。機械検証・画像の目視・ブラウザー再生を聴感評価と同一視しない。

## 連続問答

採用MP4の146.958333〜200.333333秒、015 MAKOから020 IKUMIまでの採用5台詞を53.375秒・1281フレームで抽出。lesson18-statusesの同じ画面で、Graph四状態とAPIMの状態の区別を説明する。元MP4の連続時刻範囲を飛ばさず、プレビュー内の追加カット・速度・ピッチ変更なし。ブラウザーでplaybackRate=1、開始0.170929秒から53.375秒のended=trueまで再生した。conversation-preview.jsonとplayback-review.jsonに採用MP4/プレビューのハッシュと確認結果を保存。

## 再合成・復元

リポジトリルートで、保持した原音から合成する。WAV整理後は--reuse-audio trueを付けない。音声生成APIは呼ばない。

```sh
node docs/study/video-sample/build-lesson.mjs --script episodes/18/script.json --generation-config episodes/18/generation.json --cast podcast-cast-gemini.json --output lesson-18-gemini
python3 docs/study/video-sample/verify-lesson.py --script episodes/18/script.json --generation-config episodes/18/generation.json --cast podcast-cast-gemini.json --output lesson-18-gemini
node docs/study/video-sample/build-lesson.mjs --script episodes/18/script.json --output lesson-18-gemini --preview true
python3 -u docs/study/video-sample/serve-episode.py --episode 18
```

最初のコマンドでWAVを再合成してから完全検証を行う。原稿・設定は現在の採用83台詞の選択と録音時styleを維持する。WAV整理後の音声単独再生は利用できず、音声は完成動画から再生する。確認画像はpreviewコマンドや完成MP4から再作成できる。現行サーバーは本回専用serve-review.pyで127.0.0.1の空きポートへ限定している。


## 完成後のデータ整理

2026-11-02T08:14:23.481543+00:00。完成後の整理を実施。削除は本回出力のpreviews内の再生成可能画像と、直下のlesson-narration.wav・lesson-mix.wavに限定。

削除97件、論理容量326774436 bytes。保持468件の削除前後SHA-256一致を確認。実測空き容量差327192576 bytesは他処理にも影響される。詳細はcleanup/plan.jsonとcleanup/result.json。

採用MP4、共通クロージング、ポスター、再生ページ、字幕、原稿、タイムライン、生成raw WAV/JSON、requests-sent、生成結果、audio-cache、history/full-94、episodes側QA画像と制作ソースは保持。音声単独WAV再生は整理後に利用できず、音声は完成動画で再生できます。

復元：保持した原音・台本・設定・共通素材からローカル合成する。通常のbuild-lesson.mjsに本回の--script/--generation-config/--cast/--outputを指定し、--reuse-audio trueを付けずに実行する。音声生成APIは実行しない。容量制約時のbuild-lossless.mjsはreuse-audio必須のため、整理直後のWAV復元には使わない。WAV再合成後に共通verifyを再実行する。確認画像は同じ台本の--preview trueまたは完成MP4から再抽出する。
