# Portal証明書認証の構築・受入試験

状態：工程3の下記ローカル確認を実施済み。実環境の試験は予定項目であり、成功記録ではありません。工程4のAPI用OAuthとTry接続は別設計・別受入です。[実施計画](../reference/portal-stages-3-4-plan.md)と[手動Entra登録](../setup/entra-portal.md)を参照します。

SQLはProxy維持、Redirect変更とPortal driver試作は取りやめと決定しました。既存Tedious/Kysely・SQL用MI認証を維持し、証明書認証の受入を独立して進めます。Networkの取消コミット`5f72d83`をローカルへ取り込み、SQL/NSGと証明書資材の保持を確認しました。以下の172試験は旧Redirect案のローカル統合時の履歴であり、取消後の資材を確認した結果ではありません。

## ローカル確認結果

| 対象 | 結果 |
| --- | --- |
| 証明書state・運用段階 | Python 14テスト成功 |
| Bicep生成・初期worker実行・署名権限 | 18テスト成功 |
| KV応答を置換した証明書検証 | PowerShell 12ケース成功。自己署名の改変、公開鍵不一致、exportable/private JWK、現行鍵廃止を拒否 |
| Portal認証・Graph・APIキー管理 | TypeScript 85テスト成功 |
| 配備・初期構築hook・ローカル準備 | 関連Python回帰を実施し、失敗項目の個別再実行で解消。Windowsの権限不足・対応外の試験は未実施扱い |
| 公開データ検査の回帰 | 65テスト実行、61成功、Windowsでリンク作成権限がない4件は未実施 |
| 型検査・lint・build | 型検査成功、lintのエラーなし（既存警告あり）。buildはNext.jsの更新推奨で初回停止し、案内に従った再実行で成功 |
| 依存監査 | Next.js由来のmoderate検出3件が残る。監査時は修正版なし。build再実行は脆弱性の修正を意味しない |
| 公開境界・文書 | ローカル作業ツリー混入検査成功。参照先を照合し、既存の構築試験と変更対象外の総合計画・総合試験書を保持 |
| 旧Redirect案とのローカル統合（履歴） | Core・PortalのBicep生成を含む関連172試験を実行し、168成功、Windowsリンク権限が必要な4件は未実施。混入検査のexact pathも両コミットの集合を照合して保持。APIM資材の統合とAzure反映は未実施 |

これらは合成データ・ローカルコンパイルの結果です。Azureでの鍵発行、Entra登録、ログイン、更新・復旧の実受入、および既知のNext.js脆弱性への対応は残ります。

### Proxy取消後のローカル確認

Networkの取消コミット`5f72d83`を取り込んだ後、9スイート102試験を確認しました。初回は101成功、証明書保存の1件でエラーでした。合成fixtureの親ディレクトリ作成がPOSIXの700権限を満たしていなかったため、fixtureだけを正式な`private_parent`へ合わせました。権限検査は緩めず、証明書の14試験をWSLの通常一時領域で再実行して全件成功しました。修正後の対象102試験に未解決の失敗・エラー・スキップはありません。

| 対象 | 確認内容 |
| --- | --- |
| `test_portal_auth_certificate`、`test_portal_authentication_infra` | 初期公開証明書・state・更新段階、Core/PortalのBicep生成、初期workerと署名ID・権限 |
| `test_management_client_assets`、`test_management_client_assets_bicep` | Proxyを要求する照合と限定配備、管理client・network・credential networkのBicep生成 |
| `test_deploy_applications` | 初期構築のGateway-only入口とPortal個別配備の分担 |
| `test_nsg_rule_priorities`、`test_fixed_nsg_migration`、`test_fixed_nsg_upgrade`、`test_nsg_deployment_order` | 固定NSG集合・SQL向け4規則のTCP1433・既存移行と配備順 |

Proxy取消の統合時点で、`infra/`、`scripts/`、`apps/`の実装は証明書改修済みコミット`511575f`と一致することを確認しました。その後の既存環境向け初期発行入口の追加は、次節の別検証です。Redirect専用module・CLI・manifestと残存参照を除去し、Portal用の公開データ許可定義を保持しました。これらは合成検証であり、Azure操作・SQL実接続・証明書発行・Entraログインの受入ではありません。

### 限定追加入口と全体構築の確認

