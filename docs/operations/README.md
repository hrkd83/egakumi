# 通常運用・改修の手順

[共通確認](common.md) · [スクリプト一覧](../../scripts/operations/README.md) · [運用テスト](../testing/operations-tests.md)

このプロダクトを運営し、エンハンス・不具合修正を行う担当者のための手順。変更対象に応じて次の入口を選び、変更前の値・実反映・通常利用・終了時の扱いを確認する。

設定変更は、params・config・API定義・WAF manifest等のJSON資材を編集し、`scripts/operations/`のスクリプトで計画・反映することを基本とする。通常運用ではポリシーXMLを直接編集しない。共通ポリシーの処理自体を変える場合は[開発者向けの改修手順](../../DEVELOPMENT.md#apim共通ポリシーを改修する)を使う。利用者のキー管理・購読同期・Entra所属管理は、それぞれの既存GUIを使う。

| 普段の作業 | 手順 | 主な入口 |
| --- | --- | --- |
| 作業対象・保護入力・戻し方を確認する | [共通確認](common.md) | 同じparams・state・現物の照合、変更計画 |
| Portalの画面・ガイド・コードを直す | [Portal改修](portal.md) | ローカル検査→Portal個別配備（P） |
| API・操作・入力制約・ポリシーを直す | [APIM変更](apim.md) | 定義・生成ポリシーの検査→Gateway個別配備（G） |
| モデル・容量・版・公開先を変える | [Foundryモデル管理](foundry.md) | `operations/model.py`→参照config・API・必要なPortalを整合 |
| secretの期限更新・TLS証明書更新 | [認証情報・証明書の更新](secrets-and-certificates.md) | `operations/entra_secret.py`・`certificate.py`、新規認証・公開TLS確認 |
| 所属・利用条件・キー・購読・CM容量を保守する | [利用資格・キー・プール](access-and-keys.md) | `operations/api_key.py`、P／G、Entra・Portalの正規管理操作、既存job |
| WAF・外向き許可・監視・サービス容量を変える | [基盤設定の通常変更](platform-settings.md) | `operations/waf.py`・`monitoring.py`、G等の対象限定反映 |

運用テストはこの業務のまとまりから選び、各作業の必要な検査・変更・確認・戻し方を一連で確認する。権限組合せ・全モデル・全入力の網羅は構築・統合テストに分ける。

初期構築は[構築手順](../setup/06-deploy.md)、利用終了・撤去は[撤去手順](../operations.md#撤去する)を使う。更新のために環境を再構築しない。大規模な障害復旧訓練や新規管理機能を、本手順の試験のために追加しない。

手順の机上確認・ローカル検証と、実環境での運用試験は別に記録する。実測前の結果欄は未実施とする。
