# 第3回 Azure Portal再現画面の参照と演出データ

2026-11-02（JST）確認。対象は第3回の本編115台詞。音声、元の`script.json`、タイムライン、完成済みMP4を変更せず、別映像として画面を刷新するためのデータ。

## 再現の根拠

Microsoft公式Learnの現行掲載内容を参照し、NSGの受信・送信規則一覧、規則を選択した詳細、サブネット関連付けの画面構造を再構成する。実アカウントへの接続や設定変更、稼働中Azureの画面取得は行っていない。公式画像をそのまま動画へ転載したり、実環境のスクリーンショットとして提示したりしない。

| 参照先 | 確認した構造と今回の扱い |
| --- | --- |
| [NSGの作成・変更・削除](https://learn.microsoft.com/en-us/azure/virtual-network/manage-network-security-group) | NSGを開くと設定メニューに受信・送信規則、ネットワークインターフェイス、サブネットがある。規則一覧からカスタム規則を選ぶと詳細へ進み、保存操作がある。既定規則はカスタム規則と同じ編集対象にはしない。 |
| [NSG診断の規則追加画面](https://learn.microsoft.com/en-us/azure/network-watcher/diagnose-network-security-rules) | 送信元、送信元ポート範囲、宛先、サービス、宛先ポート範囲、プロトコル、アクション、優先度、名前を分けたフォーム構造。現行手順には追加画面のNetworkingタブが記載される。 |
| [NSGとサブネットの関連付け](https://learn.microsoft.com/en-us/azure/virtual-network/tutorial-filter-network-traffic#associate-network-security-group-to-subnet) | サブネット画面から関連付け操作へ進み、VNetと区画を選ぶ。NSG側の一覧はそのNSGに関連付く区画だけなので、全6区画の比較にはVNet側の一覧を使う。 |
| [PEネットワークポリシー](https://learn.microsoft.com/en-us/azure/private-link/disable-private-endpoint-network-policy) | PEへNSGを適用する設定と、NSGのサブネット関連付けは別。`NetworkSecurityGroupEnabled`はNSGだけを有効にする値。 |
| [既定規則のREST API例](https://learn.microsoft.com/en-us/rest/api/virtualnetwork/default-security-rules/list?view=rest-virtualnetwork-2025-03-01) | 既定名の綴りと65000/65001/65500の組合せを確認。画面では既定の規則と独自4096を区別する。 |

公式掲載画像の参照先：

- [規則一覧](https://learn.microsoft.com/en-us/azure/virtual-network/media/manage-network-security-group/view-security-rules.png)
- [規則詳細](https://learn.microsoft.com/en-us/azure/virtual-network/media/manage-network-security-group/view-security-rule-details.png)
- [サブネット関連付け](https://learn.microsoft.com/en-us/azure/virtual-network/media/manage-network-security-group/associate-subnet-network-security-group.png)

画面の英語UIは、左の受信・送信メニュー、上の操作バー、白い一覧、右の詳細ブレードという構造を使う。一覧の主要列は優先度、名前、宛先ポート、プロトコル、送信元、宛先、動作。送信元ポートは詳細フォームに置く。字幕と赤枠のため、余白・文字・列幅は1600×680へ調整する。ピクセル単位の実画面複製とはしない。

## 表示上の区別

- 全編の本編画面へ「学習用 再現画面」を常時表示する。
- `nsg-<stem>-agw`などは教材の名前テンプレート。実際のリソース名を取得したように見せない。
- CIDRは公開paramsの具体値を使う。PE実IP群、管理VM実IP、ACA実内部IP、Firewall公開IP/32は動的取得値の役割を表示し、架空のIPを作らない。
- `gateway.grpc.enabled`、`clientAddressPrefixes`、`apimKeyVaultAccessEnabled`などは「リポジトリ設定」の補助ペインへ分ける。Azure native NSGフォームに存在する設定項目として描かない。
- 教材の注記`spec.note`は、Azureへ保存済みのDescriptionに偽装しない。独立した教材メモとして表示する。
- 通信追跡・状態追跡・両端比較は、NSG一覧を残した補助図。NSGの画面に接続履歴やstatefulの操作スイッチがあるようには描かない。

## データ契約

[scene-data.json](scene-data.json)の`schemaVersion`は1、基準surfaceは1600×680。トップレベルは`surface`、`rulesets`、`scenes`。

`rulesets`はAppGW/APIM/PE/管理VMの4組×受信・送信の8方向、既定規則を含め80レコード。`sourcePort`はすべて`*`。プロトコルは`TCP` / `UDP` / `Any`、動作は`Allow` / `Deny`。規則の方向は所属する配列とsceneの`direction`に持たせる。

`scenes`は本編の全115台詞IDを、元台本の再生順で保持する。追加台詞117は063の後、118は098の後、119は105の後に置き、数値IDの昇順へ並べ替えない。冒頭4台詞は既存ホログラム映像を保持するためマップへ含めない。

画面種別の内訳は、`rules` 39、`detail` 25、`subnets` 9、`flow` 33、`config` 5、`quiz` 4。一覧・詳細・サブネットが73場面で全体の大半を占め、その他も左側へPortal規則一覧を残す。

- `rows`は発話で説明する規則を選んだ部分集合。リソースや方向をまたぐ値は`flow`で両端の名称・優先度・ポートを示す。
- `rule`は選択中のカスタム規則。`detail`で送信元・宛先・ポート・動作・優先度を確認する。
- `subnets`は全6区画、`subnet`は選択中の区画。全6区画を同時に表示するためresourceはVNetとし、NSG単体の関連付け一覧とは区別する。
- `focus`はrendererが返す赤枠座標のID。`row:`、`field:`、`column:`に加え、補助ペインの`node:`、リポジトリ設定の`line:`、独立教材メモの`note`を使う。
- `focusSequence`は音声実長に対する0以上1未満の比率とfocusの配列。主要22台詞で説明の塊ごとに赤枠を移し、同時にカメラを寄せる。厳密な単語アライメントではない。
- 確認問題は質問と解答の台詞を分ける。106/107、109、111は問いを保持し、108、110、112で結果を示す。

元台本SHA-256：`169ad15ca5549cfcbe03b1ecc4f9797e543e8578b8dc5fa6a27be916c0e4b778`。

## 初期値と条件変更例

基準`rulesets`は公開例を表す。Web許可元は`0.0.0.0/0`、gRPC有効・公開8443/内部443、APIMのKVアクセス有効、証明書監視有効。運用元は空なので、管理受信の初期値はDeny4080。APIMからBrokerへの送信176は、実効`credentialProfiles`が空でない場合だけ存在する条件付き規則として登録し、常時存在すると説明しない。

| 台詞 | 条件変更の表示 |
| --- | --- |
| 069/070 | APIM送信100をDenyへ変更した説明例。公開例の元の値はAllow。既存接続の状態と、新規接続への判定を比較する。 |
| 093 | gRPCをfalseへ変えた場合の同名Deny。リポジトリ設定とPortalのAccessを分ける。 |
| 095 | クライアント配列が空なら、公開gRPC受信150はDeny。gRPC自体が有効でも公開入口は閉じる。 |
| 096/097 | 公開例の運用元空/Deny4080と、運用元を指定した条件例/Allow100を区別。指定元CIDRの架空値は入れない。 |
| 099/100/111/112 | 監視194をDenyへ変更した例でも、全IPv4向けWeb100のAllowが先に一致する。 |
| 101/102 | Web許可元が空でWeb拒否4080になり、監視194がAllowならそちらが先に一致する。 |
| 103/104 | KVアクセスをfalseへ変えるとAPIM送信110はDeny、PE受信110からAPIMを除外。基盤向け220とIAMは別。 |
| 105/119 | Broker176はプロファイル条件付き。SQL PE175/KV PE177は常時作成。176無効化時は同名Denyへ更新する方式ではなく、残存を実ルールで確認する。 |

受信4096は4 NSGすべて、送信4096はAPIMと管理VMだけ。AppGWとPEの送信へ独自4096やPE共通DNSを追加したように描かない。Azureの既定65000以降は全NSGに存在するが、4096に先に一致する方向ではその判定へ進まない。

## 出典との照合

数値・条件は`01-network-control.html`の11〜19節、`params/production.example.json`、`config/production.json`、`infra/modules/network.bicep`、`network-bindings.bicep`、`portal-network.bicep`、`credential-broker-network.bicep`に対応する。完成済み台本の全119台詞に対する技術レビュー結果を引き継ぎ、音声の説明内容を変更しない。

本編全115台詞に対応IDがあり、caption内の数字はそのsceneのrule/rows/title/note/flow/config/question/resultのいずれかに含まれることを機械的に確認した。最終映像では、説明対象が省略記号で隠れないこと、赤枠の位置・ズーム・字幕との重なり、条件例と公開初期値の区別を目視で確認する。