承認済みの3部品限定追加方式を、証明書運用CLIの`initialize`へ実装しました。新規構築の標準Core・hookも維持し、`tests/test_portal_certificate_initialization.py`の21試験を含む7スイート133試験をWSLの通常一時領域で実行しました。132成功、macOSの一時領域alias専用の1件は対象外でスキップ、未解決の失敗・エラーはありません。初回の新設試験では、保存済みmetadataを時刻付きfixtureの再生成値と比較した1件が失敗したため、保存前のmetadataとの比較へ修正して回帰を実行しました。

| 対象 | 確認内容 |
| --- | --- |
| 新規全体構築のBicep | Coreが限定追加と同じ3テンプレート・計4資源を条件付きで呼び出す。専用Entra入力なしで証明書名・有効期間を渡し、TLS/runtime・KV通信/RBAC・監査の準備へ依存する。初期Run CommandのIDを出力 |
| 正式`postprovision`と実collector | collectorを置換せず合成ARM応答で公開`.cer`と初期stateを保存。再実行はcurrentを保持。初期発行失敗は成功扱いせず停止し、Core記録保持・生成秘密入力回収を確認。違うcurrentへの上書きを拒否 |
| 限定追加と再開 | 既定計画でPUTなし、実行で3配備のみ。what-ifの追加書込み・Modify/Delete・広い鍵権限・誤principal・別worker・欠落/重複/診断を拒否。不明な受理済みPUTを再送せず、公開証明書の保存失敗から再収集。Failedを自動再実行しない |
| 保護runtimeとcheckpoint | 明示した別runtimeの正式params/Core stateを読み込み、azd選択・リンク・権限・排他を照合。入力/source/VM principal変更と不正receiptを拒否。初期発行に無関係な更新用受入フラグを拒否 |
| 既存回帰 | `test_portal_auth_certificate`、`test_portal_authentication_infra`、`test_operations_common`、`test_local_paths`、`test_lifecycle`、`test_deploy_applications`。公開データ検査も成功 |

ARMがwhat-if結果URLへ付けるopaqueな`c/h/s/t` contextと、Subscription直下・Resources providerのlocation配下の結果パスに対応しました。同じ管理ホスト・Subscription、固定API版、既知パラメータの単一値だけを受け付けます。余分なパラメータ・空値・過大値・制御文字・外部ホスト・別Subscription・対象外パス・重複パラメータを拒否します。拒否URLの保護保存で不完全な`Mock` workspaceを使ったfixtureの1エラーは、具体的な合成Pathへ修正しました。修正後はwhat-if対象6試験と、全体構築のBicep/collectorを含む専用22試験がすべて成功しました。

さらに、ARMが空の権限配列を`null`として返し、what-ifで`principalType`を省略する場合に対応しました。対象の管理VM principalId、鍵scope、ロール参照とread/write権限は照合し、実配備のreadbackでは`ServicePrincipal`を必須にしています。空配列のnull表現、追加の広い権限・User種別の拒否、実readbackの厳密さを合成試験へ追加し、what-ifと限定追加・再開の対象17試験がすべて成功しました。この追加後の専用試験は23件です。

これは資材のコンパイルと合成応答によるローカル確認です。新規Azure環境での全体構築、管理VMの実runtime・KV発行、Entra登録・実ログインを実証した結果ではありません。既存環境向け入口のAzure実適用は最終what-ifの確認と判断後に行います。

管理VMの旧鍵版無効化に使うDataActionを`Microsoft.KeyVault/vaults/keys/update/action`へ修正しました。`keys/write`は管理プレーンのActionで、DataActionとして指定するとロール定義が拒否されます。Core生成で鍵単位のreadとupdate/actionだけを要求する回帰を追加し、限定追加・再開とPortal認証資材の関連42試験は全件成功しました。これはローカルの資材・合成試験の結果です。

## ローカルで確認する契約

| 試験 | 確認する内容 |
| --- | --- |
| `tests/portal-auth-certificate-offline.ps1` | 合成RSA鍵・公開証明書をメモリー内で用意。Self・非exportable・新鍵生成方針・公開鍵対応を照合し、他鍵、disabled、private JWK、version未固定、現行版廃止を拒否。保存intentから不明POSTを再送せず再開 |
| `tests/test_portal_auth_certificate.py` | 初期構築はClient ID不要、手動登録後の2項目入力、公開DERとthumbprint・期限・KV参照、単一worker出力、登録前切替拒否、不明VM PUTの再送抑止、手動受入と旧Entra削除の必要性 |
| `tests/test_portal_authentication_infra.py` | Bicepコンパイル、CoreとPortal本体の資源責任、専用ID、鍵scope権限、secretと証明書の設定、ジョブ・DB・Gateway既存設定 |
| `tests/test_portal_certificate_initialization.py` | 限定追加の4資源照合・応答不明PUTの再送抑止・保護runtime、標準Coreと同じ3部品、正式postprovisionからの初期証明書取得と失敗時停止 |
| `tests/test_deploy_applications.py` | 初期構築のGateway-only入口がPortal本体を呼ばない。個別配備・失敗・state選択 |
| Portalの認証・Graphテスト | client assertionの固定宛先・thumbprint・短命性・digest署名・秘密非露出、ログインのtid/oid、Graph管理契約、既存セッション・実行用SQL権限 |

