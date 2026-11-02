# 第18回 中間画像の可逆圧縮driverの独立レビュー

2026-11-02（JST）に `build-lossless.mjs` と、その導出結果 `build-lossless-derived.mjs` を共通 `build-lesson.mjs` と比較した。

共通builder SHA-256：`f80f5dfd1d6a27b3e2228e4b0000f4a1fb8c5063e886549a4c53d1c97468ffa5`。
レビュー時の導出builder SHA-256：`c0da4ff8fa3478496cc6aa10730e338c3b020b9643a543aa5433c12f7ea404bd`。

## 結果

**指定された第18回の単一flag構成で実行する差分は妥当。** 共通ファイルの編集はなく、中間画像の保存形式をlossless WebPへ変える変更である。

- 共通builderのhashをproduction-profileと照合し、想定したソースと異なる場合は停止する。
- 置換は各対象がソース中に一度だけあることを確認する。共有rendererとaudio-cacheを、共有ファイルの位置からimportする。
- 入力・共通素材・sharp依存の基準位置を元builderと同じvideo-sampleへ維持する。
- still、motion、fallback logoは同じ `renderLessonRaster` の戻り値を、resize・色補正なしでlossless WebPに保存する。
- 採用済みintro MP4は従来どおり24fpsのPNGへ復号し、同じフレームをlossless WebPに変換する。元PNGは当該render-work内だけで削除する。
- 合成へ渡す画像はすべてWebPになる。公開posterはWebPからPNGへ復号して `lesson-poster.png` を維持する。
- シーン選択、音源検証、タイムライン、字幕chunk配分、フレーム数、カメラ、BGM、最終H.264/AAC・24fps・1600×900の設定には差分がない。
- `--reuse-audio true` を要求し、previewとwrite-scriptを拒否する。共有builder由来の音源hash・設定・生成証跡の検証も維持する。

## 指摘と修正

初回導出ではintro画像の拡張子置換の正規表現が過剰にescapeされ、`.png` が置換されず、sharpの入力と出力が同一になる問題を検出した。
親担当が `png.slice(0,-4)+'.webp'` へ修正し、再導出されたコードで別の拡張子になることを確認した。両ファイルの `node --check` も合格した。

flag検証は最初の同名flagを、共通parserは最後の同名flagを使うため、重複flagを受け付けない改善を親担当へ提案した。通常の指定コマンドは各flagを一度だけ渡す。単一flagでの入力と出力は第18回に限定される。

最終書き出し後の追記：親担当が必須4flagと `--reuse-audio` をそれぞれ正確に一度だけ受け付けるguardへ修正したことを独立に確認した。これにより上記の重複flagによる先頭値と最終値の食い違いは解消している。導出builderの映像処理差分とhashは変わっていない。

## 関連する検証証跡

- `intermediate-compression-verification.json`：代表PNGとlossless WebP復号のpixelが完全一致。PNG 1,180,315 bytes → WebP 458,012 bytes。
- `lossless-codec-verification.json`：WebP連結からH.264へ、48フレーム・24fps・1600×900の試験出力と全編デコードがpassed。
- `lossless-builder-audit.json`：元・導出builderのhashと13の限定置換を記録。

このレビューはdriverの差分と関連するローカル検証記録を対象とする。完成全編のデコード・尺・フレーム数・音量・最終画面は、書き出し後の共通verifyと最終映像レビューで確認する。
