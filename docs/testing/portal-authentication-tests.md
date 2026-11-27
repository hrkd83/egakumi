# Portal証明書認証の構築・受入試験

この文書は、工程3のローカル試験の記録と、現在の専用ACA Job方式の受入項目を扱います。現行資材でのAzure・Entra受入は未実施です。工程4のAPI用OAuthとTry接続は[初回構築の受入](../setup/13-verify-deployment.md)で確認します。 [当初の実施計画](../reference/portal-stages-3-4-plan.md) · [手動Entra登録](../setup/09-egakumi-portal.md)

SQLはProxy維持、Redirect変更とPortal driver試作は取りやめと決定しました。既存Tedious/Kysely・SQL用MI認証を維持し、証明書認証の受入を独立して進めます。Networkの取消コミット`5f72d83`をローカルへ取り込み、SQL/NSGと証明書資材の保持を確認しました。以下の172試験は旧Redirect案のローカル統合時の履歴であり、取消後の資材を確認した結果ではありません。

## ローカル確認結果

以下の旧Portalでの結果は取得時の記録です。現在の機能は[配備後の受入](../setup/13-verify-deployment.md)を基準にします。

| 対象 | 結果 |
| --- | --- |
| 証明書state・運用段階 | Python 14テスト成功 |
| Bicep生成・初期worker実行・署名権限 | 18テスト成功 |
| KV応答を置換した証明書検証 | PowerShell 12ケース成功。自己署名の改変、公開鍵不一致、exportable/private JWK、現行鍵廃止を拒否 |
| Portal認証・Graph・APIキー管理 | TypeScript 85テスト成功 |
| 配備・初期構築hook・ローカル準備 | 関連Python回帰を実施し、失敗項目の個別再実行で解消。Windowsの権限不足・対応外の試験は未実施扱い |
| 公開データ検査の回帰 | 65テスト実行、61成功、Windowsでリンク作成権限がない4件は未実施 |
| 型検査・lint・build | 型検査成功、lintのエラーなし（既存警告あり）。buildはNext.jsの更新推奨で初回停止し、案内に従った再実行で成功 |
| 依存監査（当時） | Next.js由来のmoderate検出3件。更新後の結果は下記の再監査を参照 |
| 公開境界・文書 | ローカル作業ツリー混入検査成功。参照先を照合し、既存の構築試験と変更対象外の総合計画・総合試験書を保持 |
| 旧Redirect案とのローカル統合（履歴） | Core・PortalのBicep生成を含む関連172試験を実行し、168成功、Windowsリンク権限が必要な4件は未実施。混入検査のexact pathも両コミットの集合を照合して保持。APIM資材の統合とAzure反映は未実施 |

これらは合成データ・ローカルコンパイルの結果です。Azureでの鍵発行、Entra登録、ログイン、更新・復旧の受入は未実施です。

### 本番依存の再監査

