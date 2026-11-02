# 第8回 設定ビュー改修の出典監査

確認日：2026-11-02 JST。最新の `docs/study/AGENTS.md`、`CONTENT-PLAN.md`、`PRODUCTION-GUIDE.md`、`SETTINGS-VIEW.md`、教材41〜43節と関連リポジトリ定義を確認した。稼働Azureへの接続・値の取得・変更は行っていない。

## 改修前の判断

**既存86台詞のspeech／captionを維持してよい。再録が必要な技術変更は確認しなかった。**

監査した移行前台本のSHA-256は `f0d67bc7d330648a466604aeefaab850b19411bc4dc3dcbc5b3c0c11f6dc4540`。旧版の保管先は `revisions/diagram-v1/`。設定ビュー完成後の台本・図の適合確認は、この監査とは別に実施する。

- 第8回の題材・到達点・主参照範囲41〜43節は変更なし。推奨受講順も第8回→A1→A2→第9回のまま。
- 管理VMのDynamic private IPv4、Public IPなし、企業側のprivate到達経路、NSGの運用元許可、鍵／Entra SSHの分担を再照合し、既存台詞との矛盾を検出しなかった。
- 空の運用元配列とログイン割当配列、OS保護、Managed Run CommandとVM agent、管理操作権限とサービス利用権限、実行状態・終了コード・hash／digestの説明も引き続き正しい。
- 画面・編集は新基準 `teaching-settings-v2` へ変更する。声・通常速度・冒頭ホログラム・ロゴ・BGM・クロージングは既存指定を維持する。制作プロファイルID `study-series-v1` と画面スタイルIDを混同しない。

## 画面に出す設定名・値の照合表

以下は教材・公開設定例・宣言コードの値であり、実環境の取得値ではない。全項目を一画面へ列挙する意図はなく、各問答に必要な代表項目を選ぶための照合表。

### management.* と認証割り当て

| 正確な項目・値 | 根拠 | 表示時の注意 |
| --- | --- | --- |
| `management.adminUsername`：公開paramsは `egakumiadmin` | `params/production.example.json:88` | 公開例の初期Linuxユーザー名。Entraのユーザー名・人の表示名とは別。rootを初期ユーザーとして描かない。 |
| `management.sshPublicKeyFile`：`../secrets/management-ssh.pub` | `params/production.example.json:90` | 公開鍵の入力ファイル。秘密鍵のパスや鍵本文を表示する必要はない。 |
| Bicep入力 `sshPublicKey` → `/home/${adminUsername}/.ssh/authorized_keys` の `keyData` | `infra/modules/management.bicep:108` | `sshPublicKeyFile`は配備処理の入力、`sshPublicKey`は解決後のBicep入力。実OSの設定項目名を `sshPublicKeyFile` としない。 |
| `management.loginAssignments`：公開例は `[]` | `params/production.example.json:91` | このIaCからEntra VMログインロールを追加しない意味。鍵ログイン無効化、Run Command停止、継承権限がないという証拠ではない。 |
| 各割当の `principalId` / `principalType` / `role` | `infra/modules/management.bicep:172`、`scripts/deployment/helpers/configuration.py:291` | IDを創作しない。`principalType`の許容値は `User` / `Group` / `ServicePrincipal`。`role`の入力値は `User` / `Administrator`。 |
| `role: User` → `Virtual Machine User Login` | `infra/modules/management.bicep:62`、178行 | 入力値とAzure組込ロール名は同一文字列ではない。省略時も「一般ログイン用」と分かるようにする。 |
| `role: Administrator` → `Virtual Machine Administrator Login` | `infra/modules/management.bicep:63`、178行 | VMのログイン用ロール。Subscription Owner付与として描かない。 |
| ロール割当scope：`vm`（対象VM単体） | `infra/modules/management.bicep:173` | 現行IaCのscope。上位scopeからの既存継承は実環境で別途確認する。 |
| `AADSSHLoginForLinux`、publisher `Microsoft.Azure.ActiveDirectory` | `infra/modules/management.bicep:133` | Entra SSHのログイン拡張名。VM agent自体、Run Command、VMログインRBACとは別。 |
| VMのマネージドID：`SystemAssigned`、DNS用ユーザー割当MIがある場合 `SystemAssigned, UserAssigned` | `infra/modules/management.bicep:90` | 人のSSHログイン権限とは別。実principalIdや付与済み権限を推測しない。 |

