# 第2回：DNSとPrivate Endpoint — 設定メニュー版

2026-11-02（JST）。最新の[コンテンツ計画](../../../CONTENT-PLAN.md)と[設定ビュー仕様](../../SETTINGS-VIEW.md)に従い、完成動画を再制作した。採用スタイルは **teaching-settings-v2**。全編の機械検証はpassed・警告なし。

[全編を再生](http://127.0.0.1:64922/) / [42秒の連続問答](http://127.0.0.1:64922/conversation-preview.html) / [完成MP4](../../output/lesson-02-gemini/egakumi-study-02-settings.mp4)

完成尺は **15分25.208秒**、1600×900・24fps・H.264/AAC・22,205フレーム。全員通常速度1.0。MP4は47,779,411バイト、SHA-256は `e187284e82e7af455af62313ccfe86a1e030f25c00cfb41c90481bb29e32819e`。localhostはこのMacの一時再生先で、完成MP4が正本。

## 最新仕様への更新

旧図中心版および制作途中のAzure模倣版から、教材専用の設定メニューへ更新した。Azureのブランドバー・検索欄・疑似操作ボタンを外し、スレート色の見出し、淡いメニュー、白い設定欄、青緑の値を共有レンダラーで描く。

本編76台詞を27の話題にまとめた。61台詞は設定表・設定欄、15台詞は設定メニュー内の補助図を使う。質問から回答・確認まで同じviewId・項目順・構図・縮尺を維持する。自動ズーム・パン、赤枠のpulse、疑似ポインターは0。本編と、採用済みの冒頭ホログラムカメラを分けている。

012〜013は中央のVNet内PEと右のKey Vault本体を同じ図で保持。021〜026は左の通常名→Private Link名→Aレコードと、右のアプリ→PE→Key Vaultを併記し、026で赤枠をデータ通信へ切り替える。027〜029はFoundry用3ゾーンと残り3ゾーンを同時に見せる。030〜033はVNetリンクとzone groupの関連付け図・代表設定を固定する。線が設定の関連付けであることを教材注釈で示す。

確認問題071・074・077は質問中の赤枠を出さず、072・075・078の回答から対象を強調する。画面に示す補足は教材注釈と分かる表記にした。実環境のIPを推測せず、PEの実IP・APIMの実内部VIP・ACAの実staticIpなど取得後に照合する値として表す。

## 内容・出典・音声

到達点は、公開名・内部名・PEのprivate IPの関係と、送信元ごとに名前解決を確認する理由を説明できること。[元資料6〜10節](../../../01-network-control.html#dns-vnet)とリポジトリの構築定義・公開設定例を基準にする。稼働Azureから取得・実測した内容とは扱わない。

根拠はnetwork.bicepのVNet/FW DNS・6ゾーン・VNetリンク、services-private-endpoints.bicep等のPE/zone group、private-endpoint-addresses.bicepの全NIC/IP取得、network-bindings.bicepのAPIM実FQDNゾーンと@ A、portal-dns.bicepのACA実defaultDomainと* A。ACAに@ Aを追加していない。公開名は公開DNSからAppGW公開IPへ向ける。NSGの詳細、UDR、ハブ接続後の切替は後続回へ残す。

最新計画の第2回の学習範囲は変わっていないため、全80台詞の本文・字幕・声・style・間・章時刻を保持した。Gemini 3.8 Flash TTS、MAKO=Aoede / MANABU=Charon / IKUMI=Orus、全員通常速度。原音80件、narration WAV、BGM入りmix WAVをハッシュ照合して再利用し、今回のAPI送信・認証取得・再録は0件。共通ロゴ、肖像、BGM、8秒の終了映像を維持した。

元の音声生成はHTTP200/STOPが80件、原音880.96秒、入力27,528・音声出力22,024・計49,552トークン。これらは初回生成の記録で、今回の新規使用量ではない。実請求額の確認はしていない。

## 確認結果

[共通検証](verification.json)はpassed・警告なし。全編の映像・音声デコード、80台詞、123字幕cue、3声、通常速度、送信証跡・原音ハッシュ、冒頭カメラ、BGMクロスフェード2秒、共通終了8秒を確認。音量は−16.03 LUFS / −1.50 dBTP。

[設定ビュー監査](settings-verification.json)もpassed。80台詞の全非visual項目、原音・mix・旧版等85ファイルのハッシュ、91シーンの順序・開始終了・フレーム数と旧版の一致を確認した。本編76台詞の0・2・10秒時点の本文は画素が同一。同じviewIdでは赤枠を除いた本文画素と作者のbaseline hashも同一。共通ツールと他の回は変更していない。

[内容レビュー](content-review-settings.json)は最新計画・教材6〜10節・IaCとの意味整合に未解決事項なし。[生成監査](generation-audit.json)は原音再利用と現在のsource/configの結び付けを記録する。

全81プレビューを生成し、代表的な設定表・PE配置・名前解決・6ゾーン・関連付け図・確認問題・まとめの文字、赤枠、図の配置を目視確認した。完成MP4からも10場面を[抽出](../../output/lesson-02-gemini/qa/settings/frames.json)して確認した。

連続プレビューは **030〜033、4:48.583〜5:31.208、42.625秒・1,023フレーム**。質問から回答・目的の補足・設定確認まで同じzone-associations画面を保持し、途中カットや速度変更を加えていない。[抜粋の検証記録](../../output/lesson-02-gemini/conversation-preview.json)は全編デコードpassed。SHA-256は `44f96ce82928bbc61f2a0578eeb438e9439184e2fbda9d5a73cd5781999d39a0`。

ブラウザーでも1600×900・通常速度1.0・readyState 4・エラーなし、全範囲のシーク対応を確認。03:12の章からの再生進行と15:17へのシーク、42.625秒プレビューの完走を確認した。連続再生中の複数時点で同じ構図と赤枠の切替を目視し、画素監査と照合した。[ブラウザー記録](../../output/lesson-02-gemini/qa/settings/browser-verification.json)へ保存済み。**音声本文の聴取・ASR、発音・自然さの新たな聴感評価は未実施。** 自動検証や映像の目視確認と区別する。

## チャプター

| 開始 | 内容 |
| --- | --- |
| 00:05 | 同じ名前でも、同じ接続先？ |
| 00:39 | 名前解決から、通信の宛先を決める |
| 01:41 | 4種類のPEは、サービスへの接続口 |
| 03:12 | 通常名からPEのIPへ |
| 05:31 | VNetとFirewallは、どのDNSを使う？ |
| 07:26 | APIM・ACAの内部名と、公開入口の名前 |
| 09:57 | 送信元ごとに、回答と実体を照合する |
| 12:34 | 3つの通信で、理解を確かめる |
| 14:11 | 今日の対応を振り返り、NSGへ |
| 15:17 | 共通クロージング |

## 再現と成果物

入力はこの回のscript.json、make-settings.mjs、settings-storyboard.jsonとdiagrams/settings-*.svg。共通settings-visual.mjsとlesson-visual.mjsを使い、他の回の完成動画や専用ビルダーへ依存しない。回専用build-settings.mjsはPNGを順次エンコードして一時画像を削除し、--reuse-audio trueでキャッシュ不一致なら停止する。

```sh
node docs/study/video-sample/episodes/02/make-settings.mjs
node docs/study/video-sample/episodes/02/build-settings.mjs --script episodes/02/script.json --generation-config episodes/02/generation.json --cast podcast-cast-gemini.json --output lesson-02-gemini --preview true
python3 docs/study/video-sample/prepare-gemini-lesson.py --script episodes/02/script.json --config episodes/02/generation.json --output lesson-02-gemini --replace
python3 docs/study/video-sample/generate-gemini-lesson.py --config episodes/02/generation.json --workers 1
node docs/study/video-sample/episodes/02/build-settings.mjs --script episodes/02/script.json --generation-config episodes/02/generation.json --cast podcast-cast-gemini.json --output lesson-02-gemini --reuse-audio true
python3 docs/study/video-sample/verify-lesson.py --script episodes/02/script.json --generation-config episodes/02/generation.json --cast podcast-cast-gemini.json --output lesson-02-gemini
node docs/study/video-sample/episodes/02/verify-settings.mjs
python3 docs/study/video-sample/make-conversation-preview.py --episode 2 --from-turn lesson02-turn-030-mako --through-turn lesson02-turn-033-ikumi --replace
python3 -u docs/study/video-sample/episodes/02/serve.py
```

既存の検証済み音声があるこの回で、prepare→generateはAPI送信なしの結び直しとして使う。音声本文・声・styleを変える再制作には新しい台詞IDと必要部分の録音が必要。

[完成MP4](../../output/lesson-02-gemini/egakumi-study-02-settings.mp4)、[字幕](../../output/lesson-02-gemini/lesson-captions.vtt)、[台本](../../output/lesson-02-gemini/lesson-transcript.md)、[タイムライン](../../output/lesson-02-gemini/lesson-timeline.json)、[書出し記録](../../output/lesson-02-gemini/lesson-export.json)を保存。旧図中心版の動画・制作記録はversions/diagram-v1とoutput側の同名フォルダーへ保管。制作途中のPortal版の原稿はversions/portal-v2へ保管し、今回の完成版とは区別する。

各問答のviewIdと台詞範囲：

- `name-to-ip`：005, 006, 007, 008
- `lesson-goals`：009, 010
- `pe-location`：012, 013
- `pe-services`：015
- `pe-ip-configs`：016, 017, 018
- `pe-responsibilities`：019, 020
- `kv-name-and-data`：021, 022, 023, 024, 025, 026
- `private-link-zones`：027, 028, 029
- `zone-associations`：030, 031, 032, 033
- `vnet-dns`：034, 035, 036
- `firewall-dns-input`：037, 038, 039, 040
- `dns-proxy-and-path`：041, 043
- `dns-fallback`：044
- `eight-zones`：045, 046, 047
- `apim-internal-record`：048, 049, 050
- `aca-internal-record`：051, 052, 053, 054, 055
- `public-hostnames`：056
- `apim-network-mode`：057
- `check-source`：058, 059, 060
- `check-answer-and-pe`：061, 062, 063, 064, 065
- `check-internal-reflection`：066, 067, 068
- `dns-to-connection`：069, 070
- `quiz-sql`：071, 072, 073
- `quiz-source`：074, 075
- `quiz-aca-apex`：077, 078, 079
- `summary-correspondence`：080, 081, 082
- `next-nsg`：084, 085
