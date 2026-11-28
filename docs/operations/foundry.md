# Foundryモデルの容量・版・配備を変更する

[共通確認](common.md) · [モデル設定](../reference/configuration/config-options.md)

`prod`・`stg`・`dev` の初回構築は、各モデルを最小容量で配備する。構築後の割当は、各サブスクリプションで承認されたクォータと用途を確認し、Microsoft Foundryの画面で配備ごとに調整する。 [初期容量とクォータ](../reference/configuration/capacity/foundry.md)

## 容量だけを変更する

1. Azure Portalの対象Foundryリソースでサブスクリプションとリソースグループを照合する。Microsoft Foundryポータルで **New Foundry** をオンにし、同じ環境のプロジェクトを選ぶ。 [Microsoft Foundryポータル](https://ai.azure.com/)
2. **Manage** → **Quota** を開く。標準配備のTPM割当は **Token per minute** で、対象モデル・版・SKUの割当、残り枠と **Scope** を確認する。共有枠の場合は、他の配備や環境の利用分も含めて変更値を決める。現在値と変更値は環境別の保護記録に控える。 [クォータの共有範囲](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/quotas-limits#check-quota-management-scope)
3. 対象の詳細を開き、**Affiliated deployments using shared quota** で変更する配備名を照合する。**Actions** の鉛筆アイコンから容量を入力して保存する。モデル・SKUごとに、画面が示す単位と許容範囲に従う。 [Microsoftの割当変更手順](https://learn.microsoft.com/en-us/azure/foundry/how-to/quota#view-and-request-quotas-in-foundry-portal)
4. クォータ画面を更新し、配備の割当が指定した値になったことを確認する。Azure OpenAIの割当変更は反映に最大15分かかることがある。公開済みのAPIは同じ配備名で小さい合成要求を送り、応答を確認する。設定値、API疎通、負荷試験の結果は別々に記録する。 [反映時間とレート制限](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/quota#view-and-request-quotas-in-foundry-portal)

## 版変更・新規配備を明示する

共通手順に従い、環境のparams、基盤記録、対象Foundryと配備名を照合する。現在のモデル・版・SKU・容量と変更内容を保護記録に控える。
[モデル保守スクリプト](../../scripts/operations/model.py)は対象を一配備ずつ照合する。既定で計画を作成し、同じ引数に `--execute` を付けて反映する。

| 作業 | 正本の変更 | CLIの指定 |
| --- | --- | --- |
| 既存モデルの版変更 | 対象の`modelVersion`。既存のモデル名・形式・SKU・更新方針は保持 | `--intent update --allow-version-change` |
| 別名の新規モデル配備 | `modelDeployments` に配備名・モデル・版・SKUを記載した項目を追加 | `--intent create` |

版変更・作成は一対象へのPUTであり、
既存配備を一旦削除しない。`create`は既存名の上書きを拒否する。モデル名・形式・SKUを
既存配備の別用途へ変更する場合は、別名配備で切替方法を確定する。
非既定のdynamic throttling等、版PUTで安全な保持を表現できない設定がある場合は停止する。
版変更の計画では運用中の容量が引き継がれることを確認する。単独の `create` でも、`azd up` と同じようにAzureが返すモデル・SKUの最小容量で新規配備する。最小容量が確認できない場合は、モデル・版・SKUの提供状況と対象環境のクォータを確認する。

計画・要求受付・読戻し結果は、環境別`state/operations/`へ保護保存する。期限内の`Succeeded`と設定一致を確認する。同じ配備へのポータル操作とスクリプトの反映は時間を分ける。

モデルの実配備だけでAPIの許可やPortal掲載が増えるわけではない。追加・版変更の採用時は
次の正本も確認し、必要な変更をローカル検査後のG→Pで反映する。

| 確認するもの | 正本・確認先 |
| --- | --- |
| 許可する配備名・操作・モダリティ | `apps/api-gateway/apis/`の対象API・profile |
| モデル別上限・契約の根拠 | `apps/api-gateway/model-capabilities.json` |
| Portalの表示・ガイド・試用 | API定義からの生成カタログと`apps/portal/lib/model-catalog.ts`等 |

新しいAPI方式や表示実装が必要なモデルは、その改修の検証を先に行う。モデル全種・全版を
追加する試験へ広げず、現在のAPI・Portalで扱える対象を選ぶ。
Gはモデル設定と実物を照合するが、その前にBroker・SQL等も処理するため、
不一致の負例を作るためにGを実行しない。

## 失敗・再開・元への復旧

応答不明・未完了・利用枠不足では変更値を反映済みと記録しない。変更要求は自動再送しない。
同じ配備IDと操作履歴を読み、進行中なら完了を待つ。要求が成功している場合は実物と正本を
照合し、再作成しない。`create`を重ねて既存資源へ上書きする復旧は使わない。

容量を戻す場合は、ポータルで対象配備の割当を記録した旧値に設定する。
旧版へ戻す場合も提供・利用枠と互換性を確認し、旧版の正本と明示オプションで実施する。
成功状態・旧値・代表要求まで確認し、戻せない場合は現在値と残件を記録する。

公開停止はAPIの許可とPortal掲載を先に止め、参照がなくなってから対象配備だけを
Foundryの管理画面等で削除する。このscriptに削除の意図はない。親Foundryや他モデルを
削除して戻さず、提供終了した版・解放済み利用枠を設定だけで復元できると扱わない。

[Microsoftのモデル配備API](https://learn.microsoft.com/en-us/rest/api/microsoftfoundry/accountmanagement/deployments/create-or-update?view=rest-microsoftfoundry-accountmanagement-2025-06-01)
と[容量更新API](https://learn.microsoft.com/en-us/rest/api/microsoftfoundry/accountmanagement/deployments/update?view=rest-microsoftfoundry-accountmanagement-2025-06-01)も参照する。