ローカル試験ではAzure/KV/Entraへ通信せず、実値をfixtureへコピーしません。型検査・lint・build・依存auditと混入検査も確認します。試験に使った合成秘密鍵をファイルへ保存しません。

## 実環境の構築試験

| 順序 | 操作・観測 | 合格条件 |
| --- | --- | --- |
| 1 | Portalの`authentication`未入力で新規`azd up` | 共有資源・Gateway・Portal ACA初期サンプルが準備される。Portal本体の登録不足で初期構築を止めない |
| 2 | 管理VM Run CommandとKV現物 | KV/PE/DNS/RBAC/runtime準備後に専用workerが実行され、終了成功と生成完了を確認。Self/RSA3072/非exportable/reuse_key=false |
| 3 | 公開証明書と保護state | `.cer`の取得、version固定keyId、thumbprint・期限と現物一致。VM/ACA/ローカルへ秘密鍵/PFXが渡らない |
| 4 | 同じ初期構築を再実行 | 証明書のcurrentと鍵版が維持される。本体配備済みの場合は初期サンプルへ戻らない |
| 5 | 発行エラー・pending・応答不明 | 成功扱いせず停止。保存intentの照合で再開し、不明POST/PUTを重ねない |
| 6 | 手動Entra登録 | 専用アプリ作成・公開証明書・固定callback・権限・同意を一度の依頼にまとめて照合。新Portal secretを作らない |
| 7 | Portal本体の個別配備 | stateと登録結果を使用し、イメージ・DB初期化・権限・ジョブを準備。基盤を作り直さない |

既存NSG/FWのKV PE向けHTTPS443、Entra HTTPS443、管理VMツール配布先を実効経路とともに確認します。EntraからKVへの通信はありません。自己署名の発行に外部CA通信はありません。SQLはProxyを維持し、取消後のpolicy・SQL向けNSGのTCP1433と証明書工程の整合性を確認します。Redirectへの変更とdriver試作の受入は実施対象から外します。

## 認証・更新・復旧試験

| 対象 | 合格条件 |
| --- | --- |
| ログイン | 固定認証tenantへ認証コードを交換し、公開HTTPS callback・Secure Cookie・署名済みtid/oidを維持。必要な構成でAzure tenantと認証Entra tenantの相違も確認 |
| Graph管理 | 専用アプリのApplication tokenでグループ・ユーザー基本情報を照会。APIキー管理と所有者判定が維持される |
| 定期ジョブ | 本体と同じkeyId/thumbprintと専用署名UAMIで、実際のスケジュール実行が完了 |
| 署名権限 | 対象鍵scopeのみread/sign。誤ったID・鍵版・disabled鍵・KV不通で署名失敗を秘匿し、他方式へfallbackしない |
| 更新準備 | 運用入口から管理VMを実行し、現行と異なる公開鍵・新しい鍵版を生成。公開`.cer`だけを出力し、現行参照を維持 |
| 手動登録前 | candidateの切替を拒否。旧公開証明書・旧鍵を維持 |
| 更新切替 | 本体とジョブの固定参照・イメージを読戻し、実ログイン・Graph管理・定期実行を確認して受入 |
| 復旧 | 旧版有効期間中、保存した旧固定参照と同じイメージへ戻し、認証・Graph・ジョブを再確認 |
| 旧版廃止 | 受入と手動Entra旧証明書削除の後だけ、旧鍵exact versionを無効化。現行鍵名全体を削除しない |
| 旧Portal撤去 | 本体・ジョブの旧secret参照、Portal用途の旧secret読取割当、旧Portal専用callbackを利用先照合後に撤去。CMのsecret/callbackを維持 |

HTTPヘルス、設定の読戻し、ローカル署名テストをEntraの実ログイン成功と同一視しません。実施していない項目、失敗、対象構成不足は成功件数へ含めません。実環境の正確な出力・参照・証跡は`.local/<environment>/evidence/`へ保護保存し、公開文書には転載しません。
