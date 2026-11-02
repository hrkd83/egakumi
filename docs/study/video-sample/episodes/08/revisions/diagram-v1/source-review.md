# 第8回 出典レビュー

確認日：2026-11-02 JST。対象は教材とリポジトリ定義であり、稼働Azureへの接続・設定取得・SSH・Run Command実行は行っていない。台本の最終レビューは別途実施する。

## 題材と受講順

- 題材：基盤管理者の接続と作業実行。
- 到達点：管理VMへのprivate接続、NSG、SSH認証、Managed Run Commandの分担を説明できる。
- 正本：[CONTENT-PLAN.md](../../../CONTENT-PLAN.md) 第8回。
- 教材：[01-network-control.html](../../../01-network-control.html) の41〜43節、アンカー `maintenance-network` / `maintenance-auth` / `maintenance-command`（374〜402行）。
- 次回案内は推奨受講順に沿い、必修A1「ハブ＆スポーク接続と往復経路」へつなぐ。第9回の前にA1・A2を受講する計画。

## 技術的主張と実装の照合

| 主張 | リポジトリ上の根拠 | 台本での注意 |
| --- | --- | --- |
| 管理VMは証明書取得・更新、内部保守、イメージビルドに使うLinux VM。業務の常駐実行層を同居させない。 | `infra/modules/management.bicep:1` | 業務アプリの常駐サーバーとして描かない。 |
| NICはDynamic private IPv4のみ。Public IPを持たない。 | `infra/modules/management.bicep:65`、実IPの出力は187行 | サブネット内の固定IPを推測しない。実VM/NICの出力を確認する。 |
| 運用端末からprivate IPへの企業側の到達経路が必要。現行infraはVPN Gateway・Bastion・VNetピアリングを作らない。 | 教材41節 `maintenance-network` | 公開IPをNSG許可元に書くだけでは接続経路は作られない。企業側で用意する経路の説明を、本基盤が自動構築する機能として扱わない。 |
| `operatorAddressPrefixes=[]` では `allow-operator-management` がDeny/4080。指定時はAllow/100。 | `infra/modules/network-bindings.bicep:123` | 空配列を自動構築全体やSSH認証設定の無効化と説明しない。NSGが実際に観測する送信元IPを使う。 |
| 未一致受信は4096で拒否する。追加規則があればその優先順位も確認する。 | `infra/modules/network-bindings.bicep:73`、145行以降 | 4080の規則だけを見て全通信の最終判定を断定しない。 |
| 公開設定の運用ポートはTCP 22。 | `config/production.json:26`、`params/production.example.json:29` | `operatorPorts`の変更だけでLinuxのSSH待受ポートも変わるわけではない。 |
| 初期管理ユーザーの `authorized_keys` へ公開鍵を設定する。秘密鍵はAzureへ渡さない。 | `infra/modules/management.bicep:101`、`scripts/deployment/helpers/configuration.py:1779` | 鍵方式のログインにEntra VMログインロールを重ねて必須とする実装ではない。実鍵を教材や画面へ表示しない。 |
| Entra SSH用に `AADSSHLoginForLinux` を導入する。 | `infra/modules/management.bicep:133` | 拡張だけでTCP到達性や利用者のログイン権限が作られるとはしない。 |
| `loginAssignments` に応じVM単体scopeへUser/Administrator Loginロールを割り当てる。 | `infra/modules/management.bicep:171` | `loginAssignments=[]` はこのIaCが追加しない意味。公開鍵を無効化する値でも、既存・継承権限がないという実測結果でもない。 |
| パスワード・キーボード対話認証・root直接ログインを禁止し、公開鍵認証を許可する。 | VM定義 `infra/modules/management.bicep:106`、`guest/configure-audit.sh:135` | Entra SSH証明書も公開鍵認証の経路を使う。パスワード開放と説明しない。 |
| SSH設定は構文と実効設定を確認後reloadする。 | `guest/configure-audit.sh:149` | `sshd -t` のみでなく `sshd -T` の値も確認している。 |
| 有効な既存公開鍵は再利用し、公開鍵・秘密鍵が両方ない場合だけrepoのsecrets配下へ鍵対を生成する。 | `scripts/deployment/helpers/ssh_keys.py:75` | 現在の生成はパスフレーズなしRSA4096。既存秘密鍵を上書きしない。鍵生成手順の詳述は動画の主目的ではない。 |
| Linux監査設定と証明書bootstrapはManaged Run Commandで実行する。 | `infra/modules/management-audit.bicep:20`、`infra/modules/certificate.bicep:37` | 手元PCからSSH22でログインする自動処理として描かない。 |
| Portal/Brokerイメージビルドは、VMに一時AcrPushを付け、管理APIへRun CommandをPUTして状態を確認する。 | `scripts/deployment/helpers/portal_image.py:274`、`scripts/deployment/helpers/credential_image.py:146` | 配備主体の管理操作権限、VMのマネージドIDによる下流操作、SSHログインRBACを分ける。 |
| ビルド成功は実行状態Succeeded・exitCode 0、source hash・image digestを照合する。 | `scripts/deployment/helpers/image_build.py:47` | 管理APIが受理・provisioning成功しただけで、業務処理が成功したと判断しない。 |
| SSHでもRun Commandでも、VMが下流へ接続する際はDNS、送信NSG、UDR、経路上のFW、サービス権限が関係する。 | 教材43節。管理VM送信NSGは `infra/modules/network.bicep:124`、PE許可は `network-bindings.bicep:121` | Run Commandを無条件の下流アクセス経路として描かない。全通信が必ずFW経由とも説明しない。 |

## 分けるべき四つの判断

