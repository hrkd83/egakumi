# 運用テスト仕様書

[通常保守の手順](../operations/README.md) · [構築・統合テスト](README.md)

このプロダクトの運営、エンハンス、不具合修正に必要な通常作業を確認する。対象は、既存の操作入口で行えるアプリ・API・モデル・認証情報・証明書・利用資格・通信保護・監視・容量の保守である。各行を一連の作業として判定し、正常・異常・復旧の機械的な行分割は行わない。

設定変更はJSON等の資材編集と`scripts/operations/`のスクリプトを基本とし、ポリシーXMLの手編集は試験の前提にしない。購読キーは基盤管理者がAzureポータル上のAPIMで操作する。

## 対象を選ぶ基準

- ソース・設定の変更が、実行中のサービスへ正しく反映されることを確認する。
- 期限や担当者交代で必要になるsecret・証明書・キー・権限の更新を含める。
- 通常の運営で使う監視・購読の管理・プール増設・容量調整を含める。
- 各作業で必要な検査・停止判断・戻し方を確認し、構築・統合テストの権限組合せ、全モデル、全WAF要求、並行競合を再網羅しない。

大規模な障害訓練、SQL全体の復元訓練、新しい管理機能の開発はこの試験へ含めない。個別配備の失敗時は、既存の状態確認・再開・切戻し手順を使う。

## 開始前に揃えるもの

1. 初期構築済みの専用検証環境、対象コミットと仕様の版、params・参照config・Core stateを固定する。再構築が必要なら[構築手順](../setup/08-provision-core.md)で1回作成し、Gateway・Portalの基準動作を確認してから開始する。
2. [共通確認](../operations/common.md)で認証先・環境・APIM識別子・現物を照合する。構築後の運用更新に`azd up`・`azd provision`を使わず、同じ環境の更新を並行実行しない。
3. 変更前のソース、稼働digest、API・WAF・モデル・容量・通知設定、グループ所属を必要範囲で保存する。旧イメージと現在のDBの互換性を確認する。保護入力・stateの正規バックアップと保存先も確認する。
4. Portalの一般利用者、Portal管理者、Azure運用担当、対象Entraアプリ・グループの管理担当を用意する。権限を兼ねる場合も、操作主体を記録する。サインイン・MFA・新secretの保管・通知受信を本人が行える時間を確保する。
5. 一般利用者が所有する専用購読1件、主副キーを切り替えられる試験用クライアント、本人接続、代表モデル1件、受信を合意した通知先を用意する。キー・token・値は保護した秘密管理先から読み、通常ログへ出さない。
6. 変更する項目と値、正常要求・拒否要求、戻す値、継続採用する値を実施票へ固定する。代表モデル・増設pool・容量等の利用可能性は、開始前に確認する。実情報は`.local/<environment>/`内だけで扱う。

## 通常保守の試験項目

表は業務単位の14シナリオである。1行の中で、必要な事前検査・実反映・業務確認・終了処理を確認する。複数作業をまとめて配備しても、各IDの確認結果を分ける。

