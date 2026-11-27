# 接続ログインのブラウザー受付と共通認証の例外

このフォルダは、AzureのPolicy fragment `core-credential-browser-exception` の生成元を管理する。
同名XMLは判定結果をAPIM変数へ保存する処理、同名C#は対象の操作かどうかを判定する条件式を持つ。

```text
core-credential-browser-exception.xml ＋ 同名C#
    ↓ 配備時に条件式とCM設定の有無を展開
Azureのcore-credential-browser-exception
    ↓ STEP2の入口確認後に実行
gatewayCredentialBrowserException（true／false）
    ↓ 共通ポリシーのchoose先頭で参照
true：ブラウザー操作の取引確認へ進む
false：通常のキー／Bearer認証へ進む
```

STEP3は、CMのログイン開始画面・受付と、認証後の戻り先（callback）用APIへのアクセスかを判定する処理である。モデル・ツール等の通常APIへのアクセスは、この例外に含めない。

通常の画面遷移・フォーム送信・認証後のリダイレクトは、API呼出し用の購読キーや `Authorization: Bearer ...` ヘッダーを付けて送る仕組みではない。これらの受付にも共通のキー／Bearer検証を要求すると、ログインを開始したり認証結果を受け取ったりする前に拒否され、接続ログインが完了しない。

そのため、次の受付と完全一致する要求だけをSTEP3で判定し、STEP4で共通のキー／Bearer検証を省略する。Entraへのサインインや、後続の操作・Broker・CMによる取引・本人の確認は引き続き行う。

| 受付の役割 | 操作ID | HTTPメソッド | 元のパス |
| --- | --- | --- | --- |
| ブラウザーの起動画面 | `launch` | GET | `/auth/connections/browser/launch` |
| ログインを開始する起動受付 | `launch-post` | POST | `/auth/connections/browser/launch` |
| 本人認証の戻り先 | `identity-callback` | GET | `/auth/connections/browser/identity-callback` |
| 外部サービスへの接続完了の戻り先 | `callback` | GET | `/auth/connections/browser/callback` |

CMが構築設定に存在し、API IDが `credential-manager-browser` と一致し、操作ID・HTTPメソッド・元のパスも表の組合せと完全一致する場合だけ `true` を返す。
`__CREDENTIAL_BROWSER_CONFIGURED__` は構築設定から生成する内部の値であり、利用者が送るヘッダーや追加の公開パラメーターではない。
この部品は共通ポリシーのSTEP2の入口確認後、STEP3で実行する。判定結果はSTEP4の認証分岐が参照する。

判定が `true` でも、社員本人の認証が成功したことを意味しない。
GETの起動画面、POSTでの起動受付、本人認証の戻り先、CM接続の戻り先はそれぞれ役割が異なり、後続の操作・Broker・CMが必要な起動秘密、Origin、Cookie、state、取引の期限、本人の一致を確認する。
例外に一致した要求だけ、共通ポリシーが既存の `gatewayCommonAuthenticationAnonymous` を `true` にする。
本人を確認した監査情報は、後続の認証結果に応じて別に記録する。

この部品は、別のフラグメントや監査ソースを取り込まない。
同名C#を同名XMLへ展開し、C#の説明コメントもAzureへ保持する。
OBOによる下流API用トークン取得は、このブラウザー入口の例外とは別の責務であり、ここでは実装しない。
