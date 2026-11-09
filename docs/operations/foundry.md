# Foundryモデルの容量・版・配備を変更する

[共通確認](common.md) · [モデル設定](../setup/05-config.md)

[モデル保守script](../../scripts/operations/model.py)で、構築済みFoundryの一配備を
対象に変更する。既定は実物を読む計画、`--execute`で反映する。
共通基盤の再配備や、全モデルの作り直しは行わない。実環境での受入と、
合成応答によるローカル回帰は分けて記録する。

## 正本と変更対象を決める

1. 共通手順でparams・Core state・認証先・APIM識別子を照合する。
   現在のモデル名・版・形式・SKU・容量・更新方針を保護記録へ控える。
2. paramsの`configFile`が参照するconfig正本の`services.modelDeployments`で、
   `name`が完全一致する一件を選ぶ。モデル設定はconfigの担当項目であり、
   paramsへ`services.modelDeployments`を追加する方法は使わない。
3. 提供地域・利用枠・購入条件を確認し、変更後と戻す値を先に決める。
   代表的な容量変更は小さい増分だけにする。capacityの単位やTPM換算は
   モデル・SKUごとに異なり、全モデル共通の単位とは扱わない。
4. 正本の対象項目を変更し、次の計画で現在値との差を確認する。
   配列の順番や同じモデル名だけで対象を選ばず、変更前の一件を丸ごと保存する。

## 容量だけを変更する

正本の対象`capacity`だけを採用値へ変更する。モデル名・形式・版・SKU・
自動更新方針を同時に変えない。次は今回選んだ配備名を指定する。

```sh
python3 -B scripts/operations/model.py --config "$DEPLOY_PARAMS_FILE" \
  --name '<DEPLOYMENT_NAME>' --intent update
python3 -B scripts/operations/model.py --config "$DEPLOY_PARAMS_FILE" \
  --name '<DEPLOYMENT_NAME>' --intent update --execute
```

scriptはstateに記録したFoundryの親ID・所有RGと、対象配備のID・種類・状態を
確認する。容量だけならSKUのPATCHを使い、モデル・RAI・spillover・tags等を
PUTで置き換えない。実行中・未知の配備状態、モデル名・形式・SKU・更新方針の
不一致があれば変更しない。

計画・要求受付・読戻し結果は、環境別`state/operations/`へ保護保存する。
stdoutの要約だけを今回の受入証拠にせず、`applied`と実容量を確認する。
HTTP受付だけでは成功とせず、期限内の`Succeeded`と設定一致を要求する。
ETagが返れば条件付き要求を使い、直前の比較も行う。API側の原子的な競合防止を
保証したものとはせず、外部GUIを含む同時保守を避ける。

容量だけの変更にG／Pは不要。変更前と同じキー・配備名・API契約で小さい合成要求を
一度実行し、モデル応答と対象外の代表要求を確認する。設定上限の反映、小さい要求の
成功、性能・TPM・利用枠の実測は別に記録する。

## 版変更・新規配備を明示する

| 作業 | 正本の変更 | CLIの指定 |
| --- | --- | --- |
| 既存モデルの版変更 | 対象の`modelVersion`。既存のモデル名・形式・SKU・更新方針は保持 | `--intent update --allow-version-change` |
| 別名の新規モデル配備 | 完全な新しい一件を`services.modelDeployments`へ追加 | `--intent create` |

どちらも同じ引数で計画した後に`--execute`を付ける。版変更・作成は一対象へのPUTであり、
既存配備を一旦削除しない。`create`は既存名の上書きを拒否する。モデル名・形式・SKUを
既存配備の別用途へ変更する場合は、別名配備で切替方法を確定する。
非既定のdynamic throttling等、版PUTで安全な保持を表現できない設定がある場合は停止する。

モデルの実配備だけでAPIの許可やPortal掲載が増えるわけではない。追加・版変更の採用時は
次の正本も確認し、必要な変更をローカル検査後のG→Pで反映する。

| 確認するもの | 正本・確認先 |
| --- | --- |
| 許可する配備名・操作・モダリティ | `apps/gateway/apim/apis/`の対象API・profile |
| モデル別上限・契約の根拠 | `apps/gateway/apim/model-capabilities.json` |
| Portalの表示・ガイド・試用 | API定義からの生成カタログと`apps/portal/lib/model-catalog.ts`等 |

新しいAPI方式や表示実装が必要なモデルは、その改修の検証を先に行う。モデル全種・全版を
追加する試験へ広げず、現在のAPI・Portalで扱える対象を選ぶ。
Gはモデル設定と実物を照合するが、その前にBroker・SQL等も処理するため、
不一致の負例を作るためにGを実行しない。

## 失敗・再開・元への復旧

応答不明・未完了・利用枠不足では変更値を反映済みと記録しない。変更要求は自動再送しない。
同じ配備IDと操作履歴を読み、進行中なら完了を待つ。要求が成功している場合は実物と正本を
照合し、再作成しない。`create`を重ねて既存資源へ上書きする復旧は使わない。

容量を戻す場合は正本の対象`capacity`を控えた旧値へ戻し、同じ`update`を計画・実行する。
旧版へ戻す場合も提供・利用枠と互換性を確認し、旧版の正本と明示オプションで実施する。
成功状態・旧値・代表要求まで確認し、戻せない場合は現在値と残件を記録する。

公開停止はAPIの許可とPortal掲載を先に止め、参照がなくなってから対象配備だけを
Foundryの管理画面等で削除する。このscriptに削除の意図はない。親Foundryや他モデルを
削除して戻さず、提供終了した版・解放済み利用枠を設定だけで復元できると扱わない。

[Microsoftのモデル配備API](https://learn.microsoft.com/en-us/rest/api/microsoftfoundry/accountmanagement/deployments/create-or-update?view=rest-microsoftfoundry-accountmanagement-2025-06-01)
と[容量更新API](https://learn.microsoft.com/en-us/rest/api/microsoftfoundry/accountmanagement/deployments/update?view=rest-microsoftfoundry-accountmanagement-2025-06-01)も参照する。
