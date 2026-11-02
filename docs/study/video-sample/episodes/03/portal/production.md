# 第3回 Azure Portal再現画面版

最新版の採用基準への移行に伴い、このAzure Portal模倣・ズーム版の制作は終了した。完成版としては採用しない。最新の制作先は [../settings-v2/production.md](../settings-v2/production.md)。以下は試作時点の履歴。

制作中（2026-11-02 JST）。ユーザー指定「Azureの設定画面を模倣し、主に画面で解説、必要に応じ図、赤枠・ズーム・動きを加える」に対応した映像の改訂版。

元の24分39.542秒の台本・声・BGM・字幕時刻を保持し、冒頭と終了を再利用、本編115台詞を新しい画面へ置き換える。元の図解版MP4は残す。本編はAzure Portalの英語UIを参考にした学習用の再現画面で、稼働Azureの画面取得や操作ではない。

## 画面と演出

- 規則一覧39、規則詳細25、VNetのサブネット9、Portal一覧＋補助図33、リポジトリ設定5、確認問題4。
- 説明する規則・入力値を赤枠で強調し、カメラを滑らかに移動・拡大する。主要22台詞では説明の塊に合わせて赤枠を順に移す。
- Source / Destination / Port / Protocol / Action / Priorityを一覧・詳細に分けて示す。字幕・話者は画面外の固定領域へ表示する。
- gRPC有効化などの構築入力は「リポジトリ設定」の補助ペインへ分け、Azure nativeのフィールドに偽装しない。教材注記はAzureリソースのDescriptionへ混ぜない。
- 状態追跡・両端の判定・優先順位は、Portal画面を残した補助図で説明する。
- 表の宛先ポート値は省略記号で隠さず、列幅に応じた縮小または2行で表示する。
- 上部に「学習用 再現画面・リポジトリの設定定義に基づく」を常時表示する。

[UIの根拠と設定データの対応](ui-reference.md)、[画面データ](scene-data.json)、[画面描画](portal-surface.mjs)、[モーション・合成](build-portal.mjs)、[専用検証](verify-portal.py)。

音声の新規生成は行わず、完成版では元MP4のAACパケットをコピーする。波形・速度・ピッチを変えない。元の音声生成の承認・実行結果は[第3回の制作記録](../production.md)を参照する。

## 再生成

リポジトリルートから実行する。共有レンダラーや素材は変更しない。

```sh
node docs/study/video-sample/episodes/03/portal/build-portal.mjs --preview
node docs/study/video-sample/episodes/03/portal/build-portal.mjs --sample-turn 036
node docs/study/video-sample/episodes/03/portal/build-portal.mjs
python3 docs/study/video-sample/episodes/03/portal/verify-portal.py
python3 -u docs/study/video-sample/episodes/03/serve-preview.py
```

既存サーバーが起動中なら同じサーバーの`/portal/`を使える。個別ロック`output/lesson-03-gemini/portal/.portal-build.lock`で重複実行を防ぎ、元の音声生成・映像ビルドとの同時実行を拒否する。ロック使用中に設定やソースを変更しない。

完成MP4・全編検証・映像確認・ブラウザー確認の結果は書き出し後に追記する。音声の聴感確認は未実施。
