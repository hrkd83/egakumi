# SQL Redirect 切替計画

共有Azure SQLサーバーをProxyからRedirectへ切り替える。Portalの接続ドライバーをMicrosoft ODBC Driver 18を利用する構成へ揃え、SQLの接続ポリシー、NSG、初期構築、既存環境への限定適用を一つの移行として扱う。

Redirectを採用する方向は確定している。SQL/NSGの固定資材と限定移行処理は実装・ローカル合成検証済みである。PortalのNode.js用接続部品は未選定で、Azure反映・Redirectの実証は未着手である。現在のProxyでの検証結果は、そのままRedirectの実証結果へ移さない。

[現行の通信要件](network-communication-requirements.md) · [NSG設計](network-security-design.md) · [Firewall設計](firewall-policy-design.md) · [実証結果の管理](network-security-validation.md)

## 対象と設計方針

対象はGatewayとPortalが共用するSQLサーバー、そのSQL Private Endpoint、ACAと管理VMからの接続である。接続ポリシーはサーバー単位なので、Gateway用DBとPortal用DBの両方に影響する。

MicrosoftはPrivate EndpointでもRedirectを性能面で推奨している。初回はTCP1433で接続し、その後は対応ドライバーが案内された接続先へ再接続する。遅延とスループットの改善幅は、同じ条件のProxyとの比較で確認する。

| 項目 | 切替後の方針 |
| --- | --- |
| SQL接続ポリシー | 初期構築の最終状態をRedirectへ固定する |
| ドライバー | Microsoft ODBC Driver 18を利用する。PortalのNode.js接続部品は認証・接続制御の成立確認後に確定する |
| NSG | Private Endpoint用のTCP1433–65535を固定資材へ定義し、通信元とSQL PE宛先を限定する |
| FirewallとUDR | 現行のSQL PE直接経路を維持する。SQL PE通信をFirewall規則へ登録しない |
| 公開入力 | params/configに接続方式やNSG/FWの作成有無を選ぶフラグを追加しない |
| 既存データと認可 | driver置換を理由とするDB schema・データ移行は行わず、MI認証、TLS検証、既存DB権限を維持する |

公開SQLのRedirectで使うTCP11000–11999を、今回のPE用ポート要件の代わりにしない。TCP1433–65535をInternet全体やSqlサービスタグへ許可する設計にも広げない。

## 改修前と今回の差分

[shared-sql.bicep](../../infra/modules/shared-sql.bicep)の早期Proxy設定を外し、PE完成後の[専用policyモジュール](../../infra/modules/shared-sql-connection-policy.bicep)へRedirect設定を移した。[credential-broker-network.bicep](../../infra/modules/credential-broker-network.bicep)のSQL向け4規則は、TCP1433からTCP1433–65535へ変更した。PE側ではNSG評価が有効で、PE受信と管理VM送信には末尾Denyがあるため、既存環境ではSQL側の変更に先立ってNSG4件の準備が必要になる。

[management_client_assets.py](../../scripts/deployment/helpers/management_client_assets.py)の通常構築後の照合もRedirectを期待する形へ改修した。移行中のProxy確認と切戻し確認は専用工程で意図した状態を照合する。通常の照合を単に「ProxyとRedirectのどちらでも可」へ緩めない。

Broker Appとそのschema・reconciliation Job、管理VMはODBC18を利用する。Portal Appとそのschema・reconciliation JobはTediousを利用し、DB接続は[auth-database.ts](../../apps/portal/lib/auth-database.ts)に集約されている。Kyselyの標準`MssqlDialect`はTediousのAPIに依存するため、PortalではODBC用のDriver/Dialectとの接続が必要になる。

## 担当と並行作業

| 担当 | 所有する変更 | 調整する箇所 |
| --- | --- | --- |
| ネットワークセキュリティ作業 | 共有SQL接続ポリシー、NSG4件と移行処理、構築順序、管理client照合、限定適用・切戻し、通信・NSG/FW文書 | 基盤の`infra/main.bicep`と`scripts/deployment/lifecycle.py` |
| Portal分離作業 | Portal接続層、Node.js接続部品の選定とODBC18連携、コンテナ、Portal App/Jobs、認証とDBの回帰 | `auth-database.ts`、Dockerfile、schema/reconciliation Job、Portal手順・試験 |
| APIM作業 | APIMポリシー、公開HTTP/WAF関連、Entra rolloutとintegration資料 | 基盤Bicepの既存WAF差分。今回の共有SQL切替ではAPIMポリシー本文を変更しない |

