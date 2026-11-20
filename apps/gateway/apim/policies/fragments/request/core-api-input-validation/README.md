# API操作のContent-Type宣言を確認する

`core-api-input-validation`は、操作定義が明示した入口で、Content-Typeの宣言が1値あり、MIME部分が空でないことを確認する部品です。XMLが同名C#の式を実行し、結果を真偽値`gatewayApiInputValidationPassed`へ保存します。C#と日本語の説明は生成時にXMLへ組み込みます。会社設定のMIME一覧との比較、HTTPメソッドの再確認、URLクエリによる受付制限は行いません。

```text
HTTP操作のinputContract={}
    ↓ 配備前に定義を検査し、Bicepが操作ID別の対応表を生成
gatewayControlSettings.inputContracts[操作ID]={}
    ↓ 共通認証・要求準備・query観測の後に、この部品を呼ぶ
gatewayApiInputValidationPassed
    ├─ true：宣言条件を満たすか検査対象外なので、APIの後続処理へ進む
    └─ false：APIがINVALID_CONTENT_TYPE_HEADERの拒否監査を記録し、400を返す
```

## 操作定義で対象を指定する

HTTPの`operations[].inputContract`に空のオブジェクトを明示します。これは検査対象を選ぶ印です。操作別のMIMEやクエリの許可辞書は持ちません。未指定の操作には、この部品の入力検査を追加しません。指定例は次のとおりです。

```json
{
  "inputContract": {}
}
```

これは操作定義の抜粋です。OpenAI embeddingsも同じ指定を使います。Bicepは指定された操作IDだけを対応表へ記録し、各値を空のオブジェクトにします。空の値は条件不足ではなく、全対象が共通の「宣言あり・1値・MIME部分が非空」という条件を使う意味です。メソッド・MIME一覧を重ねて持たず、API名・モデル名から対象を推測しません。HTTPメソッドとURLは既存の操作登録へ残します。

## 選ぶ対象と現在の判定

今回の標準対象は従来の13操作だけです。

| API | 入力検査を明示する操作 |
| --- | --- |
| `openai-inference-v1` | `chat-completions`・`responses-create` |
| `openai-images-v1` | `image-generate`・`image-edit` |
| `blackforest-images-v1` | `flux2-generate`・`flux2-edit` |
| `openai-embeddings-v1` | `embeddings` |
| `cohere-inference-v1` | `cohere-embeddings`・`rerank` |
| `microsoft-decision-v1` | `systemone` |
| `openai-audio-v1` | `audio-transcriptions` |
| `azure-speech-v1` | `tts`・`stt` |

この表は移行範囲を示すもので、C#に組み込むAPI名の一覧ではありません。既存のLive・Realtime・PII、生成MCP、WebSocket、外部MCPへ、この入力検査を自動追加しません。将来のHTTPサービスは、その操作定義で入力契約を明示できます。

現在の13操作はPOSTで登録されており、Content-Typeは1値必要です。`TryGetValue`で値の配列を一度だけ取得し、欠落または値が1つでなければ不適合とします。唯一の値を`;`で分けて先頭のMIME部分を取り、前後の空白を除いて空なら不適合とします。空値・空白だけの値・`; charset=utf-8`のようにMIME部分がない宣言も拒否します。

MIMEの種類を会社設定の一覧と比較しないため、`application/json`以外でも、この共通条件だけを満たせば後続へ進みます。`charset`やmultipartの`boundary`を含め、宣言自体は書き換えません。MIMEやパラメーターの完全な構文パーサーは追加せず、既存HTTP／WAFの構文保護を維持します。メソッドの照合はAPIMの操作登録へ任せ、URLクエリの未知名・複数値・任意の`api-version`もこの部品では拒否しません。

ここで確認するのはAPIMが解析した値の配列です。App Gateway等の正規化もあり得るため、配列が1値であることを、元のHTTPヘッダーに重複行がなかった証明とは扱いません。

## 配備時と要求受付時を分ける

配備前に、検査対象の印と操作定義の整合性、生成する対象表を確認します。C#は要求の操作IDが対象表にある場合だけ、共通のContent-Type宣言条件を確認します。HTTPメソッド、API名・操作名・URL・`kind`の固定値による再照合は行いません。

未指定の操作で`true`になるのは、この入力検査の対象外で検査を省略したという意味です。対象操作の`true`も宣言の共通条件を満たしたことだけを表し、MIMEの完全な構文や要求全体の妥当性は保証しません。APIの利用許可や認証の省略ではありません。キー／Bearerの共通認証、後続のモデル・本文検査、下流認証はそれぞれの処理で行います。

