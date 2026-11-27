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

## 初回証明書の発行と確認

[Coreの初回構築](../setup/08-provision-core.md)で証明書専用ACA Jobを実行し、Jobの終了と保護された結果を照合します。公開証明書と固定参照が揃ったら、[Entraの手動登録](../setup/09-egakumi-portal.md)へ進みます。任意の確認コマンドと発行が中断した場合の扱いは[証明書運用](../operations/portal-authentication.md#初期証明書の発行と確認)に記載します。

管理VMへの初期発行資材の限定配備は、ACA Jobへ移行する前の方式です。VM方式のローカル試験結果は履歴として記録しています。 [認証試験](../testing/portal-authentication-tests.md#従来のvm方式の確認履歴)

## 適用前に提示・確認するもの

1. 利用者が明示した対象環境の保護params、同じcheckoutのazd環境とCore stateを選択し、所有・権限・保存先を照合します。別checkoutや別環境の入力を探索・コピーして採用しません。
2. 統合した資材のCore変更計画を取得し、新規・変更・削除される資源と、実行されるhookを確認します。稼働中Portalが初期サンプルへ戻らないことも対象にします。
3. 証明書専用のACA環境、用途別のIDと権限、ACR、結果保管先を確認します。
4. 現行Portalのイメージ・認証設定・ジョブ・必要な権限の復旧参照を保護保存し、適用対象・順序・確認方法を利用者へ提示して判断を受けます。

SQL接続policyはProxyを維持し、PortalのTedious/KyselyとSQL用Managed Identity・Azure CLI認証を継続します。取消統合後の構築・照合資材もProxyへ揃えました。証明書準備・認証切替へSQL driver変更やSQL接続方式の切替を含めません。Coreとhookの実際の適用差分は引き続き確認し、証明書以外の変更がある場合は具体的な範囲を先に提示します。

対象環境を明示していない段階や、実際の変更計画を取得していない段階で、既存環境への適用差分が確定したとは記載しません。正確な入力・差分・証跡は`.local/<environment>/`へ保護保存し、公開文書には転載しません。

## 実施順序と判断の区切り

| 段階 | 実施内容 | 次へ進む条件 |
| --- | --- | --- |
| 1. 初期証明書準備 | `azd up`から証明書専用ACA Jobを実行し、Portal署名用とGateway OBO用の公開`.cer`・固定参照を取得 | 対象Job実行の正常終了、鍵と証明書の対応、非exportable・Self・版・期限、保護保存を確認 |
| 2. Entraの一度の登録 | APIアプリのOBO証明書とGraph委任を登録。専用Portalアプリを作成し、Portal用公開証明書、ログインとAPI用のcallback、`Gateway.Invoke`・`Subscription.Manage`の委任、Graph権限と管理者同意を登録 | 公開証明書と対象アプリの一致、登録結果と同意状態を照合 |
| 3. Gateway・Portal個別配備 | APIアプリのOBO用公開証明書登録後にGatewayを配備し、Portal本体とDB・署名権限・Graphジョブを準備 | 配備計画と必要な復旧参照を照合し、設定・イメージを読戻し |
| 4. 実受入 | ログイン、tid/oidによる本人照合、既存APIキー管理、Graph管理、定期ジョブ、KVへの実通信を確認 | [認証試験](../testing/portal-authentication-tests.md)の対象条件を満たす |
| 5. 更新・復旧の受入 | 新鍵の準備、手動公開証明書追加、固定参照切替、実確認、復旧、旧証明書削除・旧鍵版無効化を確認 | [更新運用](../operations/portal-authentication.md)の各段階を実受入 |
| 6. 旧Portal撤去 | Portal用途の旧secret参照・読取り権限・旧callbackと移行用ソース処理を照合して撤去 | 新Portalの実受入と復旧期間の終了。CM用secret・callbackの利用を維持 |

Entraアプリ作成、初回公開証明書登録、工程4のAPI試用用設定は一度の依頼にまとめます。工程4では認証処理とTry接続を別途受け入れます。 [工程4の詳細設計](portal-stage4-design.md)

HTTPSヘルス、構成の読戻し、ローカル署名の結果に加え、Entraログインや更新運用を実環境で確認します。依存の更新と検証時点の監査結果は[ローカル検証結果](../testing/portal-authentication-tests.md#本番依存の再監査)を参照します。
