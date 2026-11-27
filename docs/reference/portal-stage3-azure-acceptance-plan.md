# Portal工程3の統合・Azure適用計画

工程3の証明書認証と更新運用、工程4のAPI試用は資材に実装済みです。Azureでの発行、専用Entraアプリの登録・同意、Portalの実受入は、配備候補の差分と環境別の保護記録を使って確認します。 [手動登録手順](../setup/09-egakumi-portal.md) · [Portalの受入試験](../testing/portal-authentication-tests.md)

## 次の作業と担当

空の環境では、第08章の`azd up`でCoreを構築し、証明書専用のACA JobからPortal署名用とGateway OBO用の公開証明書を取得します。Entra担当が第09章の手順でAPI用証明書の登録とPortalアプリの手動作成・登録・同意を行います。構築担当が登録結果を保護paramsに反映し、Gateway、Portalの順に個別配備します。 [正式な初回構築手順](../../README.md#新規クローンから構築する)

共有SQLはProxyを使い、PortalのTedious/KyselyとSQL用Managed Identityによる認証を継続します。 [SQL Driver調査の記録](portal-sql-driver-feasibility.md)

稼働中Portalの認証切替や旧設定の撤去は、既存環境を移行する場合の作業です。新構成での実ログイン・Graph定期照合・API試用を受け入れてから進めます。

## 統合準備

以下はVM方式とSQL Redirect案を比較した当時の統合記録です。現在の手順は上記の正式な初回構築を参照してください。

工程3の当初の実装コミットは`511575f`です。WAF経路生成・公開経路集約とPortal認証資材・構築順序が重なる2ファイルをテキスト上で仮適用し、`git apply --check`が通過しました。当時の確認範囲は仮適用の可否です。

旧Redirect案の`b44e6d7`は、ローカル統合コミット`521a92f`で工程3へ取り込みました。当時のCore・PortalのBicep生成を含む関連172試験は168成功、Windowsのリンク作成権限が必要な4件は未実施でした。この結果は旧案の履歴であり、取消後の検証結果には数えません。

取消コミット`5f72d83`の取り込みでは、共有Bicep・初期証明書worker・署名ID・構築hook・Portal認証コードを保持しました。公開データ許可定義はPortalの追加pathを保持してSQL Redirect専用のpathと合成IPレコードだけを除去し、工程3の内容へ一致させました。取消後の関連試験とBicep生成は[認証試験](../testing/portal-authentication-tests.md)に記録します。これはローカルの資材確認で、Azureの配備差分・証明書発行・実ログインの確認ではありません。

## 従来のVM方式でのAzure適用計画

この節は従来のVM方式の判断記録です。現在の空環境からの構築は[Coreの初回構築](../setup/08-provision-core.md)で証明書用ACA Jobを使い、[Core後のEntra登録](../setup/09-egakumi-portal.md)とアプリの個別配備へ進みます。

旧方式ではCoreと構築hookが次の処理を担当していました。

| 対象 | 変更・処理 |
| --- | --- |
| ID用RG | Portal署名専用UAMIを作成 |
| KVの対象鍵scope | 管理VMへ公開鍵の照合・旧鍵版無効化用のread/writeを付与。発行には既存の証明書操作権限を使用 |
| 管理VM | 同期Run Commandで専用workerを配置し、`Initialize`を実行。既存TLS工程が用意する固定版PowerShellを確認 |
| Key Vault | Portal認証専用のSelf/RSA3072/非exportable証明書を初回生成。新しい秘密鍵もKV内で生成し、既存currentは照合して再利用 |
| 構築hook・保護保存先 | 今回のVMコマンドの正常終了と公開鍵・公開証明書・版・期限を照合し、公開`.cer`とstateを保存 |

秘密鍵・PFX・backing secretはVM、ACA、ローカルへ取り出しません。証明書取得だけでは稼働中Portalの認証設定は切り替わりません。

既存環境でCoreを再適用する場合は、TLS・公開DNS・通信整備等のhookも対象になります。適用判断にはテンプレートとhookの差分を含めます。

### 既存環境の初期証明書準備：承認済みの限定追加方式

従来のVM方式で構築済みの環境へ証明書準備だけを追加する場合は、次の3部品をIncrementalで追加する`initialize`を使います。当時の試験結果は[ローカル検証](../testing/portal-authentication-tests.md#限定追加入口と全体構築の確認)に記載しています。Azure適用は対象環境の最終差分を確認し、実行結果を保護記録へ残します。

| 部品 | 既存資材と処理 |
| --- | --- |
| 署名専用ID | [auth-identity.bicep](../../apps/portal/deploy/auth-identity.bicep)をID用RGへ配備 |
| 鍵運用権限 | [portal-auth-key-operations.bicep](../../infra/modules/portal-auth-key-operations.bicep)をKVのRGへ配備。ロール定義と対象鍵scopeの割当を照合 |
| 初期発行worker | [portal-auth-certificate.bicep](../../infra/modules/portal-auth-certificate.bicep)を管理VMのRGへ配備。専用workerがKV内で秘密鍵と自己署名証明書を生成 |

この入口は既存PortalのContainer Appへ書き込まず、初期サンプルへの置換や認証切替を行いません。SQL、APIM、WAF、TLS、DNS、基盤ネットワークの再配備を呼びません。管理VMの証明書操作権限、既存KV PE・DNSと、固定版PowerShellの準備を前提として照合します。実行時のworkerもPowerShellの版を検査し、条件不足のまま発行を始めません。準備不足を認めた場合は、その準備に必要な差分を先に提示します。

初期発行の運用入口は既存の[証明書運用CLI](../../scripts/operations/portal_auth_certificate.py)の`initialize`です。[実行手順](../operations/portal-authentication.md#初期構築と既存環境への限定追加)に従い、対象paramsと同じ環境の保護stateを照合し、別のsource checkoutから実行する場合だけ保護runtimeのrootを明示します。入力・stateを探索したりコピーしたりしません。既定では計画を表示し、`--execute`で3部品の対象、変更予測、保存した実行intentを確認して順に配備します。応答不明時に変更要求を重ねず、今回のRun Commandの終了と公開証明書を照合して再開します。Failed/Canceledの自動再実行は行わず、checkpointを調査して回復方法を判断します。

発行後は既存の収集処理で公開`.cer`と初期証明書stateを指定した保護runtimeへ保存します。秘密鍵・PFX・backing secretは取得しません。専用Entraアプリの手動登録、Portal本体の個別配備、実受入は後続の工程です。

適用前の変更予測では、署名ID・ロール定義・ロール割当・発行Run Command以外への変更がないことと、未解決の診断がないことを確認します。Run Commandの変更予測は、VM内部でのKV証明書生成や実通信の成功を証明しません。承認済みの方式で実行入口の実装とローカル検証を完了し、完成した入口の実適用差分を提示して判断を受けます。

## 適用前に提示・確認するもの

1. 利用者が明示した対象環境の保護params、同じcheckoutのazd環境とCore stateを選択し、所有・権限・保存先を照合します。別checkoutや別環境の入力を探索・コピーして採用しません。
2. 統合した資材のCore変更計画を取得し、新規・変更・削除される資源と、実行されるhookを確認します。稼働中Portalが初期サンプルへ戻らないことも対象にします。
3. 新規構築は証明書専用のACA環境、用途別のIDと権限、ACR、結果保管先を確認します。旧VM方式への限定追加は署名UAMI、鍵運用権限、VM worker、KV証明書の範囲を照合します。
4. 現行Portalのイメージ・認証設定・ジョブ・必要な権限の復旧参照を保護保存し、適用対象・順序・確認方法を利用者へ提示して判断を受けます。

SQL接続policyはProxyを維持し、PortalのTedious/KyselyとSQL用Managed Identity・Azure CLI認証を継続します。取消統合後の構築・照合資材もProxyへ揃えました。証明書準備・認証切替へSQL driver変更やSQL接続方式の切替を含めません。Coreとhookの実際の適用差分は引き続き確認し、証明書以外の変更がある場合は具体的な範囲を先に提示します。

対象環境を明示していない段階や、実際の変更計画を取得していない段階で、既存環境への適用差分が確定したとは記載しません。正確な入力・差分・証跡は`.local/<environment>/`へ保護保存し、公開文書には転載しません。

## 実施順序と判断の区切り

| 段階 | 実施内容 | 次へ進む条件 |
| --- | --- | --- |
| 1. 初期証明書準備 | 新規は`azd up`から証明書専用ACA Jobを実行し、Portal署名用とGateway OBO用の公開`.cer`・固定参照を取得。旧VM方式の既存環境は限定追加入口を使用 | 対象Job実行の正常終了、鍵と証明書の対応、非exportable・Self・版・期限、保護保存を確認。旧方式は対応するVMコマンドの結果を確認 |
| 2. Entraの一度の登録 | APIアプリのOBO証明書とGraph委任を登録。専用Portalアプリを作成し、Portal用公開証明書、ログインとAPI用のcallback、`Gateway.Invoke`・`Subscription.Manage`の委任、Graph権限と管理者同意を登録 | 公開証明書と対象アプリの一致、登録結果と同意状態を照合 |
| 3. Gateway・Portal個別配備 | APIアプリのOBO用公開証明書登録後にGatewayを配備し、Portal本体とDB・署名権限・Graphジョブを準備 | 配備計画と必要な復旧参照を照合し、設定・イメージを読戻し |
| 4. 実受入 | ログイン、tid/oidによる本人照合、既存APIキー管理、Graph管理、定期ジョブ、KVへの実通信を確認 | [認証試験](../testing/portal-authentication-tests.md)の対象条件を満たす |
| 5. 更新・復旧の受入 | 新鍵の準備、手動公開証明書追加、固定参照切替、実確認、復旧、旧証明書削除・旧鍵版無効化を確認 | [更新運用](../operations/portal-authentication.md)の各段階を実受入 |
| 6. 旧Portal撤去 | Portal用途の旧secret参照・読取り権限・旧callbackと移行用ソース処理を照合して撤去 | 新Portalの実受入と復旧期間の終了。CM用secret・callbackの利用を維持 |

Entraアプリ作成、初回公開証明書登録、工程4のAPI試用用設定は一度の依頼にまとめます。工程4では認証処理とTry接続を別途受け入れます。 [工程4の詳細設計](portal-stage4-design.md)

HTTPSヘルス、構成の読戻し、ローカル署名の結果に加え、Entraログインや更新運用を実環境で確認します。依存の更新と検証時点の監査結果は[ローカル検証結果](../testing/portal-authentication-tests.md#本番依存の再監査)を参照します。
