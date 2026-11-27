# 運用手順

構築後の変更は[対象環境と記録の照合](common.md)から始めます。作業内容に応じて次の手順を選んでください。

| 作業 | 手順 |
| --- | --- |
| Gateway・Portalの個別配備、失敗した配備の再開 | [構築後の更新と配備の再開](deployment.md) |
| API・操作・ポリシーの変更 | [APIMのAPI定義とポリシー](apim.md) |
| AzureポータルからClaudeモデルを公開 | [Claude Messages APIの追加](claude-messages.md) |
| Portalの画面・ガイド・コードの修正 | [Portalの改修と切戻し](portal.md) |
| Foundryのモデル・容量・版の変更 | [Foundryモデル管理](foundry.md) |
| 例外の購読キー発行・上限設定、CMプールの増設 | [購読キーと本人接続プール](access-and-keys.md) |
| 本人接続の定期処理、SQL台帳と障害復旧 | [Credential Brokerの運用](credential-broker.md) |
| Entra client secret・公開TLS・DNS用IDの更新 | [認証情報と公開TLSの更新](secrets-and-certificates.md) |
| PortalのEntra署名証明書の更新 | [Portal認証証明書](portal-authentication.md) |
| 公開TLSとPortal署名証明書のJobを確認 | [証明書Job](certificate-jobs.md) |
| WAF・外向き通信・監視・サービス容量の調整 | [基盤設定](platform-settings.md) · [API Center](platform-settings.md#api-centerのカタログ管理) · [NSG](network-security-groups.md) |
| 管理VMへの接続・権限の付与と回収・Serial Console | [管理VMへの本人接続](management-vm-access.md) |
| 管理VMからの監査・ログ取得、ACA exec、SQL保守 | [管理VMの保守操作](management-client.md) · [SQLの権限と接続](management-sql-access.md) |
| 利用を終了し、所有資源を撤去 | [環境の撤去と再構築](teardown.md) |

初回構築は[構築手順](../../README.md#新規クローンから構築する)へ進みます。配備・更新の受入は[運用テスト](../testing/operations-tests.md)から作業に合うケースを選び、ローカル確認と実環境の結果を分けて記録します。利用できる運用コマンドは[スクリプト一覧](../../scripts/operations/README.md)を参照してください。
