# モデル・機能の確認

HTTP APIで、準備済みの操作設定に従い、モデルと操作固有の本文機能を確認する部品です。Azureのフラグメント名・フォルダ名・XML・C#を`core-api-target-validation`へ揃えます。

同名XMLが外枠、同名C#が確認処理です。既存の生成処理がC#と日本語コメントをXMLへ展開し、Azureへ完成XMLを渡します。モデルを登録一覧と照合して拒否する処理は撤去し、本文・ツールの別条件は維持します。

## 呼び出しと入出力

[HTTP APIテンプレート](../../../api-templates/rest/rest.xml)が、操作設定の準備・認証結果の引き渡し・本文サイズ確認と、対象のSpeech SSML確認を通った後に呼びます。`bodyModel`・`textEmbeddings`・`rerank`は提供元がモデルと入力を判断するため、この部品の呼び出しを省きます。その他の種類はモデル別の下流URI選択・本文書き換え・利用枠の処理より前に確認します。

| 入力・出力 | 内容 |
| --- | --- |
| `gatewayOperationConstraint` | 先行処理が準備した操作・モデル設定のJSON文字列 |
| 要求の本文・パス・操作情報 | 操作の種類ごとの既存条件で使用。本文は`preserveContent: true`で保持 |
| `gatewayTargetDecision` | `allowed`・`status`・`code`を持つ結果のJSON文字列。監査・失敗応答は呼び出し元が担当 |

## 設定の取得元

準備済みの`gatewayOperationConstraint`を直接JSONとして読みます。先行処理は設定を準備できなければ500で停止し、この部品までに同変数の上書きはありません。API設定全体のBase64復元、変数の存在確認による取得元の分岐と、JSON取得成功後のnull確認を撤去しました。

`shared/stream.xml`と`mcp/generated.xml`には別の設定取得処理があり、共通のBase64生成を使います。

## この段階で維持する条件

| 操作の種類 | 現在の確認 |
| --- | --- |
| パス指定のモデル | 空欄・空白だけ・「.」「..」を拒否し、モデルの利用可否は提供元へ任せる |
| 本文のモデル、Embedding、Rerank | 共有HTTPテンプレートからこの部品を呼び出さず、入力とモデルの利用可否を提供元へ委ねる |
| Responses | JSONオブジェクトを読めない場合は400。input配列直下のtype=code_interpreter_callとtools・tool_choiceの既存条件は403。受け付けたprevious_response_id・prompt・conversation_idとinputのID・type・ファイル参照を原値で転送し、inputの必須・型とincludeの一覧・型は提供元へ任せる。後続はstore=falseを強制し、指定時のbackground=false・conversation=nullだけを書き換える。model型とメソッド・操作パスは再確認しない |
| Agent Responses（hostedAgentPost） | JSONオブジェクトを読めない場合は400。inputの必須・型・role／content／part・一般IDは提供元へ任せ、配列直下の明示code_interpreter_callだけ403。本文4項目の制限は撤去し、登録済み指示・toolsと固定Agentを使うためinstructions・tools・agent_reference・promptの既存403、状態選択previous_response_id・agent_session_idの既存403を明示して維持。その他の本文項目は原値。後段は全対象にstore:falseを強制し、指定時だけconversation:null／background:falseへ揃える。親要求ID注入はtargetAgent非空時だけ。本文modelは原値で、Agentモデルとの優先順位は実受入へ残す |
| A2A | JSONオブジェクトの読取り例外・nullは400。読めた本文のJSON-RPCの版・ID・methodと許可method一覧の条件403は維持 |
| Live／Realtime | メソッド・パスは再確認しない。固有のSDP・所有者チケットは操作ポリシーで確認 |
| Document／PII | メソッド・操作IDは再確認しない。固有のクエリ・所有者チケット・本文処理は各専用部品で確認 |
| 登録済み操作 | 操作の種類が`registeredOperation`であること |

Responses・Hosted・A2AのJSON読取り不備を除き、既存条件に合わない場合と本文側の例外は、従来どおり`allowed=false`・403・`AUTHORIZATION_DENIED`を返します。呼び出し元は`rejected`として監査へ渡し、`API target is not authorized`と要求IDを返します。正常結果は`allowed=true`・200・`TARGET_ALLOWED`です。公開の成功応答をこの部品で作るものではありません。

内部の操作設定JSONの取得・解析失敗だけを小さな`catch`で分け、`allowed=false`・500・`OPERATION_SETTINGS_INVALID`を返します。呼び出し元は結果の状態・原因を取得して、500を`policy-error`・`Gateway processing failed`へ分類します。要求を停止し、従来と同じ要求ID付きの本文なし応答を返します。形式の再検査・設定項目を追加せず、内部JSON・例外詳細・本文を新たに出力しません。

A2Aの本文JSON読取り例外・nullは下記MOD-45で400へ分けます。読めた本文に残る条件や各用途の設定項目の不備は403になり得ます。本文・ツールの条件と失敗分類は、後続で個別に棚卸します。ローカルの整理を提供元の受付確認とは扱いません。

## A2Aの本文JSON読取り不備

MOD-45は追加HTTP APIの`kind=a2aJsonRpc`で、`Body.As<JObject>`の例外・nullだけを400／`INVALID_REQUEST_BODY`へ分けます。読めた本文の`jsonrpc=2.0`、`id`の文字列／整数型、`method`と`allowedMethods`の条件403は維持し、本文読取りを増やしません。

呼び出し元の既存`rejected`監査、`Invalid request body`、本文なし・要求ID・`Cache-Control: no-store`と、設定500・本文サイズ・利用枠を維持します。今回の対象はHTTP分類だけで、JSON-RPC errorオブジェクトの対応は別項目です。標準APIにA2Aはなく、Card GET・WebSocket・MCPは対象外です。ローカル実装・関連検証済みで、固定ソースの関連22件が初回からすべて成功しました（15.057s、失敗・スキップなし）。実式・親フロー各18対照で400／403と本文・監査・応答の保持を確認しました。今回Bicepコンパイルは未実施、Azure未反映・実A2Aサーバー受入未確認です。過去のA2A読取り403は当時の記録として扱います。

## Hosted Agentの本文JSON読取り不備

Hosted入口も、本文読取りの例外とJSONオブジェクトを取得できない場合だけを400・`INVALID_REQUEST_BODY`へ分けます。既存の本文読取りに小さなtry／catchとnull確認を使い、本文を保持します。MOD-37時点では読取り回数・受付スキーマを変更せず、読めたオブジェクトのstore・metadata・inputなどの条件による403を維持しました。現在のmetadataの扱いは下記MOD-46に従います。この分類整理時点ではメソッド再確認も維持し、その後に下記の重複確認を撤去しました。

