// 目的：検査対象の操作について、本文を読む前にContent-Typeの宣言を確認します。
// この同名C#をcore-api-input-validation.xmlへ展開し、結果だけを後続の処理へ渡します。

// API設定の準備で作った制御JSONから、操作ID別の対応表を取得します。
// 空のオブジェクトで記録した操作だけを対象にし、API名・URL・製品名で対象を推測しません。
var settings = JObject.Parse((string)context.Variables["gatewayControlSettings"]);
var contracts = (JObject)settings["inputContracts"];
// 表そのものの欠落は設定不備です。検査対象外と混同して通過させません。
if (contracts == null) {
    throw new Exception("Input contracts are not configured");
}
// 有効な表にこの操作の契約がなければ、この部品では追加検査を行いません。
var entry = contracts.Property(context.Operation.Id);
if (entry == null) {
    return true;
}
// 項目が存在するのにnullや別の型なら、契約の生成・設定に問題があります。
if (!(entry.Value is JObject)) {
    throw new Exception("The operation input contract is invalid");
}

// APIMがまとめたヘッダーを一度だけ取得し、値が一つあることを確認します。
// 欠落や複数値の要求は、本文を読む前に不適合として呼出し元へ返します。
string[] contentTypeValues;
if (!context.Request.Headers.TryGetValue("Content-Type", out contentTypeValues) || contentTypeValues.Length != 1) {
    return false;
}

// セミコロンより前のMIME部分が空欄でないことを確認します。
// MIMEの種類は提供元の受付条件に任せ、charsetやmultipartのboundaryを含む元の値は保持します。
var contentType = (contentTypeValues[0] ?? "").Split(';')[0].Trim();
return contentType.Length > 0;
