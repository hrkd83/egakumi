# 変更前後の共通確認

[運用手順](README.md) · [運用テスト](../testing/operations-tests.md)

Portal、APIM、Foundryを変更する担当者向けの共通手順。初期構築は[構築手順](../setup/06-deploy.md)、環境全体の撤去は[更新・撤去手順](../operations.md#撤去する)を使う。

## 同じ環境の入力と現物を確認する

構築したリポジトリルートで、同じ環境の保護入力を選ぶ。

```sh
export DEPLOY_ENVIRONMENT='<ENVIRONMENT_NAME>'
export DEPLOY_LOCAL_DIRECTORY="$(pwd)/.local/${DEPLOY_ENVIRONMENT}"
export DEPLOY_PARAMS_FILE="$DEPLOY_LOCAL_DIRECTORY/params.local.json"
export WORKFORCE_CONFIG="$DEPLOY_PARAMS_FILE"
```

1. Azure CLI・azdの認証先、paramsのTenant／Subscription・環境名・APIM識別子、`state/latest.json`と現在の資源IDを照合する。不一致なら変更を始めない。APIM識別子を運用更新のたびに再発行しない。
2. `.local/`・`.azure/`がリンクでないことを確認する。保護領域は所有者だけが参照できる権限で扱い、実情報をソース・アプリ配下の`.env`・Gitへ転記しない。
3. 今回のソースrevision・変更差分、稼働digest、変更対象の現在値・戻す値を記録する。保護入力・stateを破棄して最初からやり直さない。
4. 同じ環境に対する別の配備・回収・保守が動作中でないことを確認する。一般利用者のパスワードや本人証明を借りて管理操作を代行しない。

## 計画してから個別に反映する

| 対象 | 計画するコマンド | 反映 |
| --- | --- | --- |
| Portal（P） | `python3 -B scripts/operations/release.py --component portal --config "$DEPLOY_PARAMS_FILE"` | 同じコマンドへ`--execute`を付ける |
| Gateway／APIM（G） | `python3 -B scripts/operations/release.py --component gateway --config "$DEPLOY_PARAMS_FILE"` | 同じコマンドへ`--execute`を付ける |
| Foundry | [モデル変更手順](foundry.md)の`operations/model.py`で対象配備を変更する | 実物に合わせた設定を確認し、必要なG→Pを行う |

通常保守の入口は[`scripts/operations/`](../../scripts/operations/README.md)にまとめる。通常のP／Gの計画はローカル処理。既存の基盤配備CLIが持つ`--deployment-name`は運用入口へ追加せず、同じ保護stateを使う。

計画は完全なARM差分表示ではない。PはDB・権限・WAF等、GはSQL・CM・Broker・API・WAF等も扱うため、ソース・設定と現物の差分を確認する。予定外の変更や実行中処理があれば止める。通常の運用更新では基盤の`azd up`・`azd provision`を実行しない。

## 完了と復旧を確認する

1. 今回の終了コードと更新された配備記録、実際の稼働digest・設定を確認する。過去の成功JSONやヘルス200だけを業務成功にしない。
2. 変更対象の正常・拒否と、既存のPortalログイン・代表APIを確認する。入力・出力や認証値を通常ログへ出さない。
3. 応答不明なら現物・配備・ジョブ履歴を読み、実行中・完了・失敗を区別する。変更要求を無条件で重ねない。
4. 切戻しは確認済み旧ソース・旧設定・旧digestを使う。現在のDB・鍵との互換性を確認し、同じ個別コマンドを計画から実行する。変更対象外の値を一括で巻き戻さない。
5. 試験用の変更・追加配備・一時権限を回収する。実測と残件は`.local/<environment>/evidence/<run-id>/`へ保護保存する。画面や大容量ログの一括採取は必要ない。