呼び出し元は既存の結果引渡しによりrejected監査、`Invalid request body`、要求ID、Cache-Control: no-storeと本文なしの応答を返します。MOD-37時点では内部操作設定の読取り不備500、A2Aの読取り不備403、先行する認証・本文サイズ・利用枠と他用途の条件を変更していません。現在のA2A読取り不備400はMOD-45に従います。ローカル実装・関連検証済みです。変更後の関連105テストが初回ですべて成功しました（13.811s、失敗・スキップなし）。Hostedの共有46条件（正常11・読取り不備400が12・残る条件403が23）を実C#48対照と親フロー73対照で確認し、設定500・A2Aの読取り403、本文保持・回数・既存監査と応答も対照にしています。前段の104件は別の変更時点の結果です。Azureへの反映と実Hosted入口での応答は未確認です。

MOD-46は追加HTTP APIの`kind=hostedAgentPost`で、metadataの欠落・型・項目・値を理由にしたサンプル制限403を撤去します。本文直下の`input`／`stream`／`store`／`metadata`の4項目限定、`store`がBoolean falseであること、stream／inputの既存条件、JSON400・設定500は維持します。親要求IDを注入する対象APIの既存処理は、object metadataの他値を保持し、非object metadataをobjectへ置換して`core_parent_request_id`を上書きします。標準APIは対象外です。ローカル実装・関連検証済みで、固定ソースの関連23件が初回からすべて成功しました（14.447s、失敗・スキップなし）。今回Bicepコンパイルは未実施、Azure未反映・実Hostedサーバー受入未確認です。過去のmetadata403は当時の記録です。

MOD-47は追加HTTP APIの`kind=hostedAgentPost`でstreamのBoolean型限定403を撤去し、値を保持して型変換・補完を行いません。metadata制限撤去と`store`のBoolean false必須・input・本文直下4項目限定、JSON400・設定500・親要求ID注入を維持します。ローカル実装・関連検証済みで、固定ソースの関連20件が初回からすべて成功しました（7.360s、失敗・スキップなし）。MOD-46の23件（14.447s）とstream検査維持は当時の別記録です。標準APIは対象外、今回Bicepコンパイルは未実施、Azure未反映・実Hostedの型受付／SSE未確認です。

MOD-48は文字列inputと、既存の1 user／1 input_text内のtextについて、空文字・空白だけを理由にした403を撤去し、値を保持します。inputの欠落・型、配列件数・role/type・許可項目とtext文字列、store Boolean false・本文4項目の条件を維持します。metadata／streamの承認済み条件と保存参照形式の受付範囲は変えません。ローカル実装・関連検証済みで、固定ソースの関連20件が初回からすべて成功しました（7.757s、失敗・スキップなし）。MOD-47の20件（7.360s）と非空検査維持は当時の別記録です。今回Bicepコンパイルは未実施、Azure未反映・実Hosted受入未確認です。

## Hostedのinput配列の最大件数

MOD-70はローカル実装・関連検証済みです。追加HTTP APIの`kind=hostedAgentPost`で、`input`配列の最大1件という条件だけを撤去します。1件以上の全要素を既存と同じ条件で検査し、各要素は`role`／`content`だけで`role=user`、`content`は1件のオブジェクトで`type`／`text`だけ、`type=input_text`・text文字列を維持します。後半要素も省略せず検査し、配列の順序・内容を変更しません。

文字列input、空配列403、本文直下の`input`／`stream`／`store`／`metadata`限定、store Boolean false必須、JSON読取り不備400、保存参照形式の拒否、metadata／streamと親要求ID注入、本文サイズ・利用枠は維持します。content件数など他の件数・入力構造は変更しません。fixture表記差を補正した後の最終120件が全件成功しました（35.950s、失敗・スキップなし）。共有Hosted85条件を実target C#と親フローで検証し、新規複数35条件（通過3・拒否32）と親のraw本文3対照で、Unicode・空白を含む複数user入力の完全保持と後半のmessage id／content file_id拒否を確認しました。既存の保存・JSON・空配列・参照・content1件と、順序・値・読取り1回・監査・標準受付境界、実Bicep生成・正本一致・容量も成功しました。MOD-48の最大1件を維持した条件と20件（7.757s）、MOD-69の56件（26.665s）とHTTP共通末尾の一区切りは、それぞれ当時の別結果として保持します。工程1全体は未完了、Azure未反映・実Hosted受入未確認です。

## Hostedのcontent配列の最大件数

MOD-71はローカル実装・関連検証済みです。各messageの`content`配列の最大1件という条件だけを撤去します。contentは1件以上の配列とし、全partへ従来と同じオブジェクト・`type`／`text`限定・`type=input_text`・text文字列の検査を適用します。後半partも省略せず検査し、順序・値を保持して結合・削除は行いません。

input全要素のuser／`role`・`content`限定、空input／content403、文字列input、store Boolean false必須、本文直下4項目限定、JSON読取り不備400、保存参照形式の拒否、本文サイズ、metadata／stream・親要求ID注入・利用枠は維持します。関連120件が初回から全件成功しました（32.205s、失敗・スキップなし）。共有Hosted116条件の新part31条件（通過3・拒否28）と、前回content件数だけで拒否した2条件の200への変更を実target C#と親フローで確認しました。親raw本文の新4対照で、Unicode・空白を含む3messages各2partの完全保持と後続ID／file_id／input_file拒否を確認しました。全要素の型・空配列境界、読取り1回・監査・標準受付境界、実Bicep生成・正本一致・容量・正本所有・Razor・説明も成功し、独立レビュー問題なしです。MOD-70の最終120件（35.950s）とfixture表記補正を含む記録、MOD-69のHTTP共通末尾の一区切りは当時の別結果として保持します。工程1全体は未完了、Azure未反映・実Hosted受入と保存仕様は未確認です。

## Hostedの空input／content配列

MOD-72はローカル実装・関連検証済みです。`input:[]`と`content:[]`だけを理由にした403を撤去します。文字列inputは従来どおり扱い、それ以外のinputと各contentで`JArray`を取得できない欠落・null・別型の拒否は維持します。全message／partのrole・型・許可属性・参照形式の検査を維持し、空contentを見つけても成功を確定せず、後続要素を検査します。

本文を編集せず、配列の原値・順序を保持します。store Boolean false必須、本文直下4項目限定、JSON読取り不備400・本文サイズ、metadata／streamと親要求ID注入・利用枠・受付監査は維持します。関連120件が初回から全件成功しました（37.063s、失敗・スキップなし）。共有Hosted135条件（200が39・403が84・400が12、label重複なし）を実target C#と親フローで確認し、旧2条件の200への変更、新5通過／14拒否、空配列とUnicode原本文の完全保持、空content後のID／ファイル参照・別型／null不備・後続part拒否を検証しました。profileの追加raw本文5対照で空input／空content200と後続ID／ファイル参照／null part403も確認しました。読取り1回・既存監査・正規化・標準操作境界、実Bicep生成・正本一致・容量・正本所有・Razor・説明も同実行で成功しました。MOD-71の120件（32.205s）とMOD-70等の個別結果は当時の別実行として保持します。工程1全体は未完了、Azure未反映・実Hosted受入未確認です。

## Hostedのstoreを転送前にfalseへ揃える

MOD-73はローカル実装・関連検証済みです。この部品のstore Boolean false必須403だけを撤去します。転送前のHTTP本文処理は`kind=hostedAgentPost`の全要求へ必ずBooleanの`store:false`を書き、省略・true・null・別型もfalseへ揃えます。`targetAgent`の有無には依存しません。

