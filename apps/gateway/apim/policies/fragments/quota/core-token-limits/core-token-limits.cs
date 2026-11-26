// 同名XMLのトークン枠有効化と既存カウンターIDの実処理を管理します。
// 単純な毎分枠・累積枠・期間の取得と標準ポリシーの分岐はXMLに維持します。

// BEGIN enabled
/* 設定はAPIM変数のJSON文字列から読みます。配備時に対象操作一覧を必ず配列として生成しています。 */
var settings = JObject.Parse((string)context.Variables["gatewayControlSettings"]);
var operations = (JArray)settings["tokenLimitOperationIds"];
/* 一覧の不備は計数なしへ読み替えず、既存on-errorへ渡します。正常な一覧の照合後に免除を適用します。 */
var supported = operations.Any(value => (string)value == context.Operation.Id) && !(bool)context.Variables["gatewayRateLimitExempt"];
/* 認証方式は社員枠の有効化と購読枠の扱いを選ぶために使います。 */
var decision = JObject.Parse((string)context.Variables["gatewayDecision"]);
if ((string)decision["method"] != "user") { return supported; }
/* 先行する共通認証が元の社員枠を検査し、利用枠取得部品がその値を後段へ渡しています。 */
var limits = JObject.Parse((string)context.Variables["gatewaySubscriptionProfile"]);
/* 社員の両枠が明示nullなら計数しません。片方が有限なら後段でその枠だけを適用します。 */
/* 利用者監査とnative usage logの設定には影響しません。 */
return supported && (limits["tokensPerMinute"].Type != JTokenType.Null || limits["tokenQuota"].Type != JTokenType.Null);
// END enabled

// BEGIN counter
// 目的：トークン利用枠に使う、認証済み社員または購読の既存カウンターIDを用意します。
// 同名XMLのgatewayTokenCounterKeyへ展開し、毎分枠と累積枠で同じ帰属先を使います。
// 本人確認は共通認証・認証結果の引き渡し・社員用利用枠の取得で完了しています。
// トークン有効化では準備済みの枠を使い、社員の認証や利用枠の検査を繰り返しません。

// APIM変数に保存されたJSON文字列から、計数の帰属先に必要な値を読み取ります。
var decision = JObject.Parse((string)context.Variables["gatewayDecision"]);
if ((string)decision["method"] == "user") {
  // 確認済みの社員IDを引き継ぎ、本人情報JSONや認証成功をここで読み直しません。
  // 従来のtid:oidをそのまま使うため、既存カウンターの帰属先と表記は変わりません。
  var identity = (string)decision["verifiedTenantId"] + ":" + (string)decision["verifiedPrincipalId"];
  return "core-tokens-v1:user:" + identity;
}
// 購読キー側も、従来どおりAPIMが検証した同じ購読IDを使用します。
return "core-tokens-v1:" + context.Subscription.Id;
// END counter