Portal側は接続層の担当候補を了承している。着手は、Portal側の証明書認証工程3のローカル確認と専用commit統合を先に行い、driver選定結果と具体的な変更案を判断してから独立した変更として扱う。証明書認証とSQL driver変更を同一commitへ混ぜない。

こちらのSQL/NSGの局所的な実装・合成検証は、独立した作業ブランチで並行する。Portalの接続層はPortal側が所有し、同じファイルを両作業から編集しない。共有Bicep・lifecycleの編集前とcommit/merge前には、両作業の変更範囲と最新mainを確認して担当と取り込み順を決める。元チェックアウトの未コミット差分をコピー、reset、まとめてcommitしない。

## SQLとNSGの固定資材

初期構築は、共有SQLサーバー作成、SQL PE・DNS・NSG完成、固定Redirect policy適用、App/Job配備・管理client確認の順にした。早期のSQLモジュールからProxy policyの所有を外し、PE完成後に実行する専用policyモジュールへ移した。通常のCore更新で毎回Proxyへ戻してからRedirectにする処理は作らない。

公式仕様では、PE作成前からRedirectを設定している場合、PE作成後にpolicy切替が必要になることがある。新規構築とPE再作成を試験し、必要な場合だけ状態を確認した内部工程で切替・読戻しを行う。通常更新と区別し、常時toggleする処理や公開switchを追加しない。

NSGの新名称は以下とする。Allow、TCP、送信元port `*`、優先度、通信IDを維持し、宛先portを4件とも`1433-65535`へ変更する。新規則の確認後に旧4規則を撤去し、最終的な固定規則数は76件を維持する。

| NSGと方向 | Proxy資材の規則名 | Redirect資材の規則名 | 優先度 | 送信元 | 宛先 |
| --- | --- | --- | --- | --- | --- |
| PE受信 | `allow-mgmt-vm-sql-1433-ingress` | `allow-mgmt-vm-sql-ingress` | 225 | 構築管理VMのIP `/32` | 構築SQL PEの全IP `/32` |
| PE受信 | `allow-aca-sql-1433-ingress` | `allow-aca-sql-ingress` | 226 | 構築ACAサブネット | 同上 |
| 管理VM送信 | `allow-sql-1433-egress` | `allow-sql-egress` | 224 | 構築管理VMのIP `/32` | 同上 |
| ACA送信 | `allow-sql-1433-egress` | `allow-sql-egress` | 221 | 構築ACAサブネット | 同上 |

`FLOW-ACA-SQL`と`FLOW-MGMT-SQL`に対応させ、正本の[nsg-rule-priorities.json](../../infra/nsg-rule-priorities.json)、初期Bicep、限定移行、試験、文書を同じ定義へ揃える。APIM NSGにある同名の`allow-sql-egress`はAPIM自身のAzure基盤依存であり、そのSqlタグTCP1433は変更しない。

Redirectで案内されるhostname・IP・portは実接続で確認する。公式のPEポート要件だけから「必ず同じPE IPへ接続する」と断定しない。SQL PE宛先限定と直接経路の設計が成立しない結果なら切替を止め、Proxyへ戻して原因を調べる。推測した宛先やFirewall規則を追加して疎通だけを成立させない。

旧4名は現在のlegacy catalogに登録されていない。新名へ改めるだけでは、[fixed_nsg.py](../../scripts/deployment/helpers/fixed_nsg.py)が旧名を未知の規則として拒否する。今回の旧4tupleを厳密に認識する撤去定義と、安定した一時優先度を用意する。既存legacyの`1000 + index`による一時優先度を再計算して、過去の移行中断からの再開を壊さない。

既存NSGでは、旧Allowを検査済み一時優先度へ移し、新Allowを所定優先度へ作成・読戻しした後、旧Allowを条件付き削除する。NSG親のPUT、既定規則の作り直し、末尾Deny解除、接続flushは行わない。未知・手動変更・優先度競合・PE IP変更・応答不明では停止し、実状態を確認してから再開する。

[nsg-fixed-upgrade.bicep](../../infra/modules/nsg-fixed-upgrade.bicep)は全76件を送信する。SQL限定移行には[sql-redirect-nsg-upgrade.bicep](../../infra/modules/sql-redirect-nsg-upgrade.bicep)を使い、4childだけを送信する。旧定義は[専用の撤去catalog](../../infra/sql-proxy-nsg-retirement.json)に保持し、過去のlegacy catalogの一時優先度を変更しない。

## Firewallと構築用通信