VMサイズ等を補足する場合、現在の公開configは `Standard_D2as_v6` / `NVMe`、Ubuntu 24.04 LTS、OSディスク128 GiB・`StandardSSD_LRS`。Bicepの既定値（例：`Standard_D2s_v5` / `SCSI`）を、公開configの採用値として表示しない。第8回の到達点には、これらの数値の列挙は必要ない。

### network.* とNSG規則

| 正確な項目・値 | 根拠 | 表示時の注意 |
| --- | --- | --- |
| `network.operatorAddressPrefixes`：公開paramsは `[]` | `params/production.example.json:29` | API利用元の `clientAddressPrefixes` と別。VPN等を作る入力でもない。 |
| `network.operatorProtocol`：`Tcp` | `config/production.json:26` | 宣言値は `Tcp`。教材表記のTCPと意味は同じ。SSH自身の設定ではない。 |
| `network.operatorPorts`：`["22"]` | `config/production.json:27` | ポート番号は文字列配列の入力。変えてもOSのSSH待受を自動変更しない。 |
| `network.managementAdditionalSecurityRules`：公開paramsは `[]` | `params/production.example.json:34` | 追加規則がある環境は優先順位も照合する。空例だけで実環境に追加規則がないとしない。 |
| 管理NSGの名前：`nsg-<stem>-vm` | 教材41節、`infra/modules/network.bicep` | VM/NIC名の `-mgmt` とNSG名の `-vm` を混同しない。`<stem>`はプレースホルダーと分かるよう表示する。 |
| 運用元指定時：`allow-operator-management` / Inbound / Allow / priority `100` | `infra/modules/network-bindings.bicep:125` | 宛先は管理VMの実private IP。送信元は入力CIDR、プロトコル・宛先ポートは公開例ならTcp/22、送信元ポートは `*`。 |
| 運用元が空の場合：同じ `allow-operator-management` / Inbound / Deny / priority `4080` | `infra/modules/network-bindings.bicep:124` | 規則名にallowがあってもaccessはDeny。送信元は `*` となる。空配列を「全許可」の値として表示しない。 |
| 終端受信：`deny-other-inbound` / Inbound / Deny / priority `4096` | `infra/modules/network-bindings.bicep:73`、145行 | 「先に一致しない受信」を拒否。先に一致した規則を覆す後段の拒否として描かない。 |
| 管理VMには終端送信 `deny-other-outbound` / priority `4096` もある | `infra/modules/network-bindings.bicep:151` | 4096を受信だけに存在する番号として一般化しない。第8回のSSH受信説明には受信規則を選ぶ。 |
| 管理サブネットの公開例：`network.subnetPrefixes.management = 10.40.2.128/26` | `params/production.example.json:23` | サブネットCIDRはVM実IPではない。未取得の実IPを `.132` 等と補完しない。 |
| 実送信元：NSGが観測するIPに一致するCIDR | 教材41節 | 手元端末の公開IP、VPNクライアントIP、NAT後IP等のいずれかを環境の確認なしに確定しない。 |

### NIC／OSの宣言値

