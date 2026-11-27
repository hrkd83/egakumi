# 変更前後の共通確認

[運用手順](README.md) · [運用テスト](../testing/operations-tests.md)

## 対象環境と記録を確認する

構築に使用したクローンで、同じparams・参照config・azd環境を使います。新しいターミナルでは次を設定し直します。

```sh
cd '<構築に使用したクローン先ディレクトリ>'
export DEPLOY_ENVIRONMENT='<ENVIRONMENT_NAME>'
export DEPLOY_LOCAL_DIRECTORY="$(pwd)/.local/${DEPLOY_ENVIRONMENT}"
export DEPLOY_PARAMS_FILE="$DEPLOY_LOCAL_DIRECTORY/params.local.json"
export WORKFORCE_CONFIG="$DEPLOY_PARAMS_FILE"
```

Azure CLI・azdの認証先、`.local/`内の環境名、paramsのTenant／Subscription・環境名、stateの所有記録と対象5RGを照合します。不一致のまま続行せず、別環境のstateや秘密を流用しません。同じ環境の構築・更新・撤去は実行順を調整してください。保護領域のリンクと所有者限定権限を確認し、秘密値はコマンド引数・通常ログへ入力しません。

`services.apimInstanceId`は構築時の値を保持します。APIM名は`apim-<environment>-<product>-<apimInstanceId>`で、更新・途中再開・撤去では同じ完全な資源IDを照合します。

| 記録 | 用途 |
| --- | --- |
| `.azure/` | azd専用の環境と配備先の設定 |
| `.local/<environment>/params.local.json`・`secrets/` | 対象環境の入力と参照configの指定、秘密・SSH鍵 |
| `.local/<environment>/state/latest.json` | 共通基盤の配備ID・outputs・所有資源・監査保護・ACA管理RGの記録 |
| `.local/<environment>/state/gateway-deployment.json`・`portal-deployment.json` | 各アプリの配備結果・イメージdigest・確認範囲 |
| `.local/<environment>/state/credential-broker-build.json`・`portal-build.json` | ビルドしたソースとイメージの対応、再利用・一時資源の回収結果 |
| `.local/<environment>/evidence/<run-id>/` | 試行ごとの実測・証跡。配備完了と業務動作の合格を分けて記録 |

Gitからクローンし直しても`.azure/`・`.local/`は復元されません。構築時の環境別領域とazd環境を、別端末への引継ぎや障害に備えて保護保管します。公開用の資材コピーや配布物へ混ぜません。既存の記録を削除して再試行したり、名前からResource IDを推測してstateを作ったりしません。

## 計画してから個別に反映する

変更前にソースrevision・設定値・稼働digestを記録し、必要なDBのバックアップを確認してください。変更する操作に応じて[個別配備](deployment.md#同じ環境を更新する)または[対象別の運用手順](README.md)から計画を作ります。計画と現物を照合し、対象外の差分や進行中の操作がある場合は実行前に確認します。個別配備の計画は完全なARM差分表示ではありません。

## 完了と復旧を確認する

1. 今回の終了コードと更新された配備記録、実際の稼働digest・設定を確認する。過去の成功JSONやヘルス200だけを業務成功にしない。
2. 変更対象の正常・拒否と、既存のPortalログイン・代表APIを確認する。入力・出力や認証値を通常ログへ出さない。
3. 応答不明なら現物・配備・ジョブ履歴を読み、実行中・完了・失敗を区別する。変更要求を無条件で重ねない。
4. 切戻しは確認済み変更前のソース・設定・digestを使う。現在のDB・鍵との互換性を確認し、同じ個別コマンドを計画から実行する。変更対象外の値を一括で巻き戻さない。
5. 試験用の変更・追加配備・一時権限を回収する。実測と残件は`.local/<environment>/evidence/<run-id>/`へ保護保存する。画面や大容量ログの一括採取は必要ない。
