# 管理VM内で実行する共通基盤の資材

`guest`はAzure管理VMのゲストOSを指します。ここには、共通基盤の構築と運用でVM内に渡して実行するソースを置きます。

| ファイル | 役割 | 呼出し元 |
| --- | --- | --- |
| [certificate-bootstrap.sh](certificate-bootstrap.sh) | Ubuntuの運用ツールを準備し、固定版PowerShellで証明書workerを起動 | [certificate.bicep](../modules/certificate.bicep) |
| [certificate.ps1](certificate.ps1) | Azure DNSによるTLS証明書の取得・検査、Key Vaultへの保存・復旧、定期更新の登録 | 初回はbootstrap、以後はVM内のsystemd timer |
| [configure-audit.sh](configure-audit.sh) | 時刻同期、認証ログ、SSHの基本設定 | [management-audit.bicep](../modules/management-audit.bicep) |
| [prepare-management-client.sh](prepare-management-client.sh) | 管理VM用保守client、固定依存、対象inventoryの検証・配置 | [management-maintenance-client.bicep](../modules/management-maintenance-client.bicep) |

Bicepはこれらの本文を埋め込み、Azure Managed Run CommandがLinux管理VM上でrootとして実行します。証明書workerは初回設定後も同じソースを使って更新します。保守clientはCoreの初期構築と、Gateway／Portalが構築した対象のinventory更新で同じBicepから準備します。認可と実際のデータ取得は、準備後に[管理VMの保守手順](../../docs/operations.md#管理vmから保守データを取得する)で確認します。

操作端末で使うCLIと配備の制御は[scripts/](../../scripts/README.md)にあります。管理VM上で行うアプリ専用のイメージ構築は、[Gateway](../../apps/gateway/deploy/build-image.sh)・[Portal](../../apps/portal/deploy/build-image.sh)それぞれの配備資材に置きます。
