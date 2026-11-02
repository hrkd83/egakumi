# 第4回 UDRと往復経路 — 制作記録

2026-11-02（JST）完成。**第4回「UDRと往復経路」は15分32秒（932.375秒）、全員通常速度の完成動画として書き出し済み。** 自動検証は警告なしで合格し、完成MP4の代表画面とブラウザーでの冒頭再生を確認した。発音・会話の自然さの聴感確認は未実施。

到達点は、既定経路と具体的な経路、Firewall経由とPE直接経路、往復をそろえる理由を説明できること。

## 出典と説明範囲

- [コンテンツ計画の第4回](../../../CONTENT-PLAN.md)：題材と到達点。
- [20節：ルートテーブルの関連付け](../../../01-network-control.html#udr-associations)：4つのRTとサブネット。
- [21節：経路の選択](../../../01-network-control.html#udr-selection)：最長一致、既定経路と具体的な経路。
- [22節：AppGWからの往復](../../../01-network-control.html#udr-agw)：APIM・ACA入口の実IP /32とAppGWサブネット宛ての復路。
- [23節：APIMの経路](../../../01-network-control.html#udr-apim)：公開宛ての既定経路、Azure管理系の直接復路。
- [24節：管理VM・ACA・PE](../../../01-network-control.html#udr-vm-aca)：公開宛てとPE宛ての違い。
- [25節：経路伝搬と設定](../../../01-network-control.html#udr-maintenance)：宣言・配備出力・実効経路の照合。
- [45節：外向き・管理・DNSの補助図](../../../01-network-control.html#network-egress-management)：E1〜E4の代表経路。

関係する定義は `infra/modules/network.bicep`、`network-bindings.bicep`、`portal-network.bicep`、`portal-network-bindings.bicep`、`infra/main.bicep`、`apps/portal/deploy/gateway-egress.bicep`、`scripts/deployment/lifecycle.py` と公開configを参照する。

教材・リポジトリ定義・公開設定例に基づく説明。稼働Azureの有効ルートやサービス実IPを取得した結果ではない。APIM VIP・ACA入口IPを固定の仮IPとして説明しない。Firewallルールの存在と、実際にその装置を通る経路は区別する。

## 制作条件

共通プロファイル `study-series-v1`。Gemini 3.8 Flash TTS、MAKO=Aoede / MANABU=Charon / IKUMI=Orus、全員通常速度（1.0倍）。速度・ピッチの事後変換は行わない。既存ロゴ・肖像・BGMを使い、1600×900・24fpsで合成する。

準備検査は合格。共通ファイル・素材ハッシュ、必要ツールとsharpを確認済み。既存Google Cloudユーザー認証の更新が可能であることを確認済み。トークンの表示・保存なし。

台本と出力は第4回の専用フォルダーに置く。共有計画・README・キャスト・素材・レンダラー、他回のファイルは変更しない。

## 台本・図の編集と事前確認

87台詞、読み上げ本文5,783字、冒頭4台詞205字。約15分の見積もりとし、全台詞を通常速度で生成してから実長を測る。省略台詞はない。

| 章 | 図と問い | 根拠 |
| --- | --- | --- |
| 返事も同じ道を通る？ | ホログラムの4台詞で「返事の近道」を問う | 22・23節 |
| UDRが決めること | 最終宛先と次ホップ、4RT・標準8UDR | 20節 |
| 具体的な経路を優先する | /32と/24、/0、同一prefixのUDR優先 | 21節 |
| AppGWと内部入口の往復 | APIM・ACAの往復、復路が直接だと非対称になる例 | 22・23節 |
| PEと内部通信の直接経路 | APIM→Foundry PE、ACA→SQL PE、APIM→ACA内部IP | 23・24節 |
| 公開宛てとAzure管理系 | 公開依存先、Portal→公開Gateway、管理系の直接復路 | 23・45節 |
| 宣言と実効経路を照合する | 実IPの出力、再配備、伝搬、同じ通信の往復を確認 | 25節 |
| 三つの通信で理解を確かめる | PE、NSGの応答許可、ApiManagementタグの3問 | 21〜24節 |
| まとめとFirewallへの問い | 宛先・区間・往復を振り返り第5回へつなぐ | 第4・5回の計画 |

第1回の図や確認問題を流用せず、回専用のdiagram/cards/quiz/summaryを41種類用意した。3人の役割に沿って質問・目的・実装を分担する。技術用語は読み上げ用の表記と画面用の正式名を分ける。ApiManagementタグを単にAPIMと表示せず、ルートの宛先範囲とポート条件を区別した。

別担当による元資料・Bicepとの独立レビュー、および短縮後の全台詞再読を完了。重大な事実誤認や到達点の欠落なし。復路の矢印ラベル、DNSの概念図ラベル、タグ名の表示を修正した。字幕は各チャンクを連結して全文と一致し、最大2行、各行40字以内。

共有ビルダーの87台詞分プレビューを生成。全41種類を独立して目視し、文字や図の切れ・重なり、矢印方向・ラベル、話者枠との重なりに問題なし。確認画像は [qa-independent](../../output/lesson-04-gemini/qa-independent/) に保存した。静止画の確認であり、完成動画の再生確認と聴感確認は別に記録する。

音声設定のprepareとdry-runは合格。送信内容は執筆した本文・演技指示・直前の会話・採用声で、リポジトリのファイルを添付送信する処理ではない。指定計画のGoogle Cloud直接生成方針と送信範囲を照合して実行した。[送信前確認](transfer-review.json) に台本ハッシュと検査結果を保存した。これは機密性を完全に保証する検査ではなく、資格情報らしい文字列の混入検査と送信範囲の記録である。

## 音声生成の結果

87台詞すべてHTTP 200・STOPで成功し、再送は0件。原音の発話は合計889.44秒（14分49.44秒）、冒頭4台詞は34.24秒。全員通常速度で、速度・ピッチ変換はない。API返却使用量は入力テキスト29,443トークン、音声出力22,236トークン、合計51,679トークン。実請求額の取得・費用換算は行っていない。

本文・設定・送信リクエスト・原音のハッシュを照合した [生成監査](../../output/lesson-04-gemini/generation-audit.json) を保存した。トークンなどの認証情報は成果物へ保存していない。機械的な成功・証跡の確認と、音声を聴いて確かめる確認は区別する。

## 再生成と確認のコマンド

リポジトリルートから実行する。音声の成功済みキャッシュは本文・声・style・ハッシュを照合して再利用する。録音の結果が不明な台詞を自動で再送しない。

```sh
python3 docs/study/video-sample/check-production.py --episode 4
node docs/study/video-sample/build-lesson.mjs --script episodes/04/script.json --cast podcast-cast-gemini.json --output lesson-04-gemini --preview true
python3 docs/study/video-sample/prepare-gemini-lesson.py --script episodes/04/script.json --config episodes/04/generation.json --output lesson-04-gemini
python3 docs/study/video-sample/generate-gemini-lesson.py --config episodes/04/generation.json --workers 1 --dry-run
python3 docs/study/video-sample/generate-gemini-lesson.py --config episodes/04/generation.json --workers 1
node docs/study/video-sample/build-lesson.mjs --script episodes/04/script.json --generation-config episodes/04/generation.json --cast podcast-cast-gemini.json --output lesson-04-gemini
python3 docs/study/video-sample/verify-lesson.py --script episodes/04/script.json --generation-config episodes/04/generation.json --cast podcast-cast-gemini.json --output lesson-04-gemini
python3 -u docs/study/video-sample/serve-episode.py --episode 4
```

完成音源の計画・間・ハッシュが同じで映像のみを再合成するときは、ビルドへ `--reuse-audio true` を追加できる。台本表示を直した場合も、制作手順に従ってprepareの設定更新とキャッシュの再結合を先に行う。第4回の出力はGit管理対象外であり、MP4と生成原音・証跡はローカル出力フォルダーに保持する。


## 完成ファイルと検証

- [完成動画：egakumi-study-04.mp4](../../output/lesson-04-gemini/egakumi-study-04.mp4)
- [ローカル再生ページ](http://127.0.0.1:64061/)（一時URL、サーバー終了後は上のコマンドで再起動）
- [保存済みの再生ページ](../../output/lesson-04-gemini/index.html)
- [字幕：WebVTT](../../output/lesson-04-gemini/lesson-captions.vtt) / [全文台本](../../output/lesson-04-gemini/lesson-transcript.md)
- [タイムライン](../../output/lesson-04-gemini/lesson-timeline.json) / [書き出しメタデータ](../../output/lesson-04-gemini/lesson-export.json)
- [検証結果](verification.json) / [生成監査](../../output/lesson-04-gemini/generation-audit.json)
- [完成MP4からの確認画像](../../output/lesson-04-gemini/qa-final/contact.png) / [ブラウザー確認記録](../../output/lesson-04-gemini/browser-review.json)

完成尺は932.375秒、1600×900、24fps、全22,377フレーム、H.264 / AAC。ファイルサイズは45,059,121バイト。MP4のSHA-256は `58d36cfbd52c7e5b1dbca26a8349b5a351b448e2d9ca5eb646c4e351492bfc49`。

87台詞・191字幕キュー・冒頭5カメラ区間・BGMの2秒クロスフェード・ブランド曲63.8〜71.8秒のクロージング8秒を検証。原音、設定、本文、リクエストのハッシュ、通常速度、字幕全文と時刻、映像と音声の全編デコードが合格した。音量は-16.06 LUFS、True Peakは-1.5 dBTP、台詞後端の最短余裕は0.141667秒。警告なし。

完成MP4から10画面を抽出して目視し、ロゴ、3人の顔・名前・役割、話者が中央になる画面、往復経路、PE直接経路、管理系の復路、確認問題、ロゴ終了を確認した。ブラウザーは再生準備完了（readyState 4）、1600×900、932.375秒、再生速度1.0、メディアエラーなし。冒頭チャプターへ移動し、実際に時間が進むことを確認して一時停止した。全編の聴感確認や発音の認識は実施していないため、技術用語・相づち・発音・会話の自然さは利用者の再生レビューで確認する。

書き出しの一時 `render-work` と実行ロックは正常終了に伴い除去済み。成功済みの生成原音と証跡は保持した。共有の計画・README・設定・素材・レンダラーに変更なし。

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
| 12:32 | 三つの通信で理解を確かめる |
| 14:16 | まとめとFirewallへの問い |
| 15:24 | 共通クロージング |
