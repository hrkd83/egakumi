# Azure Container Appsの容量

[3環境の初期配備値](../environment-capacity.md)

Brokerの最小・最大レプリカ数はparamsの `capacity.credentialBroker` で指定し、CPU・メモリは[サービス別config](../../../../infra/config/capacity/container-apps.json)で管理する。 [容量の入力項目](../parameters.md#環境ごとに確定する容量)

CPU・メモリは1レプリカあたり。レプリカ数の範囲は常駐アプリの最小〜最大。Jobは実行ごとの並列度を示す。Portal・Broker・証明書はそれぞれ専用のWorkload Profiles環境でConsumptionプロファイルを使う。監査の2行は今後の実装時に使う負荷試験の候補値。 [ACAのスケーリング](https://learn.microsoft.com/en-us/azure/container-apps/scale-app) · [ACA Jobs](https://learn.microsoft.com/en-us/azure/container-apps/jobs)

| 機能 | `prod` | `stg` | `dev` | 容量を決める試験 |
| --- | --- | --- | --- | --- |
| Portal公開前の初期アプリ | 0.25 vCPU・0.5 GiB、1レプリカ | 同左 | 同左 | 初回構築からPortal個別配備への切替 |
| PortalのAPIカタログ・試用 | 0.5 vCPU・1 GiB、1〜2 | 同左 | 同左 | 閲覧、同時ログイン、手動試用。現行値を維持 |
| Portal認証SQLのスキーマJob | 0.5 vCPU・1 GiB、並列1、上限600秒 | 同左 | 同左 | 初回スキーマ作成と再実行 |
| Credential Brokerの業務API | 1 vCPU・2 GiB、**5〜10** | 同CPU・メモリ、1〜2 | 0.5 vCPU・1 GiB、1〜2 | OBOとCredential Managerを別々に計測。通常ピークで1レプリカ停止、ストレス試験前に必要数を確保 |
| Broker SQLのスキーマJob | 0.5 vCPU・1 GiB、並列1、上限600秒 | 同左 | 同左 | 初回スキーマ作成と再実行 |
| Brokerの照合・回収Job | 0.5 vCPU・1 GiB、毎時、並列1、上限1,800秒 | 同左 | 同左 | 本番想定の登録数で定期照合が完了し、業務APIとSQLが同時に動作することを確認 |
| 公開TLSの発行・更新Job | 1 vCPU・2 GiB、並列1、上限3,300秒 | 同左 | 同左 | 初回発行、DNS待ち、更新、再実行時の排他 |
| Portal認証・Gateway OBOの署名証明書Job | 1 vCPU・2 GiB、並列1、上限900秒 | 同左 | 同左 | 用途別に実行。初回作成と更新。公開TLSとは別Job |
| 監査中継のYARP（将来） | 2 vCPU・4 GiB、**25〜50**を試験候補にする | 同CPU・メモリ、平時1〜2 | 同CPU・メモリ、1〜2 | 業務経路全体で500／1,000 RPS、同時SSE 12,000／24,000本、15分SSE、監査キューの個別検証 |
| 非同期の検査ワーカー（将来） | 1 vCPU・2 GiBの常駐アプリ、**2〜16**を試験候補にする | 同CPU・メモリ、平時1〜2 | 同CPU・メモリ、1〜2 | Event Hubsのバイト数・イベント数、検査サービスの並列・利用枠、滞留の回復。長期保管の第一候補はEvent Hubs Capture |

現行のアプリ・Jobの値は資材を参照する。Portalと各Jobは表に示す固定値を使う。Brokerの最大レプリカ数はスケール上限のため、負荷試験前に必要数の起動を確認する。 [Portalの初期アプリ](../../../../infra/modules/container-apps/portal-platform.bicep) · [Portalの配備](../../../../apps/portal/deploy/application.bicep) · [Portal認証SQL Job](../../../../apps/portal/deploy/schema-job.bicep) · [BrokerのアプリとJob](../../../../apps/credential-broker/deploy/broker.bicep) · [証明書Jobs](../../../../infra/modules/certificates/certificate-jobs.bicep)

**Portal**はAPIカタログ・ログイン・手動試用の容量を持つ。CodexやClaude CodeはGatewayへ直接接続するため、Portalの容量にはGatewayの500 RPSを割り当てない。

**Broker**はGraphなどの本人接続を使う業務要求で、応答を待つ経路に入る。GPTやClaudeの推論だけならBrokerは0回。現行GraphのBearer経路ではOBO交換を1回行い、BrokerがEntraへ交換を要求し、アプリ署名が必要なときはKey Vaultを使う。Credential Managerの個人接続ではセッションの事前認可と使用直前の検証でBrokerを通常2回呼び、両方でSQLを使う。Gatewayのモデル以外の要求を通常100 RPS・ストレス200 RPSとしたとき、全件がOBOならBrokerは100／200 RPS、全件が個人接続なら200／400 RPS。2経路を別々に試験し、接続開始・更新・再試行の負荷も別に加える。 [Graph OBOポリシー](../../../../apps/api-gateway/policies/snippets/authentication/graph-obo/graph-obo.xml) · [Brokerの接続契約](../../../../apps/credential-broker/README.md)

Brokerの試験開始値は、1レプリカが50 RPSで応答時間と成功率の基準を満たすという仮定。通常200 RPSには4レプリカと停止時の予備1、ストレス400 RPSには8レプリカを要するため、最小5・最大10から始める。1レプリカと複数レプリカで処理能力を計測する。通常ピークの99.9%以上の正常完了、経路別p95/p99、SQLとKey Vaultを含む依存先の容量を合わせて確認する。照合Jobは30,000件の合成登録を起点に業務負荷と同時に実行し、30分以内に完了することを確認する。 [Credential Managerの制限](https://learn.microsoft.com/en-us/azure/api-management/credentials-overview#limits)

**監査中継**は1レプリカの試験から始める。1レプリカあたり同時SSE 500本を検証用の仮値に置くと、通常12,000本で24と予備1、ストレス24,000本で48と予備1になる。RPS・帯域・監査処理が先に上限へ達する場合は、計測した処理能力で再計算する。アプリのレプリカに加え、ACAの管理ingress自体の処理能力と長時間SSEを測る。負荷試験では必要数を事前に起動し、同時SSEの実数を計測する。検査ワーカーは受信23／45 MB/sを起点とし、1レプリカの処理量、パーティションの並列度、製品のAPI利用枠、障害復旧時の滞留から必要数を決める。 [ACA ingressの構成](https://learn.microsoft.com/en-us/azure/container-apps/ingress-environment-configuration) · [Event Hubsの容量](https://learn.microsoft.com/en-us/azure/event-hubs/event-hubs-scalability)

中継と検査ワーカーを同じ環境でConsumptionに置く場合、表の最大値でアプリ合計116 vCPU。リビジョン切替で新旧を同時に動かす分も含め、環境ごとのコア割当とサブネットを確認する。 [ACA環境のクォータ](https://learn.microsoft.com/en-us/azure/container-apps/quotas)
