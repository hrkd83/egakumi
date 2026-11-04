# App Gateway WAFの互換性・遮断確認

GatewayとPortalは、OWASP 3.2のPreventionと本文検査を有効にしたまま使用します。SQL、HTML、シェルコード、不透明な機械生成文字列をモデル・PII等へ渡すことは正当な利用です。そのデータを実行命令として扱うURL、ヘッダー、APIの制御情報とは分け、誤検知した正規パス・引数・個別ルールだけを調整します。

コマンドはリポジトリルートで実行し、[第1章](../setup/01-prerequisites.md#1-5-環境別のローカル領域を準備する)で設定した`DEPLOY_PARAMS_FILE`と絶対パスの`DEPLOY_LOCAL_DIRECTORY`を使います。計画・実測記録は同じ環境の領域へ保存します。

除外の正本は `apps/gateway/waf-data-exclusions.json` です。各profileはGatewayまたはPortalの完全パスを指定し、`RequestArgValues`と、動的JSONキーで必要な場合の`RequestArgKeys`を使います。`StartsWith`は`state.`、`questions.`、JSON Schema配下など、区切りを含むデータ構造に限定します。集計規則949110は除外しません。

raw SSML/WAVと固定1MiBの文書APIでは、正規本文の相対パス`../`が930110に一致しました。AzureはXML/raw本文の項目単位除外に対応しないため、この3完全パスだけ930110を調整し、URI・query・全header・cookieには4本のcustom Blockを合成します。930100等の他の管理規則と本文検査は残します。正本は`apps/gateway/waf-raw-body-controls.json`です。CRSの全正規化処理と完全に同等の代替ではなく、本文内のパス文字列を許容する限定した判断です。Portal文書の`input`は1つのJSON文字列を持つmultipart項目のため、その中の通常のIDで誤検知した5 SQL規則を同項目だけ除外します。

Log4j/JNDIの例文で検知した`Known-CVEs / 800100`も、実測した正規データ欄だけへ限定します。未知のfield・query・headerの攻撃対照と、同名queryへの影響を独立して検証します。

SSMLのDTD/ENTITY宣言は、WAF本文カスタム規則のMIME対応に依存せず、正規TTS操作のAPIMガードで400 `InvalidSpeechMarkup`として拒否します。正本は`apps/gateway/apim/policies/speech-ssml-guard.xml`です。XMLや外部エンティティのparserを呼ばず、UTF-8/16/32を認識して実宣言だけを検査し、escaped例文・コメント・CDATAは許容します。元本文・認証継承・8192byte上限・上流URIは保持します。APIMネイティブの購読認証が先に働く匿名401だけではガード成功とせず、認証付きの正常音声生成と明示400応答も確認します。SSML全体のschema検証は従来どおり下流Speech側の責務です。

`infra/modules/application-gateway.bicep`と`scripts/waf_policy.py`は同じ正本を使用します。個別Gateway／Portal配備も照合するため、Azure上だけの手動例外が次回配備で消える構成を避けます。専用ポリシーは親を継承しないため、共通のPrevention・本文検査・サイズ上限・ログのマスク・customRulesと既存の画像／音声／PDF例外を合成します。企業管理の外部WAFは変更しません。既存の固有保護を表現できない場合は停止します。

通常の配備と同じparamsを指定して、まずローカル計画を確認できます。計画ではAzureに接続しません。適用時は対象環境とETagを照合し、不要なPUTを行いません。

```sh
python3 scripts/waf_policy.py --config "$DEPLOY_PARAMS_FILE"
python3 scripts/waf_policy.py --config "$DEPLOY_PARAMS_FILE" --scope all --execute
```

## 再現する試験

`waf_probe.py`は公開API契約・Portalの実ハンドラーから入口を列挙します。JSON／配列／Schema／関数引数／動的キー、Unicode・記号・コード・Base64、画像・PDF・音声、SSML、multipart、SDP、WebSocket Upgrade、callback、query・header、サイズ境界、攻撃対照を合成入力で試します。

```sh
python3 scripts/waf_probe.py --dry-run --output "$DEPLOY_LOCAL_DIRECTORY/evidence/waf-plan.json"
python3 scripts/waf_probe.py \
  --gateway-base-url https://gateway.example.com \
  --portal-base-url https://portal.example.com \
  --transport curl --workers 4 --requests-per-second 4 \
  --phase verification --output "$DEPLOY_LOCAL_DIRECTORY/evidence/waf-verification.json"

# 正規raw本文と、非本文のパストラバーサル対照を別途確認
python3 scripts/waf_probe.py --raw-body-guards-only \
  --gateway-base-url https://gateway.example.com \
  --portal-base-url https://portal.example.com --transport curl \
  --phase raw-verification --output "$DEPLOY_LOCAL_DIRECTORY/evidence/waf-raw-guard.json"
```

`curl`はWindowsのSchannelで通常の証明書検証を行う環境でも利用できます。リダイレクトは追わず、本文、応答本文、キー、Cookieを証跡へ保存しません。相関用query名はWAF誤検知を増やさない短い英数字にし、値がマスクされてもログと照合できます。

FLUX画像編集はpromptだけでなく、`input_image`のraw Base64画像も確認します。OpenAIの画像URLと形式を混同して`data:image/png;base64,...`を付けず、正規のAPI契約に従った入力で評価します。契約外入力の遮断だけを理由にWAF除外を追加しません。

通常はキー無しで通信します。401、アプリの403／400はWAFを通過した参考証拠ですが、機能成功とは数えません。HTTP 403だけでWAF遮断とは判定せず、App Gatewayの応答シグネチャとAGWFirewallLogs／AGWAccessLogsを照合します。任意の`--key-file`は、通常推論を発生させない不正モデルの認証canaryにのみ使います。実際のモデル／Tool成功は、正規キーと応答形式を確認する少数のE2Eで別途検証します。

同じシグネチャを正規データ欄と、model等の制御欄・未知のJSON欄・query・header・隣接パスへ移します。正規本文の互換性と除外範囲の維持を別々に判定します。存在しない静的パスの404や、利用しないヘッダーの受入をWAF遮断成功とは数えません。

## 適用範囲と運用上の制約

Azureの引数除外は、同じURIのqueryにある同名引数にも適用されます。App Gatewayのpath ruleは大文字小文字を区別せず、HTTP方式でも絞れません。APIM／Portalの認証、公開method、モデル許可、形式・入力制約を維持し、queryの同名欄とmethod違いも対照試験に含めます。

WebSocketはHTTP Upgradeまでを試験します。接続後のframe、デコード後の画像・音声・PDFの意味内容をWAFが検査したとは扱いません。gRPCのTCP入口はHTTP WAFの対象外です。外部A2A・中継MCP・gRPC等を追加公開するときは、その実際の契約で同じ試験を追加します。

入力空間は無限であり、有限の合成試験で将来すべての連携や誤検知ゼロを保証することはできません。新しいAPI・field・managed rule版を追加した場合は、正規データと攻撃対照を再実行します。拒否時は集計規則だけでなく、同一TransactionIdの原因規則とfieldを確認します。942150等の二段判定ログには前段候補のキー名も含まれるため、すべてを除外候補にしません。

manifestからprofileを削除する操作は、現時点のCLIでは既存の関連付けの撤去を行いません。例外を撤去・ロールバックする場合は、変更前に保存した経路とポリシーを照合し、IaCで対象経路を明示的に復元します。バックアップやログには機密ヘッダーを含むApp Gatewayの全設定をそのまま出力せず、経路・WAF・変更識別子だけを保存します。

試行ごとの計画・実測・適用記録は、[共通の証跡ルール](../../DEVELOPMENT.md#テストの共通ルール)に従って環境別のGit管理外領域`.local/<environment>/`へ保存します。試験のrun ID、manifest hash、設定照合結果、E2Eと試験用購読の回収結果を記録します。受入条件は[動作テスト仕様書](integration-tests.md)で確認し、過去の試行の合格を現在の結果へ転記しません。

仕様の根拠: [Azure WAFの除外](https://learn.microsoft.com/azure/web-application-firewall/ag/application-gateway-waf-configuration)、[URI別ポリシー](https://learn.microsoft.com/azure/web-application-firewall/ag/per-site-policies)、[パスの照合](https://learn.microsoft.com/azure/application-gateway/url-route-overview)。
