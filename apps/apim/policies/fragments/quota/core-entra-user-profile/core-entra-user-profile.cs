// 目的：共通認証が保持した社員の利用枠を、回数・トークン計数へ渡します。
// 同名XMLのgatewaySubscriptionProfile式全体へ展開し、Azure名core-entra-user-profileを維持します。
// 認証結果の引き渡しに失敗した要求は先行処理で停止するため、allowedと認証成功印を再確認しません。

// 共通認証は元設定の構造・必須項目・型・範囲・期間を検査し、不備なら先に500で停止します。
// 同じ検査済み利用枠を保持した内部の社員JSONを読みます。本人・元設定・利用枠を再検査しません。
var user = JObject.Parse((string)context.Variables["gatewayUserAuthentication"]);

// 後段が使う利用枠オブジェクトを取得します。検査専用のrate・tpm・quotaの読取りは不要です。
var limits = user["limits"] as JObject;

// 各枠の明示nullを含め、取得した既存profile JSONをそのまま返します。
// JSON読取りや利用枠オブジェクトの取得に失敗した例外は、補完やcatchを加えず既存on-errorへ渡します。
return limits.ToString(Newtonsoft.Json.Formatting.None);
