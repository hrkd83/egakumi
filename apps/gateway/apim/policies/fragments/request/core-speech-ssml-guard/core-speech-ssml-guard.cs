// 目的：音声合成の本文サイズと、SSMLに含まれるDTD・ENTITY宣言を確認します。
// 同名XMLへこのC#と説明コメントを展開し、Azureの同名フラグメントを作ります。
// XMLの解析・実体解決は行わず、元の本文を保持して後続へ渡します。

// 宣言の検査で使う成功・拒否結果を用意します。本文そのものは結果へ保存しません。
var accepted = new JObject(new JProperty("allowed", true), new JProperty("status", 200),
    new JProperty("code", "SPEECH_MARKUP_ALLOWED")).ToString(Newtonsoft.Json.Formatting.None);
var rejected = new JObject(new JProperty("allowed", false), new JProperty("status", 400),
    new JProperty("code", "INVALID_SPEECH_MARKUP")).ToString(Newtonsoft.Json.Formatting.None);

// 直前の共有サイズ検査で確認済みの操作上限を使います。内部設定の例外はAPIのon-errorへ渡します。
var constraint = JObject.Parse((string)context.Variables["gatewayOperationConstraint"]);
var maxBytes = (long)constraint["maxRequestBytes"];
// 本文を一度だけ読み出し、同じバイト列でサイズ確認と宣言検査を行います。
var bytes = context.Request.Body == null ? new byte[0] : context.Request.Body.As<byte[]>(preserveContent: true);
if (bytes != null && bytes.Length > maxBytes) {
    return new JObject(new JProperty("allowed", false), new JProperty("status", 413),
        new JProperty("code", "REQUEST_TOO_LARGE")).ToString(Newtonsoft.Json.Formatting.None);
}
// 空本文は従来どおりこの部品で拒否せず、音声合成サービスの受付へ渡します。
if (bytes == null || bytes.Length == 0) { return accepted; }

// UTF-16/32以外の非ASCII互換エンコーディングは、文字を推測せず従来どおり拒否します。
// 対象はEBCDICと、通常と異なるUCS-4のバイト順です。
if (bytes.Length >= 4 && ((bytes[0] == 0x4c && bytes[1] == 0x6f && bytes[2] == 0xa7 && bytes[3] == 0x94) || (bytes[0] == 0 && bytes[1] == 0 && bytes[2] == 0xff && bytes[3] == 0xfe) || (bytes[0] == 0xfe && bytes[1] == 0xff && bytes[2] == 0 && bytes[3] == 0) || (bytes[0] == 0 && bytes[1] == 0 && bytes[2] != 0 && bytes[3] == 0) || (bytes[0] == 0 && bytes[1] != 0 && bytes[2] == 0 && bytes[3] == 0))) { return rejected; }

// 先頭のバイトからUTF-8/16/32を選びます。宣言を調べるための文字列だけを作ります。
// 小文字化は検査用に限り、転送する本文バイト列は変更しません。
var encoding = System.Text.Encoding.UTF8;
if (bytes.Length >= 4 && ((bytes[0] == 0xff && bytes[1] == 0xfe && bytes[2] == 0 && bytes[3] == 0) || (bytes[0] != 0 && bytes[1] == 0 && bytes[2] == 0 && bytes[3] == 0))) { encoding = System.Text.Encoding.UTF32; }
else if (bytes.Length >= 4 && ((bytes[0] == 0 && bytes[1] == 0 && bytes[2] == 0xfe && bytes[3] == 0xff) || (bytes[0] == 0 && bytes[1] == 0 && bytes[2] == 0 && bytes[3] != 0))) { encoding = System.Text.Encoding.GetEncoding(12001); }
else if (bytes.Length >= 2 && ((bytes[0] == 0xff && bytes[1] == 0xfe) || (bytes[0] != 0 && bytes[1] == 0))) { encoding = System.Text.Encoding.Unicode; }
else if (bytes.Length >= 2 && ((bytes[0] == 0xfe && bytes[1] == 0xff) || (bytes[0] == 0 && bytes[1] != 0))) { encoding = System.Text.Encoding.BigEndianUnicode; }
var xml = encoding.GetString(bytes).ToLowerInvariant();

// '<'の位置を順に調べます。コメント・CDATA・処理命令に書かれた宣言の例は読み飛ばします。
var position = 0;
while (position < xml.Length) {
    var start = xml.IndexOf("<", position, StringComparison.Ordinal);
    if (start < 0) { return accepted; }
    if (start + 4 <= xml.Length && xml.Substring(start, 4) == "<!--") {
        var end = xml.IndexOf("-->", start + 4, StringComparison.Ordinal);
        if (end < 0) { return accepted; }
        position = end + 3;
    } else if (start + 9 <= xml.Length && xml.Substring(start, 9) == "<![cdata[") {
        var end = xml.IndexOf("]]>", start + 9, StringComparison.Ordinal);
        if (end < 0) { return accepted; }
        position = end + 3;
    } else if (start + 2 <= xml.Length && xml.Substring(start, 2) == "<?") {
        var end = xml.IndexOf("?>", start + 2, StringComparison.Ordinal);
        if (end < 0) { return accepted; }
        position = end + 2;
    } else if (start + 2 <= xml.Length && xml.Substring(start, 2) == "<!") {
        // 宣言の前の空白を飛ばし、doctype/entityという単語と末尾の区切りを確認します。
        var keyword = start + 2;
        while (keyword < xml.Length && (xml[keyword] == ' ' || xml[keyword] == '\t' || xml[keyword] == '\r' || xml[keyword] == '\n')) { keyword++; }
        foreach (var declaration in new [] { "doctype", "entity" }) {
            var end = keyword + declaration.Length;
            if (end <= xml.Length && xml.Substring(keyword, declaration.Length) == declaration && (end == xml.Length || !(Char.IsLetterOrDigit(xml[end]) || xml[end] == '_' || xml[end] == ':' || xml[end] == '-' || xml[end] == '.'))) { return rejected; }
        }
        position = start + 2;
    } else {
        // 通常タグを読み飛ばします。引用符の中の'>'はタグの終端として扱いません。
        // タグ内に生の'<'があればその位置へ戻り、後続の宣言を見落とさないようにします。
        position = start + 1;
        var quote = '\0';
        while (position < xml.Length) {
            var current = xml[position++];
            if (quote != '\0') { if (current == quote) { quote = '\0'; } }
            else if (current == '\'' || current == '"') { quote = current; }
            else if (current == '>') { break; }
            else if (current == '<') { position--; break; }
        }
    }
}
return accepted;
