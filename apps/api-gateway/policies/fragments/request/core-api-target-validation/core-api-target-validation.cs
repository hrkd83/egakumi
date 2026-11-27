// 目的：準備済みの操作設定に従い、モデルと操作固有の本文機能を確認します。
// この同名C#をcore-api-target-validation.xmlへ展開してAzureの同名部品を作ります。
// 結果はgatewayTargetDecisionのallowed・status・codeへ保存し、監査と失敗応答はHTTP APIが行います。
// モデルの利用可否と本文modelの入力型は提供元へ任せ、Responses・Hosted・A2AのJSON読取り不備400と残る条件の403を分けます。

// 基盤が準備した操作設定を直接読みます。形式の再検査は追加しません。
JObject constraint;
try {
  constraint = JObject.Parse((string)context.Variables["gatewayOperationConstraint"]);
} catch {
  // 内部設定を読めない原因は、モデル・本文の条件に合わない403とは別に返します。
  return new JObject(new JProperty("allowed", false), new JProperty("status", 500),
    new JProperty("code", "OPERATION_SETTINGS_INVALID")).ToString(Newtonsoft.Json.Formatting.None);
}
// 既存の判定結果だけを状態・原因へ対応付けます。監査や公開応答は呼び出し元が担当します。
var accepted = new JObject(new JProperty("allowed", true), new JProperty("status", 200),
  new JProperty("code", "TARGET_ALLOWED")).ToString(Newtonsoft.Json.Formatting.None);
var rejected = new JObject(new JProperty("allowed", false), new JProperty("status", 403),
  new JProperty("code", "AUTHORIZATION_DENIED")).ToString(Newtonsoft.Json.Formatting.None);