| 正確な項目・値 | 根拠 | 表示時の注意 |
| --- | --- | --- |
| VM名：`vm-<stem>-mgmt`、NIC名：`nic-<stem>-mgmt` | `infra/modules/management.bicep:67`、87行 | NSG名の末尾 `-vm` と分ける。例名を実環境の取得結果として表示しない。 |
| `privateIPAllocationMethod: Dynamic` / `privateIPAddressVersion: IPv4` | `infra/modules/management.bicep:77` | DynamicはIPが毎回必ず変わるという意味ではない。確定実IPは対象VM/NICの出力で調べる。 |
| Public IP：NICへ関連付けなし | `infra/modules/management.bicep:73`〜81行 | `publicIPAddress: false` という実装ではない。教材注釈として「Public IPなし」とする。 |
| 出力 `managementVm.privateIp` | `infra/modules/management.bicep:187` | NICの実出力から返す値。表示値は「対象VM/NICの実出力を確認」等とし、実IPを作らない。 |
| `disablePasswordAuthentication: true` / `provisionVMAgent: true` | `infra/modules/management.bicep:106` | パスワード禁止の真偽を反転しない。VM agentをSSHログイン方式として描かない。 |
| `PasswordAuthentication no` | `guest/configure-audit.sh:141` | パスワード認証を禁止。 |
| `KbdInteractiveAuthentication no` | `guest/configure-audit.sh:142` | キーボード対話認証の禁止。SSHの対話セッション全般が禁止される意味にしない。 |
| `PermitRootLogin no` | `guest/configure-audit.sh:143` | root直接ログインを禁止。管理者が一切管理操作できないという意味ではない。 |
| `PubkeyAuthentication yes` | `guest/configure-audit.sh:144` | 初期ユーザーの鍵とEntra SSH証明書の経路を維持する。 |
| `sshd -t` → `sshd -T` の実効値照合 → `systemctl reload ssh` | `guest/configure-audit.sh:149`〜159行 | 構文確認後すぐ無検証でreloadする絵にしない。全設定値は教材上の適用内容であり、取得した実効設定画面ではない。 |

### Managed Run CommandとinstanceView

| 正確な項目・値 | 根拠 | 表示時の注意 |
| --- | --- | --- |
| リソース型：`Microsoft.Compute/virtualMachines/runCommands` | `infra/modules/management-audit.bicep:20`、`certificate.bicep:37` | Managed Run Command。Action Run CommandのAPIやSSHのラッパーと混ぜない。 |
| 監査用名：`configure-linux-audit` | `infra/modules/management-audit.bicep:22` | 指定スクリプトをVM上で実行。`asyncExecution:false` / timeout `300`秒 / `treatFailureAsDeploymentFailure:true`。 |
| 証明書用名：`configure-public-certificate` | `infra/modules/certificate.bicep:39` | `asyncExecution:false` / timeout `3600`秒 / `treatFailureAsDeploymentFailure:true`。全Run Commandに300秒を一律表示しない。 |
| Portal/Brokerビルド：`asyncExecution:true` / timeout `2400`秒 | `scripts/deployment/helpers/portal_image.py:192`、`credential_image.py:155` | 同期と非同期の設定は用途ごとに異なる。ビルドRun Command名には実行ごとのsuffixが付くため、実名を創作しない。 |
| 管理リソース `properties.provisioningState` | `scripts/deployment/helpers/portal_image.py:286`、292行 | 管理リソースの状態。これだけでスクリプト成功やイメージ完成を断定しない。 |
| `properties.instanceView.executionState`：期待成功値 `Succeeded` | `scripts/deployment/helpers/portal_image.py:287`、`image_build.py:57` | 大文字小文字を含む識別子。スクリプトの実行状態であり、source hashやdigest自体ではない。画面の値は「成功条件」と注記する。 |
| `properties.instanceView.exitCode`：期待成功値 `0` | `scripts/deployment/helpers/image_build.py:57` | `Succeeded`と0を両方確認。実環境の成功結果を取得した表示にしない。 |
| ビルド出力 `status: built`、`sourceSha256`、`imageDigest` | `scripts/deployment/helpers/image_build.py:62` | `imageDigest`は `sha256:`+64桁の16進文字列形式。画面へ実在しない64桁値を作らず、「期待するソースhashと一致」「検証済みdigest」の教材注釈を使う。 |
| 監視する終端実行状態：`Succeeded` / `Failed` / `TimedOut` / `Canceled` | `portal_image.py:288`、`credential_image.py:164` | 終端というだけで成功ではない。 |
| ビルド時の一時権限：管理VMのMIへ `AcrPush` | `portal_image.py:274`、`credential_image.py:146` | 配備主体のRun Command管理操作権限、利用者のVMログインロールとは別。既存配備の結果として付与済みと描かない。 |

## 画面改修で維持する台詞との対応

