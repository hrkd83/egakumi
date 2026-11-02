# 第2回：DNSとPrivate Endpoint — 接続方式の比較を追加した改訂版

2026-11-02（JST）。ユーザーの「PEを作る場合と、VNet統合・インジェクションの違いを先に図解したい」という指示を反映し、完成動画を更新した。画面は[採用仕様](../../SETTINGS-VIEW.md)のteaching-settings-v2を継続する。

[全編を再生](http://127.0.0.1:64922/) / [追加部分の53秒プレビュー](http://127.0.0.1:64922/conversation-preview.html) / [完成MP4](../../output/lesson-02-gemini/egakumi-study-02-connectivity.mp4)

完成尺は **16分48.875秒**、全員通常速度1.0、1600×900・24fps・H.264/AAC・24,213フレーム、51,475,864バイト。SHA-256は `7554999832d8b3e0e9ff9b7e0ede7dc9a7732b2c4b10b5e9ccc41dce1a7f477e`。MP4が成果物の正本で、localhostはこのMacの一時再生先。

## 追加した説明

PEの詳細に入る前の **1:41.583〜3:06.042** に、新しい比較章を置いた。6台詞の会話は1:42.833〜3:06.042、約1分23秒。同じ比較図を保持して次の順に説明する。

1. 上段：アプリとPEは自分たちのVNet内。Key Vault等のサービス本体は自分たちのVNetへ移さず、private IPの接続口を作る。
2. 下段：この構成のAPIMは **VNetインジェクション／Internalモード**。ACAは専用サブネットを使う **独自VNetとの統合／内部環境**。この二つには自身のPEを作らない。
3. 接続方式は構成次第で変わると補足する。サービス名だけでPEの有無を固定的に分類しない。
4. DNSが対応させる入口を、PEの実IP・APIMの実内部VIP・ACA環境の実staticIpとして並べ、既存のPEとDNSの詳細へ進む。

正式用語は[APIMのネットワーク分類](https://learn.microsoft.com/en-us/azure/api-management/virtual-network-concepts)、[ACAのVNet設定](https://learn.microsoft.com/en-us/azure/container-apps/custom-virtual-networks)、[PEの概要](https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-overview)と現行IaCで確認した。APIM v2の外向きVNet integrationをこのAPIMへ混用しない。integration/injectionを全サービス共通の二分法として教えず、サービスごとの正式な使われ方を図に明記する。[用語と録音前の技術レビュー](vnet-terminology-review.json)はpassed。

根拠となる現行定義は、infra/modules/services.bicepのvirtualNetworkType:'Internal'、infra/modules/portal-platform.bicepのinfrastructureSubnetId・internal:true、各PE定義。本編の到達点と資料範囲は[計画の第2回](../../../CONTENT-PLAN.md)・[教材6〜10節](../../../01-network-control.html#dns-vnet)を維持する。表示は構築定義・公開設定例に基づく教材であり、稼働Azureの実測値ではない。

## 画面と会話

全86台詞。本編82台詞は28のviewIdで構図を保持する。従来の61設定表台詞と15補助図台詞へ、比較図6台詞を追加した。新比較章は全6台詞をconnectivity-comparisonで統一。最初の質問は赤枠なし、PEの説明は上段、サブネット側の問答は下段、DNSの説明はIP対応行を囲む。赤枠以外の図・メニュー・縮尺は変えない。

本編は固定カメラ。ズーム・パン・pulse・疑似ポインターは0。冒頭のホログラムカメラ、共通ロゴ、人物、BGMと8秒のクロージングは継続する。既存のPE配置、Key VaultのDNS解決順とデータ通信の併記、6ゾーン、関連付け図、APIM/ACAのレコードと確認問題を保持した。確認問題は質問時に正解の赤枠を出さない。

## 音声と生成

Gemini 3.8 Flash TTS、MAKO=Aoede / MANABU=Charon / IKUMI=Orus、全員通常速度。既存79台詞の本文・声・style・原音をそのまま再利用。比較説明6台詞と、直前文脈が変わる接続部分1台詞を新IDで生成した。旧012の本文は保ち、IDを012r3へ変えて文脈を反映したstyleで録音する。後続013以降の文脈本文は元のままであり、再録しない。

今回の新規送信7件はすべてHTTP200/STOP。失敗・再送0、認証情報の保存なし。新規使用量は入力2,491・音声出力2,278・合計4,769トークン。既存80件の生成履歴と原音を保持し、79件を選択、新7件を追加した。選択86台詞の使用量と歴史上87件の使用量は[生成監査](generation-audit.json)で区別した。実請求額の確認はしていない。

音源の長さが変わるためnarrationとBGMミックスを再構築。元のmix・narration・キャッシュ・生成記録はoutput/lesson-02-gemini/versions/settings-v2へ退避し、旧設定版完成MP4も保持する。

## 検証

[共通検証](verification.json)はpassed、警告なし。全編の音声・映像デコード、86台詞・139字幕cue、通常速度、3声、原音と送信証跡、BGMの2秒クロスフェード、終了8秒を確認。最終ミックスは **−16.04 LUFS / −1.50 dBTP**。

[独立した改訂監査](connectivity-verification.json)もpassed。既存79台詞はvisualを含む全フィールドを保持し、旧012はID以外同じ。原音80件・従来の完成MP42本・退避音源を保持。新7件と79件再利用をリクエスト・原音ハッシュで照合した。

既存90場面の尺・フレーム数は同じ。追加7場面の2,027フレームと012r3再録差−19フレームを合わせ、全体差2,008フレーム＝83.666667秒と整合する。既存79台詞の字幕は各場面内の相対時刻・文字を保持した。本編82台詞の0・2・10秒の本文画素は同一で、同viewIdは赤枠を除いた画素も同じ。新比較図6variantは基図のSHAも一致する。

全87プレビューを生成し、追加した比較図・字幕を目視確認。完成MP4から[7場面](../../output/lesson-02-gemini/qa/connectivity/frames.json)を抽出し、比較図のVNet境界、説明する対象、赤枠、既存DNSへの接続を確認した。

連続プレビューは **011a3〜011d3、1:42.833〜2:35.542、52.708333秒・1,265フレーム**。PEとサブネット側の二組の質問・回答を途中カットせず抜粋した。同じ図の上段から下段へ説明対象を移す。[抜粋検証](../../output/lesson-02-gemini/conversation-preview.json)は全編デコードpassed、警告なし。SHA-256は `b5ed0ca27d61ce2f2520bc8a3a7a2b444dbef49c1dfe35d9cd9626e7b141a480`。

ブラウザーで最新MP4の1008.875秒・1600×900・通常速度1.0・readyState 4・エラーなし・全範囲のシーク対応を確認。新章101.583秒を指定して101.727秒へ進み、53秒プレビューも52.708333秒まで完走した。[改訂版の確認記録](../../output/lesson-02-gemini/qa/connectivity/browser-verification.json)に保存済み。**音声本文の聴取・ASR、発音・会話の自然さの聴感評価は未実施。** 自動検証・映像の確認と区別する。

## チャプター

| 開始 | 内容 |
| --- | --- |
| 00:05 | 同じ名前でも、同じ接続先？ |
| 00:39 | 名前解決から、通信の宛先を決める |
| 01:41 | PEとVNet接続の方式を、先に整理する |
| 03:06 | 4種類のPEは、サービスへの接続口 |
| 04:36 | 通常名からPEのIPへ |
| 06:54 | VNetとFirewallは、どのDNSを使う？ |
| 08:50 | APIM・ACAの内部名と、公開入口の名前 |
| 11:20 | 送信元ごとに、回答と実体を照合する |
| 13:57 | 3つの通信で、理解を確かめる |
| 15:35 | 今日の対応を振り返り、NSGへ |
| 16:40 | 共通クロージング |

## 再現と保存

```sh
node docs/study/video-sample/episodes/02/add-connectivity.mjs
node docs/study/video-sample/episodes/02/build-connectivity.mjs --script episodes/02/script.json --generation-config episodes/02/generation.json --cast podcast-cast-gemini.json --output lesson-02-gemini --preview true
python3 docs/study/video-sample/prepare-gemini-lesson.py --script episodes/02/script.json --config episodes/02/generation.json --output lesson-02-gemini --replace
python3 docs/study/video-sample/generate-gemini-lesson.py --config episodes/02/generation.json --workers 1
node docs/study/video-sample/episodes/02/build-connectivity.mjs --script episodes/02/script.json --generation-config episodes/02/generation.json --cast podcast-cast-gemini.json --output lesson-02-gemini
python3 docs/study/video-sample/verify-lesson.py --script episodes/02/script.json --generation-config episodes/02/generation.json --cast podcast-cast-gemini.json --output lesson-02-gemini
node docs/study/video-sample/episodes/02/verify-connectivity.mjs
python3 docs/study/video-sample/make-conversation-preview.py --episode 2 --from-turn lesson02-turn-011a3-mako --through-turn lesson02-turn-011d3-ikumi --replace
python3 -u docs/study/video-sample/episodes/02/serve.py
```

add-connectivity.mjsは退避した採用済みsettings-v2/script.jsonを読み、新章と台詞を挿入し、make-connectivity-comparison.mjsで比較図を生成する。既存の設定画面SVGもこの回のdiagrams内に保存済み。初回の素材生成はmake-settings.mjs、その後にadd-connectivity.mjsを実行する。音声の成功キャッシュは再利用する。音声・間が変わらない映像だけの改修にはbuild-connectivity.mjs --reuse-audio trueを使える。

[完成MP4](../../output/lesson-02-gemini/egakumi-study-02-connectivity.mp4)、[字幕](../../output/lesson-02-gemini/lesson-captions.vtt)、[表示台本](../../output/lesson-02-gemini/lesson-transcript.md)、[タイムライン](../../output/lesson-02-gemini/lesson-timeline.json)、[書出し記録](../../output/lesson-02-gemini/lesson-export.json)を保存。旧設定版の原稿・制作記録はversions/settings-v2、旧図中心版はversions/diagram-v1。共通プログラム・計画・キャスト・素材・他の回は変更していない。
