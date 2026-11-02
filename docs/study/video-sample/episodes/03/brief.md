# 第3回 NSGによる通信制御

到達点：優先順位、送受信、状態追跡を使って、代表的な通信の許可・拒否を判断できる。

対応資料：`docs/study/01-network-control.html` 第11〜19節。計画表の回番号3に対応。

## 構成と例

- 4つのNSGの関連付け、AppGW・PEとAPIM・管理VMで異なる送信終端拒否。
- 同じNSG・同じ方向で、条件を照合し小さい優先度から最初の一致を選ぶ。
- AppGW → APIM TCP80と、管理VMからのTCP80/443を比較。
- APIM/AppGW → Foundry PE、ACA/管理VM → SQL PEで、送信元・実PE IP・ポートを比較。
- 許可済み接続への応答、ルール変更後の新規接続確認。
- 基盤依存、共通DNS、サービス・タグ、Firewallと認証の分担。
- gRPC切り替え、運用元の空配列、証明書監視194とWeb100/4080の重なり。
- 確認問題3問と、次回「UDRと往復経路」への問い。

## 根拠と範囲

`infra/modules/network.bicep`：関連付け、基盤依存、DNS規則。
`infra/modules/network-bindings.bicep`：実IPの規則、終端拒否、gRPCと運用元の条件。
`infra/modules/portal-network.bicep`：ACR向け送受信。
`infra/modules/credential-broker-network.bicep`：SQL・Key Vault PE受信、条件付きBroker送信。

追加管理規則なし、gRPC有効の公開設定例として説明。稼働Azureの取得値・疎通実測とはしない。
NSGのAllowは、DNS・経路・Firewall・サービス認証の成功を保証しない。

共通仕様と素材は変更せず、台本・図・入力設定はこの回のフォルダーで管理する。
音声生成前のレビューとプレビュー後に、ルート担当がstatusをready-for-generationへ変更する。

## ユーザー追加指定

「実際の設定値をしっかり解説してね。多少時間のびてもいいから」を反映。15分へ圧縮せず、約21分を編集目安として、公開params/configの具体CIDR・主要ポート・優先度・送受信元・切替条件を用途と結び付けて解説する。稼働環境へ問い合わせた値ではない。実PE IP・管理VM実IP・FW公開IPは作成後に決まり、架空の固定IPは挿入しない。

AppGW受信100/110/120/150/194と送信100/110/120/200、APIM受信100〜150・送信基盤200〜280、PE受信100/110/140/160/175/177、管理VM送信300〜360、DNS400/410と終端4096を具体的に可視化する。