- 台詞012は「図の左が運用端末、右が管理VM」と言う。教材用設定ビュー内にも左→右の小図を置き、経路・実private IP・NSGの代表設定を併記する。復路の準備にも言及しているため、片方向しか要らないと読める注釈を加えない。
- 台詞067は「図の上は公開依存先へFirewall経由、下はPE宛ての直接経路」と言う。上下の関係を実際の小図でも維持する。名前解決・送信NSG・UDR・FW・サービス権限をすべて一本の直列装置として描かない。
- 確認①073〜075、確認②076〜078、確認③079〜081は各問の質問から回答・補足まで共通viewId、メニュー・行順・構図・縮尺を保持する。質問中の赤枠は正解を指さず、回答で明らかになる時点から該当行を囲む。
- 質問から回答まで同じ画面で見せる条件は、本編の他の問答にも適用する。台詞・話者・字幕chunkの変更だけで本文画面をリセットしない。
- Azureのブランドバー・検索・疑似操作ボタンは足さない。用途・目的・権限の役割等は、実製品の項目名と混同しないよう「教材注釈」等で区別する。
- 最終的に40〜70秒程度の連続した質問・回答を通常速度で確認する。短い静止画や場面を寄せ集めた抜粋だけで、画面保持を確認済みとはしない。

## 範囲と引き継ぎ

このファイル以外は変更していない。原稿・声・styleの再録判断は、変更箇所と実送信設定の比較に基づく。現時点では本文を維持して画面を更新できる。設定ビュー完成後は、表示名・値・出典、同一viewId内の構造、赤枠の開始時点、台詞と図の対応を再監査する。

## 設定ビュー最終原稿の再監査

2026-11-02 JST、作者の最終書込み完了後に再確認した。対象 `script.json` のSHA-256は `8691d0ecc609772681943e7caed6255aff9d2703bc213a817600749c9a524500`。全86台詞のうち冒頭hologramが4、本編はsettings 60・SVG 22、20種類のviewId。`diagrams/settings-v2/` のSVG全16ファイルも対象とした。

**判定：表示値・ラベル・補助図の技術的意味・出典に、修正必須事項はない。** 旧版 `revisions/diagram-v1/script.json` と照合し、86台詞すべての非visualフィールド（speech、caption、字幕、delivery等）が完全に同一であることも確認した。画面更新のために読み上げ本文を再録する必要はない。

### 全表示内容と出典の対応

