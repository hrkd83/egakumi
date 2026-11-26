// 目的：SQL利用枠の取得応答を検査し、有効な購読profileだけを後段へ渡します。
// 同名XMLのgatewayFetchedProfile式全体へ展開します。APIへinline展開し、Azure登録fragmentは追加しません。
// 失敗時は空文字を返し、XML側が既存監査へpolicy-errorを記録して503で停止します。
// 取得応答本文・例外詳細は監査へ渡しません。cache・社員分岐・503応答はXMLに保持します。

// 接続失敗や200以外の応答は、利用枠なしとして通さず取得不備にします。
var response = context.Variables.GetValueOrDefault<IResponse>("gatewayProfileResponse", null);
if (response == null || response.StatusCode != 200) { return ""; }
try {
  var value = response.Body.As<JObject>();
  // 形式の版と検証済み購読IDを照合します。別購読の枠を使いません。
  if (value["schemaVersion"] == null || value["schemaVersion"].Type != JTokenType.Integer || (int?)value["schemaVersion"] != 1 || (string)value["subscriptionId"] != context.Subscription.Id) { return ""; }
  var rate = value["requestRate"];
  var tpm = value["tokensPerMinute"];
  var quota = value["tokenQuota"];
  // 3項目の欠落と明示nullを区別します。明示nullは従来どおり制限なしです。
  if (rate == null || tpm == null || quota == null) { return ""; }
  // 外部から取得する枠の型・範囲・期間を、従来と同じ条件で検査します。
  if (rate.Type != JTokenType.Null && (rate.Type != JTokenType.Object || rate["calls"] == null || rate["calls"].Type != JTokenType.Integer || (int?)rate["calls"] < 1 || (int?)rate["calls"] > 1000000 || rate["renewalPeriodSeconds"] == null || rate["renewalPeriodSeconds"].Type != JTokenType.Integer || (int?)rate["renewalPeriodSeconds"] < 1 || (int?)rate["renewalPeriodSeconds"] > 300)) { return ""; }
  if (tpm.Type != JTokenType.Null && (tpm.Type != JTokenType.Integer || (int)tpm < 1)) { return ""; }
  if (quota.Type != JTokenType.Null && (quota.Type != JTokenType.Object || quota["tokens"] == null || quota["tokens"].Type != JTokenType.Integer || (int?)quota["tokens"] < 1 || !new [] { "Hourly", "Daily", "Weekly", "Monthly", "Yearly" }.Contains((string)quota["period"]))) { return ""; }
  // 検査した既存profile JSONを返します。取得式の例外は既存の取得不備503へ揃えます。
  return value.ToString(Newtonsoft.Json.Formatting.None);
} catch { return ""; }
