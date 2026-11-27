# 生成時に差し込むXML部品

ポリシー生成時に呼び出し元へ差し込むXMLの部品を管理する。
これらはAzureへ登録するPolicy fragmentとは異なり、単独のフラグメントとしてポータルに表示されるものではない。
処理の棚卸時に、生成時の組み込み先と実際の適用順序を確認して移設する。

[query-observation-inbound](audit/query-observation-inbound/README.md)は外部MCP用APIポリシーへ差し込むテキスト部品である。単体で読める`<fragment>`ルートを持つが、AzureへPolicy fragmentとして登録しない。Bicepが外側の`<fragment>`／`</fragment>`を除き、APIのInboundへ展開する。展開後のAPIから観測・監査のleaf fragmentを呼ぶため、fragment同士の入れ子は作らない。観測結果を理由とする拒否分岐は持たず、外部MCPの応答・エラー段階でも既存の観測監査を呼ぶ。WebSocketにはこの処理を追加しない。

旧`query-guard-inbound.xml`は棚卸⑦でローカルソースから削除し、`query-observation-inbound.xml`へ移行済みである。後続のURI制約やWAFの採否は別の棚卸で判断する。ローカル改修・検証と、承認済みソースによるAzure上のfragment・API参照の反映・読戻しは完了した。APIM共通処理の限定受入は[管理構成](../README.md)に記載し、全入口の受入は未完了である。旧query fragment・Named Valueは参照消滅を確認して退役し、不在の読戻しも完了した。保存した正本checkpointは現行生成元へ更新し、旧資材を含まない状態に整理済みである。

未着手の他のXMLは元の位置を保ち、初めて棚卸または改修する作業時に配置・名前・説明を揃える。
構成全体と移設の確認手順は [管理構成](../README.md) を参照する。

MOD-118で、この観測フローを目的別のaudit/query-observation-inboundへ同内容移設した。今回のローカル配置整理と、上記の観測専用化のAzure反映履歴は別の作業であり、MOD-118のAzure反映・実受付は未実施である。

[audit-body-model-error](audit/audit-body-model-error/README.md)は、HTTP監査モデルの内部設定取得失敗を固定PolicyIdだけ500へ分ける共通フローである。HTTPと独自on-errorのDocument開始／状態・PIIへ生成時に差し込み、先行401／429とその他のエラー処理を維持する。空のC#やAzure fragmentは追加しない。