SQL PEへの通信は現在のVNet内の直接経路を維持する。ACAと管理VMの外部向けdefault UDR、SQL DNS、PEのNSG評価を維持し、経路をFirewall経由へ変更しない。APIMの`allow-apim-sql`も別の基盤依存通信で、今回の許可拡張の対象外である。

Portalのnative接続部品とODBC18の導入では、イメージbuild時の依存取得先を確認する。Microsoft packages等の既存許可で足りるかを実際のbuild経路と照合する。追加取得先が必要なら、実際にFirewallを通るbuild通信として個別に整理し、用途・送信元・宛先・portを確認してから計画差分を示す。SQL通信用の追加Firewall規則と混同しない。

## Portalドライバーの選定条件

MicrosoftがPE Redirect対応を明記しているODBC18本体と、それをNode.jsから利用する接続部品を分けて選定する。msnodesqlv8はLinuxとODBC18を扱える候補だが、現在の「取得済みEntra access tokenを接続時に渡す」方式を維持できる公開APIは未確認である。node-odbcもtoken受渡しとODBC18連携の成立確認が必要で、いずれも確定選定ではない。

| 選定・互換性の条件 | 確認する内容 |
| --- | --- |
| 実行基盤 | 現行Node.js 24/Debian 12、native依存の固定、再現可能なbuild、Next.js実行イメージとJobの双方で動作 |
| 認証とTLS | ACAのSQL用MI、管理用JobのMI、開発用AzureCLI token、新規接続時のtoken取得、SQL論理hostnameの証明書検証を維持 |
| SQL実行 | Kyselyの安全なパラメーター束縛、DateTime2のUTCと精度、Buffer、Boolean、BigInt、NULL、影響行数、エラー番号を維持 |
| 接続制御 | transaction中の同一物理接続、必要な分離レベル、applock、commit/rollback、poolの取得・解放・破棄、切断時の失敗・再接続を維持 |
| アプリ互換性 | Better Authのログイン・セッション・期限、キー管理、所有権、業務SQL、schema/reconciliation Jobが同じ接続層で成立 |
| Redirect | 新しい物理接続で案内先への再接続とPE内の実宛先・portを確認。query成功だけを証明にしない |

token認証が扱えない場合にSQLパスワード認証へ切り替えたり、TLS検証を無効化したりしない。既存構成で成立する接続部品の具体案と試作結果を示して選定する。追加のnative拡張や別サービスが必要になった場合も、Portal全体へ広げる前に変更案を判断する。

## 工程と統合順序

| 工程 | 作業 | 次へ進む条件 |
| --- | --- | --- |
| 1 計画と担当 | 本計画、Portal工程3との分離、共有ファイルの担当と取り込み順を整理する | 計画の確認。driverの具体選定は工程2で行う |
| 2 ドライバー成立確認 | Portal側がtoken・TLS・native実行・Kysely接続の小さな試作を行う | 必要APIと互換性の根拠を確認し、具体案を判断。ここで既存SQLのpolicyは切り替えない |
| 3 独立実装と合成検証 | Portal側は証明書認証工程3の統合後にdriverを独立実装。こちらはSQL/NSG/移行/切戻しと初期構築順を実装する | 公開合成試験と独立した試験DBで互換性・移行境界・復旧が成立する |
| 4 統合と反映準備 | 最新mainへ独立変更を順に統合し、対象・既存状態・配備版・戻し方を確認する | Portal driver対応版のsourceとイメージを識別でき、限定適用の計画が具体化している |
| 5 Proxyでの受入とNSG準備 | 新Portal driverをProxyのまま先に受入。SQL4規則をまとめて移行・読戻しする | App/Jobsと認証の互換性、SQL PEのDNS/直接経路、4規則の一致を確認する |
| 6 Redirect限定切替 | 共有SQLのconnection policy子資源だけを切り替え、読戻しする | Portal・Broker・管理VMと各Jobを新しい物理接続で確認できる |
| 7 実証と完了整理 | 接続先・port・経路、運用機能、性能、後始末を確認し正式文書へ反映する | 下記の受入条件を満たし、実証範囲と制約を区別して記録する |

commitは、Portal証明書認証、Portal driver、SQL/NSG基盤を分離する。局所的なNSG準備の統合は並行可能だが、Redirectを初期既定にする最終資材の統合と使用は、対応Portal driverのsource統合と移行手順を揃えてから行う。既存環境の通常構築・更新でも、未対応Portalが稼働中なら先にProxy下のdriver移行を求め、policyを先行変更しない。

既存環境への反映は、Portal対応版をProxyで確認、SQL4件のNSG準備、SQL policy切替の順に行う。通常の既存切替でSQL server/DB/Entra管理者/SKU/公開アクセス/PE/DNS/UDRを再PUTしない。APIM API公開やAppGW更新をSQL切替のために再実行しない。新規構築E2Eは別の確認段階とし、限定更新成功だけで再構築済みにしない。

