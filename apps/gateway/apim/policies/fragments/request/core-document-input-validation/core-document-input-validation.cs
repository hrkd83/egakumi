// 目的：Document解析のmodelクエリが、転送先を一意に指定できることを確認します。
// この同名C#と説明コメントをXMLへ展開して、Azureの同名フラグメントを生成します。
// 本文は読み取り・解析・変換せず、解析の受付可否を提供元へ任せます。
// 結果はdocumentInputStatusへ保存し、監査と公開400／500応答は呼出し元のAPIが行います。

try {
    // 同名XMLが取得したmodelを使います。撤去したモデル一覧のために設定JSONを読み直しません。
    var model = (string)context.Variables["documentModel"];

    // modelが存在し、その値が一つあることを確認します。他のクエリ項目数では拒否しません。
    // 空欄と、URLの階層を変える単独の . / .. は転送先を示すモデルIDとして使いません。
    if (!context.Request.Url.Query.ContainsKey("model")
        || context.Request.Url.Query["model"].Length != 1
        || String.IsNullOrWhiteSpace(model) || model == "." || model == "..") {
        return 400;
    }

    // 本文の形式・先頭バイト・空値・実サイズ・Content-Encodingはここで判定しません。
    // Native JSONのurlSource/base64Sourceも解釈せず、同じ本文のまま後続へ進めます。
    return 200;
} catch {
    // modelの内部変数を引き継げない例外は500です。入力条件の不一致400とは区別します。
    // 例外の詳細や内部情報は、応答と監査へ出力しません。
    return 500;
}
