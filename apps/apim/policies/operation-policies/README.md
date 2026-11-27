# 操作別ポリシー

APIの特定の操作へ適用するポリシーを管理する。
Credential Managerのログイン、文書解析、Realtimeなどの機能ごとにまとめる。
処理の棚卸時に、適用するAPI・操作と継承関係を確認して移設する。

| 正本 | Azureの適用先 | 役割 |
| --- | --- | --- |
| `credential-login/start.xml`・同名C# | credential-manager-login APIのPOST `/start` | 共通認証済みcallerを引き継ぎ、固定Brokerへログイン開始を中継する。30秒転送・回数枠を維持する |
| `credential-login/launch.xml` | credential-manager-browser APIのGET `/launch` | 起動秘密をfragmentから取り出して消し、同一originの起動POSTと限定したMicrosoftログイン先へ進むHTMLを返す |
| `credential-login/launch-post.xml` | 同APIのPOST `/launch` | 固定originを確認し、Brokerへ起動取引を中継する。取引・session照合と既存応答を維持する |
| `credential-login/identity-callback.xml` | 同APIのGET `/identity-callback` | code／state・Cookieを固定Brokerへ渡し、OIDC・取引・sessionの照合と応答を維持する |
| `credential-login/callback.xml`・同名C# | 同APIのGET `/callback` | 入力・取引・Graph本人照合の固定3区画を展開し、本人一致後に接続完了をBrokerへ通知する |
| `document-analysis/document-analysis.xml`・同名C# | `document-analysis-v1` APIの`analyze_document`操作（POST `/analyze`） | `<base />`でAPIポリシーを継承した後、既存の固定提供元へ解析開始を送り、受付チケットと公開応答を組み立てる |
| `document-analysis/document-analysis-status.xml`・同名C# | 同APIの`get_document_operation`操作（GET `/operations/{operationId}`） | `<base />`でAPIポリシーを継承した後、既存のチケット・モデル照合で固定提供元の状態と結果を取得する |
| `realtime/realtime-create.xml` | realtimeCreateを宣言したHTTP操作 | `<base />`の本人情報を通話作成前に受け取り、短期token・SDP交換後に既存v3／v4の署名チケットを発行する |
| `live/live-create.xml`・同名C# | liveCreateを宣言したHTTP操作 | 固定モデルのLive要求と公開sessionId／SDP応答を変換する。共通入口・backend・on-errorを継承し、短絡400／502へno-storeを付ける |
| `pii/pii.xml`・同名C# | PII REST操作 | 入力・固定要求・待ち時間・公開結果・監査の5区画を展開する。原文や提供元生エラーを応答・監査へ出さず、生成MCPの参照先RESTを維持する |
| `realtime/realtime-hangup.xml` | realtimeHangupを宣言したHTTP操作 | 確認済み所有者と外部チケットの署名・期限・call IDを照合し、固定終了先へ送る |

Documentの開始・状態取得ポリシーは、機能別の`document-analysis/`へ置く。API全体に適用する共有`api-templates/gateway.xml`とは、Azureの適用先が異なる。入力の確認は、継承先のAPIポリシーが[core-document-input-validation](../fragments/request/core-document-input-validation/README.md)を利用回数制限・下流接続準備・`accepted`監査より前で呼んで行い、開始操作では繰り返さない。modelの欠落・複数値・空値と`.`／`..`は400・`DocumentInvalidInput`、内部のモデル情報の引渡し失敗は500・`DocumentAnalysisUnavailable`とし、監査では後者を`OPERATION_SETTINGS_INVALID`へ分類する。2モデル一覧による拒否と、そのためだけの内部設定JSONの再読取りは撤去し、モデルの利用可否は提供元へ任せる。チケット・返却URIとのモデル照合と固定接続先は維持する。

