# 第8回 基盤管理者の接続と作業実行 — 最新版制作記録

2026-11-02（JST）。**最新CONTENT-PLANとteaching-settings-v2に合わせた再制作を完了。** 完成動画・画面・機械検証・連続した問答の視覚的確認は完了。発音や会話の自然さの聴感確認は未実施。

## 完成動画・再生・保存

- [全編を再生](http://127.0.0.1:56017/index.html) / [完成MP4](../../output/lesson-08-gemini/egakumi-study-08.mp4)。
- [約61秒の連続問答プレビュー](http://127.0.0.1:56017/conversation-preview.html) / [プレビューMP4](../../output/lesson-08-gemini/conversation-preview.mp4)。
- [約56秒の接続経路プレビュー](http://127.0.0.1:56017/continuous-previews/private-route/conversation-preview.html)。
- [WebVTT字幕](../../output/lesson-08-gemini/lesson-captions.vtt)、[台本](../../output/lesson-08-gemini/lesson-transcript.md)、[タイムライン](../../output/lesson-08-gemini/lesson-timeline.json)、[書き出し情報](../../output/lesson-08-gemini/lesson-export.json)、[再制作結果JSON](remake-result.json)。

完成尺は **999.708333秒（16分39.708秒、約16分40秒）**。1600×900、24fps、23,993フレーム、H.264 / AACステレオ48kHz。ファイルは **51,625,889 bytes（約49.2 MiB）**。

再生ページに動画・字幕・台本の保存リンクを用意。MP4が成果物の正本で、localhostはサーバー稼働中だけの一時URL。前回の一時サーバーは終了していたため、新しく127.0.0.1の空きポートを選んで起動した。再起動時は表示されるURLを使う。

```sh
python3 -u docs/study/video-sample/episodes/08/serve.py
```

## 前回版から変えたこと

旧本編の図・カード中心の画面を、教材専用の設定メニューと補助図へ更新した。本編82台詞を **20個のviewId** にまとめ、60台詞は共通settings、22台詞は設定欄と図を併記する自己完結SVGとした。問答中は見出し・メニュー・表の行順・図の座標・縮尺を保持し、説明する対象の赤枠だけを変更する。

本編は固定カメラ。自動ズーム・パン・台詞や字幕chunkごとのリセット・赤枠のpulse・疑似ポインターを使わない。Azureのブランドバー・検索欄・疑似操作ボタンも模倣しない。

- 012〜017：運用端末を左、管理VMを右へ固定した往復経路図と、企業側準備・NSGの役割・実宛先の確認を併記。
- 049〜057：SSHとAzure管理面の二つの入口、配備主体の権限、VM agent、処理例、空の運用元配列を同じ画面に保持。
- 061〜067：上に公開宛てFW経由、下にPE直接経路を置き、DNS・送信NSG/UDR・FW・サービス権限を併記。
- 確認①073〜075、②076〜078、③079〜081：各問を同じviewId・構図で保持。質問073/076/079では正解の赤枠を出さず、回答074/077/080から関係する項目を強調する。

公開設定値、設定の役割を示す教材注釈、設問の仮定条件を表示で区別した。実IPや実効値を推測せず、対象VM/NICの実出力を確認する旨を表示。OSの長い設定名はcamelCase境界で読みやすく改行した。

全20viewIdと台詞範囲・強調の対応は [settings-migration.md](settings-migration.md)、到達点と章の対応は [brief.md](brief.md) に記録した。旧版の入力・制作記録は `revisions/diagram-v1/`、旧MP4と出力記録は [出力のrevision](../../output/lesson-08-gemini/revisions/diagram-v1/egakumi-study-08.mp4) へ保存し、旧版のSHA一致を確認した。

## 到達点と出典

到達点は、管理VMへのprivate接続、NSG、SSH認証、Managed Run Commandの分担を説明できること。最新計画でも第8回のテーマと教材41〜43節は変更されていない。第45節の管理・外向き経路を補助参照とする。

教材・現行リポジトリ定義・公開設定例に基づく説明であり、稼働Azureの取得値・接続や配備の実測結果ではない。公開鍵とEntra SSH、配備主体の管理操作権限、VMのサービス利用権限を区別する。API受付とVM内処理の成功も分け、実行状態・終了コード・成果物を確認する。確認問題3問と、推奨受講順の必修A1への予告を維持した。

[最新資料との技術照合](settings-source-review.md) では、再録を必要とする技術変更は確認されなかった。settingsの実表示値・注釈・図も実装と再照合し、修正必須事項なし。

## 音声・尺・共通演出の維持

**Gemini 3.8 Flash TTS、MAKO=Aoede / MANABU=Charon / IKUMI=Orus、全員通常速度1.0倍。生成後の速度・ピッチ変更なし。** 冒頭のロゴ4秒、3人のホログラムと話者カメラ、既存BGM、最後の8秒ロゴを維持した。本編main-bgmは2秒クロスフェードと会話中のダッキング、終了音楽はbrand-theme 63.8〜71.8秒。

旧版と比較し、visualStyleとturn.visual以外の全フィールドが完全一致。86台詞のspeech、caption、字幕167cue、delivery、ID、speaker、章、間は変更していない。生成candidate全86件もstyle・文脈・声を含め完全一致。

制作手順のprepare --replace → generateによるキャッシュ再結合を実施し、**原音86件を再利用、今回の追加API送信0件、認証参照なし**。保存済みナレーションとBGMミックスのハッシュを確認し、標準ビルダーの `--reuse-audio true` で書き出した。さらに旧版と新版MP4のAAC音声パケットのSHA-256も完全一致した。[音声再利用証跡](audio-reuse-settings-v2.json)、[符号化済み音声の比較](encoded-audio-reuse.json)。

既存原音の発話は955.000秒、冒頭4台詞34.200秒。初回制作時の86成功リクエストが返した使用量はprompt 30,534 / audio candidates 23,875 / total 54,409 tokens。今回これに追加する音声生成使用量は0。実請求額は取得していない。認証トークンは成果物へ保存していない。

## 検証結果

[標準の全編検証](verification.json)：**passed、warnings=[]**。全編映像・音声の完全デコード、設定・送信証跡・原音のハッシュ、通常速度、字幕167cue、5つの冒頭カメラ区間、BGM、共通8秒クロージング、長さ・フレーム数が合格。音量は **−15.96 LUFS / True Peak −1.5 dBTP**。

[設定ビュー構造の検査](settings-verification.json) は82本編turn・20viewId・15SVGで合格。同じviewIdは連続した一群で、赤枠以外の設定行・図・メニュー・文字・座標が同一。質問中の正解強調や本編motion指定はない。[検査器の負例確認](settings-validator-selftest.json)では17個の不正例を検出し、正当な赤枠変更は受理した。

[静止プレビューの目視](settings-visual-review.md) は全20画面と追加状態を含む32枚で合格。3図・確認問題の前後・OSラベルを原寸でも確認し、はみ出し・衝突・語欠けなし。[完成MP4の36フレーム目視](final-visual-review-settings-v2.md) でも、3人の顔・名前・英語役割、冒頭の話者中央寄せ、全20画面・図・字幕・ロゴ終了に修正必須事項なし。

[新版HTTP配信検査](delivery-check-settings-v2.json)：題名・リンク・VTT/台本と実ファイルの一致、MP4 HEAD 200、先頭・末尾Rangeの206と実バイト一致、範囲外416、サイズ・SHA一致を確認した。[ブラウザー確認](browser-check-settings-v2.json) で、全編16分39.708秒・1600×900・readyState=4を確認し、全編ページを冒頭停止状態で納品用に保持した。

## 連続した質問・回答の確認

数秒ずつの寄せ集めではなく、完成MP4の同じ時刻範囲から、質問の先頭〜回答・反応の終わりまでを無カットで抽出した。共通make-conversation-preview.pyで各クリップの尺・フレーム数・全編デコード・通常速度・音楽の連続を検証し、いずれもpassed、warnings=[]。

| 会話 | 元動画の時刻範囲 | 尺・フレーム | 保持するviewId |
| --- | --- | --- | --- |
| 013〜017：経路の準備とNSGの役割 | 2:07.000〜3:02.792 | 55.791667秒 / 1,339 | mgmt-private-path |
| 053〜057：SSH鍵・管理権限・空の運用元配列 | 9:44.000〜10:44.500 | 60.500秒 / 1,452 | mgmt-management-execution |

接続経路は [記録](../../output/lesson-08-gemini/continuous-previews/private-route/conversation-preview.json)、管理実行は [記録](../../output/lesson-08-gemini/conversation-preview.json) を参照。両方の源MP4ハッシュは本完成動画と一致。

ブラウザーで2本を再生速度1.0、途中シーク・一時停止なしで最後まで連続再生し、played範囲がそれぞれ `[0,55.791667]` / `[0,60.5]`、ended=trueで完了したことを確認。質問・回答の途中で、メニュー・図・縮尺が戻ったり別画面へ切り替わったりせず、説明対象の赤枠・話者・字幕だけが変わることを視覚的に確認した。

[全フレームの固定領域検査](continuity-pixel-review.md) では2,791フレームのヘッダーとメニュー、計5,582比較を実施。最小SSIMは0.998362。最悪フレームで±2pxの位置合わせも確認し、最適な移動は(0,0)で圧縮差と整合した。対象2領域に可視的なパン・ズーム・リセット・画面切替なし。変化する赤枠の本文、字幕、話者欄はこの同一性比較へ含めていない。

この連続再生は画面保持の視覚的確認であり、音声内容の聴取・認識の検査ではない。**発音、本文の読み落とし、演技指示の発声、会話の自然さの聴感確認は未実施。** 完成動画で試聴可能。

## 変更範囲とハッシュ

変更は `episodes/08/` と `output/lesson-08-gemini/` のみ。共有計画・README・キャスト・プロファイル・素材・レンダラーは変更していない。[開始時記録](remake-baseline.json)の共通ファイルハッシュも不変。

- 新版MP4 SHA-256：`f1ddf391e8daf4c28f62bde955f8b383b96ebd740116aae3fb948b4f2441b6b3`
- 旧版MP4 SHA-256：`6cbfb3874a7e4028a4df73c83dc848ef83c3ea18beeda5b670bdab5dba9c3c3e`
- 最終script.json SHA-256：`3d0fdc4bbdfc10c2cfce85bf6a642bc4c0b79bd47a58fdd66dc555bf3f731ba2`
- 入力generation.json SHA-256：`246acc09731391898f32dc314d5b691fdb38bc6bd62e05530af818402576e27c`