1. private IPへ往復できるか：対象実IP、接続元、経路、復路。
2. SSH接続が許可されるか：NSGの実送信元、宛先、プロトコル、ポート、優先順位、OS待受。
3. OSへ誰として入るか：初期ユーザーの鍵、またはEntra SSHの拡張・本人認証・VMログインRBAC。
4. VMで処理を起動し下流を操作できるか：Managed Run Commandを実行する配備主体の管理権限、VM agent、実行状態、VMからの到達性とサービス側権限。

`operatorAddressPrefixes=[]` は2の受信NSG設定であり、4のManaged Run Commandを停止するフラグではない。

## 公式資料の確認

教材に記載されたAzureの仕組みを、次の公式資料で確認した。引用本文は台本へ転載せず、要点を教材・実装の説明へ反映する。

- [Azure VM Run Command概要](https://learn.microsoft.com/en-us/azure/virtual-machines/run-command-overview)：VM agentを使う管理実行経路。Managed Run CommandはARMリソースとして扱える。Action Run Commandと区別する。
- [LinuxのManaged Run Command](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/run-command-managed)：`instanceView`に実行状態・終了コード・出力があり、`ProvisioningState`とスクリプトの実行成否は別。
- [Linux VMへのEntra SSHログイン](https://learn.microsoft.com/en-us/entra/identity/devices/howto-vm-sign-in-azure-ad-linux)：拡張、システム割り当てMI、証明書対応クライアントとTCP到達性、VMログインRBACが関係する。Owner/ContributorだけではEntra SSHログイン権限は自動付与されない。VM個別scopeのロール割当もサポートされる。

公式資料で記載の最小バージョンや全依存先を動画へ列挙する必要はない。ここでの説明はコード上の構成であり、今回の実環境ログイン・配備試験の結果ではない。

## 台本完成案の独立レビュー

確認日：2026-11-02 JST。確認対象は `script.json` の86台詞・9章、読み上げ本文5,846字。対象ファイルのSHA-256は `78eda5e8146b5473c34208bf698d8d151151d518ad20547029d46aa4dc03129c`。

**判定：技術内容・出典・図の意味に、生成を止める修正事項はない。** 台本は読取りのみで確認し、変更していない。プレビューの画面収まり、音声の発音・実長、完成映像の検証はこのレビューに含まない。

| 確認対象 | 該当台詞 | 結果 |
| --- | --- | --- |
| 管理VMの用途・private IPのみ・実測との区別 | 005〜010 | 教材41節と一致。実Azureへの接続結果として述べていない。 |
| 企業側の往復経路、VPN等を自動作成しない範囲、動的IPの実出力確認 | 012〜021 | 経路図の左から右と台詞の方向が一致。NSGの許可だけで経路が作られる説明を避けている。 |
| NSGの実送信元CIDR、Allow/100、空配列Deny/4080、終端4096、OS待受との区別 | 022〜032 | `network-bindings.bicep`と一致。追加規則の優先順位への言及もある。 |
| 鍵とEntra SSH、秘密鍵の保持、VM単体scopeの権限、空配列と継承、OS保護 | 033〜048 | 認証方式を混同していない。二つの入力からSSHへ合流する図と台詞が整合。 |
| 管理API・VM agent・配備主体の権限、SSH許可との分担、処理例 | 049〜057 | Managed Run CommandをSSH接続の代替ラッパーとして描いていない。運用元配列が空でも管理実行を停止する意味ではないことが明確。 |
| API受付・管理状態とスクリプト成功、終了コード、source hash・image digest | 058〜060 | `image_build.py:47`以降の確認処理に一致。 |
| 下流接続、公開依存先FW経由とPE直接経路、SSHと自動処理の調査順 | 061〜072 | 上段・下段の説明が図と一致。全通信がFWを通る前提を置かず、宛先と実効経路で判断している。 |
| 3問の確認、到達点、次回A1 | 073〜086 | private経路・NSG・SSH認証・管理実行の全到達点を回収。推奨受講順とも一致。 |

追加の機械的確認では、全`sourceReferences`の参照ファイルとHTMLアンカーの存在、一意な台詞ID、字幕チャンク結合と表示全文の一致、各字幕最大2行、図ノードが1207×511内へ収まる座標定義を確認し、問題を検出しなかった。

制作状態はレビュー時点で `draft`。制作ガイドに従い、プレビューの目視確認を終えた後に制作担当が `ready-for-generation` へ更新する。

## 生成用最終台本の再確認

2026-11-02 JST、生成用に確定した `script.json` を再確認した。SHA-256は `50b0bd9baa62cf0408d4727a0cfe7117dde8917be9583e69ad3afc025c685bd3`、状態は `ready-for-generation`。9章86台詞、読み上げ本文5,872字・表示本文5,626字で、`brief.md`の記録と一致する。

- OSの「オーエス」、A1の「エーワン」等の読みカナ調整後も、表示本文の技術的意味は前回レビューと同じであり、判断を変更する問題はない。
- `sourceReferences`へ追加された `../01-network-control.html#network-egress-management`（45節）は、公開依存先へ向かう管理VMの既定経路とFirewall経由の説明を補助する正しい参照先。PE直接経路は43節の下流通信の区別と参照実装も含めて説明されている。
- 章ごとの`sourceRefs`は、主範囲41〜43節の担当に対応している。`brief.md`の章・台詞番号・図・教材対応と矛盾しない。45節を補助参照としたこともbriefに明記されている。
- 全参照ファイル・HTMLアンカーの存在、字幕chunkのcaption結合と本文の一致、各chunkのlines結合とcaptionの一致、最大2行を再確認し、問題を検出しなかった。

判定は引き続き**技術内容・出典対応ともに修正必須事項なし**。この追記以外のファイルは変更していない。画面・音声・完成映像の確認は制作担当の結果へ委ねる。
