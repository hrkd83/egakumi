# 第4回 UDRと往復経路 — 設定メニュー版

2026-11-02（JST）、最新の `CONTENT-PLAN.md` に基づく再制作を完了。画面基準は **teaching-settings-v2**。完成MP4と連続会話プレビューの検証は合格。完成版の通常速度再生とチャプター移動、連続プレビューの最後までの再生も確認済み。

到達点は、既定経路と具体的な経路、Firewall経由とPE直接経路、往復をそろえる理由を説明できること。

## 最新計画との差分

前回の図・カード中心版は [旧制作記録](versions/diagram-v1/production.md) と [旧MP4](../../output/lesson-04-gemini/versions/diagram-v1/egakumi-study-04.mp4) に保存した。前回版も15分32秒の完成動画として保持する。

今回の本編は、教材専用の設定メニュー、設定の代表行、必要な補助経路図へ作り直す。質問から回答・確認まで同一viewId・項目順・構図・縮尺を保ち、説明対象の赤枠だけを切り替える。確認問題は質問中に正解を赤枠で先取りしない。自動ズーム・パン・赤枠pulse・模擬ポインターを使わず、Azureのブランドバー・検索欄を模倣しない。共通の番組外枠・字幕・話者は共有レンダラーを使う。

最新計画でA1・A2が第8回後の必修になったため、旧turn071の「応用編で別に確かめる」を「第八回の後で別に確かめる」へ修正して新IDで再録する。直前文脈styleが変わる旧turn072も新IDで再録する。他85台詞の本文・声・style・字幕・章・間は保持し、ハッシュを照合した原音を再利用する。速度やピッチの事後変換は行わない。

技術本体の参照範囲・代表設定には変更がないことを独立レビューで確認した。第4回は独立VNetとローカルFirewallの原理を説明し、企業ハブの設定変更、中央DNS、切替・切戻しはA1・A2へ残す。

## 出典と制作条件

