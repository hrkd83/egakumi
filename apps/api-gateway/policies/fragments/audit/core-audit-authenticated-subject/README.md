# 認証済み利用者の監査情報

このフォルダは、Azureのポリシーフラグメント `core-audit-authenticated-subject` の正本を管理する。フォルダ名・XML／C#の名前・Azureのフラグメント名を一致させる。

| ファイル | 担当する処理 |
| --- | --- |
| `core-audit-authenticated-subject.xml` | 監査用JSONをAPIM変数 `gatewayAuthenticatedSubject` へ保存する |
| `core-audit-authenticated-subject.cs` | 認証完了と購読／社員の識別情報を照合し、監査に採用するJSONを作る |

生成時にXMLの `__CORE_AUTHENTICATED_SUBJECT_CSHARP__` へ同名C#を埋め込む。XMLの末尾の `return` がJSON文字列を返し、`set-variable` が保存する。C#は外部サービスとして呼び出すものではなく、Azureへ登録されたXMLの式としてAPIM内で実行する。`//` の説明は安全な `/* ... */` コメントへ変換してAzureにも残す。

```text
service.xml の STEP4：キー／Bearer認証
    ↓ 認証完了結果と購読ID／社員JSONを保存
service.xml の STEP5：core-audit-authenticated-subject
    ↓ 確認した識別情報だけを採用
gatewayAuthenticatedSubject
    ↓ 後続の監査処理
ログの authenticatedSubject へ許可項目を転記
```

この部品は監査データの準備を担当する。APIキーやJWTを再検証せず、要求本文や生トークンを読み直さない。各APIの認可結果・認証完了変数・社員の利用枠を変更せず、監査の不整合を理由に新たな401／403を返さない。

## 確認する順序

1. 初期状態を `unverified` にし、共通／Entraの認証完了結果をboolとして取得する。変数がなければfalse、型が不正なら末尾のcatchで `inconsistent` にする。Entraだけ完了した組合せも不整合とする。
2. 共通認証が完了した場合、識別情報が一致するまでは `inconsistent` とする。購読の証拠があればキー経路を選び、不整合な購読をBearerで補わない。
3. キー経路では、APIMの購読IDとSTEP4が保存したstring型の購読IDが完全一致し、空・空白でなく256文字以内の場合だけ採用する。`callerId` は `subscription:` と購読IDを連結し、最大269文字になる。購読IDは社員本人やprimary／secondaryキーを識別する情報ではない。
4. Bearer経路では、STEP4の社員JSONと設定tenantをstringとして取得する。内部JSON4096文字、設定tenant64文字の上限を確認してからJSONを解析し、形式番号 `schemaVersion` が整数の1であることを確認する。これらはアクセストークン・API本文・モデル入力の長さ制限ではない。
5. tenant IDと社員Object IDは36文字の非ゼロGUIDと小文字・ハイフンの正規形を要求する。設定tenantはGUIDとして同じ値かを照合する。`clientId` はstring項目が必要だが空文字を許容し、値があれば同じGUID形式を確認する。生トークンの `azp` が採用できなかった場合の空文字と、内部JSONの項目欠落・型不正は区別する。クライアント許可リストには使わない。
6. tenant IDと社員Object IDから `user:<tenantId>:<principalId>` を組み立て、`callerId` と `allocationId` がそれぞれstring型で完全一致することを確認する。現在の利用枠は社員単位なので、呼出者と利用枠の帰属先は同じ社員になる。
7. すべての確認が成立した場合だけ `verified` と識別項目を保存する。JSON解析・型変換などの例外ではJSONを作り直し、`inconsistent` だけを残す。部分的なID、生JSON、例外本文、原因コードは保存しない。

GUIDと識別子の確認は、前の確認が成功した場合だけ次を評価する `&&` の順序を保つ。`GetValueOrDefault<T>` は欠落時の既定値を指定して取得を簡潔にし、型不一致は既存のcatchで不整合として扱う。

## 未確認と不整合の違い

| 状態 | 意味と保存する情報 |
| --- | --- |
| `unverified` | 共通認証による本人・購読の確認が成立していない。statusだけを保存する |
| `inconsistent` | 認証完了結果や識別情報が欠落・不一致・型不正などで採用できない。statusだけを保存する |
| `verified` | 認証完了と識別情報の照合が成立した。購読または社員の許可された識別項目を保存する |

CMのブラウザーでのログイン開始・戻り先は、STEP4の共通キー／Bearer検証を省略するため、ここでは共通認証による本人を未確認として扱う。後続の操作・Broker・CMによる取引・本人の確認とは区別する。通常の認証拒否はSTEP4または共通on-errorで終了し、STEP5には到達しない。その段階の拒否監査は各失敗処理が担当する。

このフラグメントは別のフラグメントを入れ子で呼ばず、他の監査ソースも展開しない。生成・照合は `scripts/deployment/helpers/apim_audit.py`、Azureへの登録は `apps/api-gateway/api-publication.bicep` が担当する。後続の `core-gateway-telemetry` は準備済みJSONから許可項目を転記する別の部品である。