対象表そのものが欠落している場合や、存在する操作の値がnullまたはオブジェクト以外の場合は、設定不備としてポリシーエラーにします。対象の値`{}`は正常です。有効な表に操作がない場合とは区別し、設定不備を`true`へ変換しません。このエラー経路と、Content-Typeの宣言が不備で`false`になる400経路は別です。

部品は真偽値だけを作ります。`false`の場合は、呼出元の共有HTTP APIテンプレート`api-templates/gateway.xml`で直後に、監査の処理段階を`rejected`、理由を`INVALID_CONTENT_TYPE_HEADER`、状態を400にして記録し、要求を終了します。短い拒否フローはテンプレートに残し、フラグメント内から別の監査フラグメントは呼びません。本文やContent-Typeの生の値、クエリの名前・値を監査データへ追加しません。

応答は本文なしのHTTP 400で、理由は`Invalid Content-Type header`です。`apim-request-id`にAPIMの要求IDを設定し、`Cache-Control: no-store`を維持します。監査には既存の要求情報と確認済みの利用者情報を使います。操作・モデル設定の選択や本文読取りより前なので、この時点で未確認のモデルやAPI個別の利用許可を推測して記録しません。

## メソッドとURLを照合する場所

`operations[].method`と`urlTemplate`はARMの操作登録に使い、従来の定義を維持します。要求を受けたAPIMが、メソッドとURLに対応する操作を選びます。この部品は選ばれた操作のContent-Typeを確認するため、同じメソッド比較を重ねません。

C#でメソッドを読まないことのローカル検証と、実APIMで未登録のメソッドが対象の操作や下流へ到達しないことの確認は別です。後者はAzure反映後の受入項目で、未実施です。APIMの操作照合、WAF、認証等による拒否の帰属も区別します。

## クエリに関する別の処理

`core-query-observation`は、別に明示した対象でクエリの有無・複数値を監査用に観測します。要求を許可・拒否する処理ではありません。共通認証の`core-common-authentication`は、資格情報をURLに載せないため、`api-key`／`subscription-key`というクエリ名を拒否します。この認証上の拒否は維持します。

URI書換えや`copy-unmatched-params`、App GatewayのWAF除外は別の設定です。この入力検査でクエリを拒否しなくても、そのまま下流へ転送されることや、任意の版・値をモデルが受け付けることは保証しません。実接続ではどの処理・下流サービスが応答したかを区別します。

## 後続に残るMIMEの制御

後続の`core-model-limits`でも、binary／multipart／textのMIME許可一覧比較と、それによる415を撤去します。`operationConstraints.contentTypes`は個別処理やAPI定義生成などで使うため残します。Speechの上流ヘッダー設定、Documentの個別形式検査、WAFの形式制御は別に残り、共有部品の一覧比較の撤去だけで、すべてのモデル形式が経路全体を通るとは扱いません。これらは後の適用順で棚卸し、Azure反映後の関連受入では拒否主体・ヘッダー転送・下流の受付を確認します。

## 今回の整理と未判断の範囲

検査対象の選択と部品構成の整理、クエリ制限とメソッド再確認の撤去、Content-Type取得の簡潔化に加え、この入口のMIME許可一覧比較を撤去する方針を承認しました。宣言の存在・単一値・非空MIME部分だけを確認します。不備時の監査と400応答を維持し、理由と結果変数をこの検査の実態に合わせて改名する方針も承認済みです。短い拒否フローは共有HTTP APIテンプレートへ残します。後続の設定選択は`core-api-operation-settings`へ整理しています。本文／モデル制約・URI書換え・Speech転送・WAFに残る処理は、それぞれの棚卸で扱います。

入口検査と不備時の400監査はローカル整備済みで、承認済みソースのAzure反映・読戻しは完了しました。空本文でContent-Type宣言がない要求の400と、それに対応する拒否理由・AppGW監査の相関を実接続で確認しました。APIM共通処理の限定受入は[管理構成](../../../README.md)に記載しています。その他のContent-Type不備・大本文413・その他の経路／モデルは未検証で、受入全体は未完了です。ローカルの生成・検証、Azure反映・読戻し、実接続と監査の受入を区別します。この整理だけで、モデル機能の制約がなくなったことや、すべてのクエリが下流へ転送されることを確認したとは扱いません。