既存metadata注入と同じ本文読取り・set-bodyにまとめ、metadataは従来どおり`targetAgent`が非空の場合だけ、objectの他値を保持し、非objectをobjectへ置換して`core_parent_request_id`を上書きします。非Hostedへstoreを追加せず、その既存metadata処理も保持します。input／streamの値、本文直下4項目限定・参照形式の拒否、JSON400・元本文サイズ・利用枠は維持します。

関連123件が初回から全件成功しました（42.035s、失敗・スキップなし）。共有Hosted139条件（200が49・403が78・400が12、label重複なし）で旧store6条件を200へ更新、新4型を通過させ、親の追加76条件（store18・metadata14・input／stream12・非Hosted24・先行拒否8）を確認しました。target直接は原本文を保持する1読取り、Hosted親はtarget1＋編集1の2読取りで、サイズ測定は別です。metadataを同じ編集で扱い、input／stream保持・targetAgent空・非Hosted除外・既存400／403／413／500の先行拒否を確認しました。合法追加HTTPの実Bicep生成XMLと正本読戻しでAgentあり／なし、Hosted2操作とoverride、非Hostedneighbor、制約引渡し・size→target→編集→rate順と先行拒否時の後段未到達、容量・正本所有・Razor・説明も同実行で成功しました。利用枠の擬似／実カウンター検証ではなく、独立レビュー所見なしです。

MOD-72等の個別結果とparams統合結果は別実行として保持します。工程1全体は未完了・Azure未反映で、反映は工程1の区切りでまとめます。APIMがfalseを送る指定と、提供元がfalseを遵守して保存しないことを分け、実Hosted受入・保存防止は未確認として扱います。

## Agentの通常オプションと保存防止

MOD-75はローカル実装・関連検証済みです。ユーザーが選んだ「Foundry登録済みAgentの指示・ツールを使う」契約に合わせ、本文4項目の許可表を撤去しました。instructions・tools・agent_reference・promptと、既存状態を選ぶprevious_response_id・agent_session_idは、従来の403を項目の存在条件として明示して維持します。nullや型違いも同じ扱いです。これら以外のinclude・text・出力設定・tool_choice・model・追加項目は、入力の適否を提供元へ任せて原値で渡します。modelの登録一覧は追加せず、指示・toolsを使う契約を、Agentモデルや定義全体の優先順位の保証へ広げません。

既存Hosted本文編集のstore:falseと同じ読取り・set-bodyに、指定時だけconversation=null／background=falseを追加しました。未指定の2項目は追加せず、非Hostedの本文は変更しません。metadataの対象・親要求ID・他値の保持、inputの透過、明示CI拒否、JSON400・内部設定500・元サイズ・認証／利用枠を維持します。

関連123件が初回から全件成功しました（38.115s、失敗・スキップなし）。共有Hosted264条件（200が201・400が12・403が51）で既存3条件を200へ更新し、通常オプション・定義／状態選択・conversation／backgroundと深さの90条件を追加しました。型比較は代表項目へ集約しています。親の追加70条件で同じ本文編集・全指定型・省略・同時指定・通常オプション保持・非Hosted非変更、6項目の先行403と後段未到達を確認しました。target1回＋編集1回の読取り、実Bicep生成・配備／読戻しsource・正本所有・容量・Razor・説明も同じ実行で成功しました。MOD-74の123件（35.467s）とは別実行です。

この共通Agent受付のローカル棚卸は一区切りです。6項目と明示CIの拒否は、固定指示・tools／対象と既存状態の参照を分ける契約として維持します。既存資源の所有者、Hostedのsession・volume・内部モデル、requestのmodelとAgentモデルの優先順位、正式A2AとMCP＋CMの本人接続は残る設計・実受入の対象です。Hosted全状態の保存停止を達成したとは扱わず、工程1全体は未完了、Azure未反映です。

## Agentのinputを提供元へ渡す

MOD-74はローカル実装・関連検証済みです。`hostedAgentPost`のinputの必須・型、`role=user`・content配列・`input_text`・許可属性というサンプル検査をまとめて撤去しました。Prompt／Hostedの通常メッセージ・reasoning・functionの完全なItemsと、一般のid・call_id・file_id・引用情報は、値・順序・未指定を保持して提供元へ渡します。inputを読めた配列の直下に、文字列typeが明示的に`code_interpreter_call`のオブジェクトがあれば、通常Responsesと同じ条件で403を維持します。深い位置や一般IDの再帰走査は追加しません。

MOD-74時点では本文直下4項目の制限、JSON読取り不備400、内部設定500、元本文サイズ、認証・利用枠、store:false強制と既存metadata処理を維持しました。関連123件が初回から全件成功しました（35.467s、失敗・スキップなし）。共有Hosted174条件（200が146・400が12・403が16）で既存74条件を403から200へ更新し、35条件を追加しました。親フローには入力透過・CI境界の22条件を追加し、store:falseと既存metadata以外の値を保持すること、inputの欠落を補完しないこと、target1回＋本文編集1回の読取りと先行拒否後の未到達を確認しました。実Bicep生成・配備／読戻しsource、容量・正本所有・Razor・説明の検証も同じ実行で成功しました。

input内の参照も提供元へ渡す変更であり、所有者隔離やHosted全状態の保存停止を達成したとは扱いません。本文直下の受付契約は別の残件です。MOD-73の123件（42.035s）とは別実行で、工程1全体は未完了、Azure未反映・実Agent受入未確認です。

## 7操作のメソッド・パス・操作IDの重複確認

Hosted、Live開始、Realtime開始／切断、Document開始／結果取得、PIIについて、共通部品のメソッド・パス・操作IDの再確認を撤去しました。正式設定が各操作の契約を検査し、実行時はAPIMが登録済みのメソッド・URLで操作を照合します。操作設定はその操作IDで選ばれ、モデル別設定から別の種類へ変更することも正式設定が禁止しています。

Hostedは本文JSON読取り400と残る本文条件403を維持し、他の6種類はこの部品で本文を読まず成功結果を返します。Live・RealtimeのSDPと所有者チケット、Documentのクエリと結果取得チケット、PIIの公開本文スキーマは引き続き専用処理が確認します。認証・サイズ・利用枠・固定接続先も維持します。

新しい操作を追加する変更ではありません。未登録のメソッド・経路はAPIMの操作照合に従います。Azure Portalで操作定義だけを手動変更した場合の二重拒否はなくなりますが、正式な配備前検査は維持します。既存WAF対応経路の正規の操作名・入力契約を照合する検査も維持します。

ローカル実装・関連検証済みです。最終147テストがすべて成功しました（39.102s、失敗・スキップなし）。6種類の実C#42対照と親フロー42対照で成功・本文読取り0回・本文保持を確認し、Hostedの共有46条件（正常12・読取り400が12・本文条件403が22）と、正式設定39対照を確認しています。Document・Realtimeの所有者チケット、LiveのSDP、PIIの入力検査と先行する監査・保存防止も同じ選択で成功しました。初回は操作名変更2ケースが既存WAFの整合性検査に当たり、テストの期待値を修正しました。WAFを変更せず、APIMの設定検査単体の名前の扱いを別の4対照で確認して再実行しています。Azure反映と実経路の動作は未確認です。

