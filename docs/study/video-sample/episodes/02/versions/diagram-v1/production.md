# 第2回：DNSとPrivate Endpoint

完成動画を制作済み。全編の自動検証と画面・ブラウザー再生確認は合格。発音・会話の聴感レビューは未実施。完成尺は15分25.208秒。

制作開始日：2026-11-02（JST）。[コンテンツ計画](../../../CONTENT-PLAN.md)の第2回を、[共通制作手順](../../PRODUCTION-GUIDE.md)と[制作プロファイル](../../production-profile.json)に従って制作する。

到達点は、公開名・内部名・Private Endpoint（PE）のprivate IPの関係と、送信元ごとに名前解決を確認する理由を説明できること。[01 ネットワークの制御の6〜10節](../../../01-network-control.html#dns-vnet)と関係するBicep・公開設定例を根拠とし、稼働Azureの取得値・実測として説明しない。

## 内容と出典

Key Vaultの通常FQDNからPrivate Link名、PEのIPへの名前解決を代表例にし、DNSの問い合わせとデータ通信を別の図で説明する。4種のPEと6つのPrivate Linkゾーン、VNetリンクとDNS zone group、APIM・ACAの内部名と利用者向け公開名、送信元別の確認を扱う。NSGの評価順・UDR・Firewallの全ルール・企業ハブへの切替実作業は後続回へ残す。

- 教材6節：VNetとFirewallのDNS入力、proxyとクライアント設定の分担。`infra/modules/network.bicep`、`params/production.example.json`。
- 教材7節：4種のPE、サービス本体との区別、NICの全IP構成。`services-private-endpoints.bicep`、`portal-network.bicep`、`credential-broker-network.bicep`、`private-endpoint-addresses.bicep`。
- 教材8節：6ゾーン、VNetリンク、zone group、Key VaultのCNAMEとAレコード。`network.bicep`と上記PE定義。
- 教材9節：APIMの実FQDNゾーンの`@ A`、ACA環境のdefaultDomainゾーンの`* A`、AppGW公開IPへの公開名。`network-bindings.bicep`、`portal-dns.bicep`、`portal-network-bindings.bicep`。
- 教材10節：送信元・DNS・レコード・実IPをそろえた確認、DNS後の接続と認証。

公式仕様の補助確認：[Private Endpoint DNS](https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-dns)、[DNS統合](https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-dns-integration)、[Firewall DNS設定](https://learn.microsoft.com/en-us/azure/firewall/dns-settings)、[ACAのVNetとDNS](https://learn.microsoft.com/en-us/azure/container-apps/vnet-custom)。

## 制作条件

Gemini 3.8 Flash TTS、MAKO=Aoede / MANABU=Charon / IKUMI=Orus。3人全員通常速度（1.0倍）、生成後の速度・ピッチ変更なし。Google Cloudの既存プロジェクト・ユーザー認証を使い、認証トークンを成果物へ保存しない。共有ロゴ・肖像・BGMは既存素材を再利用する。

1600×900・24fps、H.264 / AAC。冒頭ロゴ4秒、その回専用のホログラム対話、図解と焼き込み字幕、WebVTT、まとめと次回NSGへの問い、ロゴ8秒の共通クロージングを含める。

## 完成ファイル

[一時再生ページ](http://127.0.0.1:64922/)で再生し、「動画を保存」でMP4を保存できる。URLはこのマシンで再生サーバーが動いている間だけ有効。完成MP4が成果物の正本で、音声・字幕・図・ロゴ・BGMを含む。

- [完成MP4：egakumi-study-02.mp4](../../output/lesson-02-gemini/egakumi-study-02.mp4) — 925.208333秒、45,034,528バイト。
- [保存済み再生ページ](../../output/lesson-02-gemini/index.html)
- [字幕：WebVTT](../../output/lesson-02-gemini/lesson-captions.vtt)
- [表示用台本](../../output/lesson-02-gemini/lesson-transcript.md)、[制作台本](script.json)
- [チャプター・字幕・音源のタイムライン](../../output/lesson-02-gemini/lesson-timeline.json)
- [共通クロージング：8秒](../../output/lesson-02-gemini/egakumi-common-closing.mp4)
- [完成動画の検証記録](verification.json)、[音声生成監査](generation-audit.json)
- [ブラウザー確認](../../output/lesson-02-gemini/qa/browser-verification.json)、[HTTP Range確認](../../output/lesson-02-gemini/qa/range-verification.json)

完成MP4のSHA-256：`7830d7b9ae938cccd646e25f23c7b93525a96ee61edf964fc608af92114f5e43`。

出力はGit管理対象外の`output/lesson-02-gemini/`。台本・生成設定・制作記録・検証結果はこの回の`episodes/02/`に保存した。共有の計画表・README・キャスト・素材・レンダラーは変更していない。

## 編集とチャプター

80台詞・9章、読み上げ5,643字（冒頭205字）、独自visual 32種類。冒頭の対話は実測33.28秒。字幕は句点・読点を優先して最大2行ずつに分け、全123キューの全文と時刻を確定した。通常速度の実音声で尺を決め、加速・ピッチ変更・長い無音の追加は行っていない。

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

Key VaultのDNS解決とデータ通信を別図にした。VNetリンクとzone groupも通信経路ではなく設定の関連付けと示した。APIM・ACAの内部AレコードとPE連携を分け、3問の確認問題から次回NSGへつなぐ。

## 検証結果

`verify-lesson.py`は**passed、警告なし**。1600×900・24fps・H.264 / AAC、全22,205フレームと音声を終端までデコードした。80台詞・123字幕キュー・指定3声・速度1.0・原音と送信記録のハッシュ・カメラ・BGMの2秒クロスフェード・共通クロージング8秒を確認。最終ミックスは**−16.03 LUFS / −1.50 dBTP**、各発話後の余裕は最短0.12秒。

資料6〜10節・Bicepとの独立した技術レビュー、および32種類のプレビューの目視確認に未解決の不整合はない。完成MP4から確認画像を抽出し、3人の顔・表示名・肩書き、各話者の中央寄せ、Key Vault図、確認問題、まとめ、ロゴ終了を目視確認した。[抽出位置](../../output/lesson-02-gemini/qa/frames.json)を保存した。

ブラウザーでduration 925.208333秒、1600×900、playbackRate 1、readyState 4、メディアエラーなし、通常再生の進行・チャプター移動・一時停止を確認。終盤の917.208秒指定で917.515秒、本編の192.792秒指定で193.015秒へ移動した。

**発音、読み上げ内容の聴取・音声認識、会話の自然さの聴感レビューは未実施。** 機械的な生成・映像検証と区別し、試聴できる完成動画を提示する。

## 音声生成と使用量

Gemini 3.8 Flash TTSで全80台詞がHTTP 200・STOP。MAKO/Aoede 27台詞、MANABU/Charon 14台詞、IKUMI/Orus 39台詞。ユーザーの続行承認を受け、指定の既存Google Cloudプロジェクトから生成した。全員通常速度。原音合計880.96秒（14分40.96秒）。失敗・結果不明・再送は0。

API返却の使用量は入力27,528トークン、音声出力22,024トークン、合計49,552トークン。実請求額の確認は行っていない。本文・style・送信request・原音WAVを独立監査し、ハッシュと対応を確認した。認証トークンはメモリーで扱い、成果物へ保存しない。APIの応答JSON本体は保持しないため、そのresponsePayloadSha256の独立再計算は監査対象外。

## 再生成と再生

リポジトリルートから実行する。入力・出力は回別に固定し、成功済みの原音は一致するリクエストとハッシュで再利用する。

```sh
python3 docs/study/video-sample/prepare-gemini-lesson.py --script episodes/02/script.json --config episodes/02/generation.json --output lesson-02-gemini
python3 docs/study/video-sample/generate-gemini-lesson.py --config episodes/02/generation.json --workers 1 --dry-run
python3 docs/study/video-sample/generate-gemini-lesson.py --config episodes/02/generation.json --workers 1
node docs/study/video-sample/build-lesson.mjs --script episodes/02/script.json --generation-config episodes/02/generation.json --cast podcast-cast-gemini.json --output lesson-02-gemini
python3 docs/study/video-sample/verify-lesson.py --script episodes/02/script.json --generation-config episodes/02/generation.json --cast podcast-cast-gemini.json --output lesson-02-gemini
python3 -u docs/study/video-sample/episodes/02/serve.py
```

再生サーバーは空きポートを自動で選び、127.0.0.1だけで待ち受ける。標準`serve-episode.py`のPython SimpleHTTPRequestHandlerはRange要求へ200を返し、このIABで章移動時に先頭へ戻る現象が再現したため、共有ファイルを変更せず[この回のRange対応サーバー](serve.py)を追加した。先頭・途中・末尾のRange要求が206と正しい1024バイトを返し、MP4の実バイト列と一致すること、および実ブラウザーでの章移動を確認済み。今回起動した旧サーバーは停止した。

映像だけの再書き出しには、音源と音楽の計画・ハッシュが一致する場合、上記ビルドへ`--reuse-audio true`を追加できる。台本を変更した場合のID・生成設定の更新は共通制作手順に従う。