| ID | 普段行う作業と手順 | 確認すること・終了時の扱い | 結果 |
| --- | --- | --- | --- |
| OPS-01 | [更新・引継ぎの準備](../operations/common.md)：正規の保護入力・state・ソースを確認してP／Gを計画する | 認証先・現物との一致、今回の変更範囲、旧digest・戻す値を確認する。不一致の隔離入力は変更前に止め、元の入力を破壊せず計画し直す | PASS |
| OPS-02 | [Portalの改修・FIXをリリース](../operations/portal.md)：代表画面の文言・ガイド等を小さく変更する | 変更に必要なlint・テスト・buildを通し、Pで更新、稼働digestと実画面・代表APIを確認する。不正候補はローカルで止め、確認済み旧版への切戻しも行う | PASS |
| OPS-03 | [API定義・ポリシーを修正](../operations/apim.md)：Chatの検証モデルの本文上限を変更して、生成されるポリシーを反映する | Gで実反映し、4096／4097 bytesの正常・413拒否、APIポリシーの実値、未認証拒否を確認する。旧定義・ポリシーへ戻し、購読・他APIを保持する | PASS |
| OPS-04 | [Foundryモデルを保守](../operations/foundry.md)：既存配備の容量を小さく変更する | `scripts/operations/model.py`でconfigの採用値を反映し、実容量を揃え、同じGateway・Portal要求を確認する。容量だけならG／Pは不要。旧容量・configへ戻す。モデル追加・版変更時の公開先整合も手順で確認する | PASS |
| OPS-05 | [共用Entra client secretを更新](../operations/secrets-and-certificates.md)：`entra_secret.py`のcreate→switch→retireを新旧併存で実施する | 保護ファイル、共有KVの全対象、全CM provider、Broker・Portal・同期jobの参照を揃える。新規認証を確認して旧secretを廃止し、その後も認証成功を確認する。新値を継続採用する | PASS |
| OPS-06 | [TLS証明書を更新](../operations/secrets-and-certificates.md)：`certificate.py --stage renew --rotate`で既存workerの計画更新を行う | 新しいKV版・SAN・期限、APIM／AppGWの同期、公開HTTPSの新leafと信頼を確認する。timerと通常APIを保持し、作業用タグを戻す。新証明書を継続利用する | PASS |
| OPS-08 | [購読キーを段階更新・停止](../operations/access-and-keys.md) | Azureポータルで限定した購読を発行し、スコープ外の拒否、主副の切替、旧値と停止後の拒否を確認する | 要確認 |
| OPS-10 | [CMプール増設・定期jobの点検](../operations/access-and-keys.md)：pool数を1つ増やす | GでCM・権限・SQL・Brokerの追加先を揃え、既存接続を保持する。既存毎時jobのイメージ・MI・直近結果・集約ログを点検する。増設先は稼働中に縮小せず、環境撤去まで保持する | PASS |
| OPS-11 | [WAFの限定反映](../operations/platform-settings.md)：`operations/waf.py`で対象profile・パス・ルールを計画・反映する | 正常入力と関係する攻撃対照、Prevention・対象外除外・参照経路を確認する。既存の誤検知がない場合は現在の承認済み除外を再適用し、差分なしと正常・拒否を確認する。新たな誤検知を修正した結果とは区別する | PASS |
| OPS-12 | [バックエンドと外向き許可を整合](../operations/platform-settings.md)：共通公開メタデータを返す専用APIを1つ追加する | API定義と必要な固定FQDN・HTTPS443をGで反映・照合し、認証付き応答・未認証拒否・既存APIを確認する。既存許可の再利用と新規追加を区別し、専用API等だけを明示回収する | PASS |
| OPS-13 | [監視の設定変更・運営時のログ確認](../operations/platform-settings.md)：`monitoring.py`で既存通知先を照合し、アラート1規則の閾値を変更する | scope・条件・宛先・正本を揃え、合意した宛先でテスト通知を1回確認する。代表要求の集約・相関ログを狭い期間で確認する。閾値を旧値へ戻し、大量発報を作らない | PASS |
| OPS-14 | [サービス容量を調整](../operations/platform-settings.md)：代表としてBrokerの最小replicaを1段階増やす | config正本と実設定・revision・digest、ヘルスと本人APIを確認する。最大値の範囲内で調整し、旧値へ戻す。最大負荷や障害を発生させない | PASS |

### 管理VMの一時保守権限

初期構築した環境で、担当者本人への付与前、作業中、回収後を順に確認する。今回のAzure実環境確認は未実施。

- [管理VMの本人接続](../operations/management-vm-access.md) に従い、作業中だけ対象VMにEntra SSHで接続できること、sudo権限を持たないこと、割当回収後に再接続できないことを確認する。作業終了時にSSHと残存セッションを閉じる。
- [SQLの本人保守](../operations/management-sql-access.md) に従い、許可したDBと操作だけが成功することを確認する。接続を終了してDB権限を回収し、VMに接続したまま新たなSQL接続が拒否されること、スキーマ用IDの管理者復元とEntra専用設定を確認する。
- Azure RBACでは監査コンテナー、Workspace、対象ACAの操作のうち、割り当てた操作とスコープだけで成功することを確認する。本人の保守権限を回収した状態で、証明書の自動更新と監視のログ転送が継続することを確認する。

### API・モデル変更の代表条件

