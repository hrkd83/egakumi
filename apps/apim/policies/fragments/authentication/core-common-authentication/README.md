# 共通のキー／Bearer認証

このフォルダは、Azureポータルのポリシーフラグメント `core-common-authentication` の正本を管理する。同名XMLがSTEP4の分岐と処理順を定義し、同名C#が各 `set-variable` に差し込む5つの式を持つ。C#ファイル全体を1つのメソッドとして呼び出す構成ではない。

## MOD-145の失敗監査生成の補正

Bicepが認証失敗監査へ渡す主体readerを、現在の監査本文のraw引用符と一致する形へ補正しました。旧XML entityを探す置換はMOD-134の本文encoder変更後に一致せず、`gatewayAuthenticatedSubject`のままになっていました。現在はPythonと同じく、失敗時に確認した`gatewayEntraFailureSubject`を一意に参照します。正常監査、認証条件、401・内部500・要求ID・no-storeの応答は変更しません。

変更前Bicepと監査生成の3説明modeを保存し、Bicepの変更がreaderの2文字列だけ、監査本文の変更がreader1箇所だけで他の構造・属性・式が同じことを確認しました。この3mode対照は監査生成の比較で、Bicep全体の3mode展開証明ではありません。補完したcompiled-sourceの別実行1件は4.451秒で不一致を検出しました。補正後はその既存試験を含む40件32.503秒が全件成功し、Entra有効／無効の資源・Python正本・保護source一致、失敗主体・内部500の主体・401監査と応答を確認しました。独立レビューに追加所見はありません。ローカル検証済みでAzure未反映です。

```text
service.xml の STEP4
    ↓ include-fragment
core-common-authentication.xml ＋ core-common-authentication.cs
    ↓ 配備時にXMLのマーカーへ対応するC#区画を展開
Azureの core-common-authentication
    ├─ STEP3でCMのログイン開始・戻り先の受付と判定済み
    │     → 共通認証を省略し、各操作・Broker・CMの取引／本人確認へ
    ├─ キー用ヘッダーあり
    │     → APIMの購読検証結果とキーの形式を確認
    └─ キー用ヘッダーなし
          → Product／Bearer形式 → Entra標準検証 → 本人／会社設定の確認
    ↓ 成功した経路だけ共通認証の完了を記録
service.xml の STEP5で監査用の認証済み利用者情報を準備
```

キー用ヘッダーがあればキー認証を選ぶ。不正なキーを、一緒に送られたBearerで代わりに認証しない。キーがなければBearerを検証する。キーとBearerのどちらかを有効化する公開パラメーターや、API名・クライアントアプリIDの許可リストは設けない。

| C#の区画 | XMLで設定する変数 | 処理 |
| --- | --- | --- |
| `key-request-validation` | `gatewayAuthenticationFailure` | APIMが確認した購読の存在・ID、キーの空値／値数、URLでのキー指定を確認する |
| `bearer-request-validation` | `gatewayAuthenticationFailure` | 社員用Productと購読混在、URLでのキー指定、Authorizationの欠落／重複／Bearer形式を確認する |
| `entra-identity` | `gatewayEntraIdentityResult` | 標準検証が成功したトークンから本人・tenantを確認し、この要求と検証箇所に結び付いたsnapshotを作る |
| `entra-authentication-result` | `gatewayEntraAuthenticationResult` | 本人snapshotと会社設定・利用枠を確認し、正常な7項目のJSONまたは失敗分類を返す |
| `entra-failure-subject` | `gatewayEntraFailureSubject` | 会社設定や内部処理で500になった場合に、確認済み本人だけを失敗監査へ引き継ぐ |

区画は単独行の `// BEGIN <区画名>` と `// END <区画名>` で囲む。生成処理は固定の5区画だけを受け付け、未知・重複・閉じ忘れ・区画外のコードを拒否する。日本語の `//` 説明行はXML内で安全に読める `/* ... */` へ変換してAzureにも残す。

署名・有効期限・認証tenant・audience・v2トークン・必要な委任scopeは、XMLの `validate-azure-ad-token` が検証する。本人情報を準備しただけでは認証完了にしない。すべての確認が成功した最後にだけ、`gatewayUserAuthentication` と認証完了マーカーを設定する。`azp` は検証済みトークンに有効な値がある場合の監査情報であり、認可対象クライアントの制限には使わない。

会社設定は既存の4つのNamed Values（`core-entra-tenant-id`、`core-entra-api-audience`、`core-entra-required-scope`、`core-entra-user-limits`）を参照する。利用枠の確認・型・数値範囲、`null` の意味、後続APIへ渡す正常JSONの7項目は移設前のまま保持する。

拒否時の監査は、`fragments/audit/core-gateway-telemetry/` のXMLとC#を生成時に再利用して本文へ展開する。実行時にそのフラグメントを入れ子で呼び出す構成ではない。共有監査式の詳しい説明は `core-gateway-telemetry` にあり、複製側は正本への案内と既存コメントを保持する。500の失敗監査だけは、同じ共有式の利用者情報の参照先を `gatewayEntraFailureSubject` へ置き換える。

標準Entra検証の失敗は通常のinboundを中断するため、共通 `on-error` の別フラグメント `core-entra-authentication-error` が監査して401を返す。その他の本人不備は401、会社設定や内部処理の不備は500に分類する。本文・キー・トークン・Cookieを監査へ保存しない。

生成・完全照合のPython側は `scripts/deployment/helpers/apim_common_authentication.py`、IaC側は `apps/apim/api-publication.bicep` が担当する。フラグメント名だけでは内容を信用せず、配備時と読戻し時に実際の本文と固定生成元を照合する。旧inline構成との比較は説明行だけを除いた互換生成を使い、条件式・処理順・式の属性値を保持して明示移行する。