## 現在のResponses転送と保存防止

履歴ID・入力種別ごとの本文加工を撤去しました。下記のCode Interpreter呼出しItemの拒否を除き、`previous_response_id`・`prompt`・`conversation_id`、input配列の`item_reference`、type省略／nullの`id`、完全Itemsの`id`・`call_id`・内容・順序を原値で転送します。`file_id`・`file_ids`・`vector_store_ids`の再帰的な独自403も撤去し、引用注釈を含めて本文の値を保持します。

後続のHTTPテンプレートは`store`を必ずBooleanの`false`へ上書きします。保存済み会話へ今回の入力・出力を追加する`conversation`は指定時だけnullへ、保存を必要とする非同期モードの`background`は指定時だけfalseへ揃えます。この2項目は保存防止のために維持し、通常のItemの履歴ID・typeを加工しません。`store=false`は既存の保存済み資源を削除したり、その参照権限を無効にしたりする設定ではありません。

残るtools・tool_choiceの条件は続く工程1の棚卸対象です。includeの一覧・型検査の撤去はローカル実装・関連検証済みです。Foundryアカウントの設定は変更しておらず、`storedCompletionsDisabled`がResponses／Conversationsにも適用されるかは未確認です。参照透過の変更と、Code Interpreter呼出しItemの拒否も各追加時点のローカル検証済みです。Azureへの反映と実提供元での受付・非保存の確認は未実施です。

Code Interpreter呼出しItemの拒否を追加する前の関連113件では、固定ソース再実行で112件が成功し、生成MCPの合成宣言不足による対象1件を修正・再実行して成功しました。当時の未解決の失敗・スキップはありません。実HTTP本文編集式で履歴ID・prompt・input全体・ファイルIDと引用注釈の保持、store=false・指定時conversation=null／background=falseを確認し、残るtools等の403・本文JSON不備400・内部設定500、alias・元本文サイズ・利用枠と生成XMLの対照を維持しました。この過去結果を、追加したCode Interpreter拒否の検証成功へ転用しません。

## ツールの保存境界と個人MCP接続の方針

続く工程1の判断で、利用者別に隔離できない保存資源は基本的に蓄積させない方針を採用しました。`store=false`は応答履歴の指定で、内蔵Code Interpreterのコンテナ・入力ファイル・生成ファイルの保存や再利用を止める指定ではありません。[Microsoft公式手順](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/responses)は、コンテナが同じproject内で利用者別に隔離されず、input内の`code_interpreter_call`でも既存コンテナを参照できると説明しています。toolsのfunction限定を維持し、内蔵ツールを一律開放する変更は進めません。

カスタムツールは、アプリがfunction呼出しを受けて個人接続付きのMCPへ実行要求を送り、その結果をFoundryへ返す構成で扱えます。追加APIの外部MCPプロキシには既存CM連携を組み込めますが、MCP用のOAuth接続先・scope・固定の本人確認先と、MCP側の本人認証・資源隔離が必要です。購読キーだけでは個人を確定できず、本人専用の接続セッションまたは検証済みの利用者情報を使います。[本人接続の契約](../../../../../../docs/reference/credential-manager-login.md)を参照してください。

Foundryのremote MCPから同じCM個人接続を自動で使う経路は未実装です。また、function限定はinputからの既存コンテナ参照まで無効にする保証ではありません。この方針整理の時点では新しい拒否条件・本文加工・MCP資源を追加せず、続く判断で下記の明示的なCode Interpreter呼出しItemだけを拒否しました。他の既存資源の参照・所有者確認は継続棚卸の対象です。Azure設定は変更していません。

## Code Interpreter呼出しItemの拒否

Responsesのinputを配列として取得できた場合だけ、直下のオブジェクトを確認します。`type`が文字列の`code_interpreter_call`と完全一致するItemが一つでもあれば、要求全体を既存の403・`AUTHORIZATION_DENIED`で拒否します。そのItemだけを削除して処理を続ける方式ではありません。

既に読んだJSONを使うため本文読取りを増やさず、再帰走査・IDの形式確認・typeの補完を追加しません。深い位置の同名type、大小文字や別値、非文字列type、typeの欠落・null、非配列inputをこの追加条件の拒否理由にしません。通常のmessage・reasoning・function Items、一般のid・call_id、ファイルIDと引用注釈は原値で保持します。内部設定500・本文JSON不備400、tools・tool_choiceと先行する認証・サイズ・aliasの条件も維持します。この拒否条件の追加時点ではincludeの条件も維持し、続く判断で下記の一覧・型検査を撤去しました。

拒否時は後続の本文編集・利用枠・下流送信へ進まず、呼び出し元が既存のrejected監査、本文なしの403、要求ID、Cache-Control: no-storeを返します。本文・コンテナID・秘密情報を新しく監査へ記録しません。ローカル実装・関連検証済みで、Azureへの反映と実Foundryでの受入・既存コンテナ参照の実動作は未確認です。

この拒否条件の追加後、include検査の撤去前に関連71テストが初回ですべて成功し、失敗・スキップはありません。共有21条件（拒否5・通過16）と他kindの3条件を、実C#の判定24対照・alias／直接モデルを含む親フロー45対照で確認しました。拒否時の元本文不変、受け付けたItems・ID・引用注釈の保持、本文読取り回数、既存監査・403・要求ID・no-storeと処理順序を確認しています。内部設定500・本文JSON不備400・本文サイズ413、既存ツール条件とstore=false等の回帰対照、実BicepとPythonのXML生成一致、正本の所有先と実ポリシーのRazor構文も確認しました。過去の113件をこの追加条件の検証結果へ転用していません。

## includeの一覧・型検査を提供元へ任せる

includeを2種類の文字列配列へ限定する独自の403を撤去しました。応答へ追加情報を含める指定であり、この部品ではincludeをツール実行や保存資源の選択に使いません。未指定・既存値・新しい値・null・別型を変更せず提供元へ渡し、入力仕様と返却情報の適否は提供元が判断します。

Code Interpreter呼出しItemの拒否、function限定とtool_choiceの条件、store=false・指定時conversation=null／background=false、本文JSON不備400・内部設定500、認証・本文サイズ・利用枠を維持します。本文は残る入力・ツール条件のために読むので、この変更で本文読取り回数は減りません。includeの入力不備も他の条件を通れば利用枠・受付記録を経て提供元へ進みます。既存資源の参照・所有者確認をincludeの一覧で代用せず、別の棚卸で扱います。

ローカル実装・関連検証済みで、Azure反映と実Foundryでの新しい指定や型・組合せの受入は未確認です。先行するCode Interpreter拒否の71件を、このinclude検査撤去の成功結果へ転用しません。