移行専用工程では、Proxy下で行うPortal配備・管理client準備も、その時点で意図したProxy状態を照合する。通常構築後のRedirect照合を途中状態へ誤適用して配備を止めないよう、検証済みの内部移行状態から期待値を決める。公開params/configに移行用の接続方式switchを設けない。

## SQL限定操作の実装状態

[SQL NSGの運用入口](../../scripts/operations/sql_nsg_upgrade.py)は、旧4規則の厳密な確認・一時優先度への退避、4childだけのwhat-ifとIncremental配備、全4tupleの読戻し、旧4規則の撤去を一括して行う。変更前の5 NSG、Core state、構築IP、compiled templateとparametersを保護journalへ固定する。別の部署・手動規則・親NSG・SQL policyは変更しない。

リポジトリルートで、選択済みの保護入力と、そのCore stateがあるチェックアウトを明示して読取計画を実行する。次の変数は利用者が選択する保護ファイル・チェックアウトの参照で、実値を公開docsへ書かない。

```sh
python3 -B scripts/operations/sql_nsg_upgrade.py --config "$DEPLOY_PARAMS_FILE" --runtime-root "$PROTECTED_RUNTIME_ROOT"
python3 -B scripts/operations/sql_connection_policy.py --config "$DEPLOY_PARAMS_FILE" --runtime-root "$PROTECTED_RUNTIME_ROOT" --target-mode Redirect --expected-current Proxy
```

NSGはPortal対応版のProxy受入後に、計画と担当を確認して同じコマンドへ`--execute`を付ける。送信前journalを保存し、childやdeploymentの応答不明から再開するときは、同じ送信先の状態と保存した内容を照合する。旧状態や404のままなら未実行と決めつけず停止し、変更を再送しない。確認できた変更だけの残り工程を再開する。実運用のCLI・Azure応答は別途受入対象である。

NSGのProxy切戻しは、SQL policyのProxy読戻し、Appのpoolと実行中Jobのdrain、新しいProxy接続の確認後にだけ行う。現在の公開CLIは、この内部runtime verifierがないためProxyの`--execute`を拒否する。drain済みを指定する公開フラグは設けない。

[SQL policyの運用入口](../../scripts/operations/sql_connection_policy.py)は、選択済みの保護paramsとCore stateから対象を確認し、読取計画を既定とする。別worktreeで実装資材を使う場合も、`--runtime-root`で既存の保護入力・stateがあるチェックアウトを明示し、探索やコピーは行わない。

SQL policy子資源だけを一回変更し、送信前journalと読戻しを保護保存する。公式APIはETag/If-Matchによる条件付き更新を明記していないため、operation lockと変更直前・直後の照合を使う。他checkoutやAzureポータルからの同時更新に対する原子的な競合防止は保証しない。応答不明の送信を自動で繰り返さない。

Redirectの実行には、Portal対応版をProxyで受入した内部runtime証跡、現在のApp/Jobs/active revisionとの世代一致、旧規則撤去済みのSQL NSG4件が必要である。証跡を作る本番用verifierはPortal対応と合わせて実装する。現段階の公開CLIにはverifierを登録せず、Redirectの`--execute`は拒否する。`--ready`や任意のproofファイルを指定して迂回する入口は設けない。Proxyへのpolicy切戻しと、接続drain・NSG復旧は別工程として扱う。

## 実証と受入条件

| 確認 | 合格条件 |
| --- | --- |
| 資材と限定操作 | Bicep展開・構築順・管理client照合・厳密な旧4件認識・一時優先度互換・中断再開・競合停止・切戻しを合成検証する |
| 既存データ | driver変更でschema/dataを移行しない。最初の既存DB確認は読取とschema readinessを使う |
| Portal | 全稼働revision/replicaで接続・ログイン・セッション・キー管理が成立。証明書認証の成果を維持する |
| Brokerと管理VM | Gateway DBとPortal DBへの必要操作と保守接続が成立。業務用MIの権限を拡張しない |
| Jobs | 各Jobの接続・正常終了・保存ログを確認。schema Jobのmigration・設定/購読・権限更新効果を把握し、通常配備と合わせて確認する |
| 実通信 | 新規物理接続のRedirectをdriver/接続情報と実socketの宛先・portで確認し、SQL PEのDNS/直接経路と照合する |
| 性能 | 同じclient・SQL・データ・同時実行条件でProxyとRedirectを比較。token取得・接続確立・pool再利用時のSQL処理を分けて測る |
| 再現性 | 初期構築とPE再作成後の最終policy、App/Job/管理client接続を別途確認し、通常更新で不要なProxy切替が起きない |
| 終了 | 一時的な診断設定・ログ・権限等を確認して回収し、未確認の削除・応答不明を完了扱いにしない |

