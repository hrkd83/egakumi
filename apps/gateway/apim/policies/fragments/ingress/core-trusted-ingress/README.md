# 信頼したHTTP入口の確認

このフォルダは、AzureのPolicy fragment `core-trusted-ingress` の生成元を管理する。
ローカルのXMLは生成前のひな形、Azureポータルに表示されるXMLは部品を組み込んだ生成後の内容である。

```text
core-trusted-ingress.xml                 処理フローと差し込み位置
        ＋
core-trusted-ingress.cs                  入口ヘッダーを判定する条件式
        ＋
core-gateway-telemetryのXML・C#           拒否した要求の監査処理
        ↓ 生成時に組み込む
Azureのcore-trusted-ingress              実行するXML全体
```

| 生成元の表記 | Azureへ登録する内容 |
| --- | --- |
| `__CORE_TRUSTED_INGRESS_CSHARP__` | 同じフォルダの `core-trusted-ingress.cs` の条件式 |
| `__L7_INGRESS_REQUIRED__` | `core-l7-ingress-enabled` の名前付きの値を確認する式 |
| `__L7_INGRESS_EXPECTED__` | `{{core-l7-ingress-secret}}` という名前付きの値の参照 |
| `__AUTHENTICATION_TELEMETRY_XML__` | 入口確認に失敗した要求を記録する監査コード |

名前付きの値は `{{名前}}` として参照する。秘密の実値を公開ソースへ埋め込む構成ではない。
入口ヘッダーの欠落・不一致・重複なら403を返し、認証とバックエンドへの転送へ進まない。

この処理は社員や購読キーの認証より前に実行する。拒否時には要求情報と拒否理由を記録し、本人を確認した情報がなければ未確認として扱う。
生成時の `key_only_gateway_telemetry` は、まだ存在しないAPIの利用許可結果と社員の利用枠を照合する部分を省く処理であり、Bearer認証を禁止する意味ではない。

APIMのフラグメント内から別のフラグメントを呼び出す構成にはできないため、拒否監査のコードは生成時に直接組み込む。
この入口フラグメントにも、実際に組み込む監査コードの説明を保持する。
認証前に使わない社員の利用枠照合は、従来どおりコードごと対象外となるため、その部分の説明も展開しない。
通常の監査コード全体は、Azureの `core-gateway-telemetry` で確認できる。

未反映の確認では、ひな形のXMLではなく、現在の生成処理で作ったXMLとAzureから読み戻したXMLを照合する。
生成処理は [apim_ingress.py](../../../../../../../scripts/deployment/helpers/apim_ingress.py) と [api-publication.bicep](../../../../api-publication.bicep) に定義する。