include検査の撤去後、関連73テストが初回ですべて成功し、失敗・スキップはありません。共有19条件（透過16・別条件拒否3）を実C#22対照・親フロー41対照で確認しました。未指定、旧・新・未知の値、null・別型、順序・重複、aliasと保存防止変換の併用、拒否時の元本文不変と他kindの範囲を確認しています。先行するCode Interpreter拒否24／45対照、本文JSON不備400・内部設定500・本文サイズ413、既存ツール条件とstore=false等、実BicepとPythonのXML生成一致、正本所有先とRazor構文にも未解決の失敗はありません。

includeの整理時点では、tool_choiceのオブジェクトをfunction指定へ限定する既存条件を維持し、保存資源を持つ内蔵ツールの明示選択を止める目的コメントを整えました。続くallowed_toolsの整理で、現在の受付を以下へ広げています。toolsのfunction限定だけで未宣言ツールや過去のツールを選べないという保証には依存しません。

## functionだけを選ぶallowed_toolsの受付

tool_choiceのオブジェクトは従来のtype=functionに加え、type=allowed_toolsでtoolsが配列、各要素がオブジェクトかつ文字列type=functionの場合も受け付けます。選択対象の名前・mode・追加項目と空配列の適否は提供元へ任せ、本文の値を変更せず転送します。未指定・null・配列でない選択対象や、function以外・type不明・別型の要素を含むallowed_toolsは403です。

none／auto／required等の文字列や、従来この条件の対象外だった非オブジェクトのtool_choiceはそのまま提供元へ渡します。本文toolsのfunction限定、内蔵ツールやMCPを明示選択するオブジェクトの拒否、input直下のCode Interpreter呼出しItemの拒否と保存防止の書換えを維持します。既に読んだJSONを使い、本文読取り・書換えを追加しません。JSON不備400・内部設定500、認証・サイズ・利用枠・監査と既存応答も維持します。

ローカル実装・関連検証済みです。監査モデル名の一覧照合撤去と、Bearerの取消・切断の計数免除修正を合わせた最終104テストがすべて成功しました（14.853s、失敗・スキップなし）。この項目は共有69条件（通過29・拒否40）を実C#72対照と親フロー141対照で確認し、原値保持・保存防止・別条件による拒否と生成XMLの一致を検証しています。初回はXML属性内の行コメントによる式解析エラーがあり、ブロックコメントへ修正した後、旧監査テストも現仕様へ更新して再実行しました。Azureへの反映と実Foundryでの新しい指定の受入は未確認です。

## 登録モデル一覧による拒否の撤去

APIMで登録モデル一覧を利用可否の条件にしない方針を採用しました。この部品では`pathModel`に加え、`bodyModel`・`textEmbeddings`・`rerank`・`statelessResponses`の`allowedModels`一覧による403を撤去します。比較にだけ使っていた`gatewayEffectiveModel`の再取得も省きます。追加APIでも一覧やモデル別設定にないことだけで拒否しません。

同じ固定接続先・同じAPI形式のモデルは、提供元へ配備した後、APIMのモデル登録を増やさずに指定できます。先行する操作設定の準備は、モデル別の差分がなければ操作の基本設定を使います。共通認証、利用回数制限、本文サイズ枠と本文保持を維持します。aliasの解決とFLUX等の固定転送先選択は、接続パスが異なるモデルに必要な処理として残します。提供元自身がモデルを利用可能にしていることは別途必要です。

`ws/ws.cs`の動的WebSocketモデル選択は、指定値または既存の`defaultModel`を使います。固定接続先用query契約、queryの単一値確認と認証は維持します。Portalの候補・能力表示と配備前の設定検査には一覧データを使います。共通監査は文字種・長さ条件を満たすモデル名を記録します。

## 本文model型の再確認と専用読取りの撤去

本文の`model`が文字列でない、または存在しないことだけで403にする再確認を撤去しました。この部品では、その値をモデル別設定や接続先の選択に使いません。`bodyModel`・`textEmbeddings`・`rerank`は、型確認だけに使っていたJSON読取りも削除し、本文を読まず正常結果を返します。読めないJSON・非オブジェクト・本文なしも、この部品の拒否理由にはしません。

Responsesは、状態参照と入力・ツール条件の確認に本文を使うため、既存のJSON読取りとオブジェクトを取得できたかの確認を維持します。本文の`model`だけを再確認しなくなります。この型確認撤去では、読取り失敗や非オブジェクトの403、メソッド・パス・残る本文条件を維持し、続く棚卸で読取り不備の分類だけを変更しました。

先行する操作設定の準備がモデル別設定やaliasを選ぶ場合、そのために必要なJSONとmodelの確認、入力不備400・設定不備500・本文超過413は維持します。先行処理がモデルを選択しない操作では、この部品の型確認で止めていた要求も後続へ進み、入力の適否は提供元が判定します。本文は変更せず、認証・利用枠・本文サイズ枠・固定接続先も維持します。今回の再確認撤去はローカル実装・関連検証済みで、Azureへは未反映です。

型確認撤去後、Protocol・API設定選択・設定ソース・監査生成に関係する87件がすべて成功しました。実C#の73対照で本文モデル・Embedding・Rerankの読取り0回、Responsesの必要な読取りと残る拒否を確認しました。先行処理がモデルを選ばない24対照では、本文JSONの不備も元本文を保持してサイズ確認後に進み、先行処理がモデルを選ぶ場合は必要なJSON・model不備400で停止することを確認しました。実Bicep生成も検証しています。

## Responsesの本文JSON読取り不備

Responsesでは、残る状態参照・入力・ツール条件のために本文をJSONオブジェクトとして読みます。この読取りの例外とオブジェクトを取得できない場合だけを、403・`AUTHORIZATION_DENIED`から400・`INVALID_REQUEST_BODY`へ分けました。読取り部分だけの小さな`try`／`catch`と既存のnull確認で分類し、新しい本文条件を追加しません。

呼び出し元は結果の状態・原因をそのまま監査へ渡し、400は`rejected`、応答理由は`Invalid request body`とします。要求ID付きの本文なし応答を維持し、本文・内部JSON・例外詳細を新たに記録しません。状態参照・input・background・tools・tool_choice・include、内部設定JSONの500は維持します。この分類変更ではメソッドと操作パスの403も維持し、続く棚卸でその重複確認を撤去しました。Hosted／A2Aの本文読取りと分類は今回変更しません。

標準Responsesではモデル別設定を選ぶ先行処理が通常のJSON不備を既に400で止めます。今回の変更は、その選択をしない追加Responsesなど、後段の読取りへ進む場合を対象にします。モデル別設定・aliasの選択とその失敗分類は維持します。この分類変更はローカル実装・関連検証済みで、Azureへは未反映です。

分類変更後、同じ4対象の87件がすべて成功しました。実C#の読取り例外・null・非オブジェクト・本文なし400と、実HTTP API式への状態・原因・監査分類・応答理由・要求IDの引き渡しを確認しました。Responsesの残る本文条件403、Hosted／A2Aの読取り不備403、内部設定500とgenericの本文読取り0回・正常結果、先行処理の400・500・413も対照で維持を確認しました。合成ローカル検証であり、Azureの実応答・監査や提供元の受付確認とは区別します。

