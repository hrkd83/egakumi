// 目的：操作の基本設定に、要求したモデルの設定を反映して後続処理へ渡します。
// この同名C#をcore-api-operation-settings.xmlへ展開してAzureの部品を生成します。
// 本文・キー・トークンは結果や監査へ保存せず、モデル選択に必要な情報だけを返します。

// 設定を準備できるまではallowed=falseにします。成功結果にはエラー用のstatusを持たせません。
// 失敗を返す箇所で、原因に対応する応答コード400・413・500と監査コードを指定します。
var result = new JObject(new JProperty("allowed", false), new JProperty("settingId", ""));
JObject operation;
try {
    // API冒頭で準備した制御JSONから、APIMが照合した操作IDの基本設定を取得します。
    var controls = JObject.Parse((string)context.Variables["gatewayControlSettings"]);
    var operations = controls["operationConstraints"] as JObject;
    operation = operations == null ? null : operations[context.Operation.Id] as JObject;
} catch {
    // 設定を取得できなかった場合は、次の欠落判定へまとめます。例外の詳細は記録しません。
    operation = null;
}
if (operation == null) {
    result["status"] = 500;
    result["code"] = "OPERATION_SETTINGS_INVALID";
    return result.ToString(Newtonsoft.Json.Formatting.None);
}

try {
    // モデル別設定とaliasは操作の直下にあります。未指定と不正な型を区別します。
    var modelsProperty = operation.Property("modelSettings");
    var aliasesProperty = operation.Property("modelAliases");
    var models = modelsProperty == null ? null : modelsProperty.Value as JObject;
    var aliases = aliasesProperty == null ? null : aliasesProperty.Value as JObject;
    if ((modelsProperty != null && models == null) || (aliasesProperty != null && aliases == null)) {
        throw new Exception("The operation model settings are invalid");
    }

    // 後続には基本設定を渡します。検索用のモデル表は、選択後の設定JSONへ複製しません。
    var effective = (JObject)operation.DeepClone();
    effective.Remove("modelSettings");
    // Graph等のモデル選択を使わない操作は、本文を読まずに基本設定を返します。
    if (models == null && (aliases == null || aliases.Count == 0)) {
        result["constraint"] = effective;
        result["allowed"] = true;
        return result.ToString(Newtonsoft.Json.Formatting.None);
    }

    // 配備時にモデル別の値を集約した操作上限を使います。要求ごとの一覧集計は行いません。
    long maxBytes;
    try { maxBytes = (long)operation["maxRequestBytes"]; }
    catch { maxBytes = 0; }
    // 取得失敗と既存範囲外は、本文を読む前の設定不備500です。
    if (maxBytes < 1 || maxBytes > 524288000) {
        result["status"] = 500;
        result["code"] = "OPERATION_SETTINGS_INVALID";
        return result.ToString(Newtonsoft.Json.Formatting.None);
    }
    // 元の本文バイト数を最初のJSON解析より前に確認します。上限超過413は維持します。
    var bytes = context.Request.Body == null ? new byte[0] : context.Request.Body.As<byte[]>(preserveContent: true);
    if (bytes.Length > maxBytes) {
        result["status"] = 413;
        result["code"] = "REQUEST_TOO_LARGE";
        return result.ToString(Newtonsoft.Json.Formatting.None);
    }

    // 本文JSONを読めない場合と、modelが文字列でない場合を、それぞれ要求不備400へ分類します。
    JObject body;
    try { body = JObject.Parse(System.Text.Encoding.UTF8.GetString(bytes)); }
    catch {
        result["status"] = 400;
        result["code"] = "INVALID_REQUEST_BODY";
        return result.ToString(Newtonsoft.Json.Formatting.None);
    }
    if (body["model"]?.Type != JTokenType.String) {
        result["status"] = 400;
        result["code"] = "INVALID_REQUEST_MODEL";
        return result.ToString(Newtonsoft.Json.Formatting.None);
    }

    // aliasはこの操作の表だけで解決します。登録モデルの一覧による受付判定は、この部品では行いません。
    var requestedModel = (string)body["model"];
    var alias = aliases == null ? null : aliases.Property(requestedModel);
    var effectiveModel = alias == null ? requestedModel : (string)alias.Value;

    // 設定があるモデルだけ、その項目を基本設定へ上書きします。設定がないモデルは基本設定を使います。
    // 未指定項目は基本の値を引き継ぎます。後段の認証・本文検査・転送先の判定は変更しません。
    var selected = models == null ? null : models.Property(effectiveModel);
    if (selected != null) {
        var overrides = selected.Value as JObject;
        if (overrides == null) { throw new Exception("The selected model settings are invalid"); }
        foreach (var property in overrides.Properties()) {
            effective[property.Name] = property.Value.DeepClone();
        }
    }
    // settingIdは任意の既存監査用ラベルです。転送先は解決済みモデル名から選びます。
    // 既存監査のprofileIdへの転記は、呼出し元が明示的に行います。
    result["settingId"] = (string)effective["settingId"] ?? "";
    effective.Remove("settingId");
    result["requestedModel"] = requestedModel;
    result["effectiveModel"] = effectiveModel;
    result["constraint"] = effective;
    result["allowed"] = true;
    return result.ToString(Newtonsoft.Json.Formatting.None);
} catch {
    // 操作・モデルの設定を読めない場合は設定不備500です。要求JSONの400とは区別します。
    result["status"] = 500;
    result["code"] = "OPERATION_SETTINGS_INVALID";
    return result.ToString(Newtonsoft.Json.Formatting.None);
}
