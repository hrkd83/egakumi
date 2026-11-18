// 目的：共通認証が作った本人・購読の識別情報を、監査ログへ渡す前に形式と項目の整合性を再確認します。
// ここで新たにキーやトークンを検証するのではなく、gatewayAuthenticatedSubjectへ保存された共通認証の結果だけを扱います。
/* 共通主体の投影。 */
// authenticatedSubjectは監査へ出力するJSONです。情報が取得できない場合のstatusをunverified（未確認）にします。
var authenticatedSubject=new JObject { ["status"]="unverified" };
// APIM変数には異なる型の値が入り得るため、取得した値をまずobject型のobsVで受け取ります。
object obsV;
// TryGetValueは変数があればtrueを返し、out obsVへ値を渡します。変数がなければ初期状態unverifiedのままです。
if(context.Variables.TryGetValue("gatewayAuthenticatedSubject",out obsV)) {
  // 変数があるだけでは確認済みにしません。内容の確認に通らない場合に残るstatusをinconsistent（不整合）にします。
  authenticatedSubject["status"]="inconsistent";
  // 不正なJSONや型変換による例外が、監査のために通常のAPI処理を中断しないようtry/catchで囲みます。
  try {
    // as stringは値が文字列ならその文字列、別の型ならnullを返します。型が違うだけでは例外にしません。
    var obsR=obsV as string;
    // 文字列であり1600文字以下の場合だけJSONとして解析します。nullや長すぎる値は不整合のままです。
    if(obsR!=null&&obsR.Length<=1600) {
      // JObject.ParseでJSON文字列をオブジェクトに戻し、status項目を文字列として取り出します。
      var obs=JObject.Parse(obsR); var obsS=(string)obs["status"];
      // 未確認・不整合という既知の状態はstatusだけを引き継ぎます。付随する任意の項目は監査へ転記しません。
      if(obsS=="unverified"||obsS=="inconsistent") { authenticatedSubject["status"]=obsS; }
      // verified（確認済み）と書かれていても、そのまま採用せず、以下で項目と識別子の対応を確認します。
      else if(obsS=="verified") {
        // kindがentraUserなら社員本人の経路です。その他の値は、後の条件でAPIM購読の経路かどうかを確認します。
        var obsU=(string)obs["kind"]=="entraUser";
        // 共通4項目に、本人なら利用枠・テナント・本人・クライアントのID、購読なら購読IDを加えた必須項目名の配列を作ります。
        // 「条件 ? 本人用の文字列 : 購読用の文字列」で項目を選び、Split(',')でカンマ区切りを配列へ変換します。
        var obsN=("status,kind,method,callerId,"+(obsU?"allocationId,tenantId,principalId,clientId":"subscriptionId")).Split(',');
        // 本人の経路はmethod=user、購読の経路はkind=apimSubscriptionかつmethod=subscriptionKeyである必要があります。
        // 本人の識別と購読の識別を混在させないよう、三項演算子で経路ごとの組み合わせを選んで確認します。
        if((obsU?(string)obs["method"]=="user":(string)obs["kind"]=="apimSubscription"&&(string)obs["method"]=="subscriptionKey")
          // Properties().Count()で余分な項目がないこと、Allで全必須項目の存在・文字列型・項目ごとの長さを確認します。
          // 購読IDは256文字、購読のcallerIdは接頭辞subscription:の13文字を足して269文字まで扱います。その他の上限は253文字のままです。
          // obs[n]?.Typeの?.は項目がないときにnullを返すため、その項目を「文字列型ではない」として拒否できます。
          &&obs.Properties().Count()==obsN.Length
          &&obsN.All(n=>obs[n]?.Type==JTokenType.String
            &&((string)obs[n]).Length<=(n=="subscriptionId"?256:!obsU&&n=="callerId"?269:253))) {
          // callerIdの期待値を作り直します。本人はテナントID＋本人ID、購読は購読IDを使い、同じ識別子として混同しません。
          var obsC=obsU?"user:"+(string)obs["tenantId"]+":"+(string)obs["principalId"]:"subscription:"+(string)obs["subscriptionId"];
          // callerIdが期待値と一致し、本人の経路ではallocationIdも同じ利用者を指す場合だけ、検査したJSON全体を採用します。
          // !obsU || ...は「購読なら利用枠IDの比較は不要、本人なら比較が必要」という条件です。
          if((string)obs["callerId"]==obsC&&(!obsU||(string)obs["allocationId"]==obsC)) { authenticatedSubject=obs; }
        }
      }
    }
  // 解析や変換に失敗しても例外の詳細はログへ転記せず、status=inconsistentを保って後の監査処理へ進みます。
  } catch { }
}
// この後の監査処理はauthenticatedSubjectを出力します。本人確認結果を新たに変更する処理ではありません。