## Responsesのメソッド・操作パスの重複確認

この部品の`POST`と`/responses`の再確認を撤去しました。正式設定検査は`statelessResponses`を同メソッド・パスと`foundryOpenAi`の組合せへ限定し、同じ操作定義からAPIMの操作と制御設定を生成します。APIMが登録操作を照合し、先行部品がその操作IDで設定を選ぶため、後段で同じ条件を繰り返さない形にしました。

未登録のメソッド・パスは引き続きAPIMの操作照合に従います。新しい操作を登録する変更ではなく、URI書換え・認証・利用枠・本文サイズも変更しません。Azure Portal等で登録操作のメソッド・パスだけを手動変更した場合の二重チェックはなくなります。本文JSONの読取り不備400、状態参照・input・tools等の403、内部設定JSONの500は維持します。他の用途に残るメソッド・パス確認は個別の棚卸対象です。この撤去はローカル実装・関連検証済みで、Azureへは未反映です。

影響する90件がすべて成功しました。直前の87件に正式設定検査の3件を加え、POST `/responses`の正常登録と、メソッド・パス変更や別kindによる迂回の拒否を確認しました。部品単独の実C#ではメソッド・パスで再拒否せず、正常200・JSON読取り不備400・状態参照403と必要な本文読取り1回を確認しました。これらは合成ローカル検証で、未登録経路の実APIM応答はAzure反映時に別途確認します。

## 過去段階のResponses参照加工と検証記録

以下のprevious_response_id・promptのnull化、Item／ID削除、ファイル参照拒否は、棚卸の各時点で実装・検証した履歴です。現在は上記「現在のResponses転送と保存防止」へ変更済みで、これらを現行の受付条件や検証結果として扱いません。conversationのnull化とbackgroundのfalse化は現在も維持します。

### Responsesの過去応答IDをnullへ上書き

`previous_response_id`が存在するだけで403にする条件を撤去しました。代わりに、HTTP APIの既存の`store=false`本文編集で、同項目があれば指定値をJSONの`null`へ上書きします。文字列・null・数値・配列・オブジェクトなど元の値によらず同じ処理を行い、項目がなければ追加しません。

共有の下流認証で保存済みの過去IDを参照させない方針を、要求の拒否から転送値の上書きへ変更しました。提供元はクライアントが送った`input`を使い、指定された過去IDの履歴は引き継ぎません。今回の応答を保存しない`store=false`も維持します。

この部品は結果を返すだけで、本文を変更しません。呼び出し元は既に読んでいる同じJSONオブジェクトで`store`と`previous_response_id`を編集するため、読取りやパーサーを追加しません。先行する元本文のサイズ確認とaliasのmodel変換、他の本文項目・暗号化されたItems、応答のJSON／SSE透過を維持します。本文JSON不備400・内部設定JSON不備500、他の状態参照項目・input・background・tools等の403は維持し、未判断の項目は続く棚卸で扱います。過去IDや本文を新たに監査へ記録しません。

ローカル実装・関連検証済みで、Azureへは未反映です。関連91件がすべて成功しました。実C#の指定値7種類でこの項目だけの拒否がなくなったことを確認し、実HTTP APIの本文編集式を使う16対照で、指定値のnull化・未指定の保持・store=false・aliasとinputや他項目の保持・本文編集の読取り1回を確認しました。残る本文条件403・JSON読取り400・内部設定500、先行処理の400・500・413と正式操作の登録条件も維持しました。提供元の実受付・実応答はまとめ反映時に確認します。

### Responsesの会話IDをnullへ上書き

`conversation`も、項目が存在するだけで403にする条件を撤去しました。既存の`store=false`・`previous_response_id`の本文編集へ加え、同項目があれば値をJSONの`null`へ上書きし、未指定なら追加しません。指定値の型や内容によらず同じ処理を行います。

指定された保存会話IDの履歴を使わず、その会話へ今回の入力・出力を追加させない方針です。クライアントが必要な履歴を`input`へ含めて処理を続けます。`previous_response_id`との同時指定も、両方の値を同じ既存のJSONオブジェクトでnullへ上書きするため、本文読取りを増やしません。

元本文のサイズ確認・aliasのmodel変換・inputや他の本文値・JSON／SSE応答の透過を維持します。本文JSON不備400・内部設定JSON不備500、`conversation_id`・`prompt`とinput・background・tools等の残る403は今回変更しません。会話IDや本文を新しく監査へ記録しません。ローカル実装・関連検証済みで、Azureへは未反映です。

関連91件がすべて成功しました。実判定で`previous_response_id`・`conversation`それぞれの指定値7条件がこの項目だけでは拒否されないことを確認し、実HTTP APIの本文編集式を使う40対照で、単独・両方指定・両方null・片方null、未指定保持・store=false・aliasとinputや他値の保持・本文編集の読取り1回を確認しました。残る403・本文JSON不備400・内部設定500、先行処理の400・500・413と正式操作の登録条件も維持しました。両項目をnullにした実Foundryの受付は、まとめ反映時に確認します。

### Responsesのconversation_idを提供元へ渡す

`conversation_id`が存在するだけで403にする独自条件を撤去しました。確認した公開のFoundry／OpenAI Responses入力仕様で会話の指定は`conversation`であり、`conversation_id`は定義されていません。この部品や後続のGateway処理にも同項目を使う箇所はないため、値を変更せず提供元へ渡します。提供元が受け付けるか、無視するか、拒否するかは提供元の仕様に従います。

指定値の型や内容を新たに検査せず、項目が存在することを理由に拒否しません。`previous_response_id`・`conversation`の指定値null化と`store=false`を維持し、`conversation_id`を同じ上書きの対象へ加えることもありません。元本文のサイズ確認・alias・inputや他値の保持、本文JSON不備400・内部設定JSON不備500とprompt・input・background・tools等の残る403、JSON／SSE応答の透過は維持します。新しい読取り・パーサー・設定や監査項目を追加しません。

ローカル実装・関連検証済みで、Azureへは未反映です。関連92件がすべて成功しました。実判定で3項目それぞれの指定値・未指定8条件を確認し、実HTTP APIの本文編集式では`conversation_id`の16対照で元の値と未指定状態の保持、既存の`previous_response_id`・`conversation`の40対照で指定値null化を確認しました。`store=false`・aliasとinputや他値の保持・本文編集の読取り1回、残るprompt等の403・本文JSON不備400・内部設定500、先行処理の400・500・413と正式操作の登録条件も維持しました。提供元の実受付は未確認で、実応答はまとめ反映時に確認します。

### Responsesの保存プロンプトをnullへ上書き

Responsesの`prompt`は、提供元に保存したプロンプトテンプレートをID・version・variablesで参照する項目です。指定時の403を撤去し、既存の本文編集で`prompt`全体をJSONの`null`へ上書きします。項目がなければ追加せず、指定値の型や内容によらず同じ処理を行います。状態項目の拒否一覧は、この変更で不要になったため削除しました。

