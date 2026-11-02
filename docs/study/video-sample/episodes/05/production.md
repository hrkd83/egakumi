# 第5回 Firewallは何を判断するか — 設定メニュー版

制作日：2026-11-02（JST）。状態：完成。teaching-settings-v2への再制作、全編の最終検証、47.79秒の連続動画確認を完了。

## 最新計画との対応

最新の `CONTENT-PLAN.md`、`AGENTS.md`、`PRODUCTION-GUIDE.md`、`SETTINGS-VIEW.md`、`production-profile.json` を確認した。第5回の到達点・資料範囲・次回は変更されていない。本編の採用基準が `teaching-settings-v2` になったため、旧図・カード中心版を設定メニュー版へ再制作した。

到達点：Network／Applicationルールの違いと評価順、内部中継・基盤通信の許可を説明できること。80台詞、9章、95字幕cue。教材26〜30・34〜35節と台本を再照合し、読み上げ本文・字幕・声・style・台詞ID・間の変更は不要と判断した。全80台詞が旧版と一致している。

旧版の[制作記録](revisions/diagram-v1/production.md)、[台本](revisions/diagram-v1/script.json)、[検証](revisions/diagram-v1/verification.json)、[旧MP4](../../output/lesson-05-gemini/revisions/diagram-v1/egakumi-study-05.mp4)は担当回の履歴に保存した。

## 参照資料

