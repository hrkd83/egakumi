# APIM工程1・2の棚卸判断

工程1は各API・操作のInbound、工程2はbackend・outbound・on-errorを対象とする。制御を一つずつ読み、先行処理で保証済みの重複、提供元へ任せられる入力仕様、Gatewayが維持する認証・所有者・保存防止の条件を判断した。この文書は、採用した判断と実装内容をまとめた確認用の報告である。

本OSSはWIPである。ローカルの実装・合成検証、Azureの管理プレーン読戻し、実接続の受入を区別する。対象環境の反映・受入状況は保護記録で管理し、この文書で工程1・2の完了を宣言しない。旧認証互換XML・依存C#と旧形式の再生成処理を公開資材から撤去し、現在の正本へ整理した。保存した過去rawの照合、現在の正本と初期構築用inline、実fragment辞書による限定移行は維持し、ソース整理を実環境の工程完了と同一視しない。

## 判断と変更内容

| 分類 | どう判断したか | 何を変更したか | 何を維持したか |
| --- | --- | --- | --- |
| 応答・会話の保存抑止 | 保存指定の拒否より、転送値をGatewayで確定する方式を採用する。会話への保存とbackground実行も別の指定として扱う | 対象のChat・Responses・Hosted要求へBooleanの`store:false`を強制。Responses・Hostedは、指定がある場合だけ`conversation:null`・`background:false`へ変更 | ChatはFoundry OpenAIへ送る宣言済みPOST操作で選び、API名・モデル一覧に依存させない。入力・認証・サイズ・利用枠の条件は継続。Foundry側の保存機能を無効化する管理設定は、この整理では変更していない |
| Responsesの入力透過とツール | 通常の履歴ID・Item・typeの適否は提供元へ任せる。利用者別に隔離されない内蔵資源の利用は別の境界として扱う | `input`内のID・type・`file_id`や`previous_response_id`・`prompt`を保持。`include`の型・一覧による独自拒否を撤去し、functionだけを選ぶ`allowed_tools`を受付 | 本文`tools`はfunction限定。ツール選択オブジェクトの内蔵ツール指定と、`input`配列直下の明示的な`code_interpreter_call`を403で拒否。一般IDや深い位置を一律に走査・加工しない |
| Foundry Agentの契約 | 登録済みAgentの指示・ツールを使う方針で、サンプルの入力形式を共通契約にしない | Hostedの本文4項目限定、metadataのサンプル条件、stream型、user・input_text限定、件数・非空等の独自条件を撤去。通常オプションと完全な入力Itemを保持し、保存指定を強制編集 | Hostedの`instructions`・`tools`・`agent_reference`・`prompt`と、本文直下の`previous_response_id`・`agent_session_id`の拒否を継続。直下のCode Interpreter Item拒否、必要な親要求IDのmetadata付与も継続。A2Aは宣言済みRPCメソッドとJSON-RPC形式を継続 |
| 共通認証結果の再利用 | 共通認証と結果引き渡しが先に停止条件を保証し、その後に上書きがない箇所は再検査を省く | HTTP・WebSocket・MCP、監査、利用枠、CMで、社員JSONの再読取り・GUID再整形・ID再照合・成功印再確認を整理。キー／Bearerの結果は検証済み購読情報から選択 | キー／Bearerの入口認証、署名・期限・tenant・audience・委任scopeの確認、認証結果の引き渡し失敗時の停止、既存の呼出者・利用枠ID表記。本人や権限の検証そのものは先行処理へ保持 |
| 回数枠と取得元 | 社員設定は共通認証へ検査を集約する。SQL応答は独立した外部入力なので取得時の検査を維持する | 計数時の本人・設定の再照合を省き、準備済みの枠を使用。SQL枠を使う要求では不要な埋込み設定の復元・購読別検索を省く。SQL取得不備503へ専用監査を追加 | 社員／購読ごとの既存カウンター、回数・期間、設定の優先順位、明示`null`による無制限、取消・切断等の免除、キャッシュ。SQLの形式・購読ID・項目・型・範囲の検査と障害時停止 |
| トークン枠と利用量監査 | 枠や監査の対象を、操作の実効接続先と一致させる | `backendOverride`を含む接続先からChat／Responsesのトークン枠を選択し、Chatの監査照合対象とAPI LLMログも同じ実効接続先へ整理。内部対象一覧を読めない場合は既存on-errorへ渡す | 毎分／累積枠の値・期間、明示`null`、社員／購読の計数キーと免除、標準ポリシーの分岐。監査照合はChatの現行範囲を継続し、本文収集量は0を維持 |
| CMの所有者とセッション | Gateway内の本人再確認と、外部接続・セッションの所有者確認を分けて評価する | 確認済み`callerId`を使用。未使用の所有者JSON、Graph照合成功の再確認、保持済みセッションのヘッダー再読取りを省く。CM有無の選択と固定設定検査を生成・配備前へ集約 | 接続所有者、Broker本人セッション、取得トークンとGraph本人ID、接続・provider・profileの照合、SQLの使用直前の期限・失効・世代・rotation確認、更新・再認証。接続拒否を別の認証方式で救済しない |
| 入力・認証・内部エラー | 入力を読めない400、無効な認証401、権限や機能条件403、内部処理不備500を区別する | Responses・Hosted・A2AのJSON読取り不備を400へ分離。Document・PII・CMの共通Entra検証失敗を共通401へ引き継ぐ。認証情報引き渡し・CM内部ID・操作設定等の不備を専用500と`policy-error`へ整理。残るHTTPエラーへ`Cache-Control: no-store`を付与 | 401のBearer challenge、要求ID、公開エラー形式、本文なし応答の対象、本文超過413、SQL取得503、提供元障害の個別分類。PIIの標準500応答定義を最終反映・読戻しの必須条件として保持 |
| 操作・モデル・入力条件 | APIMの登録照合や正式設定検査と同じ再検査、提供元仕様を狭める汎用検査は必要性を見直す | 選択済み操作のメソッド・パス・ID再確認、登録モデル一覧照合、汎用の出力項目・型・件数等の独自制限を整理。モデル・内部操作設定はAPI・操作と本文モデルから自動選択 | 本文サイズ、必要なContent-Type宣言、固定接続先、URL用のモデル値条件、SpeechのDTD／ENTITY拒否、Document・Realtimeの署名付き所有者チケット、PIIの公開入力契約。提供元の実受付は別途確認 |
| 監査と要求ID | 相関候補を認証や所有者の証明として使わず、同じ値を重複して設定しない | 親要求の監査へ確認済み呼出者を使用。HTTP・WebSocket・MCP末尾の要求IDの同値上書き、使われないHTTP専用設定IDの転記、モデル値の存在再確認を整理 | 共通要求準備による`x-ms-client-request-id`の確定、応答の要求ID、親要求IDの単一・非空GUID条件とヘッダー除去、提供元IDの受入検査。監査への本文・資格情報・内部例外詳細の非出力 |
| 下流認証と通信 | 入口の利用者認証と、下流へ送る資格情報を分けて維持する | CM未設定の空代入や重複分岐を省き、宣言済みの下流認証を直接実行。転送・ストリーミングのフローと説明を正規資材へ整理 | 入口キー・本人Bearer・CMセッション等の除去、固定backendと操作override、MI・CM等の下流専用認証、既存timeout・buffer設定、自動再送を行わない転送。通常Foundry推論はMIを継続 |
| WindowsのTLSとJSON文字列 | 受入clientの証明書検証をOSの信頼設定へ揃え、本文編集で日時文字列の値を変えない | WindowsのREST・WebSocket・MCPに共有の検証付きTLS contextを使用。Chat・Responses・Hosted・モデルaliasの本文編集と、PIIの入力・下流要求・提供元結果・最終応答、生成MCPのPII引数で、ISO形式を含む文字列を日付型へ自動変換せず保持 | 信頼ストアへの変更なし、証明書チェーン・ホスト名検証の必須条件、依存欠落時の接続前停止。他OSの既定TLS検証、JSON構造解析、store等の既存代入、本文読取りの順序と既存catch／on-error。PIIの型・言語・サイズとMCPの検査対象ID範囲 |