保存テンプレートの内容やvariablesは適用せず、クライアントが送った`input`を使って処理を続けます。variablesに含まれる画像・ファイルの保存済みIDも、`prompt`全体の上書きで転送値から取り除きます。この項目の改修時点ではinput必須条件を維持し、続くinput棚卸でその独自条件を撤去しました。promptだけの要求に対する実受付は提供元が判定します。

`store=false`と`previous_response_id`・`conversation`のnull上書き、`conversation_id`の原値転送を維持します。同じ既存のJSONオブジェクトで編集するため、本文読取りを追加しません。元本文のサイズ確認・alias・inputや他値の保持、本文JSON不備400・内部設定JSON不備500とbackground・input・tools等の残る403、JSON／SSE応答の透過は維持します。プロンプトID・variables・本文を新しく監査へ記録しません。

ローカル実装・関連検証済みで、Azureへは未反映です。初回の93件は、試験の旧ケース名の参照が1件残りエラーになりました。その参照を修正後に同じ93件を再実行し、すべて成功しました。実HTTP APIの本文編集式の26対照でprompt全体のnull化・variablesと保存IDの除去・未指定保持、conversation_id原値保持・既存2項目のnull化・store=false・aliasとinputの保持・本文編集の読取り1回を確認しました。残るbackground・input・files・tools等の403、本文JSON不備400・内部設定500、先行処理の400・500・413と正式登録条件も維持しました。提供元の実受付はまとめ反映時に確認します。保存テンプレートの正式利用は、受付方法と保存ファイル等の参照範囲を別途棚卸します。

### Responsesのbackgroundをfalseへ上書き

`background`の型やtrue指定を理由に403にする条件を撤去しました。既存の`store=false`本文編集で、同項目があれば値をJSONのBoolean `false`へ上書きし、未指定なら追加しません。true・false・null・文字列・数値・配列・オブジェクトなど元の型や値によらず同じ処理を行います。

このGatewayではバックグラウンドジョブを作らず、通常のJSON／SSE方式で処理を続けます。`stream`は変更せず、応答待機とタイムアウトは既存の転送条件に従います。Foundryの公式仕様はバックグラウンドモードにstore=trueを要求し、Gatewayは結果取得・取消・ジョブIDの所有者確認を提供していないため、非同期実行の正式受付は別に設計します。

同じ既存のJSONオブジェクトで編集し、本文読取りを追加しません。元本文のサイズ確認・alias・model/inputや他値、`store=false`・保存参照3項目のnull化・`conversation_id`の原値転送、応答透過を維持します。本文JSON不備400・内部設定JSON不備500、input・files・tools等の残る403は維持します。ローカル実装・関連検証済みで、Azureへは未反映です。

関連94件が初回からすべて成功しました。実判定でbackgroundの指定値・未指定8条件を確認し、実HTTP APIの本文編集式の32対照でBoolean falseへの上書き・未指定保持・stream true/falseとalias/inputや他値の保持・本文編集の読取り1回を確認しました。既存のprompt26対照・履歴ID40対照・conversation_id原値保持16対照、残るinput/files/toolsの403・本文JSON不備400・内部設定500、先行処理の400・500・413と正式登録条件も維持しました。実Foundryの通常JSON／SSE応答は、まとめ反映時に確認します。

### Responsesのinputの必須・型を提供元へ任せる

`input`が欠落している、または文字列・配列以外であることだけを理由に403にする条件を撤去しました。inputの値は保持して提供元へ渡し、入力仕様の判定と不備への応答は提供元と既存の応答処理に従います。任意の型が提供元で利用できることは未確認です。

この変更時点では、inputを`JArray`として取得できた場合だけ、既存の配列内保存済みitem・file・vector-store等の参照検査を行いました。欠落時の`.Type`アクセスや型不一致を理由に外側のcatch403へ戻らず、保存参照の条件や走査範囲を増やしません。現在の明示item_referenceの扱いは、次の節に従います。

他の条件を通った入力不備の要求は、利用枠・受付記録を経て提供元へ進みます。本文JSONオブジェクトの読取り不備400、先行するmodel確認400・内部設定500・本文サイズ413、tools等の残る403とPortalの入力検査を維持します。既存のstore=false・background=false・保存参照3項目のnull化・conversation_id原値転送、aliasと他値の保持・応答透過も維持し、本文読取りやパーサーを追加しません。ローカル実装・関連検証済みで、Azureへは未反映です。

関連95件が初回からすべて成功しました。実判定でinputの指定値・未指定10条件の正常結果と配列内の保存参照4条件の403を確認し、実HTTP APIの本文編集式の20対照でinput原値・未指定状態と既存の本文補正・aliasや他値・本文編集の読取り1回の保持を確認しました。既存のbackground32・prompt26・履歴ID40・conversation_id16対照、本文JSON不備400・内部設定500、先行処理の400・500・413と正式登録条件も維持しました。提供元の実応答はまとめ反映時に確認します。

### Responsesの明示item_referenceだけを取り除く

input配列内の`type=item_reference`による再帰的な403を撤去しました。既存の`store=false`本文編集で、配列直下のオブジェクトが文字列の`type: "item_reference"`を明示する場合だけ、その要素を取り除きます。末尾から削除するため、連続する参照も除去し、残るItemsの順序・内容・id・call_id・phase・暗号化内容を保ちます。本文読取りは追加しません。

全要素が該当する場合は空配列になり、残る入力の適否は提供元が判断します。この変更時点では、深い同名データ、type省略・null・別型、非配列は削除対象外でした。現在のtype省略／nullのID指定の扱いは、次の節に従います。file_id・file_ids・vector_store_idsの従来の参照検査、tools等の条件、既存の本文補正とJSON／SSE応答の透過を維持します。

関連96件が初回からすべて成功し、失敗・スキップはありません。実target16条件と実gateway本文編集の16条件×alias／直接モデル32対照で、削除位置・連続・全部参照・混在・対象外と完全Itemsの保持、本文編集の読取り1回を確認しました。保存file系4条件403、既存のinput20・background32・prompt26・履歴ID40・conversation_id16対照、本文読取り400・内部設定500・先行400／500／413と正式登録3件にも回帰はありません。ローカル実装・検証完了で、Azure反映・新条件の実提供元受付とJSON／SSE応答は未確認です。

### Responsesのtype省略／nullのID指定を解除する

同じ既存の本文編集で、input配列直下のオブジェクトに`id`項目があり、`type`が未指定またはJSONのnullなら、ID指定を解除します。項目が`id`と任意の`type`だけの場合は、内容のないオブジェクトを残さず要素ごと取り除きます。`role`・`content`など他の項目もあれば、`id`項目だけを削除し、残る本文・call_id・暗号化内容・独自項目を保持します。`id`をnullへ置き換える処理ではありません。

IDの値の型・空欄・長さによる検査やtypeの補完は追加しません。種類が明示された完全ItemsのID、深い位置のtype・id、非配列のinputは変更せず、残る要素の順序を維持します。本文読取りを追加せず、file_id・file_ids・vector_store_idsの既存検査とtools等の残る条件、store=false・background=false・保存参照3項目のnull化・aliasと応答透過を維持します。