⑫で開始POSTだけ、`model`を元のクエリから削除した後、pathだけの`rewrite-uri`と`copy-unmatched-params=true`で`pages`・`features`などの追加クエリを転送する。⑭では、その後の標準`set-query-parameter`を、api-versionは公開Schemaの単一値（現在`2024-11-30`）へ上書きし、stringIndexTypeは`exists-action=skip`で指定値を保持して未指定時だけ`unicodeCodePoint`を補完する形へ整理する。不正値・複数値の適否は提供元へ任せ、APIMに追加検査を入れない。㉑では状態取得GETの追加クエリを無視して同じジョブを取得し、クエリの有無だけで403にしない。チケット・UUID・本人・期限の照合、固定URI・対応版とURLへのAPIキー指定の共通拒否は維持する。㉒では`copy-unmatched-params=false`による未使用クエリの非転送を維持し、目的コメントを整える。対応版の結果取得GETはapi-versionのみを使う。将来正式なGET追加クエリが必要になれば、公開結果契約と合わせて見直す。この変更でWAF設定やクエリ名・値の監査出力を追加しない。

⑬のAPI版の正本は[公開Schema](../../schemas/document-analysis.json)の`DocumentAnalysisResult.analyzeResult.apiVersion.enum`の単一値である。標準API定義の開始・状態取得にある重複値を削除し、Pythonの正規化で両操作へ補完する。明示された別版は配備前に拒否する。Bicepは正規化済み`entry.constraint.apiVersion`を`__DOCUMENT_API_VERSION__`へ展開し、開始クエリの上書き・受付URLの照合・状態GETに使う。⑱では結果照合が共通API準備済みの`gatewayOperationConstraint`から`apiVersion`・`maxResponseBytes`をoutboundの既存try内で直接読む。Status専用`documentSettings`と`__DOCUMENT_SETTINGS_BASE64__`の復元・Bicep展開を撤去し、baseURL・API版markerの生成と上限・版・チケット・結果の条件を維持する。⑲ではStatusの操作設定`gatewayOperationConstraint`の取得・JSON解析失敗だけを500へ分類し、応答前に`core-gateway-telemetry`で`policy-error`・`OPERATION_SETTINGS_INVALID`・500を1回記録する。㉙では開始・状態取得の認証結果`gatewayDecision`の取得・JSON解析と認証方式・owner引渡し不備だけを500・`policy-error`／`AUTHENTICATION_PROCESSING_ERROR`へ分け、⑲の操作設定不備と区別する。㉜でGETのチケット・認証結果確認の403・410・500を単一`document-operation-rejection`へ集約し、応答前に既存telemetryを1回呼ぶ。403は`rejected`／`DOCUMENT_OPERATION_DENIED`（文書チケット段階の拒否）、410は`rejected`／`DOCUMENT_OPERATION_EXPIRED`（期限切れ）、500は`policy-error`／`AUTHENTICATION_PROCESSING_ERROR`を記録する。旧GET500専用chooseを統合し、POST500専用、既存拒否条件・公開応答・要求ID・no-storeを維持する。捕捉済みで`on-error`へ進まないため、この明示監査を行う。先行する共通`<base />`の`BACKEND_RESPONSE`監査は維持し、提供元200と後段Gateway500を別段階として扱う。提供元本文・版・容量などの不備は既存502を維持し、網羅的な再検査を追加しない。Portalの公開表示・結果検査も既存生成物を通じて同じSchemaの版を使い、実行時の追加読取り・再検査、公開仕様・チケット形式の変更は行わない。

棚卸①〜㉝の変更はAzure反映・読戻し照合を完了した。Document固有inboundはここで区切り、独立して作成した小文書の原本・Base64入力をキー／Bearerで202受付から本文なしGETの200まで一部確認した。申告サイズ境界・内部障害500・期限切れ410など残る受入は未実施である。状態取得側のチケット・接続先・結果構造・応答変換など全条件の棚卸完了とは扱わない。他の未棚卸XMLは元の位置を保ち、対象の棚卸時に機能ごとへ移設する。Portalの原本アップロード・2モデル選択・BFF制約は今回変更しない。Portal画面は結果のspanの位置情報を利用していない。
構成全体と移設の確認手順は [管理構成](../README.md) を参照する。
