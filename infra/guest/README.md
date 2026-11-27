# Azure内で実行するスクリプト

| ファイル | 役割 | 実行元 |
| --- | --- | --- |
| [certificate.ps1](certificate.ps1) | 公開TLS証明書の発行・更新、専用Vaultへの保存と復旧 | 証明書用Container Apps Job |
| [portal-auth-certificate.ps1](portal-auth-certificate.ps1) | Portal・Gateway OBOの認証用鍵と公開証明書の作成、検査、切替 | 証明書用Container Apps Job |
| [configure-audit.sh](configure-audit.sh) | 管理VMの時刻同期、認証ログ、SSHの基本設定 | [管理VMの監査設定](../modules/management/management-audit.bicep) |
| [initialize-management-serial.py](initialize-management-serial.py) | 管理VMの初期シリアル接続を準備 | [シリアル初期化](../modules/management/management-serial-initialization.bicep) |
| [prepare-management-client.sh](prepare-management-client.sh) | 管理VM用保守クライアントと参照する資源一覧の配置 | [保守クライアントの準備](../modules/management/management-maintenance-client.bicep) |

証明書ワーカーは [証明書Jobのイメージ構築処理](../../scripts/deployment/helpers/certificate_image.py) が組み込みます。管理VM用スクリプトはBicepに埋め込まれ、Managed Run Commandで実行されます。操作と認可は [管理VMの保守手順](../../docs/operations.md#管理vmから保守データを取得する) を参照してください。