[OpenAI公式の入力型](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_input_param.py)では、input直下でidが定義され、typeが省略可能な型はItemReferenceです。通常の省略可能なMessageにはidが定義されず、非参照の完全Itemsでidが定義される型はtype必須です。余分な独自IDやtypeが脱落した不完全な入力まで履歴参照と断定せず、曖昧なIDを渡さず本文を残すGateway側の整理として扱います。残る入力の適否と、Foundryで省略形を受け付けるかの実確認は別です。Azure反映・新条件の実提供元受付とJSON／SSE応答は未実施です。

関連97件が初回からすべて成功し、失敗・スキップはありません。実targetの追加43条件と実gatewayの43条件×alias／直接モデル86対照で、IDの値に依存しない要素除去・ID項目だけの削除、本文・call_id・暗号化内容・深いID・順序の保持を確認しました。既存の参照16条件と本文編集32対照、input20・background32・prompt26・履歴ID40・conversation_id16対照、保存file系4条件403・本文読取り400・内部設定500・先行400／500／413と正式登録3件にも回帰はありません。本文編集は既存の読取り1回を維持します。ローカル実装・検証完了で、Azureへは未反映です。

### Responsesのfile_id:nullによる403を撤去する

input配列内の`file_id`を確認する既存の再帰走査で、JSONのnullだけを403の対象外にしました。nullはファイルの参照先IDを指定していないため、URLやインラインのファイル入力に併記されても、この項目だけでは拒否しません。本文の`file_id:null`を削除・上書きせず、そのまま提供元へ渡します。新しい本文編集・走査・読取り・型検査は追加しません。

非nullの`file_id`は、空文字・数値・真偽値・配列・オブジェクトを含め、従来どおり拒否します。`file_ids`・`vector_store_ids`は、nullや空配列であっても、存在による既存の403を維持します。inputが配列でない場合の扱い、本文JSON不備400・内部設定500、tools等の別条件と既存の本文補正も変更しません。非nullのfile_idは入力ファイル指定のほか引用情報にも存在するため、その用途と場所を分けた後続の棚卸対象です。

[Microsoftの入力ファイル型](https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/azureopenai/responses?view=rest-microsoft-foundry-v1&preserve-view=true#openaiinputfilecontentparam)と[OpenAIの入力ファイル型](https://raw.githubusercontent.com/openai/openai-python/main/src/openai/types/responses/response_input_file_param.py)はfile_idを任意・nullを許す型として定義します。Gateway側のnullによる拒否だけを省く整理であり、残る入力の適否は提供元が判定します。Azure反映・新条件の実Foundry受付とJSON／SSE応答は未実施です。

関連98件が初回からすべて成功し、失敗・スキップはありません。追加の共有39条件（許可18・拒否21）と他kindの3条件を、実target42条件・実gateway81対照で確認しました。nullの原値保持、非nullや他の保存資源との混在、既存の走査範囲・拒否時の本文未編集を確認し、成功時の既存の本文補正と編集読取り1回を維持しました。前段のItem参照・ID整理、本文読取り400・内部設定500・先行400／500／413と正式登録の検証も成功しました。ローカル実装・検証完了で、Azureへは未反映です。

## パス指定モデルの受付と転送

`pathModel`でモデルを登録一覧と照合する403を撤去します。標準の対象は画像編集`/models/openai-images/v1/deployments/{deployment}/edits`と文字起こし`/models/openai-audio/v1/deployments/{deployment}/transcriptions`です。追加APIの同じ種類にも作用します。空欄・空白だけ・「.」「..」の既存拒否を維持し、それ以外のモデルの利用可否は固定接続先の提供元へ任せます。

転送先URIのtemplateを明示した操作では、templateに含まれる各値を`Uri.EscapeDataString`で一つの要素の値として埋め込みます。提供元の固定パスとAPI版・query、認証・本文サイズ枠を維持します。template未指定が可能な追加APIでは、既存のAPIM標準URL・query転送を保ちます。本文を書き換える処理を追加しません。

`allowedModels`はPortal・正式設定の用途に残し、`parameter`はパスと監査モデル名の取得に使います。監査モデル名を一覧と照合する条件も撤去済みです。現AzureのAppGWは、一覧で展開したモデルの完全パスへ用途別WAFを適用し、別のモデルパスには既定WAFを使います。画像編集と文字起こしについて、モデル名の一覧に依存せず公開操作のパスに従って同じ用途別WAFを適用する整理を承認しました。WAFの実装・Azure反映・読戻しは別に確認し、APIMの変更だけで同じ受付枠や提供元の成功を保証しません。このパス指定モデルの整理はローカル実装・関連検証済みで、Azureへは未反映です。

追加APIで非モデルの変数をパス要素に使う場合、`.`／`..`はエスケープ後も残ります。クエリ由来の変数をパスに使う定義は正式設定検査を通り、通常の置換と新しいエスケープ置換の両方で.NETのURL正規化が起こることを合成値で確認しました。実APIMの旧rewriteとの比較は未実施で、今回の新規回帰と認定せず追加APIの既存設計の棚卸候補にします。新しい拒否条件は追加しません。

## 検証と続く棚卸

登録一覧拒否の撤去後は、実C#の2モジュール73件を実行し、72件成功・既存POSIX権限確認1件スキップでした。HTTPの4種類について、登録内外・model型と本文JSONの不備・一覧欠落や不正型・不要な解決済みモデル取得の45対照を確認しました。同じOpenAI接続先の未知GPTがChat／Responsesの基本設定を使って本文を保持すること、動的WebSocketの指定値と既定値、複数値と固定queryの別条件も確認しました。実Bicepコンパイラーによる生成・下流接続・監査関連の2モジュール14件も成功しました。これらは合成ローカル検証で、Azure反映・実受入は未実施です。

ローカル実装と関連13モジュールの検証を完了しました。289件を実行し、288件成功・1件スキップでした。スキップはWindowsでは対象外の既存POSIXファイル権限確認です。同名XML・C#の実Bicep生成値、正本の所有先、コメント展開とRazorの既知構文制約、既存の許可・拒否と呼出し順序を確認しました。移動前後のモデル・本文・ツールの判定条件も照合し、保持を確認しました。

内部設定の欠落・不正JSON500、正常200、本文JSON不備403の対照で、実結果から呼び出し元の監査変数・応答式へ状態・原因と要求IDが引き継がれること、監査の実行順序と本文なし応答を確認しました。登録外モデルは一覧撤去後の正常200として検証します。

パス指定モデルの変更後は、関連14モジュールの292件を実行しました。当初1件がARM試験評価器の標準関数`sub`未対応で失敗し、同評価器を補完した後に影響する10件を再実行して全件成功しました。残る失敗はなく、Windowsでは対象外の既存POSIXファイル権限確認1件はスキップです。実Bicepが生成したURI式をC#で実行する22対照で、標準2操作・別のパラメーター名と複数のパス値を使う追加API・区切り文字やUnicodeの符号化・template省略時の既存転送を確認しました。Azure反映・読戻し・実接続の確認は未実施です。続く本文JSON・残る設定不備の応答分類、操作別の本文・ツール条件は一つずつ判断します。