保存抑止の実装が保証するのはGatewayから送る保存指定である。既存の資源の削除、参照先の利用者別所有権、Hostedコードやカスタムツールによる保存は、それぞれの提供元・サーバー契約で確認する。カスタムMCP側での本人識別・保存・所有者確認も同じ境界で設計する。Hosted／A2Aの実サーバーでの受入は、このAPIMの合成検証から成功と推定しない。

Graph等のOBO対応Entra委任操作は、**本人BearerはOBO、購読キーはCM本人接続**という役割分担を採用した。工程5でGraphプロフィールAPIに両方式の振り分けを実装した。Bearer要求ではBrokerがAPI用証明書でGraph向けトークンを交換する。OBO失敗時は停止する。同意・証明書登録後の実交換と本人の照合は実環境で確認する。 [工程5の資材と受入条件](entra-app-rollout-plan.md)

## 正規の管理構成

現行の生成元を、処理の責務と適用先で次の配置へ整理した。XMLはフロー・短式・通信・分岐、同名C#は分離した実式、READMEは目的・入出力・維持条件を所有する。複数式は固定区画を明示し、多行式のない部品に空のC#を作らない。

| 配置 | 所有する内容 |
| --- | --- |
| `policies/service.xml` | 共通InboundのSTEP1〜5と全体フロー |
| `policies/fragments/<責務>/<Azure部品名>/` | 登録する共通fragment。同名XML・必要なC#・README |
| `policies/snippets/<責務>/<部品名>/` | APIへ生成時に埋め込む共通処理。同名XML・必要なC#・README |
| `policies/api-templates/` | HTTP・WebSocket／外部MCP・生成MCP・CM等のAPIフロー。同名XML／C#と説明 |
| `policies/operation-policies/<機能>/` | Document・PII・Live・Realtime等の操作固有フローと実式 |
| `apis/<API名>.json` | 1 API 1ファイルの公開操作・認可・下流接続設定 |

