# 第15回 APIキー発行の裏側

完成：2026-11-02T17:02:07.816024+09:00（JST）。**完成動画・字幕・再生ページを制作済み。** 完成後のデータ整理は別項に記録する。

- 完成尺：16分48.458秒（1008.4583333333334秒）
- 1600×900 / 24fps / H.264・AAC、24,203フレーム
- 採用78台詞、字幕176区間。全員通常速度1.0。速度・ピッチ変換なし。
- [完成MP4](../../output/lesson-15-gemini/egakumi-study-15.mp4)
- [再生ページ](../../output/lesson-15-gemini/index.html) / [一時再生URL](http://127.0.0.1:64361/)
- [字幕](../../output/lesson-15-gemini/lesson-captions.vtt) / [対話原稿](../../output/lesson-15-gemini/lesson-transcript.md)
- [連続した問答63.208秒](../../output/lesson-15-gemini/conversation-preview.html)

成果物の正本はファイル。一時URLは127.0.0.1限定のローカルサーバーを起動している間に利用できる。

## 到達点と出典

入力・所属・承認確認からSQL予約、APIMサブスクリプション作成、SQL確定・監査までを追える。

元資料：../../03-subscription-management.html#key-create。関連実装と章の出典はscript.jsonのsourceReferencesと[source-review.md](source-review.md)に記録。稼働Azureの取得画面・実測結果ではなく、リポジトリ定義に基づく教材。A社・分析チーム・月次分析バッチは架空例。

9章、原稿79台詞から導入051を省略し78台詞を採用。採用字幕本文5,545字。入力・現在所属・登録済み承認→独立commitのcreating予約→排他と再確認→APIM GET/不在のみPUT→結果照合→active更新とcreate監査を同じSQL transactionで確定する。二つの保存先の失敗と、同じ依頼ID・入力による再開を確認する。カタログ・試用・停止・移管・制限設定の詳細は後続回に残した。

## 音声と制作仕様

Gemini 3.8 Flash TTS、Google Cloud egakumi-example / global、既存の指定gcloud認証。MAKO=Aoede / MANABU=Charon / IKUMI=Orus。全員通常速度。既存の肖像・ロゴ・BGM、4秒導入、8秒終了を使用。終了の音楽はbrand-themeの63.8〜71.8秒。本編BGMは2秒クロスフェードとダッキング。

成功原音合計968.0秒。保存できた78件のAPI使用量：promptTokenCount=27,060 / candidatesTokenCount=24,200 / totalTokenCount=51,260。金額のAPI報告はないため未算定。下記051の使用量は保存できず、この合計に含めない。

通常sandbox初回はgcloud設定の書込み制限で、TTS送信前に停止。許可されたsandbox拡張実行で50台詞を保存した後、051の応答メタデータ保存時にディスク不足（ENOSPC）。051の音声・結果は永続化できなかったので再送せず採用外とし、元依頼・状態・旧設定をrevisions/disk-interruptionとdisk-interruption.jsonへ保持した。採用外051の再開予定はない。未送信052に障害条件の導入を加え、残り28台詞を新規生成して完了。保存済み50台詞は同一リクエスト/原音ハッシュで再利用。採用78件すべてHTTP 200、結果不明の自動再送なし。認証トークンを成果物へ保存していない。

## 映像と画面保持

teaching-settings-v2、16設定ビュー＋2補助図、固定カメラ。質問から回答まで同じviewId・構図・縮尺を保つ。枠は台詞の説明対象へ対応。確認問題069/072/074は枠なし、回答070/073/075から選択肢Bを強調。全viewの対応はsource-review.mdに記録。

ディスク不足への対策として、担当回専用stream-build-lesson.mjsを共有builderから派生した。共通ファイルは変更せず、共有renderer・音声処理・タイムライン・字幕・色・エンコード条件を維持し、同じPNGをフレーム数だけimage2pipeへ順次送る。開幕画像の一括保存を避け、個別work WAVをミックス完成後に削除する。独立差分レビューと標準検証で、順序・24fps・色・字幕・音声・カメラ・出力記録の整合を確認した（streaming-builder-review.json）。

## 最終検証

[verification.json](verification.json)：passed、warningsなし。生成本文・声・style・送信証跡・原音ハッシュ、通常速度、字幕、冒頭カメラ、BGM、共通終了、全編A/Vデコード、フレーム数・尺に合格。最終音量は-15.99 LUFS、True Peak -1.5 dBTP。

[原音QA](audio-audit.json)：78件の形式・証跡は構造合格。無音/長さの異常フラグなし。PCM full-scale到達の要聴取フラグ40件（うちnear-full-scale比率フラグ7件）、連続near-full-scale最長0.5417ms。これを可聴歪みの確定や修復済みとは扱わず、原音は保持する。

事前検査はpreproduction-verification.json、静止画はvisual-review.md、完成MP4の31枚はfinal-visual-review.mdに記録。発音、読み上げ欠落、会話の自然さ、可聴歪みの聴感評価は未実施。

## 連続した問答

採用MP4の679.500〜742.708333秒（11:19.500〜12:22.708333）、056 MAKO〜060 MANABU、63.208333秒・1,517フレーム。同じlesson15-retry画面で、同じ依頼ID/入力の再利用、入力を変えた場合の競合、新規依頼との違いを問答する。内部カット・速度変更・ピッチ変更・追加音声生成なし。プレビューの全編A/Vデコードはpassed。

IABで0.062秒からplaybackRate=1の再生開始を確認し、同じタブをリロード・シークせずに63.208333秒の終了（ended=true、errorなし）まで確認した。一時的な検証接続の不調はCUAの接続を復元して解消。playback-review.jsonに開始/終了を記録。さらに0/10/20/30/40/50/62秒の7標本で本文構図・話者・字幕・赤枠を照合した（conversation-review.md/json）。聴感確認とは分ける。

## 再制作と再生

保持原音を使い、APIへ再送せず、通常ビルドで音声を再合成できる。合成WAVを整理した場合は --reuse-audio true を付けない。

```sh
node docs/study/video-sample/episodes/15/stream-build-lesson.mjs --script episodes/15/script.json --generation-config episodes/15/generation.json --cast podcast-cast-gemini.json --output lesson-15-gemini
python3 docs/study/video-sample/verify-lesson.py --script episodes/15/script.json --generation-config episodes/15/generation.json --cast podcast-cast-gemini.json --output lesson-15-gemini
python3 -u docs/study/video-sample/serve-episode.py --episode 15
```

確認用出力画像の復元は、共有build-lesson.mjs --script episodes/15/script.json --output lesson-15-gemini --preview true（API送信なし）。再制作後の完成フレーム確認はextract-final-frames.pyとqa-final-contact-sheets.mjsの --replace を使う。連続プレビューはmake-conversation-preview.pyの056〜060を --replace で再抽出する。

完成MP4 SHA-256：`6b9f183cc76ab89de8907ede1a7524524e9bd133f83a51a40d7b5a40fd6a5231`

## 完成後のデータ整理

2026-11-02T17:04:39.190885+09:00（JST）、完成状態と整理済み状態を照合した。CONTENT-PLAN.mdの追加手順に従い、第15回出力内の確認用PNGと合成済みWAVだけを削除。削除82件、323,661,446bytes（308.668MiB）。削除前の一覧・容量・全SHAはcleanup-plan.json、削除後の保持検査と空き容量はcleanup-result.jsonに記録した。

保持313件の存在・SHA一致を確認。完成MP4、連続プレビュー、共通終了、ポスター、字幕、台本、タイムライン、書出し・検証結果、原音78件とメタデータ、requests-sent、生成設定/結果/試行記録、audio-cache、制作ソース・SVG・共通素材を保持。他回は変更していない。episode内の最終QA画像とJSONも保持した。

音声単独のWAV再生は利用できない。再生ページの音声単独コントロールを外し、完成MP4で音声を再生する。復元は上記stream-build-lesson.mjsの通常ビルド（--reuse-audio trueなし）で、保持原音から再合成する。音声APIを再送しない。完全再検証も、まずWAVを復元してから実行する。整理前の合格検証結果をそのまま保持した。
