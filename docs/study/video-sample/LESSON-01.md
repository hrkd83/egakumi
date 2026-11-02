# 第1回：egakumiのネットワーク全体像

採用版は、教材専用の設定メニューと補助図で説明する**設定メニュー版（約15分58秒／957.541667秒）**です。完成動画の正本は`output/lesson-01-gemini/`に置きます。[保存済みの再生ページ](output/lesson-01-gemini/index.html)から確認できます。画面と編集の詳細は[採用版の制作記録](episodes/01/settings-v2/production.md)、新しい回の共通基準は[SETTINGS-VIEW.md](SETTINGS-VIEW.md)、現行の制作手順は[PRODUCTION-GUIDE.md](PRODUCTION-GUIDE.md)を参照します。

今回の到達点は、VNet・アドレス空間・サブネットの関係、6区画の役割、通常APIの往復経路、PEとサービス本体の違いを説明できることです。[コンテンツ計画](../CONTENT-PLAN.md)の第1回に対応し、[01章の構造と配置](../01-network-control.html#purpose)、[全体構成図と経路](../01-network-control.html#network-overview)を使います。説明はリポジトリ定義・公開設定例に基づき、稼働Azureの取得値とは区別します。

## 採用版の出力と検証

- [完成動画：egakumi-study-01-settings.mp4](output/lesson-01-gemini/egakumi-study-01-settings.mp4)
- [連続プレビュー：52.25秒](output/lesson-01-gemini/preview.html)
- [字幕：WebVTT](output/lesson-01-gemini/lesson-captions.vtt)
- [台本](output/lesson-01-gemini/lesson-transcript.md)
- [チャプター・字幕・音源の記録](output/lesson-01-gemini/lesson-timeline.json)
- [完成動画の検証記録](output/lesson-01-gemini/verification.json)

完成動画は1600×900・24fps、全22,981フレームです。87台詞・115字幕キューの時刻を保持し、冒頭の話者カメラと8秒の共通クロージングを使います。本編カメラは固定し、一つの質問から回答まで同じ設定画面・縮尺を保ちます。

全編の映像・音声デコード、本文・字幕・声・通常速度・元ミックスの一致、冒頭・終端・フレーム数の検証は`passed`、警告なしです。完成MP4のSHA-256は`e6e538f1c6c5945c28f4567fcd0b32b3a699496b8ab9cf748dbaa18675c1fe39`です。

連続プレビューは、3:42.166667〜4:34.416667の質問・回答・補足（027〜031）を途中で切らずに取り出した52.25秒・1,254フレームです。SHA-256は`d34f43e8f59e28305013dc76edc5e02b1b659200af2ed4a9a4b930cf5b9ec2b7`です。新たな全編の聴感・発音評価を実施したとは扱いません。

出力はGit管理対象外です。完成動画・生成原音・生成記録を`output/lesson-01-gemini/`へまとめて保持します。第1回は完成成果物として保管し、再制作手順は運用しません。第19回以降を含む新しい回は、共通ツール・共通素材と各回の原稿から制作し、第1回の出力を参照しません。

## 音声・映像の制作条件

| 人物 | 表示する役割 | 声 |
| --- | --- | --- |
| MAKO | Moderator | Aoede |
| MANABU | Product Manager | Charon |
| IKUMI | Solution Architect | Orus |

モデルは`gemini-3.8-flash-tts`。Google Cloudの`egakumi-example`から直接生成し、全員通常速度（1.0倍）で使用しています。話し方は人物・場面ごとの自然言語指示で指定します。[現行キャスト](podcast-cast-gemini.json)と[Podcast形式](PODCAST-FORMAT.md)に共通方針を記録しています。

採用版は、元のGemini版で確定した本文・87台詞の編集範囲・115字幕キュー・間・章の時刻を保持し、設定メニューと補助図を改訂したものです。元版の検証済み音声・BGMミックス、冒頭40.833333秒、ロゴの終了8秒を再利用し、画面改訂のための追加音声生成・API送信は行っていません。

## 保存する生成原音と記録

採用版の出力フォルダーに、87台詞の生成原音`*-raw.wav`・対応する`*-raw.json`、実際の送信要求`requests-sent/`、[生成結果](output/lesson-01-gemini/generation.json)、[音声生成の監査記録](output/lesson-01-gemini/generation-audit.json)、[費用と使用量の記録](output/lesson-01-gemini/speech-cost-summary.json)を統合しています。生成原音と送信記録は保持し、追加API送信は行いません。

採用版の書き出し・タイムライン・検証結果は出力直下に残します。同名だった元版の書き出し・タイムライン・検証・音声キャッシュ・生成結果のJSONは、`provenance/source-*.json`に制作履歴として保持します。これらは制作当時の内容を記録するもので、削除した旧動画や旧フォルダーを実行時に読み込むための参照ではありません。

元台本は[lesson-01-script.json](lesson-01-script.json)、採用版の台本・図の指定は[script.json](episodes/01/settings-v2/script.json)と[storyboard.json](episodes/01/settings-v2/storyboard.json)です。原稿・SVGは説明内容の記録として残します。一時的に分離していた再制作元フォルダー、元版の動画・不要な派生音声や確認画像、旧版から再書き出しする専用ツールは整理しました。

## Gemini版の音声生成記録と費用

使用する87台詞はすべてHTTP 200・正常終了（STOP）で生成され、再送はありません。原音の発話時間は合計904.92秒（15分04.92秒）、APIが返した使用量は入力テキスト30,411トークン・音声出力22,623トークンです。生成時の公開標準単価による本編の試算は約$0.44（$0.437625）で、実請求額ではありません。割引・税・為替・過去の比較音声の費用を含まない生成時点の記録です。

## 納品データの検証と再生

リポジトリルートから次を実行します。専用検証は、統合済みの完成動画・原音・送信記録・台本・図・履歴JSONを確認し、廃止した再制作元フォルダーへ依存しません。

```sh
python3 docs/study/video-sample/episodes/01/settings-v2/verify.py
python3 -u docs/study/video-sample/episodes/01/settings-v2/serve.py
```

新しい回の制作や原音再利用の手順は、[現行の制作手順](PRODUCTION-GUIDE.md)を使います。第1回の旧版を再生成するコマンドは、現在の制作手順に含めません。

## 削除済みの旧試作出力

旧DragonHD初稿と旧Azure模倣版の出力は、ユーザー指定により整理・削除しました。採用版とGemini生成原音・各記録は、上記の一つの出力フォルダーに保持しています。旧Azure模倣版の制作経緯は[履歴](episodes/01/production.md)として残します。