- [コンテンツ計画・第4回](../../../CONTENT-PLAN.md)
- [20節：関連付け](../../../01-network-control.html#udr-associations)
- [21節：宛先による経路選択](../../../01-network-control.html#udr-selection)
- [22節：AppGWの往復](../../../01-network-control.html#udr-agw)
- [23節：APIMの復路と管理系](../../../01-network-control.html#udr-apim)
- [24節：管理VM・ACA・PE](../../../01-network-control.html#udr-vm-aca)
- [25節：伝搬・配備・実効経路](../../../01-network-control.html#udr-maintenance)
- [45節：外向き・管理・DNS](../../../01-network-control.html#network-egress-management)
- [設定ビュー仕様](../../SETTINGS-VIEW.md) / [制作手順](../../PRODUCTION-GUIDE.md)

関係する根拠は `infra/modules/network.bicep`、`network-bindings.bicep`、`portal-network.bicep`、`portal-network-bindings.bicep`、`infra/main.bicep`、`apps/portal/deploy/gateway-egress.bicep`、`scripts/deployment/lifecycle.py` と公開config。画面は公開設定例と教材注釈に基づくもので、稼働Azureから取得した実画面・実効経路・実IPではない。

Gemini 3.8 Flash TTS、MAKO=Aoede / MANABU=Charon / IKUMI=Orus、全員通常速度（1.0倍）。映像は1600×900・24fps・H.264/AAC。共通ロゴ4秒、ホログラムの冒頭カメラ、BGM、8秒クロージングは採用仕様を維持する。

今回の準備検査は合格し、最新共通ファイル・素材ハッシュ、必要ツール、sharpを確認した。既存87台詞の原音と送信リクエストのハッシュも確認済み。共有の計画・README・キャスト・素材・レンダラー、他の回のファイルは変更しない。

## 検証項目

- 同一viewIdの連続範囲、赤枠を出す台詞、補助図との対応。
- 全場面の静止プレビューと、質問から回答を途中で切らない40〜70秒程度の連続動画。
- 再利用85台詞と新IDの局所再録2台詞、追加使用量、原音・設定・証跡ハッシュ。
- 共通検証による字幕全文・時刻・通常速度・音量・映像音声の全編デコード。
- 完成MP4の長さ、SHA-256、再生・保存先、ブラウザー確認。

本環境では発音・会話の自然さの聴感確認は未実施。機械検証・画面確認・再生状態の確認と混同しない。


## 固定ビューと赤枠

本編83台詞を27のviewIdへまとめ、共有 `renderSettingsShell()` で74SVGを生成した。同一viewIdの見出しと、赤枠用 `<g id="highlights">` を除いたSVGのハッシュは一致し、構図・文字・表の並びが固定されている。再登場による画面往復はなく、固定ビューの機械検査はエラー・警告とも0件。[設定ビュー検証](settings-verification.json) に各台詞の対象行とハッシュを保存した。

| viewId | 保持する台詞番号 | 内容 |
| --- | --- | --- |
| udr-role | 005〜007 | UDR・NSG・Firewallの分担 |
| udr-next-hop | 008〜009 | 最終宛先と次ホップ |
| udr-associations | 010〜013 | サブネットとルートテーブル |
| udr-resolved-ip | 014〜015 | 名前解決後の宛先IPから調べる |
| udr-prefix | 016〜018 | 宛先範囲の具体性を比べる |
| udr-specific | 019〜021 | AppGW → APIMの経路候補 |
| udr-equal-prefix | 022〜024 | APIM → AppGWの復路候補 |
| udr-route-conditions | 025〜026 | UDRが指定する条件 |
| udr-appgw-roundtrip | 027〜032 | AppGWとAPIMの往復経路 |
| udr-asymmetry | 033〜036 | 復路が別経路になる場合を考える |
| udr-portal-roundtrip | 037〜040 | AppGWとPortal入口の経路 |
| udr-pe-direct | 041〜045 | APIMからPEへ向かう経路 |
| udr-vm-aca-compare | 046〜048 | 管理VMとACAの送信経路を比べる |
| udr-internal-direct | 049〜050 | APIM → ACA内部IPの経路 |
| udr-public-egress | 051〜053 | 公開依存先への既定経路 |
| udr-portal-public | 054〜055 | Portalから公開Gatewayを呼ぶ |
| udr-apim-management | 056〜061 | APIM管理系の直接経路 |
| udr-dns-context | 062〜063 | DNS設定と経路の確認を分ける |
| udr-inspection-source | 064〜066 | 対象通信を決めて定義と照合する |
| udr-redeploy-preserve | 067〜069 | 再配備で保持する既存経路 |
| udr-bgp-propagation | 070〜071r2 | ゲートウェイ経路伝搬の設定 |
| udr-effective-check | 072r2〜072r2 | 実効経路と往復を確かめる |
| udr-quiz-pe | 073〜075 | 確認1：PEのIP宛ての経路候補 |
| udr-quiz-return | 076〜078 | 確認2：NSGの応答許可と復路UDR |
| udr-quiz-management | 079〜081 | 確認3：このルートの用途は？ |
| udr-summary | 082〜084 | 宛先で選び、区間で分け、往復で確かめる |
| udr-next-firewall | 085〜087 | 次回：Firewallは何を判断するか |

確認問題は073・076・079で候補表と補助図を中立に表示し、赤枠なし。最初に答える074・077・080から該当行・経路を強調し、回答後の確認まで同じ構図を保つ。確認3では、ルート名が用途のヒントになることを避けて質問・回答の全状態からルート名を省いた。まとめは直接経路の対象をPEおよびAPIM→ACA内部IPへ限定した。

全74SVGの独立目視と、全27ビューを共通外枠・話者・字幕と合わせた確認を行い、値・矢印方向・文字の切れ・重なり・枠の配置に問題なし。確認画像は [全体の静止画1](../../output/lesson-04-gemini/revision-previews/full-frame-1.png)、[全体の静止画2](../../output/lesson-04-gemini/revision-previews/full-frame-2.png)、`revision-qa/settings-sheet-01.jpg`〜`13.jpg` に保存した。静止画と構造の点検であり、完成動画の連続再生の確認は別に記録する。

## 音声の再利用と局所再録

85台詞は前回と本文・声・style・送信リクエスト・原音ハッシュが完全一致。新ID `lesson04-turn-071r2-mako` と `lesson04-turn-072r2-ikumi` の2台詞だけを再録し、2件ともHTTP 200・STOPで正常終了、再送0件。旧2台詞の原音・証跡も削除していない。[今回の生成監査](../../output/lesson-04-gemini/revision-generation-audit.json) と [生成実行記録](../../output/lesson-04-gemini/generation-settings-v2-execution.json) を保存した。

今回の追加API使用量は入力テキスト700トークン、音声出力685トークン、合計1,385トークン。現行87台詞に対応する合計は入力29,445・音声出力22,301トークン。前回の不採用2原音を含む累積請求量とは区別し、実請求額・費用換算は取得していない。

現行原音の発話は合計892.04秒。全員通常速度で、速度・ピッチの事後変換なし。2台詞が変わったため音声・BGMは新しい実長で再合成した。資格情報は成果物へ保存していない。


## 連続した会話のプレビュー

途中のカット・速度変更・音声の追加生成をせず、完成MP4の連続したフレーム範囲から作成した。2本ともH.264/AAC・24fps・48kHzステレオ・元の声とBGMを保ち、フレーム数・時刻・全編デコードの検査が合格している。

| 対象 | 台詞範囲 | 元動画の範囲 | 尺 | フレーム |
| --- | --- | --- | --- | --- |
| AppGWの往復 | lesson04-turn-027-mako〜lesson04-turn-032-manabu | 257.833〜314.000秒 | 56.167秒 | 1,348 |
| 確認問題1・2 | lesson04-turn-073-mako〜lesson04-turn-078-ikumi | 756.583〜824.875秒 | 68.292秒 | 1,639 |

- [AppGWの往復：連続プレビュー](http://127.0.0.1:54658/conversation-preview.html) / [保存済みHTML](../../output/lesson-04-gemini/conversation-preview.html)
- [確認問題1・2：連続プレビュー](http://127.0.0.1:54658/quiz-conversation/conversation-preview.html) / [保存済みHTML](../../output/lesson-04-gemini/quiz-conversation/conversation-preview.html)
- [実MP4から抽出した42フレームのレビュー](../../output/lesson-04-gemini/qa-settings-continuity/review.json)

42フレームの独立した目視では、往復経路の問答・字幕境界7組・話者切替5組で構図・項目順・縮尺を保持している。確認問題3問は、質問の中ほどと最終フレームで赤枠なし、回答開始から意図した対象を赤枠で示している。連続再生の観察結果と聴感の確認は、このフレーム比較とは区別する。

## 再生成

リポジトリルートから実行する。成功済み原音は、生成CLIが本文・声・style・リクエスト・原音のハッシュを照合して再利用する。未知の結果の自動再送や、旧原音・履歴の削除はしない。

```sh
python3 docs/study/video-sample/check-production.py --episode 4
node docs/study/video-sample/episodes/04/render-diagrams.mjs
python3 -B docs/study/video-sample/episodes/04/check-settings.py
node docs/study/video-sample/build-lesson.mjs --script episodes/04/script.json --cast podcast-cast-gemini.json --output lesson-04-gemini --preview true
python3 docs/study/video-sample/prepare-gemini-lesson.py --script episodes/04/script.json --config episodes/04/generation.json --output lesson-04-gemini --replace
python3 docs/study/video-sample/generate-gemini-lesson.py --config episodes/04/generation.json --workers 1 --dry-run
python3 docs/study/video-sample/generate-gemini-lesson.py --config episodes/04/generation.json --workers 1
node docs/study/video-sample/build-lesson.mjs --script episodes/04/script.json --generation-config episodes/04/generation.json --cast podcast-cast-gemini.json --output lesson-04-gemini
python3 docs/study/video-sample/verify-lesson.py --script episodes/04/script.json --generation-config episodes/04/generation.json --cast podcast-cast-gemini.json --output lesson-04-gemini
python3 docs/study/video-sample/make-conversation-preview.py --episode 4 --from-turn lesson04-turn-027-mako --through-turn lesson04-turn-032-manabu --replace
python3 -u docs/study/video-sample/episodes/04/serve.py
```

保存済みミックス・間・音源計画のハッシュが一致し、映像だけを再構成するときはビルドに `--reuse-audio true` を追加できる。今回の局所再録後は音声が変わったため、一度すべてを新しい実長で合成し直している。出力はGit管理対象外で、台本・回専用SVG・生成原音・生成証跡と完成MP4を第4回専用フォルダーへ保存する。


## 完成動画と全編検証

- [設定メニュー版の完成MP4](../../output/lesson-04-gemini/egakumi-study-04.mp4)
- [字幕WebVTT](../../output/lesson-04-gemini/lesson-captions.vtt) / [全文台本](../../output/lesson-04-gemini/lesson-transcript.md)
- [タイムライン](../../output/lesson-04-gemini/lesson-timeline.json) / [書き出し情報](../../output/lesson-04-gemini/lesson-export.json)
- [全編の自動検証](verification.json) / [設定ビュー検証](settings-verification.json)
- [完成MP4からの13画面](../../output/lesson-04-gemini/qa-settings-final/contact.png)
- [共通入力と前回版の保持](../../output/lesson-04-gemini/revision-source-preservation.json)

長さは **934.958333秒（約15分35秒）**、1600×900・24fps、全22,439フレーム、H.264/AAC。ファイルサイズは47,934,875バイト。SHA-256は `3c23fd32735c8d46767fe16147fda110d3039439bd2c1c92f4f148cbaec23572`。

自動検証は警告なしで合格。87台詞の生成証跡・本文・声・通常速度・ハッシュ、191字幕キューの全文と時刻、冒頭5カメラ区間、2秒クロスフェードのBGM、ブランド曲63.8〜71.8秒の8秒クロージング、映像と音声の全編デコードを確認した。最終音量は-16.06 LUFS、True Peakは-1.5 dBTP、台詞後端の最短余裕は0.141667秒。

ブラウザーでは完成MP4の再生準備完了、1600×900、934.958333秒、再生速度1.0、メディアエラーなしを確認。確認問題68.292秒とAppGW往復56.167秒の2本を通常速度で連続再生し、両方とも終了まで到達した。開始・中盤・終了の観察と実映像42フレームの比較で、問答中の構図と赤枠の対応を確認した。**音声の内容を聴取・認識しての発音・会話の自然さの確認は未実施**であり、全編デコードや動画の画面確認と区別する。

共通の計画・制作仕様・キャスト・レンダラーに変更なし。前回MP4のハッシュを保持し、今回のMP4が別ファイルであることを確認した。一時render-workと生成・ビルドロックは正常終了で除去済み。

### チャプター

| 開始 | 内容 |
| --- | --- |
| 00:05 | 返事も同じ道を通る？ |
| 00:40 | UDRが決めること |
| 02:06 | 具体的な経路を優先する |
| 04:16 | AppGWと内部入口の往復 |
| 06:34 | PEと内部通信の直接経路 |
| 08:20 | 公開宛てとAzure管理系 |
| 10:44 | 宣言と実効経路を照合する |
| 12:35 | 三つの通信で理解を確かめる |
| 14:19 | まとめとFirewallへの問い |
| 15:26 | 共通クロージング |


## 再生先とチャプター移動

- **[完成版を再生](http://127.0.0.1:54658/)**
- [56秒の往復経路プレビュー](http://127.0.0.1:54658/conversation-preview.html)
- [68秒の確認問題プレビュー](http://127.0.0.1:54658/quiz-conversation/conversation-preview.html)
- [再生状態の確認記録](../../output/lesson-04-gemini/browser-review.json)
- [HTTP Rangeの検証](../../output/lesson-04-gemini/http-range-verification.json)

通常速度で再生されること、チャプターの256.583秒へ移動して256.944秒から再生されること、動画全体0〜934.958秒がseekableであることをブラウザーで確認した。最後は先頭で停止し、完成版の再生ページを残した。上記localhost URLは一時的で、サーバー終了後は `python3 -u docs/study/video-sample/episodes/04/serve.py` で新しい空きポートから再開する。完成MP4は保存先ファイルが正本。

共有の `serve-episode.py` は今回の環境でHTTP Rangeを返さず、video.seekableが0〜0秒になり、章ボタンで先頭へ戻る現象を確認した。共有ファイルを編集せず、第4回専用の [serve.py](serve.py) に単一Range対応を実装した。127.0.0.1の動的ポートで第4回出力だけを読み取り、GET/HEAD・先頭/途中/末尾の206応答・不正範囲416・通常HEAD/HTMLを実HTTPで検証し、部分応答が原MP4の同じバイトであることを照合した。共通サーバーへRange対応を取り込むかは、各回とは別の共有ツール改善として扱う。
