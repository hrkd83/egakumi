# 構築後の更新と配備の再開

## 同じ環境を更新する

[対象環境と記録](common.md)を確認し、変更対象に応じた入口を選びます。

| 変更対象 | 実行する入口 |
| --- | --- |
| 共通基盤の設定 | [WAF・外向き通信・監視・容量](platform-settings.md)から対象を選び、変更後に現物と構築入力を合わせます |
| Foundryのモデル | [モデル運用](foundry.md)に従い、配備・容量・版を変更する |
| Gatewayのみ | `python3 -B scripts/operations/release.py --component gateway --config "$DEPLOY_PARAMS_FILE"`で計画を確認し、`--execute`で反映 |
| Portalのみ | `python3 -B scripts/operations/release.py --component portal --config "$DEPLOY_PARAMS_FILE"`で計画を確認し、`--execute`で反映 |
| 両アプリを順に更新 | `python3 -B scripts/operations/release.py --component all --config "$DEPLOY_PARAMS_FILE"`で計画を確認し、`--execute`でGateway→Portalを実行 |

初回配備と基盤構築途中の再試行は[構築手順](../setup/08-build-infra.md)に従ってください。構築後のGateway・Portal更新は上表の入口を使い、保護された基盤の配備記録を参照します。計画はローカルで生成し、反映する場合は同じコマンドに `--execute` を付けます。

アプリ配備の入口 `deploy_gateway.py` と `deploy_portal.py` で `--deployment-name` を明示すると、計画時も指定した成功済み基盤配備の出力をAzureから読み取ります。この指定はアプリ配備に使います。

Gatewayは、採用時のBrokerイメージ作成・DB／CM配備・SQL初期化・起動確認の後に、API・Product・ポリシー・WAFを反映します。Portalはイメージ作成・DB／権限・SQL初期化を経て同じContainer Appを更新し、公開`/api/health`を確認します。イメージは管理VMで作成し、基盤内の非公開ACRへ登録します。確認済みのソースとdigestが一致する場合は既存のビルド結果を再利用します。

更新前に、対象revision、params・非秘密の設定hash、現在のアプリrevision・digest、必要なDBバックアップ、API／ポリシーの変更点を記録します。SQL初期化・migration失敗時はアプリや後続APIの公開へ進みません。復旧は原因を直して同じ入口を再実行し、自動ロールバックが完了したものとして扱いません。

構築入力へ戻す基盤変更は、NSG・UDR・APIMホストと証明書・外向き許可の現在値を確認し、次回配備の入力に反映してください。企業ネットワーク変更時は[ハブ接続手順](../setup/10-hub-spoke.md)も確認します。変更後の公開API実行・Portalログイン・本人接続・ログ到達は [動作テスト](../testing/integration-tests.md)で別途確認します。Gatewayの`status: deployed`は業務APIの`runtimeVerified`を合格へ変更するものではありません。

API・Product・WAFを変更する場合は[APIMの運用](apim.md)を確認してください。

## アプリ配備の途中で失敗した場合

1. 終了した工程・終了コード・配備記録と、現在のAzure上の状態を確認する。
2. 同じparamsと`.local/<environment>/state/latest.json`を保持して原因を修正する。SQLやイメージ構築の失敗を飛ばさない。
3. Gatewayで停止した場合はGatewayを再試行し、その成功後にPortalを実行する。Portalで停止した場合はPortalの専用コマンドから再開できる。
4. アプリの稼働・公開ヘルス・イメージdigestと、変更対象の業務動作を確認して記録する。

同じAPI範囲を更新する途中でAPIMのARM配備が `Failed` になり、`state/gateway-api-closed-stage.json` がある場合は、原因を解消して次のコマンドを使います。同じコード・params・基盤の配備記録と、前回成功時の `state/gateway-api-source.json` が必要です。APIM上のポリシー、ルーティング、Productの現物を照合し、Bearer対象のProductが閉じたままであることを確認してから、API配備とGatewayのWAF設定を再開します。

```sh
python3 -B scripts/deployment/deploy_gateway.py --config "$DEPLOY_PARAMS_FILE" --execute --resume-closed-stage
```

初回配備でAPIMの子資源が部分的に作成された場合や、製品の関連付け・ポリシーに照合できない変更がある場合は、自動再開を停止します。Azure側の配備操作とローカルの保護記録を照合して復旧範囲を確認します。
