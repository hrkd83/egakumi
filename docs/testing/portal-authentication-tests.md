# Portal証明書認証の構築・受入試験

[手動Entra登録](../setup/09-egakumi-portal.md)と[Portal個別配備](../setup/12-deploy-portal.md)の受入に使います。SQLはProxy構成です。

## ローカルで確認する契約

| 試験 | 確認する内容 |
| --- | --- |
| `tests/certificates/portal-auth-certificate-offline.ps1` | 合成RSA鍵・公開証明書をメモリー内で用意。Self・非exportable・新鍵生成方針・公開鍵対応を照合し、他鍵、disabled、private JWK、version未固定、現行版廃止を拒否。保存intentから不明POSTを再送せず再開 |
| `tests/certificates/test_certificate_jobs.py` | 専用Jobの配備と初回実行順序、非公開イメージ、対象実行と結果の照合、開始応答不明からの再開 |
| `tests/certificates/test_portal_auth_certificate.py` | 初期構築ではClient ID不要、登録後の入力と公開DERの照合、証明書Jobの同一操作IDでの再開、実行前の登録確認、切替・受入・旧証明書の削除確認 |
| `tests/portal/test_portal_authentication_infra.py` | Bicepコンパイル、CoreとPortal本体の資源責任、専用ID、鍵scope権限、secretと証明書の設定、ジョブ・DB・Gateway既存設定 |
| `tests/certificates/test_portal_certificate_initialization.py` | ACA Jobが保存した初期状態の読取り、必要なJobが未作成の場合の停止、明示した保護runtimeと環境の照合、排他 |
| Portalの認証・Graphテスト | client assertionの固定宛先・thumbprint・短命性・digest署名・秘密非露出、ログインのtid/oid、Graphの本人照合、既存セッション・実行用SQL権限 |

ローカル試験ではAzure/KV/Entraへ通信せず、実値をfixtureへコピーしません。型検査・lint・build・依存auditと混入検査も確認します。試験に使った合成秘密鍵をファイルへ保存しません。

## 実環境の構築試験

| 順序 | 操作・観測 | 合格条件 |
| --- | --- | --- |
| 1 | Portalの`authentication`を省略して`azd up`（`azd provision`でも同じCore工程） | CoreとPortalの初期Container Appが作成され、初回の公開証明書2枚を取得できる。Portalバックエンドのヘルスは本体の配備後に判定する |
| 2 | ACA証明書Jobの結果とKey Vault | 非公開ACRの承認済みイメージから専用Jobが実行され、Portal署名証明書の生成と終了成功を確認。Self/RSA3072/非exportable/reuse_key=false |
| 3 | 公開証明書と保護state | `.cer`の取得、version固定keyId、thumbprint・期限と現物一致。VM/ACA/ローカルへ秘密鍵/PFXが渡らない |
| 4 | 同じ初期構築を再実行 | 証明書のcurrentと鍵版が維持される。本体配備済みの場合は初期サンプルへ戻らない |
| 5 | 発行エラー・pending・応答不明 | 成功扱いせず停止。保存intentの照合で再開し、不明POST/PUTを重ねない |
| 6 | 手動Entra登録 | 専用アプリ作成・公開証明書・固定callback・権限・同意を一度の依頼にまとめて照合。新Portal secretを作らない |
| 7 | Gateway、Portalの順に個別配備 | stateと登録結果を使用し、Gateway成功後にPortalのイメージ・DB初期化・権限・ジョブを準備する。本体の公開ヘルスを確認する |

証明書専用ACA JobからのKV PEへのHTTPS、Portal本体からのKV PEとEntraへのHTTPSを実効経路とともに確認します。認証用証明書はKVが自己署名します。公開TLS向けのACME通信は公開TLS Jobの受入で確認します。SQLはProxyとSQL向けNSGのTCP1433を照合します。

## 認証・更新・復旧試験

| 対象 | 合格条件 |
| --- | --- |
| ログイン | 固定認証tenantへ認証コードを交換し、公開HTTPS callback・Secure Cookie・署名済みtid/oidを維持。Azureと認証用Entraは同じテナントを使う |
| Graph OBO | PortalのBearer試用から本人の `/me` が取得でき、別人・無効なトークンで取得できないことを確認 |
| 署名権限 | 対象鍵scopeのみread/sign。誤ったID・鍵版・disabled鍵・KV不通で署名失敗を秘匿し、他方式へfallbackしない |
| 更新準備 | 運用入口からPortal署名用のACA Jobを実行し、現行と異なる公開鍵・新しい鍵版を生成。公開`.cer`だけを出力し、現行参照を維持 |
| 手動登録前 | candidateの切替を拒否。旧公開証明書・旧鍵を維持 |
| 更新切替 | 本体の固定参照とイメージを読み戻し、ログインとBearer試用・Graph OBOを確認して受入 |
| 復旧 | 旧版有効期間中、保存した旧固定参照と同じイメージへ戻し、ログインとBearer試用を再確認 |
| 旧版廃止 | 受入と手動Entra旧証明書削除の後だけ、旧鍵exact versionを無効化。現行鍵名全体を削除しない |

実施した内容と証跡は、同じ環境の `.local/<environment>/evidence/` に保護保存します。
