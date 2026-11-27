// 同名XMLの複数statement処理を固定BEGIN/END区画で管理します。
// use-result / expires-onを各markerへ個別展開し、短い固定組立て2式はXMLに保持します。


// BEGIN use-result
// 目的：Graph本人照合後、SQLによる使用直前の期限・失効確認の応答を検査します。
// 同名XMLのgatewayConnectionUseResult式だけを所有し、更新候補・世代・期限の検査を維持します。
var denied=new JObject(new JProperty("allowed",false),new JProperty("status",503),new JProperty("errorCode","ConnectionUnavailable"));
try {
  var response=context.Variables.ContainsKey("gatewayConnectionUseResponse") ? context.Variables["gatewayConnectionUseResponse"] as IResponse : null;
  if(response==null || response.StatusCode!=200 || response.Body==null) { return denied.ToString(); }
  var bytes=response.Body.As<byte[]>(preserveContent:true); if(bytes.Length>16384) { return denied.ToString(); }
  var value=JObject.Parse(System.Text.Encoding.UTF8.GetString(bytes));
  if(value["allowed"]?.Type!=JTokenType.Boolean || value["status"]?.Type!=JTokenType.Integer) { return denied.ToString(); }
  var allowed=(bool)value["allowed"]; var status=(int)value["status"];
  if(!allowed && (string)value["errorCode"]!="SessionUpdateRequired") {
    if(status==409 || status==403) { denied["status"]=status; denied["errorCode"]=status==409 ? "ConnectionRequired" : "ConnectionNotAuthorized"; } return denied.ToString();
  }
  if((allowed && status!=200) || (!allowed && status!=409) || value["protocolVersion"]?.Type!=JTokenType.Integer || (int)value["protocolVersion"]!=2) { return denied.ToString(); }
  var result=new JObject(new JProperty("allowed",allowed),new JProperty("status",status),new JProperty("protocolVersion",2));
  foreach(var field in new [] { "generation","idleExpiresAt","absoluteExpiresAt","nextRotationAt" }) {
    if(value[field]?.Type!=JTokenType.Integer || (long)value[field]<1 || (long)value[field]>4102444800L) { return denied.ToString(); } result[field]=value[field];
  }
  if((long)result["idleExpiresAt"]>(long)result["absoluteExpiresAt"]) { return denied.ToString(); }
  if(value["rotation"]!=null) {
    var rotation=value["rotation"] as JObject;
    if(rotation==null || !System.Text.RegularExpressions.Regex.IsMatch((string)rotation["rotationId"]??"","^[a-f0-9]{64}$")
      || !System.Text.RegularExpressions.Regex.IsMatch((string)rotation["candidateSession"]??"","^cs2[.][a-f0-9]{64}[.][a-f0-9]{64}$")) { return denied.ToString(); }
    var candidate=new JObject(new JProperty("rotationId",rotation["rotationId"]),new JProperty("candidateSession",rotation["candidateSession"]));
    foreach(var field in new [] { "fromGeneration","generation","expiresAt" }) {
      if(rotation[field]?.Type!=JTokenType.Integer || (long)rotation[field]<1 || (long)rotation[field]>4102444800L) { return denied.ToString(); } candidate[field]=rotation[field];
    }
    if((long)candidate["fromGeneration"]!=(long)result["generation"] || (long)candidate["generation"]!=(long)candidate["fromGeneration"]+1
      || ((string)candidate["candidateSession"]).Substring(0,69)!=((string)context.Variables["gatewayConnectionRequestSession"]).Substring(0,69)) { return denied.ToString(); }
    result["rotation"]=candidate;
  }
  if(!allowed) { if(result["rotation"]==null) { return denied.ToString(); } result["errorCode"]="SessionUpdateRequired"; }
  return result.ToString(Newtonsoft.Json.Formatting.None);
} catch { return denied.ToString(Newtonsoft.Json.Formatting.None); }
// END use-result


// BEGIN expires-on
var auth = (Authorization)context.Variables["gatewayConnectionContext"]; object raw; long expiry;
if (auth.Claims != null && auth.Claims.TryGetValue("expires_on", out raw) && (raw is string || raw is long || raw is int)) {
  var value = Convert.ToString(raw);
  if (System.Text.RegularExpressions.Regex.IsMatch(value, "^[0-9]{9,10}$") && Int64.TryParse(value, out expiry) && expiry >= 946684800L && expiry <= 4102444800L) { return expiry; }
} return 0L;
// END expires-on
