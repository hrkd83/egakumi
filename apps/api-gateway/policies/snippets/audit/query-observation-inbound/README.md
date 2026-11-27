# 外部MCPのクエリ観測呼出し

外部MCPのAPI Inboundへ生成時に差し込むXMLフローです。要求準備の後に`core-query-observation`を呼び、`gatewayQueryPhase: received`を設定し、`core-query-telemetry`へ記録を渡します。クエリや本文の加工・拒否を行わず、名前・値・本文もこのフローから記録しません。WebSocketとRESTから生成するMCPには差し込みません。

Bicepが外側の`fragment`を除き、`mcp/external.xml`で指定した受付区画へ展開します。Azureへ独立Policy fragmentとして登録せず、展開後のAPIからleaf fragmentを呼ぶため、fragmentの入れ子は作りません。C#式を持たない呼出しフローなのでXMLと説明を正本とし、空のC#や新しいmarkerは追加しません。

MOD-118で旧snippets直下からこの目的別フォルダーへ同内容移設しました。Bicepと合成試験の参照を揃え、XMLのバイト不変を確認しました。今回のAzure反映・実外部MCP受入は未実施です。以前の観測専用化の反映記録は[snippetsの説明](../../README.md)と区別します。

関連5件は24.090秒で全件成功しました。外部MCP限定の差し込み・固定接続先・全protocol生成・配備と保護sourceの一致・容量を確認し、新しいC#試験やfixtureは追加していません。
