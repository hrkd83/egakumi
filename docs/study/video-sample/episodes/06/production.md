# 第6回 外向き通信と出口IP — 制作記録

制作日：2026-11-02（JST）。状態：最新計画の **teaching-settings-v2で再制作完了**。本編の設定メニュー・補助図・固定画面・回答時の赤枠へ更新し、完成MP4・全編検証・画面QA・49.375秒の連続再生確認を完了した。

## 到達点と最新計画への対応

Web依存先の許可、生成されるFQDN、SNAT、出口IPの期待値と実測の違いを説明できること。[最新計画](../../../CONTENT-PLAN.md)の第6回は、テーマ・到達点・対応節・配役が旧版と同じ。変更された本編基準を[SETTINGS-VIEW](../../SETTINGS-VIEW.md)に従って反映した。

参照範囲は[31節](../../../01-network-control.html#fw-base-applications)・[32節](../../../01-network-control.html#fw-portal-applications)・[33節](../../../01-network-control.html#fw-generated-settings)・[36節](../../../01-network-control.html#egress-explicit)・[37節](../../../01-network-control.html#egress-snat)。教材・リポジトリ定義・公開設定例に基づき、稼働Azureの設定取得や出口IPの実測結果として説明しない。必修となったA1・A2の切替手順へは広げず、次は第7回を案内する。

## 再制作した画面

83本編台詞を21の固定viewIdでまとめた（settings 54台詞・共有settings shell内のSVG 29台詞）。設定欄・表を説明の入口とし、Portalの公開Gateway呼出し、VMの明示出口、公開/privateのSNATには同じ画面内へ補助図を置く。Network・Application・PEは一つの比較表で扱う。

同じ問答は内容・メニュー・行順・構図・縮尺を保持し、説明対象の赤枠だけ切り替える。本編の自動ズーム・pan・台詞/字幕chunkごとのリセット・pulse・模擬ポインターはない。Azureのブランドバーや検索欄を模倣せず、教材用ビューと明記する。確認問題の質問074/077/080では赤枠なし、回答075/078/081から対象を強調する。

[構図と音声再利用監査](settings-audit-v2.json)：passed。settingsはhighlight指定以外の属性が同一。SVG variantsは赤枠rectを除いたXMLが同一。87台詞の非visual属性と全生成候補も旧版へ完全一致で照合した。[独立内容レビュー](source-review-v2.md)：最新教材・実装との重大な矛盾や到達点の欠落なし。

| 台詞番号 | 固定viewId | 画面 |
| --- | --- | --- |
| 005〜009 | `egress-reading` | 外向き通信は、許可・経路・送信元をそろえて読む |
| 010〜012 | `application-conditions` | Applicationルールで照合する条件 |
| 013〜017 | `apim-vm-web` | APIMと管理VMのWeb依存先を分ける |
| 018〜022 | `portal-build-web` | 実行基盤・ビルド・Portal試用の依存先 |
| 023〜025 | `generated-fqdns` | 入力の空配列を、最終許可と同一視しない |
| 026〜029 | `certificate-generation` | 証明書の選択設定から管理VMの許可先を生成 |
| 030〜035 | `api-publication-fqdns` | API公開でホストを追加し、既存許可を保持する |
| 036〜036 | `additional-rules` | 追加ルールの種別と、変更するグループを確認 |
| 037〜042 | `vm-explicit-egress` | 管理VMの公開IP・既定経路・暗黙出口を分ける |
| 043〜047 | `public-ip-resources` | AppGWの入口IPと、Firewallの出口IPは別 |
| 048〜050 | `public-snat` | SNATで、外部から見える公開送信元が変わる |
| 051〜056 | `private-network-snat` | Networkのprivate宛ては、送信元を保持する |
| 057〜062 | `snat-by-path` | ルール種別と直接経路で、SNATの扱いを分ける |
| 063〜066 | `expected-vs-measured` | 期待値の受渡しと、実測・照合を分ける |
| 067〜069 | `measurement-origin` | 対象の実行場所から、同じ条件で出口を測る |
| 070〜073 | `unexpected-egress` | 出口IPが違うときに、比較条件と経路を確かめる |
| 074〜076 | `quiz-generated` | 確認1：入力の空配列から、最終許可を判断できる？ |
| 077〜079 | `quiz-private-ranges` | 確認2：非SNATの範囲なら、Allow確認は不要？ |
| 080〜082 | `quiz-measured` | 確認3：期待値と確認先があれば、自動照合済み？ |
| 083〜085 | `egress-summary` | 外向き通信を、設定から観測結果まで説明する |
| 086〜087 | `next-troubleshooting` | 第7回：同じ通信の調査材料をそろえる |

## 音声と共通仕様

Gemini 3.8 Flash TTS / MAKO=Aoede（Moderator）、MANABU=Charon（Product Manager）、IKUMI=Orus（Solution Architect）。全員通常速度1.0倍、速度・ピッチ変換なし。87台詞、表示本文5,651字、読み上げ6,051字、9章、131字幕cue。ロゴ4秒、3人のホログラム導入、本編BGMのクロスフェードとダッキング、共通クロージング8秒を維持。

新しい台本ハッシュをprepare --replaceで結び直し、generateは87原音をすべて検証済みキャッシュから再利用した。**今回の追加API送信0件・認証取得なし**。原音だけでなく、保存済みナレーションとBGMミックスも旧ハッシュへ一致し、`--reuse-audio true`で再使用した。[再制作監査](rebuild-audit-v2.json)に記録。

初回生成は87件HTTP 200、発話950.64秒、冒頭32.56秒。元のAPI返却使用量は入力29,410・音声出力23,766・合計53,176トークンで、今回の追加使用量ではない。実請求額は未確認。[初回の使用量記録](generation-audit.json)を保持し、認証トークンは保存していない。

## 完成・検証結果

- 完成尺：16分34.917秒（約16分35秒）。1600×900、24fps、23,878フレーム、H.264 / yuv420p、48kHzステレオAAC。
- [全編自動検証](verification.json)：passed、warningsなし。台本・設定・実送信・原音・完成MP4のハッシュ、全字幕、通常速度、カメラ、音源サンプル数、全編映像/音声デコード、BGMと共通クロージングを確認。
- 最終ミックスWAV：−16.03 LUFS / −1.5 dBTP。AAC化後の音量再測定は未実施。
- [独立画面QA](visual-review-v2.md)：静止画79状態と重点11枚を確認後、完成MP4の代表22枚・追加7枚・連続プレビュー6時点の計35画面を確認。文字切れや重なり、質問時の先取り強調なし。設定と補助図、3問、まとめ、第7回案内、ロゴ終了を確認。
- [ブラウザー検証](browser-verification.json)：全編のplaybackRate=1、seekable=[0,994.916667]、video.errorなし。11:33の章へ693.6087秒まで移動して再生を確認。終了後は冒頭の停止状態へ戻して再生ページを残した。
- 音声内容の聴感・発音確認は未実施。機械検証やブラウザー再生を、聴感確認や全編の人による視聴とは扱わない。

## 連続した問答の確認

[49.375秒のプレビュー](../../output/lesson-06-gemini/conversation-preview.html)は、turn063の質問からturn066の補足の終わりまで。元動画の **694.833333〜744.208333秒**（16676〜17861フレーム、1185フレーム）を途中で飛ばさず抽出した。元の音声・BGMを保持し、speed=1、cutsWithinExcerpt=false。[抽出・全編デコード検証](../../output/lesson-06-gemini/conversation-preview.json)：passed、warningsなし。

同じ`expected-vs-measured`の4行を保つ。質問は赤枠なし、回答で期待値の出力/環境変数、次に確認先/自動実測なし、最後の補足で期待値と実測の違いへ赤枠を切り替える。ブラウザーで0秒から終了49.375秒までシークせず1.0倍で再生し、17.90秒と41.62秒の画面を観察した。ended=true、errorなし。独立QAも0/10/20/30/40/48秒を比較し、構図・縮尺が保たれ赤枠だけ変化することを確認した。音声の聴感判定は含めない。

## 再生・保存先

MP4：50,818,290 bytes（48.46 MiB）。SHA-256：`2c5efb11437267a27bc2adb3c08b74a145b6d369c5b0594d4441b3ac669038bb`。

[完成動画（MP4）](../../output/lesson-06-gemini/egakumi-study-06.mp4) / [再生ページ](../../output/lesson-06-gemini/index.html) / [49秒プレビュー動画](../../output/lesson-06-gemini/conversation-preview.mp4) / [WebVTT字幕](../../output/lesson-06-gemini/lesson-captions.vtt) / [台本](../../output/lesson-06-gemini/lesson-transcript.md) / [完成画面の時刻](../../output/lesson-06-gemini/qa-final/manifest.json) / [設定・連続プレビューQA](../../output/lesson-06-gemini/qa-settings-v2/manifest.json)。

一時再生URL：[http://127.0.0.1:54700/](http://127.0.0.1:54700/) / [連続問答](http://127.0.0.1:54700/conversation-preview.html)。旧51321は停止済みだったため、自動承認レビュー通過後に127.0.0.1限定の空きポートで起動した。localhostは起動中だけ有効。成果物の正本はMP4で、再生ページの「動画を保存」「字幕を保存」からも保存できる。

再起動：`python3 -u docs/study/video-sample/episodes/06/serve.py`。byte range対応により章移動が可能。共有サーバーは変更していない。

## 履歴と再生成

旧図中心版は[旧制作記録](revisions/diagram-v1/production.md)、原稿・設定・検証・音声キャッシュのハッシュ、および[旧完成動画](../../output/lesson-06-gemini/revisions/diagram-v1/egakumi-study-06.mp4)へ保存した（旧SHA：ce7f6a6f6f5b991f23c04af2e59d3ab129cceb0f0a30d17f618a2d675b7f0ee8）。旧版の原音・送信証跡は消していない。

リポジトリルートから再ビルドする。画面だけを変更する場合もprepare/generateで結び直し、検証済み原音を再利用する。

```sh
node docs/study/video-sample/episodes/06/render-settings.mjs
python3 docs/study/video-sample/prepare-gemini-lesson.py --script episodes/06/script.json --config episodes/06/generation.json --output lesson-06-gemini --replace
python3 docs/study/video-sample/generate-gemini-lesson.py --config episodes/06/generation.json --workers 1
node docs/study/video-sample/build-lesson.mjs --script episodes/06/script.json --generation-config episodes/06/generation.json --cast podcast-cast-gemini.json --output lesson-06-gemini --reuse-audio true
python3 docs/study/video-sample/verify-lesson.py --script episodes/06/script.json --generation-config episodes/06/generation.json --cast podcast-cast-gemini.json --output lesson-06-gemini
python3 docs/study/video-sample/make-conversation-preview.py --episode 6 --from-turn lesson06-turn-063-mako --through-turn lesson06-turn-066-manabu --replace
```

変更範囲はepisodes/06とoutput/lesson-06-geminiのみ。共有計画・README・プロファイル・キャスト・素材・レンダラー、他回の原稿・ロック・出力は変更していない。
