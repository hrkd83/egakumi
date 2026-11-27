// 目的：共通認証が確認した利用者を、HTTP APIの後続処理と監査へ引き渡します。
// この同名C#をcore-api-authentication-result.xmlへ展開し、Azureの同名フラグメントを作ります。
// 失敗時の監査記録と500応答は、呼び出し元のAPIテンプレートが行います。

// 引き渡しに失敗した場合の結果を用意します。キーやBearerの認証不備は共通認証が先に拒否します。
var failure = new JObject(new JProperty("allowed", false));
JObject decision;

// APIMが検証した購読があれば、キー認証の結果を使います。方式の選択をヘッダーでやり直しません。
if (context.Subscription != null) {
    // 購読IDとキーの形式は共通認証で確認済みです。購読IDは社員本人のIDではありません。
    decision = new JObject(new JProperty("allowed", true),
        new JProperty("callerId", "subscription:" + context.Subscription.Id),
        new JProperty("method", "subscriptionKey"));
} else {
    try {
        // この分岐では購読なしが確定し、共通認証の完了は先行する要求準備で確認済みです。
        // 社員情報を使うため、共通ポリシーが記録したBearer認証成功を確認します。
        if (!context.Variables.ContainsKey("gatewayEntraAuthenticationValidated")
            || !(bool)context.Variables["gatewayEntraAuthenticationValidated"]) {
            return failure.ToString();
        }

        // 共通認証が作った内部JSONを読みます。クライアントの本文やトークンは読みません。
        var user = JObject.Parse((string)context.Variables["gatewayUserAuthentication"]);
        // 形式バージョンと社員IDは共通認証で確認・整形済みなので、その値を引き継ぎます。
        var tenant = (string)user["tenantId"];
        var principal = (string)user["principalId"];
        // 呼出者と利用枠の帰属先は同じ社員と確認済みなので、IDを作り直さず引き継ぎます。
        var caller = (string)user["callerId"];
        var allocation = (string)user["allocationId"];

        // allowedはこの部品の引き渡し成功を示します。利用枠や接続先の確認は後続処理で行います。
        decision = new JObject(new JProperty("allowed", true), new JProperty("method", "user"),
            new JProperty("callerId", caller), new JProperty("allocationId", allocation),
            new JProperty("verifiedTenantId", tenant), new JProperty("verifiedPrincipalId", principal),
            // clientIdは呼び出し元アプリの監査情報です。任意の情報なので、なければ空文字を渡します。
            new JProperty("verifiedClientId", (string)user["clientId"] ?? ""));
    } catch {
        // 内部JSONの読取りなどで失敗した場合も止めます。例外の詳細や生JSONは出力しません。
        return failure.ToString();
    }
}

// 先行処理の準備済み結果から、管理側が付けた操作・モデル設定のラベルを読みます。
var settingId = (string)JObject.Parse((string)context.Variables["gatewayOperationSelection"])["settingId"];
// 空でなければ既存の監査項目profileIdへ転記します。クライアントのprofile選択ではありません。
if (!String.IsNullOrEmpty(settingId)) { decision["profileId"] = settingId; }

// 後続処理が共通に参照できるよう、引き渡し結果を1行のJSON文字列にして返します。
return decision.ToString(Newtonsoft.Json.Formatting.None);