`OPS-03`は、`chat-completions.modelSettings[検証モデル名].maxRequestBytes`の現在値または未指定の状態を記録し、隔離した試験だけ4096へ変更して、UTF-8本文全体が4096／4097 bytesの正しいJSONを使う。本文の`model`に同じ検証モデル名を指定し、profileヘッダーは付けない。操作の基本値とほかのモデルの差分は維持する。現行の標準値は500 MiBで、試験後は記録した元の値または未指定へ復旧する。下表の過去PASSを、容量緩和や設定構造変更後の再検証結果として扱わない。共通認可・要求ID・`<base />`の継承を維持し、制約から生成される実ポリシーを確認する。XMLを扱う共通管理ブロックの改修は[開発手順](../../DEVELOPMENT.md#apim共通ポリシーを改修する)へ分け、この代表試験には含めない。

`OPS-04`は容量変更を実測の代表とする。追加モデルや版変更の全組合せを実行する項目ではない。実モデルとconfigの不一致検出は、`tests/test_publish_apis.py`の`PublicationTests.test_model_deployment_and_scoped_role_prerequisites_are_checked_before_firewall_changes`等の偽ARM応答でローカル確認する。Gの計画は実モデルを読み取らず、G実行のAPI公開検査までにBroker・SQL等も配備されるため、不一致入力を実環境へ渡して全書込0の試験にしない。

### NSGの初期構築と再開

[NSGの初期仕様](../operations/network-security-groups.md)と、構築後の実ルールを照合し、環境別の保護実施票へ結果を記録します。下記は現在の受入条件で、各環境の実測前にPASSとはしません。

各規則の通信対応と採用判断は[通信要件](../reference/network-communication-requirements.md)・[NSG設計](../reference/network-security-design.md)・[Firewall設計](../reference/firewall-policy-design.md)、合成結果と実証原本の管理は[共通検証](../reference/network-security-validation.md)を参照します。

- 各サブネットのカスタム規則を通信要件・名称・方向・優先度・送信元・宛先・ポート単位で比較する。AppGWとPEの送信で評価する既定規則と、ACA両方向の明示Denyを区別する。
- 新規構築と中断後の再実行が同じ現行規則の集合を再現することを確認する。未知の規則・条件のずれ・所有不一致があればCoreのネットワーク配備前に停止する。
- 廃止したNSG／標準DNS／NTP入力と`gateway.grpc`全体・gRPC API定義を、params・config・合成入力で拒否する。VNet／FirewallはAzure提供DNSを使い、APIMの外部NTP UDP123と管理VMのAzureホストPHC同期を区別する。
- 管理VMはUbuntu24.04のChrony、`/dev/ptp_hyperv`のcharacter device、`clock_name=hyperv`を要求し、PHC／`AZUR`を唯一sourceとして時間を限定したwaitsync・選択・`Normal`同期を確認する。既知Ubuntu外部NTP経路と完全一致する旧管理ファイルだけを移行し、未知の企業時刻設定・変更済み旧ファイルは変更前に停止する。サービス・同期確認失敗時の元設定への復旧を合成条件で検証する。
- 既存VMは時刻専用moduleを単独先行適用し、SSH／rsyslogを変更しない。所有・現行script全文一致・RunCommand成功と`configured`／`azure-host-phc`／`timeSynchronized: true`／`timeOnly: true`を読取gateで確認してからCoreへ進む。新commandが404の場合だけ現行full audit成功へfallbackし、新commandの失敗や進行中を隠さない。VM作成とcommand開始／終了のUTC時刻を照合し、前VMインスタンスの成功証跡を拒否する。未適用・旧結果・失敗はCore前停止、厳密な整数HTTP404だけが新規構築へ進むことを確認する。先行成功を現在精度の保証へ読み替えず、現在source／`Normal`／offsetを実VMで別に確認する。
- 専用運用CLIのinspect原本・VM binding・入力/Core不変を確認し、旧CoreでvmIdが欠落していても最初の現世代を固定する。receiptの再開で追加PUTしないことと、新しい読取と過去結果の再確認の区別を検証する。旧NSG325または260の既知1件とFW管理NTP1件だけを撤去し、非対象・readonly size・未知条件・競合・応答不明を合成確認する。実環境の結果は保護証跡で管理する。
- PHC同期成立後に旧管理NSG325の完全一致tupleをETag付きで限定削除し、所有FW collectionの旧管理NTPだけを撤去して読戻しする。NSG・Firewallの規則正本と、APIMのNSG328／UDP123維持を照合する。管理VMのVMBus／PHCへUDP123／319／320の許可を追加しない。旧102PASS／76 NSG／36 FWの記録は旧条件として保持し、今回の合格へ転用しない。今回のAzure反映・実VM同期は未実施である。
- AppGWの専用L4入口・公開8443・gRPC許可を撤去し、通常APIM／ACA向けのHTTPS443と管理VMのHTTPS443保守を確認する。APIMの旧所有customProxyだけを除去し、native FQDN／Host／SNIでHTTPS保守できることを確認する。
- ACAの受送信の末尾Deny、公衆宛TCP443のFirewall経由、未許可の新規接続拒否と、イメージ取得・起動・スケール・監視を確認する。[受入条件](../reference/network-security-validation.md#実環境の受入確認)
- 所有ETag、保護された変更journal、受理後の読戻し、部分完了からの再開、再実行時の無変更を確認する。APIMの他custom domain・customProperties、WAF・認証・認可・監査・CM・所有者署名を維持する。
- 新規接続で通常API、Portal転送、SSE／WebSocket／MCP、公開TLS、L7入口証明不備の拒否を確認する。設定読戻し、認証拒否、実推論・ストリーミング成功を分けて記録する。

## 実施順とまとめ方

1. 共通準備、ソース・設定変更、ローカル検査、秘密・証明書更新の前提を先にまとめる。不具合は一覧にして同系統ごとに直し、独立した確認は続ける。
2. 新secretとKV、モデル容量等の準備を行い、計画済みのAPI・ポリシー・プール増設・Broker容量・Portal変更をG→Pの1組へまとめる。各変更の確認項目を個別IDへ対応付ける。
3. TLS workerと参照同期、キー・所属・同期画面、WAFの限定反映、監視確認を進める。同じ資源を更新する操作は重ねず、secret取得・ログ取り込み等の待機中は別の読取・記録を行う。
4. 新規認証と通常利用を確認して旧secretを廃止し、もう一度確認する。途中の未確認がある場合は旧secretを廃止しない。
5. 一時的なソース・API・容量・所属・監視設定を戻し、必要なG→旧digest指定Pの反映をまとめる。専用API・不要になった追加宛先・試験キー・一時権限を回収して最終確認する。

新Entra secret・新TLS証明書・増設poolは、通常の更新・増設の成果として保持する。元のparams全体をコピーして、新しい有効secretや増設先まで巻き戻さない。専用環境の撤去は、結果確認と合意後に[正式な撤去手順](../operations.md#撤去する)を使う。

## 実施時間の目安

必要な権限・サインイン・通知受信の担当が揃い、構築済み環境が正常な場合、14シナリオの実測は合計3～4時間を見込む。項目ごとに30分を積み上げず、G／Pへの変更をまとめ、secretの参照更新・証明書同期・定期job・ログ取り込みの待機中に独立した確認を進める。

| 工程 | 経過時間の目安 |
| --- | --- |
| 変更計画・ローカル検査・更新前の確認 | 20～30分 |
| 変更をまとめた反映と並行確認 | 90～120分 |
| 切戻し・一時資源回収・最終確認と結果整理 | 60～90分 |

初期構築から必要な場合は構築に1.5～2時間、終了後の撤去には45～90分を別に見込む。構築・運用試験・撤去までの全体では約6～8時間が目安である。Azure側の処理遅延や、新たな不具合の修正・再反映に要する時間はこの目安と分けて報告する。

## 判定と記録

結果は未実施／実施中／PASS／FAIL／ブロック／合意した対象外を使う。机上確認は実装・CLI・手順・ローカル回帰の整合確認であり、実配備のPASSではない。実施中は実施票と表を区切りごとに更新する。PASSには、その行で定めた確認と終了処理がすべて完了した項目だけを含める。

実施票は`ID / 対象・担当 / revision・正本 / 変更前後 / 操作・期待 / 実結果 / 継続採用・切戻し・回収 / 判定・残件`を持つ。実測値を含む票と必要最小限のテキスト証跡は`.local/<environment>/evidence/<run-id>/`へ保護保存する。画像・動画や大容量ログは追加採取しない。
