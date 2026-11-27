# Gatewayの利用資格・例外発行キー・本人接続プールを保守する

既定の接続は、クライアントが本人の `Gateway.Invoke` アクセストークンを取得して送信するBearer接続です。Portalも本人のBearerで試用します。[APIアプリ登録](../setup/02-entra-api.md) · [Portalアプリ登録](../setup/09-egakumi-portal.md)

## 1. APIMで例外の購読キーを発行する

CoreはAPIMのDeveloper Portalを無効にします。基盤管理者はAzureポータルのAPIM管理画面で購読を管理します。 [APIMの構成仕様](https://learn.microsoft.com/en-us/azure/templates/microsoft.apimanagement/2024-05-01/service)

1. 基盤管理者は、Bearerを利用できない理由、利用アプリ、必要なAPI、管理担当、利用終了の予定を確認する。
2. Azureポータルの対象 **API Management → API → サブスクリプション → サブスクリプションの追加** を開く。対象のAPIまたは必要なAPIだけを含む製品をスコープに選ぶ。
3. 作成した購読の用途と管理担当を保護された運用記録に記載し、利用アプリには承認された秘密保管先を通じてキーを渡す。キーを通常のログ、URL、チケット、クライアント配布物に書かない。
4. 対象APIで利用できることと、対象外APIが拒否されることを確認する。不要になった購読はAzureポータルで停止し、利用先の終了を確認してから削除する。

同じ購読の主キーと副キーは利用範囲を共有する。計画更新は使用していない側のキーを再生成し、利用アプリを切り替えて疎通を確認した後、旧キーを再生成する。漏えい時は事故対応の手順に従い、対象のキーまたは購読を停止する。[Microsoftの購読作成手順](https://learn.microsoft.com/ja-jp/azure/api-management/api-management-howto-create-subscriptions)

## 2. 必要に応じてAPIMで上限を設定する

標準資材は購読キーの共通リクエスト上限を設定しない。APIM製品に属する購読へ同じ上限を掛ける場合は、Azureポータルで対象製品の受信ポリシーに `rate-limit` を追加する。購読ごとに別の上限が必要なら、APIMのAPIまたは共通ポリシーで検証済みの `context.Subscription.Id` を使い、対象購読へ `rate-limit-by-key` を適用する。ProductスコープだけではAPIスコープ等の購読に適用されないため、実際の発行スコープで確認する。[rate-limit](https://learn.microsoft.com/ja-jp/azure/api-management/rate-limit-policy) · [rate-limit-by-key](https://learn.microsoft.com/ja-jp/azure/api-management/rate-limit-by-key-policy)

Bearerの既定は、検証済みテナントIDとユーザーのObject IDの組を単位として、同じGateway上で60秒あたり1,000要求。対象のcancel・hangupなどには例外があり、標準ではトークン上限を適用しない。[設定一覧](../reference/config-options.md) · [利用量と検証](../testing/subscription-limits.md)

## 3. CMプールを増設し、定期ジョブを点検する

### 既存プロファイルのプール増設

1. 同じ環境のparamsの`credentialProviders.graphDelegated`登録、configの同slotの`pool.count`・`pool.capacity`、既存の物理provider名、SQLプールの容量・割当数を確認する。追加providerは双方の`additional[]`を論理名で照合する。現在の入力範囲は個数1～26・各容量1～8,000である。保管枠と同時処理性能は別である。
2. 必要数に合わせてconfigの対象providerの`pool.count`だけを増やす。論理プロファイル名、既存の接尾辞付き物理名、容量、Client ID・secret参照、APIM識別子は維持する。通常の増設でEntraアプリや戻り先を作り直さない。
3. Gの計画で追加先と既存資源の保持を確認し、同じ入力で実行する。Gateway一式の照合を伴い、CMだけの専用配備ではない。

```sh
python3 -B scripts/operations/release.py --config "$DEPLOY_PARAMS_FILE" --component gateway
python3 -B scripts/operations/release.py --config "$DEPLOY_PARAMS_FILE" --component gateway --execute
```

4. 今回の終了コード・`state/gateway-deployment.json`とSQL初期化ジョブの成功を確認する。APIMの追加provider・必要なアクセス権、SQLの追加プール行、Brokerの許可先が揃い、既存本人の接続ID・割当数と代表的な本人API利用が保持されていることを確認する。
5. 新規割当先は各プールの負荷率等で選ばれるため、増設先を選ばせる目的で既存プールを埋めない。追加先に接続・予約ができた後は、`count`を元へ減らすだけの切戻しをしない。追加先を保持して調査し、縮小や既存接続の修復をこの増設作業へ混ぜない。

### 既存の毎時照合ジョブを点検する

Azure Portalの**Container Apps Jobs**で対象環境の`caj-<stem>-recon`を選び、配備記録と完全な資源IDを照合する。現在のスケジュールは`17 * * * *`、[Container AppsのcronはUTC基準](https://learn.microsoft.com/en-us/azure/container-apps/jobs#scheduled-jobs)である。Brokerと同じ採用イメージ・実行MI・許可pool設定と、直近の実行結果を確認する。スキーマ初期化の手動ジョブと取り違えない。

通常は既存の定期実行を観測する。実行中に重ねて開始せず、成功／失敗と、対象実行の集約件数・残件を確認する。同じジョブは60日未使用・解除待ち接続の回収も行い、ログインや有効セッションがあるだけでは未使用期間を延長しない。CM不存在とSQLの同一世代確認前に枠を手動で減らさない。

ログは対象実行と狭い時間範囲で読む。到達待ち・取得不能を処理失敗と即断せず、ジョブ成功だけでも対象接続の削除完了と判断しない。件数増加や確認不能が続く場合は対象と時刻を保護記録へ残して調査へ引き継ぐ。この通常点検のために利用日時変更・削除故障・競合を注入する必要はない。