Azureの既存部品名は維持し、責務別の正本・日本語説明・生成参照を合わせた。PythonとBicepが同じXML／C#から配備用XMLと読戻し期待値を生成する。固定slotでの展開も、式区画・引用符変換・trim・字下げの対応を共有する。

旧認証互換15 XML・依存C#と、旧literal／audit版・gRPC免除の再生成・受入分岐を撤去し、`policies`直下のファイルは`service.xml`と`README.md`だけである。実旧fragment辞書の限定した説明差分、canonical派生inline、有限のBroker/cache移行と、検証済みcurrent scopeを伴う過去rawの完全照合は維持する。工程2を閉じる前に、不要な旧資材・参照が残らず、最終配置と生成・試験が一致することを確認する。

## 確認範囲と完了までの条件

変更ごとの合成回帰では、実C#式の正常・拒否・内部不備、先行処理での停止、既存カウンターと所有者条件、生成XMLの構造・式・文字列、Python／Bicepの生成一致、容量・構文・正本所有を確認している。TLSはOS別選択・検証必須・接続前停止、本文編集はISO表記・escape・空文字・元から退避文字を含む値等を対象にする。これらを実ネットワーク・提供元・分散カウンターの動作確認へ読み替えない。

工程1・2を閉じる前に、次を個別に確認する。

1. 正式なAPI公開処理で、旧構成の全体事前照合、生成世代の拘束、競合検出、全新構成と正確なProduct所属の読戻しを完了する。PIIの500応答定義は、後半反映と読戻しまで確認する。
2. Gatewayに限定したWAF反映と管理読戻しを確認し、Portal等の担当外構成を保全する。
3. キー／BearerのREST・SSE・WebSocket・MCP、保存指定・文字列保持・function継続・Code Interpreter拒否・不正Bearerと監査を照合する。接続開始だけで全transportの正常実行を成功と判定しない。
4. 旧互換XML・旧生成処理と参照の撤去、最終フォルダ構成・生成・回帰・公開情報の整合性を確認する。最終反映の保存世代と同じcanonical ARM・分割テンプレート・全入力から、ソース整理によるAzure差分がないことを照合する。
5. 残件と確認範囲を含めてユーザーへ工程1・2の結果を報告し、確認を挟む。**工程1・2が完了しても、そのまま次工程へ進めない。**

## ログ照合の欠測を扱うとき

成功応答とAPIMの要求・traceが確認できても、対応するApplication Gatewayのアクセスログを取得できない場合がある。この欠測を拒否ログや要求失敗と判定せず、原データと未完了の相関結果を保持して別のログ調査へ引き継ぐ。backend応答コードの不明値を0へ補完したり、追加の試験結果で元の欠測を成功へ書き換えたりしない。

ソース・配備資材・機能の確認と、全要求のログ相関完了は個別に報告する。残るログ調査を別途管理して変更をマージする場合も、確認範囲と未確認の点をレビューへ明記し、完全なログ照合の完了とは記述しない。対象環境の結果・識別子・原本は保護記録だけで管理する。

## 関連する正本

- [APIMポリシー一覧](apim-policy-catalog.md)
- [ポリシーの管理構成](../../apps/apim/policies/README.md)
- [APIポリシーテンプレート](../../apps/apim/policies/api-templates/README.md)
- [Responses／Hostedの入力・ツール条件](../../apps/apim/policies/fragments/request/core-api-target-validation/README.md)
- [Entra分離とOBOの工程計画](entra-app-rollout-plan.md)
- [APIまとめ反映](../../scripts/operations/apim_phase12.md)・[REST受入](../../scripts/operations/apim_phase12_acceptance.md)・[WebSocket／MCP受入](../../scripts/operations/apim_phase12_transports.md)
