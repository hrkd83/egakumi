# 第4回 UDRと往復経路

出典：`docs/study/CONTENT-PLAN.md`。到達点：既定経路と具体的な経路、Firewall経由とPE直接経路、往復をそろえる理由を説明できる。

対応資料：[20〜25節](../../../01-network-control.html#udr-associations)、[45節](../../../01-network-control.html#network-egress-management)。共通仕様は[制作手順](../../PRODUCTION-GUIDE.md)、配役は[現行キャスト](../../podcast-cast-gemini.json)を使う。

代表例はAppGW → Firewall → APIMとその応答、APIM → Foundry PE、ACA → SQL PE、公開依存先への送信、Azure APIM管理系の直接復路。標準4ルートテーブル・8独自経路を背景に、宛先と次ホップの違い、最長一致、独自RTがなくてもシステム経路があることを説明する。

確認問題は、既定経路だけでPEまでFirewall経由になるか、NSGの応答許可だけで往復がそろうか、ApiManagementの直接経路を通常API要求と同一視してよいかを中心にする。全CIDR・ルール名の暗記や、全設定の読み上げには広げない。

第3回のNSGから「許可と経路の分担」を引き継ぐ。第5回のNetwork/Applicationルールと評価順、第6回のSNAT・出口IP、第7回のログ調査、第8回の管理接続、第8回後に受講する必修A1・A2の企業ハブ接続・切替は詳述しない。

最新版の画面基準は `teaching-settings-v2`。教材専用の設定メニューと、代表的なルート設定・補助経路図を併記する。質問から回答まで同じviewId・項目順・構図・縮尺を保ち、説明対象の赤枠だけを変える。確認問題の質問中に正解を赤枠で示さず、本編の自動ズーム・パン・pulse・疑似ポインター、Azureブランドの部分模倣を使わない。連続した40〜70秒程度の問答も動画で確認する。

現行リポジトリ定義と公開設定例に基づく教材とし、稼働Azureの取得・実測とは区別する。APIM VIP・ACA環境入口は実出力を使う設計として示す。全員通常速度で生成した音声の実長から尺を確定する。

編集範囲はこのフォルダーと `output/lesson-04-gemini/`。完成状態・検証・再生先は [production.md](production.md) に記録する。
