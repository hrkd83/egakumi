# 基盤テスト

リポジトリルートで実行します。新規構築、同じ資材での再試行、構築後の更新・撤去を合成データで検査します。環境への反映と実接続の確認は、対象環境の受入手順で行います。 [依存関係と実行手順](../DEVELOPMENT.md#テストの実行方法) · [環境の受入確認](../docs/setup/13-verify-deployment.md)

```sh
python -B -m unittest discover -s tests -t . -v
python -B -m unittest discover -s tests/gateway/publication -t . -v
python -B -m unittest tests.network.test_ingress_bootstrap -v
```

`-t .` を指定すると、追加したテストも `tests` パッケージとして読み込まれます。BicepとPowerShellの実行ファイルは `BICEP_CLI` と `WORKFORCE_PWSH` で指定できます。

| フォルダー | 検査対象 |
| --- | --- |
| [deployment/](deployment/) | 新規構築、Gateway配備、更新、再試行、撤去。入力とSchemaは [configuration/](deployment/configuration/) |
| [network/](network/) | App GatewayのHTTPS入口、Firewall・NSG、Private Endpoint、ルートとDNS |
| [certificates/](certificates/) | 公開TLSとPortal署名証明書の発行・更新・復旧、ACME状態の保全 |
| [management/](management/) | 管理VM、ゲスト構成、時刻・SSH、保守クライアント、権限 |
| [gateway/publication/](gateway/publication/) | API・Product・APIMポリシーの配備、読戻し、再試行 |
| [gateway/policies/](gateway/policies/) | APIMポリシーによる認可、ルーティング、入出力と各APIの契約 |
| [waf/](waf/) | WAFの生成・適用範囲・運用時の保護と検証クライアント |
| [identity/](identity/) | Entra登録と認可、Graph OBO、Credential ManagerとBrokerの本人接続 |
| [portal/](portal/) | Portal配備、認証、GatewayへのBearer試用、ローカル起動 |
| [audit/](audit/) | 監査ログ、本文の保存、ログ基盤・診断設定 |
| [clients/](clients/) | REST・MCP・音声・ブラウザーなどの検証クライアント |
| [repository/](repository/) | 公開情報・シークレット検査とGitフック |
| [support/](support/) · [fixtures/](fixtures/) | 共通のPython fixture、ローカルBicep・PowerShellの実行補助、合成入力 |

[browser/](browser/) はRealtimeのローカル検証画面です。Broker、本文監査アプリ、Portalアプリ固有のテストは各アプリの `tests/` にあります。 [アプリのテスト実行手順](../DEVELOPMENT.md#ローカル確認)