| 話題 | 元資料 | 確認する設定・関係 |
| --- | --- | --- |
| 関連付け・経路 | [26節](../../../01-network-control.html#fw-roles)、[34節](../../../01-network-control.html#fw-decisions) | afwとafwp、FW経由とPE直接経路 |
| 種類と照合条件 | [26節](../../../01-network-control.html#fw-roles) | Network、HTTP Host / HTTPS SNI、非復号TLS、NTP FQDN |
| 評価順・優先度 | [27節](../../../01-network-control.html#fw-evaluation) | 種類→グループ→コレクション、一致で終了、組み込み許可、既定拒否 |
| 内部中継・応答 | [28節](../../../01-network-control.html#fw-internal) | AppGW→APIM・ACA、gRPC条件、状態追跡と復路UDR |
| APIM基盤通信 | [29節](../../../01-network-control.html#fw-apim-network) | StorageタグTCP443、基盤SQLと業務SQLの目的・送信元 |
| 管理・ACA・DNS | [30節](../../../01-network-control.html#fw-vm-aca-network) | NTP、監視・認証、独自DNSの生成条件・送信元 |
| 確認問題と保守 | [34節](../../../01-network-control.html#fw-decisions)、[35節](../../../01-network-control.html#fw-maintenance) | 許可理由、DNS proxy、AlertとDeny、同じ通信を照合 |

`infra/modules/network.bicep`、`network-bindings.bicep`、`portal-network.bicep`、`credential-broker-network.bicep`、`apps/portal/deploy/gateway-egress.bicep`、公開configを照合。実IPの数値は推測せず「実内部VIP」「実IP」と記す。説明は教材・公開定義・設定例に基づき、稼働Azureの取得画面・実測結果とは区別する。

## 画面の変更と固定する単位

教材専用の設定メニュー、淡い背景、白い設定欄、青緑色の値、赤い対象枠を共通シェルで描画した。Azureのブランドバー・検索欄・疑似操作ボタンは追加していない。用途・評価順・状態追跡などは「教材注釈」と明記する。

本編76台詞は25個の連続viewIdで整理。32台詞は共有settings、44台詞は共有設定シェル内の設定表＋補助図。38SVGを使う。同じ問答ではメニュー・行順・構図・縮尺を保持し、説明対象の赤枠だけを変更する。台詞・話者・字幕chunkでのズーム・パン・画面リセット、pulse、模擬ポインターはない。冒頭ホログラムの話者カメラは従来の採用仕様を維持した。

| 台詞番号 | viewId | 設定・補助図 |
| --- | --- | --- |
| 005–010 | `fw-association-path` | 関連付けを確認してから、通信の経路を追う |
| 011–014 | `rule-inputs` | NetworkとApplicationの照合条件 |
| 015–017 | `application-tls` | 標準Applicationルールと検査の分担 |
| 018–019 | `ntp-fqdn` | FQDNを使う管理VMのNTP許可 |
| 020–023 | `rule-processing` | 独自ルールの種類と評価順 |
| 024–026 | `hierarchy` | グループ・コレクション・ルールの階層 |
| 027–029 | `priority-comparison` | 種類の順序が優先度の数字より先になる |
| 030–031 | `allow-default` | 独自Allowと最終的な既定拒否 |
| 032–035 | `agw-apim` | AppGW → APIMの内部中継を許可する |
| 036–037 | `agw-aca` | Portal入口の許可先とUDRの宛先を比べる |
| 038–040 | `stateful-return` | 許可済み接続への応答と復路UDR |
| 041–041 | `direct-internal` | 内部ルールの定義と標準の直接経路 |
| 042–049 | `apim-storage` | APIMの基盤依存通信：Storage |
| 050–052 | `apim-platform` | 基盤SQLと業務SQLを、送信元・目的で分ける |
| 053–056 | `ntp-sources` | APIMと管理VMのNTP許可は独立して読む |
| 057–059 | `vm-aca` | 管理VMとACAの管理・監視・認証 |
| 060–063 | `custom-dns` | 独自DNS許可の生成条件・送信元・経路 |
| 064–065 | `dns-proxy` | Firewall側のDNSとVNet側のDNSを区別する |
| 066–067 | `quiz-storage` | 確認1：Storage通信は二重の許可が必要か |
| 068–069 | `quiz-ntp` | 確認2：FQDNを使うNTPはどちらの種類か |
| 070–071 | `quiz-pe` | 確認3：SQL PEへの接続失敗をどう考えるか |
| 072–073 | `linux-web` | Network不一致のWeb通信はFQDNで照合する |
| 074–076 | `change-review` | 変更前後に同じ通信について照合する |
| 077–078 | `alert-settings` | 本番の公開config：検知のAlertと規則のDeny |
| 079–080 | `final-review` | 今日の到達点と、次回の外向き通信 |

赤枠は質問31台詞では空にし、回答・説明開始から該当する行・値・小図へ表示する。確認問題066→067 / 068→069 / 070→071は、条件と候補を同じ中立色で配置したまま、回答開始から「追加登録を重ねない」「Network」「PE宛ての具体的な直接経路」を強調する。質問中の答えの先取りと旧版の画面往復を解消した。

033/037/044/054/058/073の「図」への言及には、該当ルール名を含む設定行と小図を同一画面に置いた。優先度400/350は仮例と表示し、実定義の360の2グループと区別した。第6回のFQDN生成・SNAT、第7回の詳細ログ調査、第10回のIDPS詳細には範囲を広げない。

## 音声・共通素材

Gemini 3.8 Flash TTS、MAKO=Aoede / Moderator、MANABU=Charon / Product Manager、IKUMI=Orus / Solution Architect。全員1.0倍で、速度・ピッチ変更なし。通常速度の発話905.44秒とBGMミックスをそのまま再利用した。

prepareで更新した台本ハッシュと生成設定を結び直し、成功済み80原音を検証して再利用。今回のAPI送信0件、認証取得なし。旧版の累計使用量は入力27,966・音声出力22,636トークンで、新たなTTS使用量は発生していない。ロゴ4秒、冒頭ホログラム、main-bgmのクロスフェードとダッキング、brand-themeの63.8〜71.8秒による共通終了8秒は従来どおり。

## 静止画・構造・連続動画の確認

全81静止画プレビューを生成し、25ビューの一覧と代表画像を目視。独立レビューで、設定・小図・字幕の意味と文字の収まり、確認3問の強調タイミングに問題なし。末尾1〜2字だけが折り返す設定ラベルを短縮し、制作上の説明を教材の問いへ置換した。

[画面監査](visual-verification.json)では、76本編台詞のviewIdに欠落・重複がなく、同じviewIdの構図JSONが一致すること、38SVGが赤枠のレイヤー以外で完全一致すること、全31質問で強調が空であることを確認した。設定ビューの型・文字量も描画時の検査に合格。これは音声の聴取とは分けて記録する。

連続プレビューは共通 `make-conversation-preview.py` で内部中継の032–035を指定した。元動画の342.916667〜390.708333秒、47.791667秒、1,147フレームを、内部カット・速度変更・ピッチ変更なしで抽出し、音声・BGMも連続している。出力動画の全編デコード、尺・フレーム数・音声形式はpassed、警告なし。

[連続確認用動画](../../output/lesson-05-gemini/conversation-preview.mp4) / [連続確認ページ](../../output/lesson-05-gemini/conversation-preview.html) / [時刻と検証根拠](../../output/lesson-05-gemini/conversation-preview.json)。

ブラウザーでも0秒から終端47.791667秒まで通常速度1で連続再生。0.58秒、25.78秒、終了時の表示と再生進行を確認し、設定表・小図の位置と縮尺、赤枠の説明対象、字幕の対応を確認した。途中のシーク・一時停止なし、video.errorなし、ended=true。音声内容の聴感・発音確認は利用ツールの制約で未実施。全編の機械デコードと再生進行確認を聴感確認とは扱わない。

## 完成ファイル

尺：15分47.958秒（約15分48秒）。1600×900、24fps、H.264 / AAC。22,751フレーム、49,942,323 bytes（47.63 MiB）。

SHA-256：`f55ce02203d0e373fc432faf0c45af2c3b30860cb8c471b7c67ade8f936607a3`。

[完成MP4](../../output/lesson-05-gemini/egakumi-study-05.mp4) / [WebVTT字幕](../../output/lesson-05-gemini/lesson-captions.vtt) / [完成台本](../../output/lesson-05-gemini/lesson-transcript.md) / [タイムライン](../../output/lesson-05-gemini/lesson-timeline.json) / [最終検証](verification.json)。

一時再生URL：[http://127.0.0.1:53790/](http://127.0.0.1:53790/)。動画・字幕の保存と9章のチャプター移動ができる。127.0.0.1だけへ配信する担当回のHTTP Range対応サーバーを使用。既存の他回のサーバーや共有ファイルは変更していない。localhostは起動中だけ有効で、成果物の正本はMP4。

再起動はリポジトリルートで `python3 -u docs/study/video-sample/episodes/05/serve.py`。空きポートを自動選択するので、表示されたURLを使う。

## 再現手順

担当回の `author.py` が台本と `settings-views.json` を作り、`render-diagrams.mjs` が共有 `renderSettingsShell()` で補助図を描画する。共有のprepare / generateによる音声キャッシュの結び直し、`build-lesson.mjs --reuse-audio true`、verify、連続プレビューの順で作る。別回や第1回の生成済み成果物を依存先にしていない。

## 最終検証結果

共通verify-lesson.py：passed、警告なし。80台詞、95字幕cue、22,751フレーム。台本・生成設定・実送信・原音・最終MP4のハッシュ、通常速度、全編の映像・音声デコード、カメラ、字幕全文・時刻、BGM、共通終了8秒を確認した。最終ミックスWAVは−16.0 LUFS / −1.5 dBTP。AAC化後の音量再測定は行っていない。

本編もブラウザーで341.91秒へのチャプター移動、seekable=[0,947.958333]、playbackRate=1、1600×900、video.errorなしを確認。設定ビューを表示した345.892秒で一時停止し、最新版の再生ページを閲覧用に残した。音声の聴感確認は未実施という範囲を維持する。
