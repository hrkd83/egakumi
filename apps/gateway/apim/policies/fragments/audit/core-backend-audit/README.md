# 提供元の要求IDの退避

AzureのPolicy fragment名`core-backend-audit`を維持し、同名XML／C#を正本として管理します。旧`backend-audit.xml`は撤去します。BicepとPythonのrendererが、同じC#と説明をXMLへ展開します。

HTTP・WebSocket・生成／外部MCPのoutboundで、`base`より前に呼び出します。共有outboundは`apim-request-id`をGatewayの要求IDへ上書きするため、それより前に提供元のIDを`gatewayBackendResponseIds`へ退避します。監査の出力は後続の監査部品が担当します。

提供元の応答は外部入力です。次の検査は、内部認証済みIDの重複確認とは異なるため維持します。

- ヘッダー名は`apim-request-id`・`x-ms-request-id`・`x-request-id`の3種類だけ。
- 値が1件の場合だけ取得。
- 非空GUIDをD形式へ正規化。または既存の`req_`形式を保持。
- 応答なし、未知名、複数値、不正な形式は記録しない。

本文・資格情報は取得せず、SSEを読み切りません。退避値はログの照合用で、認証・所有者確認には使いません。初回棚卸では全受付条件を維持し、正規配置と説明だけを整理します。応答変換前後や短絡経路の監査順序は、呼出し元のoutbound棚卸で扱います。
