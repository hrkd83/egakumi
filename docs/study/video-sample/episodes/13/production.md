# 第13回 エラーを追い、安全に設定を変える

完成：2026-11-02T16:05:04.849815+09:00。完成尺 **16分59.917秒**（1019.916667秒）、1600×900 / 24fps / H.264・AAC。全員通常速度。全編自動検証は合格・警告0件。

## 再生・保存

- [完成MP4](../../output/lesson-13-gemini/egakumi-study-13.mp4)（50,501,129 bytes）
- [再生ページ](../../output/lesson-13-gemini/index.html)、一時ローカル再生URL：<http://127.0.0.1:60984/index.html>
- [字幕WebVTT](../../output/lesson-13-gemini/lesson-captions.vtt)、[採用台本](../../output/lesson-13-gemini/lesson-transcript.md)
- [連続会話プレビュー](../../output/lesson-13-gemini/conversation-preview.html)、[プレビューMP4](../../output/lesson-13-gemini/conversation-preview.mp4)
- [全編検証](verification.json)、[画面と再生の確認](visual-review.json)、[台本レビュー](script-review.json)

ファイルが成果物の正本。ローカルURLは、このMacの再生サーバー稼働中のみ利用できる一時的な閲覧先。127.0.0.1だけで公開し、他の回のサーバーは変更していない。

## 到達点と教材の範囲

要求の到達段階・相関キー・実行位置を照合し、原因に合う正本の変更、正常系と拒否系の検証、復元へつなげる。CONTENT-PLAN.mdの第13回と、02-external-protection.htmlの19〜22節を正本にした。説明はリポジトリ定義と公開設定例に基づき、稼働Azureの取得値・実測結果ではない。実サービスへの設定変更は行っていない。

主例は、文書APIで400を返しても、先に実行されたAPI共通処理のacceptedと利用枠計数が残り得る要求。Operationのbase、共通制約、利用枠、下流認証準備、accepted、文書固有形式検査、業務呼出を同じ図で追う。return-responseを例外と混同せず、LastErrorやrejected traceが必ず残るとは説明しない。正当な拒否なら呼出側を修正し、正規データの誤検知や契約変更が必要な場合に原因の正本へ戻る。

WAF / AppGWのTransactionId、FWのリソースID・UTC・IP / port / protocol、APIMの要求ID・CorrelationId・phaseを区別する。3層共通IDや元クライアントIPによる単純結合は示さない。正本・生成物・有効設定・経路関連付け、ローカル検証と実サービス試験、変更と復元を対応させる。

## 構成

| 開始 | 章 |
| --- | --- |
| 00:05 | 今回の問い |
| 00:34 | 到達段階と相関キー |
| 04:36 | 400とacceptedを読み解く |
| 08:01 | 原因に合う正本を変える |
| 11:13 | 正常系・拒否系・復元 |
| 13:45 | 三つの確認問題 |
| 16:01 | 一要求から変更と復元へ |
| 16:51 | 共通クロージング |

原稿93台詞、採用92台詞、7章、本編20ビュー、字幕136 cues。実音声で初期構成が17分13秒となったため、終盤の重複補足 lesson13-turn-093-ikumiだけをfirstCut.omitTurnIdsで省略。稼働環境との区別はscope・各ビュー注記・再生ページへ維持した。次回の問いから共通ロゴへつなぎ、通常速度のまま17分以内に整理した。省略分を含む93台詞の原音と生成証跡はすべて保存している。

## 映像・音声

study-series-v1 / teaching-settings-v2を採用。教材用設定メニュー、固定カメラ、質問から回答まで同じviewId・構図・縮尺を維持。自動ズーム・パン・繰り返すpulse・模擬ポインターは使わない。字幕・話者の切替で本文をリセットしない。

Gemini 3.8 Flash TTS、MAKO=Aoede / MANABU=Charon / IKUMI=Orus、全員1.0倍。読み上げ後の速度・ピッチ変更はない。共通ロゴ4秒、冒頭3人のホログラムと話者へ移るカメラ、既存main-bgmの2秒クロスフェードとダッキング、共通クロージング8秒を使った。終了音楽はbrand-themeの63.8〜71.8秒。

## 検証

全編A/Vデコード、24,478フレーム、字幕の全文保持と最大2行、本文・生成設定・送信証跡・原音のハッシュ、声・通常速度、冒頭カメラ、BGM、8秒クロージングを共通verify-lesson.pyで検査し合格。音量は-15.97 LUFS、True Peak -1.5 dBTP。

静止画では全20ビュー、SVG3版、3人の顔・名前・役割を確認。完成MP4からも冒頭の全員表示、各話者の中央寄せ、acceptedと形式検査の赤枠、確認問題の質問と回答、ロゴ終了を抽出して確認した。実行順図はrender-diagrams.mjsから再現できる。

連続プレビューはlesson13-turn-032-mako〜lesson13-turn-037-manabu、元動画329.125〜399.458333秒（05:29.125〜06:39.458）、**70.333秒**。同じlesson13-document-orderを保持し、途中のカット・速度変更はない。推奨上限70秒を0.333秒超えるが、質問から回答・補足の終わりまでを残すため採用した。抜粋の全編デコードも合格。ブラウザーで1.0倍の連続再生を最後まで確認し、media errorなし、ended=true。図の構図と縮尺を保持し、説明時に対応する赤枠だけが変わることを確認した。

**聴感の確認は未実施。** 利用ツールで音声内容を聴取・認識できないため、発音、読み上げの欠落、演技指示の発声、会話の自然さを確認済みとはしない。全原音の非無音PCM、長さ、音量、生成本文と証跡、完成動画のデコードは検査済み。完成動画で試聴できる。

## 音声生成と許可

ユーザーがこのチャットで「送信と音声生成を許可する」と回答し、93台詞と話し方指示を既存Google Cloudプロジェクトegakumi-exampleの指定Gemini TTSへ送信すること、既存プロジェクトの料金発生を明示許可した。許可前の自動承認拒否はプロセス作成前で、送信はなかった。許可後に93台詞を生成し全件HTTP 200、再試行0件。編集後は検証済み原音の結び直しだけで追加API送信0件。

元の93台詞の音声計991.28秒。API報告使用量はprompt 31,049 / candidates 24,782 / total 55,831 tokens（省略分も含む）。現行generation.jsonは採用92台詞の記録で、全93台詞の履歴はgemini-attempts.json・各raw.json・audio-signal-review.jsonに残る。認証トークンはメモリーのみで扱い、成果物へ保存していない。プロジェクト・IAM・既定アカウントの変更はしていない。

## 主な実装参照

- apps/gateway/apim/policies/document-analysis.xml：base、固有入力拒否、解析開始の順。
- apps/gateway/apim/policies/gateway.xml、gateway-telemetry.xml：利用枠、下流認証準備、accepted、backend-response、policy-error、requestIdとphase。
- apps/gateway/apim/api-publication.bicep、policies/subscription-profile.xml：本文0 byte・マスク・診断設定、30秒キャッシュ。
- scripts/waf_policy.py、scripts/render_apim_policy.py、apps/gateway/deploy/api_publication.py、scripts/deployment/helpers/apim_authentication.py：正本、計画、ETag付き統合・読み戻し。

## 完成MP4 SHA-256

`de7813f6be5bda86c4b9d2885e7d68743d610ec03fef64ce094b1c8b2aab1474`

担当範囲episodes/13/とoutput/lesson-13-gemini/だけを変更。共有計画、キャスト、素材、レンダラー、他の回は変更していない。
