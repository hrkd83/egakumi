// 目的：選択済みの操作設定に従い、本文サイズ・出力トークン数・項目条件を確認します。
// この同名C#をcore-model-limits.xmlへ展開して、Azureの同名フラグメントを作ります。
// 結果はgatewayLimitDecisionへ保存し、監査とHTTP応答は呼び出し元のAPIが行います。
// 応答本文は読まず、要求本文も後続へ渡せるように保持します。

// 設定を使えない場合の初期結果を用意します。すべての検査を通るまでallowed=falseにします。
var result = new JObject(new JProperty("allowed", false), new JProperty("status", 500), new JProperty("code", "LIMITS_NOT_CONFIGURED"));
// 先行処理が選んだ内部設定JSONを読みます。クライアントの本文とは別の情報です。
JObject constraint;
try {
    // 変数の取得・JSON解析に失敗した場合は、初期結果の500とLIMITS_NOT_CONFIGUREDを返します。
    constraint = JObject.Parse((string)context.Variables["gatewayOperationConstraint"]);
} catch {
    return result.ToString(Newtonsoft.Json.Formatting.None);
}

try {
    // 本文形式の既定値は配備時に補完します。ここでは準備済みの文字列だけを使います。
    var configuredBodyFormat = constraint["bodyFormat"];
    var format = configuredBodyFormat?.Type == JTokenType.String ? (string)configuredBodyFormat : null;
    // 検査方法を選べない欠落・不正値は、本文を読む前に設定不備500を返します。
    if (format != "json" && format != "binary" && format != "multipart"
        && format != "text" && format != "none") { return result.ToString(Newtonsoft.Json.Formatting.None); }
    // noneは、この部品で本文を検査しない設定です。本文の存在を禁止する意味ではありません。
    // 本文と、その検査に使うmaxRequestBytesを読まずに成功を返します。
    if (format == "none") {
        result["allowed"] = true; result["status"] = 200; result["code"] = "NO_BODY_CONTRACT";
        return result.ToString(Newtonsoft.Json.Formatting.None);
    }
    // 本文を扱う4形式だけ、maxRequestBytesの整数設定を一度だけlongへ変換します。
    var configuredMaxBytes = constraint["maxRequestBytes"];
    long maxBytes;
    // 変換できない値や桁あふれ、1〜500 MiBの範囲外は、本文を読む前に設定不備500を返します。
    if (configuredMaxBytes == null || configuredMaxBytes.Type != JTokenType.Integer
        || !Int64.TryParse(configuredMaxBytes.ToString(), out maxBytes)
        || maxBytes < 1 || maxBytes > 524288000) { return result.ToString(Newtonsoft.Json.Formatting.None); }
    // バイナリ・ファイル送信・テキストでは、操作設定に従って本文サイズを確認します。
    // 受付形式の判断は提供元に任せ、この部品はContent-Typeの許可一覧を持ちません。
    if (format == "binary" || format == "multipart" || format == "text") {
        // Content-Lengthは本文の申告バイト数です。ヘッダーの構文はHTTPの受信処理に任せます。
        var declaredLength = context.Request.Headers.GetValueOrDefault("Content-Length", "");
        long length;
        // 上限超過の申告なら、この部品で本文をバイト配列へ読み出す前に413を返します。
        // 本文の読取りと後続処理を省きます。HTTP受信層での受信・保持は別の処理です。
        // 省略・数値化できない値・上限以下は、次の操作設定による検査へ進みます。
        if (Int64.TryParse(declaredLength, out length) && length > maxBytes) {
            result["status"] = 413; result["code"] = "REQUEST_TOO_LARGE";
            return result.ToString(Newtonsoft.Json.Formatting.None);
        }
        // declaredは申告サイズだけを使う指定です。この部品では本文を読み出さず転送へ進みます。
        // 未指定またはactualは、これまでどおり実受信サイズも確認します。
        var declaredSizeOnly = (string)constraint["requestBodySizeCheck"] == "declared";
        if (!declaredSizeOnly) {
            // APIMから本文の全バイト数を読み、上限と比較します。後続へ渡す元の本文を保持します。
            var uploaded = context.Request.Body == null ? new byte[0] : context.Request.Body.As<byte[]>(preserveContent: true);
            if (uploaded.Length > maxBytes) {
                result["status"] = 413; result["code"] = "REQUEST_TOO_LARGE";
                return result.ToString(Newtonsoft.Json.Formatting.None);
            }
        }
        // 成功結果は、申告サイズのみの方式と実サイズも確認した方式を区別します。
        result["allowed"] = true; result["status"] = 200;
        result["code"] = declaredSizeOnly ? "DECLARED_SIZE_ONLY" : "BOUNDED_UPLOAD";
        return result.ToString(Newtonsoft.Json.Formatting.None);
    }
    // その他の形式では、元の本文バイト数をJSON解析より先に確認します。
    var bytes = context.Request.Body == null ? new byte[0] : context.Request.Body.As<byte[]>(preserveContent: true);
    // 操作の種類から、PII・埋め込み・並べ替えなど後続の検査を選びます。
    var kind = (string)constraint["kind"];
    // PIIはJSONのエスケープで送信サイズが増えるため、設定枠の6倍を受信枠にします。
    // その場合もAPIMの500 MiBを超えません。PII操作側で解析後のサイズを別に確認します。
    var receivedMaxBytes = kind == "pii" ? Math.Min(524288000L, 6L * maxBytes) : maxBytes;
    if (bytes.Length > receivedMaxBytes) {
        result["status"] = 413;
        result["code"] = "REQUEST_TOO_LARGE";
        return result.ToString(Newtonsoft.Json.Formatting.None);
    }

    // 出力トークン項目を検査する設定の場合だけ、その項目名と指定値を確認します。
    // クライアントが出力トークン数を指定しないことは認めます。
    if (kind == "statelessResponses" || constraint["outputTokenParameter"] != null) {
        // 設定から正しい出力トークン項目名と上限を読み、異なる項目名の混在を検出します。
        var tokenField = (string)constraint["outputTokenParameter"];
        var maxTokens = constraint["maxOutputTokens"];
        var tokenNames = new string[] { "max_output_tokens", "max_completion_tokens", "max_tokens" };
        if (!tokenNames.Contains(tokenField) || (kind == "statelessResponses" && tokenField != "max_output_tokens")) { return result.ToString(Newtonsoft.Json.Formatting.None); }
        var body = context.Request.Body.As<JObject>(preserveContent: true);
        if (body == null || tokenNames.Any(name => name != tokenField && body[name] != null)) {
            result["status"] = 400;
            result["code"] = "INVALID_OUTPUT_LIMIT";
            return result.ToString(Newtonsoft.Json.Formatting.None);
        }

        // モデルごとの公表上限表があれば使います。nullは、上流が決める自動上限として扱います。
        var byModel = constraint["maxOutputTokensByModel"] as JObject;
        var automatic = false;
        if (byModel != null) {
            // 本文のmodelを基にし、先行処理が解決済みのモデル名を用意していればそちらを使います。
            JToken model = body["model"];
            if (context.Variables.ContainsKey("gatewayEffectiveModel")
                    && !String.IsNullOrEmpty((string)context.Variables["gatewayEffectiveModel"])) {
                model = new JValue((string)context.Variables["gatewayEffectiveModel"]);
            }
            // モデル名または対応する上限がない場合は、初期結果の設定不備を返します。
            if (model?.Type != JTokenType.String || byModel.Property((string)model) == null) { return result.ToString(Newtonsoft.Json.Formatting.None); }
            maxTokens = byModel[(string)model];
            automatic = maxTokens == null || maxTokens.Type == JTokenType.Null;
        }
        if (!automatic && (maxTokens == null || maxTokens.Type != JTokenType.Integer || (long)maxTokens < 1)) { return result.ToString(Newtonsoft.Json.Formatting.None); }
        // 指定された出力トークン数は整数・1以上とし、公表上限があれば超過も400にします。
        var value = body[tokenField];
        if (value != null && (value.Type != JTokenType.Integer || (long)value < 1)) {
            result["status"] = 400;
            result["code"] = "INVALID_OUTPUT_LIMIT";
            return result.ToString(Newtonsoft.Json.Formatting.None);
        }
        if (value != null && !automatic && (long)value > (long)maxTokens) {
            result["status"] = 400;
            result["code"] = "OUTPUT_LIMIT_EXCEEDED";
            return result.ToString(Newtonsoft.Json.Formatting.None);
        }
    }
    // 整数範囲・配列の要素数・文字列の選択肢は、設定のある項目だけを検査します。
    var integers = constraint["integerLimits"] as JObject;
    var arrays = constraint["arrayLimits"] as JObject;
    var choices = constraint["stringChoices"] as JObject;
    // これらの検査が必要な場合だけ本文をJSONとして読み、条件違反は後ろのcatchへ渡します。
    if ((integers != null && integers.Count > 0) || (arrays != null && arrays.Count > 0) || (choices != null && choices.Count > 0)) {
        var body = context.Request.Body.As<JObject>(preserveContent: true);
        if (body == null) { throw new Exception(); }

        // 整数は必須・任意と上下限を確認します。APIMの構文に合わせ、ifとforeachの波括弧を明示します。
        if (integers != null) {
            foreach (var field in integers.Properties()) {
                var value = body[field.Name];
                if (value == null && ((bool?)field.Value["required"] != true)) { continue; }
                if (value == null || value.Type != JTokenType.Integer || (long)value < (long)field.Value["minimum"] || (long)value > (long)field.Value["maximum"]) { throw new Exception(); }
            }
        }
        // 配列が指定されている場合は、その要素数が設定上限を超えていないか確認します。
        if (arrays != null) {
            foreach (var field in arrays.Properties()) {
                var value = body[field.Name] as JArray;
                if (body[field.Name] != null && value != null && value.Count > (int)field.Value) { throw new Exception(); }
            }
        }
        // 文字列が指定されている場合は、設定した選択肢のいずれかと一致するか確認します。
        if (choices != null) {
            foreach (var field in choices.Properties()) {
                var value = body[field.Name];
                if (value != null && (value.Type != JTokenType.String || !((JArray)field.Value).Any(v => (string)v == (string)value))) { throw new Exception(); }
            }
        }
    }
    // 埋め込みでは、提供元ごとに入力項目と受け付ける形式を確認します。
    if (kind == "textEmbeddings") {
        var openai = (string)constraint["embeddingInputField"] == "input";
        var field = openai ? "input" : "texts";
        var body = context.Request.Body.As<JObject>(preserveContent: true);
        var texts = body?[field] as JArray;

        // 空白だけの入力を判定するため、Portalと同じECMAScriptの空白文字を用います。
        // Trimは判定に使い、送信する本文は書き換えません。
        var whitespace = "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff".ToCharArray();
        // 別の提供元の項目が混在していないか確認します。
        var otherProviderFields = openai ? new string[] { "texts", "input_type", "output_dimension", "embedding_types" } : new string[] { "input", "dimensions", "encoding_format" };
        if (body == null || otherProviderFields.Any(name => body.Property(name) != null)) { throw new Exception(); }
        var input = body[field];
        // OpenAIは単一テキスト・テキスト配列・トークン配列を扱い、他方はテキスト配列を扱います。
        var scalarText = openai && input?.Type == JTokenType.String && ((string)input).Trim(whitespace).Length > 0;
        var textBatch = texts != null && texts.Count > 0 && texts.All(item => item.Type == JTokenType.String && ((string)item).Trim(whitespace).Length > 0);
        var tokenInput = openai && texts != null && texts.Count > 0 && (texts.All(item => item.Type == JTokenType.Integer && (long)item >= 0)
            || texts.All(item => item is JArray && ((JArray)item).Count > 0 && ((JArray)item).All(token => token.Type == JTokenType.Integer && (long)token >= 0)));
        if (!scalarText && !textBatch && !tokenInput) { throw new Exception(); }
        // 登録モデルの寸法表が揃っているか確認し、表にあるモデルだけ寸法条件を検査します。
        var models = constraint["allowedModels"] as JArray;
        var dimensions = constraint["embeddingDimensionsByModel"] as JObject;
        if (models == null || dimensions == null || models.Count != dimensions.Count) { return result.ToString(Newtonsoft.Json.Formatting.None); }

        var model = body["model"];
        if (model?.Type == JTokenType.String && models.Any(item => (string)item == (string)model)) {
            // モデルの最大寸法・既定寸法と、任意の許可寸法一覧の整合性を確認します。
            var bounds = dimensions[(string)model] as JObject;
            var maximum = bounds?["maximum"];
            var defaultValue = bounds?["default"];
            var allowed = bounds?["allowed"] as JArray;
            if (maximum?.Type != JTokenType.Integer || (long)maximum < 1
                    || defaultValue?.Type != JTokenType.Integer || (long)defaultValue < 1 || (long)defaultValue > (long)maximum
                    || (!openai && allowed == null)
                    || (allowed != null && (allowed.Count == 0 || allowed.Any(item => item.Type != JTokenType.Integer || (long)item < 1 || (long)item > (long)maximum)
                            || !allowed.Any(item => (long)item == (long)defaultValue) || !allowed.Any(item => (long)item == (long)maximum)))) { return result.ToString(Newtonsoft.Json.Formatting.None); }
            // クライアントが寸法を指定した場合は、最大値と、一覧があればその選択肢に照合します。
            var requested = body[openai ? "dimensions" : "output_dimension"];
            if (requested != null && (requested.Type != JTokenType.Integer || (long)requested < 1 || (long)requested > (long)maximum
                    || (allowed != null && !allowed.Any(item => (long)item == (long)requested)))) { throw new Exception(); }
        }
    }
    // 並べ替えでは、検索文query・対象documents・任意の取得件数top_nを確認します。
    if (kind == "rerank") {
        var body = context.Request.Body.As<JObject>(preserveContent: true);
        var documents = body?["documents"] as JArray;
        var query = body?["query"];
        var top = body?["top_n"];

        // Portalと同じ空白文字で空欄を判定します。検索文と文書の送信内容は変更しません。
        var whitespace = "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff".ToCharArray();
        // 検索文と文書は空でない文字列、文書配列は1件以上、top_nは文書数以内の整数とします。
        if (query?.Type != JTokenType.String || ((string)query).Trim(whitespace).Length == 0
                || documents == null || documents.Count == 0
                || documents.Any(item => item.Type != JTokenType.String || ((string)item).Trim(whitespace).Length == 0)
                || (top != null && (top.Type != JTokenType.Integer || (long)top < 1 || (long)top > documents.Count))) {
            throw new Exception();
        }
    }
    // すべての検査を通ったので、後続へ進める結果と200を返します。
    result["allowed"] = true;
    result["status"] = 200;
    result["code"] = "REQUEST_WITHIN_LIMITS";
    return result.ToString(Newtonsoft.Json.Formatting.None);
} catch {
    // 本文の解析や以降の条件確認で起きた例外は、既存の400・INVALID_REQUESTへまとめます。
    // 例外の詳細や本文、内部設定の生JSONは返しません。
    result["status"] = 400;
    result["code"] = "INVALID_REQUEST";
    return result.ToString(Newtonsoft.Json.Formatting.None);
}