2026-11-26の `npm audit --omit=dev` では、Next.js 16.4.3に7件の公表済みアドバイザリが該当し、npm集計はmoderateの依存パッケージ1件でした。該当アドバイザリは16.4.5で修正されています。 [Next.js 16.4.5セキュリティリリース](https://github.com/vercel/next.js/releases/tag/v16.4.5)

2026-11-27にPortalのNext.jsと `eslint-config-next` を16.4.6へ更新しました。更新後のロックファイルに対する `npm audit --omit=dev` は検出0件でした。開発依存を含む `npm audit` では、`minimatch` と `typescript-eslint` 系の推移依存にmoderateのパッケージが7件あります。 [Next.js 16.4.6](https://github.com/vercel/next.js/releases/tag/v16.4.6)

更新後のPortalテストは789件中771件成功、SQL統合の18件は未実施、失敗0件でした。型検査と `npm run build -- --webpack` は成功しました。既定の `npm run build` も、公開資材の一時コピーで同梱カタログを使うTurbopackビルドを実行し、成功しました。管理VMで構築用カタログを使うコンテナーイメージのビルドと、Azureでの受入は未実施です。

### Proxy取消後のローカル確認

Networkの取消コミット`5f72d83`を取り込んだ後、9スイート102試験を確認しました。初回は101成功、証明書保存の1件でエラーでした。合成fixtureの親ディレクトリ作成がPOSIXの700権限を満たしていなかったため、fixtureだけを正式な`private_parent`へ合わせました。権限検査は緩めず、証明書の14試験をWSLの通常一時領域で再実行して全件成功しました。修正後の対象102試験に未解決の失敗・エラー・スキップはありません。

| 対象 | 確認内容 |
| --- | --- |
| `test_portal_auth_certificate`、`test_portal_authentication_infra` | 初期公開証明書・state・更新段階、Core/PortalのBicep生成、初期workerと署名ID・権限 |
| `test_management_client_assets`、`test_management_client_assets_bicep` | Proxyを要求する照合と限定配備、管理client・network・credential networkのBicep生成 |
| `test_deploy_applications` | 初期構築のGateway-only入口とPortal個別配備の分担 |
| `test_nsg_rule_priorities`、`test_fixed_nsg_validation`、`test_nsg_deployment_order` | 固定NSG集合・SQL向け4規則のTCP1433・固定通信の照合と配備順 |

Proxy取消の統合時点で、`infra/`、`scripts/`、`apps/`の実装は証明書改修済みコミット`511575f`と一致することを確認しました。その後に検証した旧VM方式の初期発行は、次節に履歴として記録します。Redirect専用module・CLI・manifestと残存参照を除去し、Portal用の公開データ許可定義を保持しました。これらは合成検証であり、Azure操作・SQL実接続・証明書発行・Entraログインの受入ではありません。

### 従来のVM方式の確認履歴

ACA Jobへ移行する前は、管理VMへ初回発行用のRun Commandを追加する入口がありました。当時は既存資源への書込み範囲、応答不明時の再送抑止、公開証明書の収集・保存、入力と対象の照合を合成データで検証しました。初回は関連133件中132件成功、macOSの一時領域を使う1件が対象外でした。その後の権限とロール参照の修正を含む関連45件が成功しました。この旧入口に関する結果は履歴です。

## ローカルで確認する契約

### 工程4の設計前ライブラリ確認

[詳細設計案](../reference/portal-stage4-design.md)の根拠として、既存Better Auth `1.7.14`の公開`better-auth/oauth2`を直接読み、合成データで9ケースを確認しました。公開export、S256 PKCE・form_post・state/nonce、`private_key_jwt`の非同期assertionによるコード要求と更新要求、保護パラメーター、client secret併用の拒否、既存JOSEによるID tokenの署名・本人・nonce・別署名の検査は全件成功しました。

当時の試験範囲は、配布APIの呼出しとメモリー内の署名検証です。実KV・Entra・SQLへの通信や実際のOAuth・Cookie・全Tryの受入は別の確認として扱います。

| 試験 | 確認する内容 |
| --- | --- |
| `tests/portal-auth-certificate-offline.ps1` | 合成RSA鍵・公開証明書をメモリー内で用意。Self・非exportable・新鍵生成方針・公開鍵対応を照合し、他鍵、disabled、private JWK、version未固定、現行版廃止を拒否。保存intentから不明POSTを再送せず再開 |
| `tests/test_certificate_jobs.py` | 専用Jobの配備と初回実行順序、非公開イメージ、対象実行と結果の照合、開始応答不明からの再開 |
| `tests/test_portal_auth_certificate.py` | 初期構築はClient ID不要、手動登録後の2項目入力、公開DERとthumbprint・期限・KV参照、単一worker出力、登録前切替拒否、旧VM入口での不明PUTの再送抑止、手動受入と旧Entra削除の必要性 |
| `tests/test_portal_authentication_infra.py` | Bicepコンパイル、CoreとPortal本体の資源責任、専用ID、鍵scope権限、secretと証明書の設定、ジョブ・DB・Gateway既存設定 |
| `tests/test_portal_certificate_initialization.py` | ACA Jobが保存した初期状態の読取り、旧構成への初期配備の停止、保護runtimeと排他、旧Coreの発行済み結果の収集 |
| `tests/test_deploy_applications.py` | アプリ配備補助入口のGateway-only指定と、両アプリの配備順、失敗時の停止、state選択 |
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
| ログイン | 固定認証tenantへ認証コードを交換し、公開HTTPS callback・Secure Cookie・署名済みtid/oidを維持。必要な構成でAzure tenantと認証Entra tenantの相違も確認 |
| Graph OBO | PortalのBearer試用から本人の `/me` が取得でき、別人・無効なトークンで取得できないことを確認 |
| 署名権限 | 対象鍵scopeのみread/sign。誤ったID・鍵版・disabled鍵・KV不通で署名失敗を秘匿し、他方式へfallbackしない |
| 更新準備 | 運用入口からPortal署名用のACA Jobを実行し、現行と異なる公開鍵・新しい鍵版を生成。公開`.cer`だけを出力し、現行参照を維持 |
| 手動登録前 | candidateの切替を拒否。旧公開証明書・旧鍵を維持 |
| 更新切替 | 本体の固定参照とイメージを読み戻し、ログインとBearer試用・Graph OBOを確認して受入 |
| 復旧 | 旧版有効期間中、保存した旧固定参照と同じイメージへ戻し、ログインとBearer試用を再確認 |
| 旧版廃止 | 受入と手動Entra旧証明書削除の後だけ、旧鍵exact versionを無効化。現行鍵名全体を削除しない |
| 既存環境から移行する場合 | 旧Portalの秘密参照とアクセス割当、不要になった専用の戻り先を受入後に回収。既存CMのsecret/callbackを保持 |

HTTPヘルス、設定の読戻し、ローカル署名テストをEntraの実ログイン成功と同一視しません。実施していない項目、失敗、対象構成不足は成功件数へ含めません。実環境の正確な出力・参照・証跡は`.local/<environment>/evidence/`へ保護保存し、公開文書には転載しません。
