# 公開TLSとPortal署名証明書のJob

公開TLSの初回発行・定期更新と、PortalのEntra署名証明書の管理を、専用の内部Azure Container Apps環境で行います。Jobごとに実行IDを分け、イメージ取得だけに使うIDも分けます。公開TLSのACMEアカウントと証明書は公開TLS用Key Vaultへ、Portalの非エクスポート署名鍵はアプリ用Vaultへ保存します。 実行結果と排他用のBlobコンテナーも用途別で、各Jobには自分のコンテナーだけを割り当てます。端末が実行結果を確認するときは、管理VMに対象コンテナーの読取権限を一時付与し、取得後に回収します。

## 空環境から構築する

1. [通常の初回構築](../setup/08-build-infra.md)に従い、公開DNSゾーンの委任と、そのゾーンを検証するDNS用IDを準備します。DNS-01で使うTXTレコードは公開入口より先に作成できます。
2. 初回は`azd up`を実行します。`azd provision`でも同じ基盤の構築工程を実行できます。基盤では管理VM、非公開ACRとPrivate Endpoint、Key Vault、証明書用の専用ACA環境、状態保存用Storageを作ります。初回構築前に、VNet内で証明書専用ACA用の/27を確保します。
3. 端末から既存管理VMへRun Commandでイメージ作成を指示します。初回イメージ作成や検証済みソースの変更時は、管理VMが一時的な `AcrPush` でPrivate Endpoint経由で既存ACRへ登録します。ACRの公開アクセス、信頼サービスの例外、管理者アカウントは無効のままです。イメージのdigestを確定してから、ビルド時の一時権限とRun Commandを回収します。初回の標準NSGではSSHは未開放です。運用中に再ビルドする場合は通常のSSH保守と時間を分けます。
4. `postprovision` が固定digestのJobを開始します。対象の実行が成功し、両公開ホストをカバーする証明書の登録を確認した後、AppGWとWAFを配備します。両ホストに対応する証明書が配信されていることを確認してから公開DNSを設定します。
5. Portal署名証明書とGateway OBO署名証明書の公開部分は保護された生成出力へ保存します。Gateway OBO用は同じ専用環境の手動Jobで固有の鍵とチェックポイントを使い、GatewayのBrokerに対象鍵の署名だけを付与します。Entra登録は[APIとPortalの証明書登録手順](../setup/09-egakumi-portal.md)に従います。

Jobの結果と排他用Blobは、公開TLSとPortalで別のコンテナーへ保存します。各実行IDは自分のコンテナーにだけ書き込めます。管理VMが結果を取得する際は、対象コンテナーの読取を一時的に付与し、結果取得後に回収します。

Private DNSで、管理VMと証明書用ACAの双方からACRのログイン名とリージョン別データ名がPrivate Endpointへ解決することをAzure受入で確認します。管理VMのpushとACAのpullは別々に確認します。標準ACRは組み込みの`AcrPush`／`AcrPull`を使う権限モードと、ACAが要求するARMオーディエンスのトークンを使います。既存ACRが別のモードやトークン設定なら基盤配備前に停止します。 [ACRのPrivate Link](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-private-endpoints)

## 再開と更新

- 初回発行やイメージ登録が中断した場合は、同じ環境の保護stateを保持して再実行します。開始応答が不明な場合は既存の実行を照合します。同じ環境を別端末から同時に構築・更新しません。ACMEの新規注文が受理されたか不明で、注文の識別子がまだ永続化されていない場合は、注文を重ねず調査のために停止します。
- 公開TLSの定期更新はACAのスケジュールで起動します。手動確認・更新・同期の入口は [公開TLS証明書の運用](secrets-and-certificates.md#tls証明書を更新し公開httpsまで確認する) です。既定では12時間ごとに確認し、有効期限30日前から更新を試みます。計画更新と定期実行はBlobリースで調整します。
- AppGWのKey Vault参照はバージョンなしです。Key Vaultの新しい版と実際に両ホストが配信する版を別々に確認します。Jobの失敗、長時間未実行、証明書の期限を監視します。 [AppGWの証明書更新](https://learn.microsoft.com/en-us/troubleshoot/azure/application-gateway/troubleshoot-application-gateway-key-vault-certificate)
- [Portal署名証明書の更新](portal-authentication.md)は、候補を発行し、Entraへ公開証明書を登録し、切替・確認を経て旧鍵を無効化します。このJobを開始すると実行テンプレートを変更できるため、開始・変更権限は証明書運用担当へ限定します。通常のVM保守にJobの起動権限は含めません。 [ACA Jobの開始権限](https://learn.microsoft.com/en-us/azure/container-apps/jobs#permissions)
