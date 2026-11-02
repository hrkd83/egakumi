# 第9回 WAFの検査と誤検知への対応

到達点：攻撃パターン・本文上限・適用ポリシーを区別し、正規データの除外範囲を絞る理由を説明できる。

参照：02-external-protection.htmlの1〜3節を復習し、4〜8節を扱う。通常HTTP APIを主例に、Portal callbackのcode値を狭く除外する例、raw音声と文書の専用調整を結び付ける。第10回で扱うFW/IDPS、第11〜12回のAPIM詳細へ広げない。稼働環境を取得・変更した結果として説明しない。

採用画面：teaching-settings-v2。質問から回答まで同一viewId・構図・縮尺。説明対象のみ赤枠を変更し、確認問題の問いでは正解を強調しない。入口の経路・適用優先順位・raw本文の補完は同じ設定メニュー内へ補助図を併記する。

音声：Gemini 3.8 Flash TTS、MAKO=Aoede / MANABU=Charon / IKUMI=Orus、全員通常速度1.0。共通ロゴ・既存肖像・BGM・8秒closingを使う。
