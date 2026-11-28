# 共通基盤の構築資材

[main.bicep](main.bicep) が `azd up` の入口です。[main.bicepparam](main.bicepparam) は構築フックが生成した入力を渡します。事前登録用の名前とリダイレクトURIは [registration-names.bicepparam](registration-names.bicepparam) から計算します。

| ディレクトリ | 担当する資材 |
| --- | --- |
| [config](config/) | 検証・本番の標準設定と、標準設定・合成済み設定に対応するJSON Schema。初回構築では準備コマンドで選んだprofileの標準値を参照 |
| [network](modules/network/) | VNet、[サブネットの命名](modules/network/subnet-names.json)、Firewall、経路、Private Endpoint、[NSGの現行ルール](modules/network/nsg/) |
| [ingress](modules/ingress/) | App Gateway、WAF、公開TLS参照。初回発行後に [public-ingress.bicep](modules/ingress/public-ingress.bicep) で構築 |
| [services](modules/services/) | APIM、Foundry、業務用Key Vault、共通SQL、サービス間の権限 |
| [container-apps](modules/container-apps/) | PortalとCredential Brokerの実行環境、共用ACR、内部DNS |
| [certificates](modules/certificates/) | 証明書用ID、Vault、実行環境、ネットワーク、[初回発行と更新のJob](modules/certificates/certificate-jobs.bicep) |
| [management](modules/management/) | 管理VM、保守時のロール、OS監査、シリアル接続と保守クライアントの準備 |
| [monitoring](modules/monitoring/) | ログ送信、監査BlobとそのPrivate Endpoint、アラート |
| [guest](guest/) | 管理VMで実行するスクリプトと証明書Jobイメージに入れるワーカー |

空のAzure環境に [正式手順](../docs/setup/08-build-infra.md) で構築します。この資材での構築を再開するときは、NSGカタログが対象とするサブネットの作成済みルールを読み取り、現行の定義と照合します。規則名・通信条件が一致しない場合は、基盤のネットワーク配備に進む前に停止します。

導入先固有の設定は[paramsの例とSchema](../params/)を基に、環境別の保護paramsに記入します。 [入力手順](../docs/setup/07-params.md)

GatewayのAPI・ポリシー・アプリ配備は [apps/api-gateway/deploy](../apps/api-gateway/deploy/)、Portal本体の配備は [apps/portal/deploy](../apps/portal/deploy/) にあります。
