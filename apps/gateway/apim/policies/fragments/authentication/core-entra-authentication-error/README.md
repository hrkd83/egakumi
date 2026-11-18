# 標準Entraトークン検証の失敗処理

このフォルダは、Azureポータルのポリシーフラグメント `core-entra-authentication-error` の正本を管理する。同名XMLだけで処理を定義できるため、空のC#ファイルは作らない。

共通認証の `validate-azure-ad-token` が失敗すると、通常のinbound処理を中断して共通 `on-error` へ移る。このフラグメントは、共通の要求IDヘッダー設定の後から呼び出す。

```text
STEP4の validate-azure-ad-token が失敗
    ↓ 共通 on-error
apim-request-id を設定
    ↓ core-entra-authentication-error
LastError.PolicyId が core-entra-token-validation か確認
    ├─ 一致：認証拒否を監査へ記録し、401・Bearer案内・no-storeを返す
    └─ 不一致：この部品では応答を終了せず、後続のエラー処理へ進む
```

監査は、`fragments/audit/core-gateway-telemetry/` のXMLとC#を生成時に再利用して本文へ展開する。別のフラグメントを実行時に入れ子で呼び出す構成ではない。共有式の詳しい説明は `core-gateway-telemetry` にあり、ここでは正本への案内と既存コメントを保持する。

失敗したトークンから本人を推測せず、アクセストークンや例外の詳細を応答・監査へ載せない。会社設定の不備や内部処理による500の分類は、`core-common-authentication` 側が担当する。このフラグメントは標準Entraトークン検証の失敗だけを扱う。

生成・完全照合は `scripts/deployment/helpers/apim_common_authentication.py`、IaCへの登録は `apps/gateway/apim/api-publication.bicep` が担当する。読戻しでは、共通認証本文・呼出位置・標準検証とこの失敗処理の同時存在も確認する。