| viewIdと対象台詞 | 照合結果 |
| --- | --- |
| `mgmt-vm-overview` 005〜008 | 用途、Linux、Dynamic private IPv4、Public IPなし、常駐実行層との分離は教材41節・management.bicepと一致。用途・役割の境界は教材注釈。 |
| `mgmt-ssh-stages` 009〜011 | 到達・NSG・待受と認証の三段階は教材41節。説明の根拠と実測結果ではないことを表示。 |
| `mgmt-private-path` 012〜017 | 運用端末を左、管理VMを右に配置。企業側経路とNSGを経由する説明、復路の照合、VPN等を現infraが作らないこと、実IPを推測しないことが41節と一致。 |
| `mgmt-nic-output` 018〜021 | `privateIPAllocationMethod: Dynamic` / `privateIPAddressVersion: IPv4` はmanagement.bicep:77。Public IPなしの説明と、実出力を確認する方針が正しい。 |
| `mgmt-operator-rules` 022〜029 | []時のDeny/4080、指定時Allow/100、終端Deny/4096、実送信元CIDR、VM実IP、Tcp/22、規則名とaccessの区別はnetwork-bindings.bicep・公開configに一致。[]時のsource `*` も教材注釈に反映。 |
| `mgmt-operator-port` 030〜032 | 公開config値を `Tcp` と文字列配列 `["22"]` で表示。NSG入力がOS待受を自動変更する説明になっていない。 |
| `mgmt-auth-overview` 033〜034 | 鍵／Entra SSHの二方式、共通のTCP到達・NSG・待受、パスワード開放ではないことは42節と一致。 |
| `mgmt-ssh-key` 035〜039 | `adminUsername`は公開例 `egakumiadmin`、`sshPublicKeyFile`は公開例 `../secrets/management-ssh.pub`。役割説明を実設定値欄に置く状態は解消。OS配置先の変数表現は教材注釈として明示され、実鍵や秘密鍵を表示していない。 |
| `mgmt-entra-ssh` 040〜045 | 拡張名、VM単体scope、[]の意味、継承・下流利用権限の区別は42節・management.bicepと一致。割当要素とロール名は教材注釈として分離し、入力 `role: User / Administrator` とAzureの二つの正式ロール名を混同していない。 |
| `mgmt-ssh-os` 046〜048 | `disablePasswordAuthentication:true`、Password/KbdInteractive/rootのno、Pubkeyのyes、構文・実効設定検証後reloadはconfigure-audit.shと一致。 |
| `mgmt-management-execution` 049〜057 | SSHとManaged Run Commandを上下に比較。管理API→VM agent→VM内処理、実行用途、管理操作権限、運用元配列とRun Commandの分担は43節と一致。 |
| `mgmt-execution-result` 058〜060 | provisioningStateの説明は「教材注釈」と明記。Succeeded・exitCode 0は「ビルドの成功条件」であり、取得済み成功結果の表示になっていない。source hashとimage digestの照合はimage_build.pyと一致。 |
| `mgmt-downstream` 061〜067 | 上段は管理VM→FW→公開依存先、下段は管理VM→PE→対象サービスの直接経路。DNS・NSG/UDR・経路上FW・サービス利用権限の確認も43節・補助45節と参照実装に整合。 |
| `mgmt-ssh-triage` 068〜070 | 対象、往復経路、実送信元とNSG、OS待受、選んだ認証方式の順は43節の調査範囲と一致。 |
| `mgmt-command-triage` 071〜072 | 配備主体、agent/実行状態、VM下流接続、処理先認証、結果を区別。受信NSGだけを原因とする説明になっていない。 |
| `mgmt-check-private` 073〜075 | 「今回の変更」を「設問の条件（教材例）」へ修正済み。実環境に公開IP許可を追加した結果として表示していない。 |
| `mgmt-check-empty` 076〜078 | 二つの[]の対象と、鍵・管理実行を無効化する指定ではない意味は正確。 |
| `mgmt-check-build` 079〜081 | 「今回の観測」を「設問の条件（教材例）」へ修正済み。取得済みRun Command実行結果と誤読させない。確認対象は43節・ビルド実装に一致。 |
| `mgmt-summary` 082〜084 | 到達点の回収、権限の区別、下流接続と実行結果を確認する要点に漏れなし。 |
| `mgmt-next-a1` 085〜086 | 次回A1の位置付け、接続・出口の採用範囲を個別選択し、ローカル経路と往復を維持する案内は最新計画と一致。 |

### 実設定・教材注釈・取得値の区別

- 公開値であるTcp、`["22"]`、egakumiadmin、公開鍵ファイル例、拡張名、OS保護設定は参照元へ照合済み。実private IP、実principalId、実ソースhash、実image digestは表示していない。
- 「Public IP付与しない」はNICへ関連付けを宣言しないコードの説明であり、存在しない設定項目 `publicIPAddress:false` を表示していない。
- 手順・用途・役割・成功条件・設問条件を、実際のAzure取得値のように表示していない。全画面の教材用メニュー表記と「稼働環境の取得値ではない」のfooterも保持されている。
- SVGの「管理操作の権限≠SSH秘密鍵・Entra VMログイン権限」は教材上の分担説明。SSH鍵をAzure管理操作の資格情報として扱うものではない。

### 図と画面保持の静的確認

- 台詞012の左右、067の上下はSVGの実配置と一致する。復路と直接経路を省いた意味へ変わっていない。FWは経路上にある通信を判定し、PE直接経路をFWへ折り返す図ではない。
- 全20viewIdで、同一viewId内のメニュー・欄・表・行順などの基礎構造が一致。SVG16ファイルも各3系統内では赤枠グループ以外のXMLが同一。台詞・話者の変更を画面再構成の理由にしていない。
- settingsの`camera` / `zoom` / `motion`指定なし。全赤枠IDが実在する行・欄・図要素を参照する。
- 確認問題の質問073・076・079では赤枠指定が空。各問の回答・補足まで同じviewIdを保持し、回答時点から説明する項目を囲む。
- 全sourceReferencesと章sourceRefsの参照ファイル、HTMLアンカーの存在を確認した。

本節は原稿・SVGソースの静的監査。プレビュー上の文字の収まり、完成動画の連続した問答での見え方、音声の聴感はこの監査の結果に含めない。このmd以外のファイルは変更していない。