policyの読戻し、TCP1433でのquery成功、`client_net_address`がprivate IPであることだけではRedirectを証明できない。新規物理接続の再接続先を確認する。収集可能なdriver/socket/session情報を使い、恒常的な診断設備や業務MIへの広い管理権限を追加しない。

Portal schema Jobは疎通専用ではなく、migrationのほか設定・購読・権限も更新する。完全なmigration/Job試験は独立した試験DBで先行し、既存環境でDDL Jobを繰り返し起動して接続試験の代わりにしない。定期Jobは自然実行または通常更新に伴う実行を確認し、作用と実行結果を区別する。

既存のpoolを使い続けた確認では旧接続方式が残り得る。Brokerのpyodbc pooling、Portalのpool、実行中Jobを把握し、安全なdrain・pool破棄・対象Appの更新で新しい物理接続を確認する。影響を限定し、接続試験のためのVM再構築や基盤全体の再配備を行わない。

## 切戻し

1. 共有SQLのpolicy子資源をProxyへ戻し、変更後の状態を読戻す。応答不明なら現在値と配備結果を調べ、推測で再送しない。
2. Portal/Brokerの接続poolと実行中Jobを安全にdrainする。この間はRedirect用のport許可を保持する。
3. driverに問題がある場合は、対応する設定と旧Portalイメージへ戻す。Portal側の証明書認証工程3を維持できる復旧版を先に準備し、DBデータを巻き戻さない。その後、App/Job/管理VMの新しい物理接続がProxyで成立することを確認する。
4. Redirect接続が残っていないことを確認してから、SQL4規則の許可を1433だけへ戻す。旧名へ戻す場合も優先度競合を避ける逆移行を使い、手動規則・既定規則・末尾Denyを保持する。
5. App/Job/管理VMの復旧と一時資源の回収を確認し、失敗した切替と復旧結果を別々に記録する。

変更前の状態、対象世代、ETag、適用結果、実宛先、イメージと設定の対応、性能の実測原本は、[AGENTS.md](../../AGENTS.md)に従いチェックアウト直下の`.local/<environment>/state/`・`evidence/`で保護する。公開文書へ実環境の値や実測原本を転記しない。

## 正式文書と残る工程

通信要件の`FLOW-ACA-SQL`/`FLOW-MGMT-SQL`、NSG4規則、SQL構築・保守・切戻し手順、[operationsのNSG](../operations/network-security-groups.md)、[SQL配備](../setup/credential-sql.md)、[構築試験](../testing/construction-tests.md)、[実証結果の管理](network-security-validation.md)を対応資材へ揃えた。Firewall設計にはSQL PE直接経路を明記し、通過しないSQL規則は追加していない。Portal固有のdriver・認証・Jobs手順と試験はPortal担当が更新し、APIM側が所有する総合rollout/integration資料は変更内容を共有して担当側で統合する。

残るのは、PortalのNode.js接続部品とtoken受渡しの成立・実装、Portal工程3とdriverの統合、Proxy受入とpool/Job drainの内部runtime verifier、Azure限定適用と実宛先・性能の確認、新規構築/PE再作成の実証である。SQL/NSGの合成検証済み資材を、未確認のPortalや既存環境へ先行適用しない。

## 参照する公開仕様

- [Azure SQL Private EndpointでのRedirect](https://learn.microsoft.com/en-us/azure/azure-sql/database/private-endpoint-overview?view=azuresql#use-redirect-connection-policy-with-private-endpoints)：PE用port、対応driver、PE作成後のpolicy条件。
- [Azure SQLの接続方式](https://learn.microsoft.com/en-us/azure/azure-sql/database/connectivity-architecture?view=azuresql#connection-policy)：Proxy/Redirectと公開接続のport要件。
- [Microsoft ODBC DriverのEntra認証](https://learn.microsoft.com/en-us/sql/connect/odbc/using-azure-active-directory?view=sql-server-ver17)：ODBC本体の認証とaccess token属性。Node接続部品からの受渡しは別に確認する。
- [msnodesqlv8](https://github.com/TimelordUK/node-sqlserver-v8)、[node-odbc](https://github.com/IBM/node-odbc)：候補接続部品の公開契約。採用済みの記載ではない。
