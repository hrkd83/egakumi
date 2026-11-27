// 目的：この要求が、CMのログイン開始画面・受付や、認証後の戻り先（callback）用APIへのアクセスか判定します。
// 通常の画面遷移・フォーム送信・認証後のリダイレクトにはAPI用のキー／Bearerヘッダーが付かないため、その受付だけ共通検証から除外します。
// この式は除外対象かを返すだけです。実際のキー／Bearer検証の省略はSTEP4、取引や本人の確認は後続の操作・Broker・CMが担当します。
// 生成時にCM設定の有無をtrue／falseへ置き換えます。CM未設定なら、後の条件を評価せずfalseを返します。
return __CREDENTIAL_BROWSER_CONFIGURED__
    // APIMがAPIと操作を特定できており、CMブラウザー専用APIであることを確認します。
    && context.Api != null && context.Operation != null && context.Api.Id == "credential-manager-browser"
    // 操作IDだけで許可せず、HTTPメソッドと転送前の元のパスも組合せで完全一致させます。
    && ((context.Operation.Id == "launch" && context.Request.Method == "GET"
         // GETの起動画面へ進みます。この判定だけで取引や本人が確認済みになることはありません。
         && context.Request.OriginalUrl.Path == "/auth/connections/browser/launch")
        || (context.Operation.Id == "launch-post" && context.Request.Method == "POST"
            // 同じパスへのPOSTは起動受付です。後続がOrigin・起動秘密・取引の期限を確認します。
            && context.Request.OriginalUrl.Path == "/auth/connections/browser/launch")
        || (context.Operation.Id == "callback" && context.Request.Method == "GET"
            // CM接続の戻り先です。後続がCookie・state・取引と、接続先の本人との一致を確認します。
            && context.Request.OriginalUrl.Path == "/auth/connections/browser/callback")
        || (context.Operation.Id == "identity-callback" && context.Request.Method == "GET"
            // 本人認証の戻り先です。後続がCookie・stateとEntraの認証結果を確認します。
            && context.Request.OriginalUrl.Path == "/auth/connections/browser/identity-callback"));
// trueならSTEP4で共通のキー／Bearer検証を省略し、falseなら通常APIのキー／Bearer検証へ進みます。ここでは資格情報を読まず、ログも出力しません。