try {
  var kind = (string)constraint["kind"];
  if (kind == "pathModel") {
    var target = context.Request.MatchedParameters.GetValueOrDefault((string)constraint["parameter"], "");
    // モデルの利用可否は提供元へ任せます。空欄とURL階層を表す名前は従来どおり拒否します。
    // 後続のURI書換えが、templateのパラメーター値を一つの要素としてエンコードします。
    return (!String.IsNullOrWhiteSpace(target) && target != "." && target != "..") ? accepted : rejected;
  }
  if (kind == "bodyModel" || kind == "textEmbeddings" || kind == "rerank") {
    // この部品ではモデル値を使いません。型確認だけの本文読取りを省き、提供元へ渡します。
    // モデル別設定とaliasの選択が必要な場合は、先行処理が必要な確認を行います。
    return accepted;
  }
  if (kind == "statelessResponses") {
    // メソッドとパスは正式設定検査とAPIMの操作照合へ任せ、本文の機能条件を確認します。
    var invalidBody = new JObject(new JProperty("allowed", false), new JProperty("status", 400),
      new JProperty("code", "INVALID_REQUEST_BODY")).ToString(Newtonsoft.Json.Formatting.None);
    JObject body;
    try {
      body = context.Request.Body.As<JObject>(preserveContent: true);
    } catch {
      // 本文をJSONオブジェクトとして読めない場合だけ、機能の受付条件による403から分けます。
      return invalidBody;
    }
    // モデル値は再確認せず、コンテナ参照と残るツール条件のために本文を読みます。
    if (body == null) { return invalidBody; }
    // 内蔵Code Interpreterのコンテナは利用者別に隔離されず、inputからも参照できます。
    // 配列直下の明示的な呼出しItemだけを拒否し、深い位置や一般のIDを走査しません。
    var inputItems = body["input"] as JArray;
    if (inputItems != null && inputItems.Any(item => item.Type == JTokenType.Object
      && item["type"]?.Type == JTokenType.String && (string)item["type"] == "code_interpreter_call")) { return rejected; }
    // 受け付けたinputのID・type・ファイルIDやprevious_response_id・promptの参照は、原値で提供元へ渡します。
    // 本文の参照要素を削除せず、保存済み資源のIDを理由に独自の403へしません。
    // 後続の保存防止変換だけがstore=false・指定時background=false・conversation=nullへ揃えます。
    // conversationはResponseのstoreとは別に会話へ入出力を保存するため、指定を解除します。
    if (body["tools"] != null) {
      var tools = body["tools"] as JArray;
      if (tools == null || tools.Any(t => t.Type != JTokenType.Object || (string)t["type"] != "function")) { return rejected; }
    }
    // 保存資源を持つ内蔵ツールの明示選択を止め、function指定とfunctionだけのallowed_toolsを受け付けます。
    // toolsの限定だけで未宣言ツールや過去のツールを選べない保証には依存しません。
    var choice = body["tool_choice"] as JObject;
    if (choice != null && (string)choice["type"] != "function") {
      // 選択対象の種類だけを確認し、名前・modeや空配列の適否は原値を受け取る提供元へ任せます。
      var selectedTools = choice["tools"] as JArray;
      if ((string)choice["type"] != "allowed_tools" || selectedTools == null
        || selectedTools.Any(t => t.Type != JTokenType.Object || t["type"]?.Type != JTokenType.String
          || (string)t["type"] != "function")) { return rejected; }
    }
    // includeは応答の追加情報を選ぶ指定です。一覧・型は提供元へ任せ、値を保持します。
    return accepted;
  }
  if (kind == "hostedAgentPost") {
    var invalidBody = new JObject(new JProperty("allowed", false), new JProperty("status", 400),
      new JProperty("code", "INVALID_REQUEST_BODY")).ToString(Newtonsoft.Json.Formatting.None);
    JObject body;
    try {
      body = context.Request.Body.As<JObject>(preserveContent: true);
    } catch {
      // Agent入口も、本文を読めない入力不備と固定Agent・状態参照の条件による403を分けます。
      return invalidBody;
    }
    if (body == null) { return invalidBody; }
    // Foundryに登録済みの指示・ツールと固定Agentを使い、要求から定義・対象を切り替えません。
    // サンプルの4項目許可表は使わず、この目的に関わる項目の既存拒否だけを明示します。
    var definitionFields = new string[] { "instructions", "tools", "agent_reference", "prompt" };
    if (body.Properties().Any(p => definitionFields.Contains(p.Name))) { return rejected; }
    // 既存応答・実行sessionの選択は、所有者と状態の契約を別に扱うため現在の拒否を維持します。
    // input内の完全なItemsや一般IDは、この本文直下の選択指定とは区別して保持します。
    var stateFields = new string[] { "previous_response_id", "agent_session_id" };
    if (body.Properties().Any(p => stateFields.Contains(p.Name))) { return rejected; }
    // include・text・出力設定などの入力仕様は提供元へ任せ、その他の項目も原値で渡します。
    // streamの型の適否は提供元へ任せ、値を変更せず渡します。
    // 後続の同じ本文編集がstore=falseと、指定時だけconversation=null・background=falseへ揃えます。
    // metadataのサンプル用項目・型・値では拒否しません。親要求IDを付ける後続処理は維持します。
    // Prompt／Hostedの直接入力をuser・input_textだけに限定せず、入力仕様は提供元へ任せます。
    // 欠落・null・別型も補完せず、完全なmessage／reasoning／functionや一般のID・file_idを保持します。
    // 内蔵Code Interpreterの入力だけは、通常Responsesと同じ配列直下の明示Itemを拒否します。
    // 深い位置や一般のIDを走査せず、共有コンテナの保存・参照を通常入力と区別します。
    var inputItems = body["input"] as JArray;
    if (inputItems != null && inputItems.Any(item => item.Type == JTokenType.Object
      && item["type"]?.Type == JTokenType.String && (string)item["type"] == "code_interpreter_call")) { return rejected; }
    // 本文は編集せず、inputの値・順序と未指定をそのまま後続へ渡します。
    return accepted;
  }
  if (kind == "a2aJsonRpc") {
    var invalidBody = new JObject(new JProperty("allowed", false), new JProperty("status", 400),
      new JProperty("code", "INVALID_REQUEST_BODY")).ToString(Newtonsoft.Json.Formatting.None);
    JObject body;
    try {
      body = context.Request.Body.As<JObject>(preserveContent: true);
    } catch {
      // 本文を読めない入力不備を、読めたJSON-RPC本文の受付条件による403から分けます。
      return invalidBody;
    }
    if (body == null) { return invalidBody; }
    // idは要求と応答の対応付け用です。JSON-RPCの形式と許可メソッドの既存条件を維持します。
    return ((string)body["jsonrpc"] == "2.0" && (body["id"]?.Type == JTokenType.String || body["id"]?.Type == JTokenType.Integer) && body["method"]?.Type == JTokenType.String && ((JArray)constraint["allowedMethods"]).Any(m => (string)m == (string)body["method"])) ? accepted : rejected;
  }
  if (kind == "liveCreate") {
    // 操作の照合はAPIMへ任せ、Live固有のSDP・固定モデル・session設定は操作ポリシーで確認します。
    return accepted;
  }
  if (kind == "realtimeCreate" || kind == "realtimeHangup") {
    // 操作の照合はAPIMへ任せ、SDPと署名付きセッション所有者チケットは操作ポリシーで確認します。
    return accepted;
  }
  if (kind == "documentAnalysis" || kind == "documentAnalysisStatus") {
    // 操作の照合はAPIMへ任せ、解析開始のmodelクエリは後続の共通部品で、結果取得の所有者チケットは操作ポリシーで確認します。
    return accepted;
  }
  if (kind == "pii") {
    // 公開の{text,language}スキーマ、入力エラー、Languageへの要求・応答変換は操作ポリシーが担当します。
    // メソッド・操作IDは正式設定検査とAPIMの操作照合へ任せ、ここでは本文を読みません。
    return accepted;
  }
  return (kind == "registeredOperation") ? accepted : rejected;
} catch { return rejected; }
