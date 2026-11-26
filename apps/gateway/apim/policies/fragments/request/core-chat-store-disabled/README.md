# Chatの保存指定の無効化

`core-chat-store-disabled.xml`と同名C#から、同名のAzureフラグメントを生成します。

APIと操作の定義から配備時に選んだ、FoundryのOpenAI接続先へ送るHTTPの`POST /chat/completions`だけを親フローが呼びます。操作の接続先上書きがあればそれを使い、`bodyModel`以外や別の接続先・パス・メソッド、protocol／MCPには適用しません。対象操作は準備済み制御設定の`chatStoreDisabledOperationIds`で渡し、API名やモデル一覧には依存しません。

raw本文を一度だけ消費して読み、指定の有無・型・値によらず`store`をBooleanの`false`へ揃えます。日時形式を含む文字列値の先頭へ退避用`~`を一つ足してからJSON.NETで解析し、StringのJValueで先頭一文字だけを戻すため、小数秒やoffsetをDate型への自動変換で失いません。元の`~`・空文字・escape・property名を保持し、数値・Boolean・null・配列・オブジェクトと本文の不備はJSON.NET自身へ任せます。`model`・`messages`・`stream`などのほかの項目は維持します。前段のalias反映後の本文を使うため、解決したモデル名を元のaliasへ戻しません。

結果は今回の`set-body`へ渡す文字列として`gatewayChatRequestBody`に保持します。入力本文を監査へ記録しません。退避用Regexは各2秒で停止し、解析不備は既存catchへ渡します。読取り不備・非オブジェクト・null・本文なしでは空文字を返し、親フローが`rejected`／`INVALID_REQUEST_BODY`を記録して要求ID付きの400を返します。正常時の文字列は親の`set-body`がそのまま使い、再読取り・再解析しません。

元の本文サイズ、認証、利用枠の確認とalias反映の後、下流への転送前に実行します。保存は要求側で無効にするもので、Foundryアカウントの設定や既存の保存済み資源を変更しません。Responsesは別の既存処理でstore=falseと指定時conversation=null・background=falseへ揃え、履歴ID・inputのtype・ファイル参照は原値を保持します。

ローカル実装・関連検証済みです。関連113件の固定ソース再実行では112件が成功し、生成MCPの合成宣言が不足した対象1件を修正・再実行して成功しました。未解決の失敗・スキップはありません。途中のBicep構文、手書きARM式と生成XMLの字下げ差も修正し、実C#のstore変換、本文保持・読取り回数、実BicepとPythonの対象選択・XML生成を確認しました。実提供元での受付と非保存の確認、Azureへの反映は未実施です。
