# 第9回 出典・内容監査

監査日：2026-11-02 JST。題材は「WAFの検査と誤検知への対応」。対象は `02-external-protection.html` の1〜3節の復習と4〜8節。稼働Azureへの設定取得・変更・攻撃試験は行っていない。ここでいう指定値は現行リポジトリのconfig・IaCと公開教材の値である。

## 到達点と代表例

攻撃パターン、本文の受入上限と検査範囲、実際に適用するポリシーを別々に確認し、正規データの除外を「パス × 対象項目 × 原因ルール」に限定する理由を説明できること。主例はPortalのMicrosoft認証callbackで、正規の不透明な `code` 値がSQLi規則に誤一致するケース。通常HTTP APIの通信経路は導入の復習として使い、callback自体をAPIM経由とは説明しない。

## 原稿へ必要な事実

| 論点 | 確認結果 | リポジトリの根拠 |
| --- | --- | --- |
| 防御の配置 | WAFはAppGWで復号したHTTPを検査。FW通信規則、IDPS、APIMは別の判断。通常APIはWAF→FW→APIM、Portal入口はAPIMへ送らない。gRPCのTCP入口にはHTTP WAFを適用しない。 | `docs/study/02-external-protection.html:31`、`:43`、`:52` |
| 前段で遮断 | WAFで拒否した要求はFW/APIMへ業務転送しない。403という状態コードだけではWAF原因と断定できず、APIMなど別層でも403を返す。 | 同HTML `:56`、`:58`、`:60`、`:63` |
| 現行モード | testとproductionともPrevention。Enabled、本文検査true、本文・ファイル上限の強制trueは共通Bicep定義。 | `config/test.json:369`、`config/production.json:381`、`infra/modules/application-gateway.bicep:181` |
| 管理ルール | OWASP CRS 3.2。SQLi、XSS、パストラバーサル、プロトコル違反などのパターンを調べる。業務上の認可やモデル契約を確認する機能ではない。 | HTML `:69`、Bicep `:193` |
| 異常スコア | 通常のCRS管理規則は重大度に応じたスコアを加算し、合計5以上でPrevention遮断。Critical=5、Warning=3。MatchedとBlockedを区別。サイズ上限・custom Blockは別の拒否条件。 | HTML `:76`、公式「異常スコア」資料 |
| 共通サイズ指定 | 受入2000 KiB、検査2000 KiB、multipartファイル4 MiB。raw音声/PDFはfilename付きmultipartファイル扱いではなく、通常本文の上限が適用される。 | HTML `:80`〜`:93`、`config/test.json:373`、`config/production.json:385` |
| 既定値との区別 | Bicepの省略時既定値は受入128 KiB、検査128 KiB、ファイル100 MiB。configで明示する値とは異なる。動画で既定値を現行指定値として示さない。 | HTML `:93`、Bicep `:79` 以降、`infra/main.bicep:513` |
| 受入と検査 | 許容するサイズと、どこまで検査するかは別設定。受入だけ増やすと検査範囲との差が生まれ得る。ファイル上限の4 MiBをすべてのHTTP本文の上限とみなさない。 | HTML `:87`〜`:92`、Bicep `:186`〜`:190` |
| 適用ポリシー | URI別→listener別→Gateway全体の順に具体的なポリシーが優先。親子で順に検査する仕組みではない。専用ポリシーに必要な保護はIaCのsharedWafPropertiesで明示合成。 | HTML `:96`〜`:103`、Bicep `:179`〜`:198`、`:456` |
| 既存企業WAF | existingWafPolicyIdがあれば既存企業ポリシーを使用し、自動生成専用ポリシーで置き換えない。実適用先と企業管理範囲の確認が必要。 | Bicep `:130`、HTML `:103` |
| 空配列 | configのwafCustomRules/wafExclusionsが空でも、用途別ルールやmanifestの除外が別に合成される。WAF全体に例外がないとの意味ではない。 | HTML `:102`、Bicep `:467`〜`:496` |
| Portal callbackの除外 | `/api/auth/callback/microsoft`。RequestArgValues / Equals / code。codeという引数名で選択した「値」を942430/942440のSQLi評価から外す。state、他項目、他規則、サイズ/本文検査/ログ保護を維持。 | HTML `:110`、Bicep `:208`〜`:230`、`:448`〜`:454` |
| Gateway callbackとの区別 | `/auth/connections/browser/identity-callback` は942430/942440/942450の3規則。Portalの2規則と混同しない。 | HTML `:111`、Bicep `:233`〜`:256` |
| 除外の境界 | パス照合は大文字小文字を区別せずHTTP方式で絞れない。同じURIの同名queryにもRequestArgValues除外が適用されるため、本文だけの例外とは扱わない。method・認証・入力契約はAPIM/Portalで維持し、query/method違いも試験。 | HTML `:112`、公式「除外」「パス照合」 |
| ログマスクとの区別 | logScrubbingはログ中の値の保護で、検査の除外とは別設定。マスクしたので検査されない、とは説明しない。 | HTML `:74`、Bicep `:171`〜`:191` |
| raw形式の調整 | 対応パスのSSML/WAVで920420を無効化。正本Speechのraw本文と固定文書APIでは930110も調整するが、930100など他規則と本文検査は維持。 | HTML `:121`〜`:125`、Bicep `:259`〜`:337` |
| 補完Block | URI/query/header/cookieの4面をcustom Blockで補う。raw本文全体へ元のCRS規則と同等の防御を復元するわけではない。 | Bicep `:268`〜`:285`、`apps/gateway/waf-raw-body-controls.json:5`、HTML `:125` |
| 文書の境界 | `/tools/documents/v1/analyze` のWAF受入/検査は1025 KiB。公開API契約の1 MiBはAPIMがbytes長で強制。両値を同じ設定と扱わない。WAFは画像・音声・PDFを復号/デコードして意味内容を審査しない。 | Bicep `:324`〜`:336`、HTML `:123`〜`:125`、`apps/gateway/apim/policies/document-analysis.xml:19`〜`:27` |

## 話し方・画面で避ける断定

- 「SQLという単語があれば必ず攻撃」「Matchedログが1件あれば遮断」「403はWAF」を避ける。攻撃に似た文字列を正規データとして送るケースと、実際の拒否Actionを分ける。
- 「専用ポリシーは親から自動継承」「WAF全体と専用を二度通る」を避ける。関連付けの優先順位とIaCによる設定の合成を分ける。
- 「ファイルはすべて4 MiB」「2000 KiB=2000 KB/2 MiB」と丸めない。サイズの測定対象と単位を示す。
- 「RequestArgValuesはcodeという値に一致したら除外」を避ける。selectorは引数名codeであり、除外されるのはその値。
- 「callbackのPOST本文だけを除外」を避ける。WAFパス関連付けはHTTP方式条件を持たず、queryの同名引数も対象。
- 「949110を無効化」を対策にしない。これは合計スコアによる強制の必須規則で、原因パターンではない。同じTransactionIdのMatched規則を調べる。
- 「custom Allowで安全な入力だけ通す」を誤検知の標準対策にしない。後続管理ルールを止め、CRS3.2では高優先Allowがサイズ強制より先に作用し得る。限定した項目/原因ルールの除外を使う。
- 教材の画面は設定例。稼働設定取得や実機検証の結果とは表示しない。

## 推奨する3つの確認問題

1. 「WarningのMatchedが1件。直ちに遮断済みと言えるか」→スコア3単独は閾値5未満。Actionと同一要求の集計結果を確認。
2. 「raw PDFを3 MiB送る。ファイル上限4 MiBだから通るか」→raw本文なので4 MiBのmultipartファイル枠は使えない。さらに固定文書APIはWAF1025 KiB/APIM1 MiBの専用境界。
3. 「正規codeの誤検知。全体SQLi無効化とcode値の原因2規則除外、どちらか」→当該callbackパスのcode値と原因規則に限定。他項目/他規則/認証・method等を維持し、正規対照と攻撃対照で検証。

## Microsoft一次資料の確認

2026-11-02に参照。以下は製品仕様の確認であり、egakumiの稼働値を示すものではない。

- [WAF request and file upload size limits](https://learn.microsoft.com/en-us/azure/web-application-firewall/ag/application-gateway-waf-request-size-limits)：本文受入と検査の独立設定、filename付きmultipartファイルだけがファイル上限対象、他形式には本文上限。CRS3.2高優先custom ruleの例外にも留意。
- [WAF policy overview](https://learn.microsoft.com/en-us/azure/web-application-firewall/ag/policy-overview)：per-URIがper-site/globalを置換する。
- [WAF exclusion lists](https://learn.microsoft.com/en-us/azure/web-application-firewall/ag/application-gateway-waf-configuration)：RequestArgValuesの対象、フォーム/JSON/queryの属性、できるだけ規則別の除外に限定する推奨。
- [Application Gateway URL path routing](https://learn.microsoft.com/en-us/azure/application-gateway/url-route-overview)：パス照合はcase-insensitive。
- [CRS/DRS rules and anomaly scoring](https://learn.microsoft.com/en-us/azure/web-application-firewall/ag/application-gateway-crs-rulegroups-rules)：重大度と加点、5以上の閾値、MatchedとBlocked/Detectedの区別。
- [Troubleshoot anomaly scoring and rule 949110](https://learn.microsoft.com/en-us/troubleshoot/azure/application-gateway/troubleshoot-waf-anomaly-scoring-rule-949110-blocks)：949110は変更不可の集計規則、同一transactionの寄与規則を追跡する。
- [Custom WAF rules overview](https://learn.microsoft.com/en-us/azure/web-application-firewall/ag/custom-waf-rules-overview)：custom ruleが管理ルールより先に評価され、Allow/Blockの一致で後続評価を止める。

## 監査結論

教材1〜8節と現行config/IaCの代表値・除外例は整合している。上記の粒度と制約を維持すれば第9回の到達点に沿う。IDPSの詳細、APIMポリシー順・トークン制限、KQL全文は次回以降へ残す。完成原稿は別途台詞と画面の対応を確認する必要がある。


## 完成原稿の技術再監査

2026-11-02、`script.json`（80台詞、SHA-256 `3c9665dfa69b0a560b4b613b5c0fc6d9f11cccf8b020ebb558706d7281150f00`）、`author.py`、`diagrams/*.svg` の文字内容を再確認した。音声・映像の実長や聴感は本監査の対象外。

結論：技術的な必須修正はない。指定範囲の到達点を満たす原稿として音声生成へ進める。

| 確認対象 | 対応するturn | 判定 |
| --- | --- | --- |
| 通常APIとPortal入口/APIMの区別、gRPC対象外 | 005〜012、045〜048 | 合格。導入は通常API、callback例はPortalと明記し、Portal入口はAPIMへ転送しないと説明。 |
| スコア・Matched/Blocked・949110 | 020〜024、054〜056、068〜070 | 合格。Warning3点と閾値5、Critical5点、サイズ/custom Blockの別条件、949110が集計規則である点を区別。949110の無効化を提案していない。 |
| サイズ単位と対象 | 025〜033、063〜065 | 合格。共通2000 KiB、filename付きmultipart4 MiB、raw本文との区別、固定文書WAF1025 KiB/APIM1 MiBを正しく説明。 |
| ポリシー選択と明示合成 | 034〜041、071〜073 | 合格。具体性の優先順位、親との二重検査でない点、sharedWafPropertiesの合成、既存企業ポリシーの分担を説明。 |
| callbackの除外単位と境界 | 042〜053、074〜076 | 合格。Portal完全パスのcode値に942430/942440、Gateway別パスでは942450も含む。state等の保持、HTTP方式やqueryへの広がり、大文字小文字の扱いも説明。 |
| 920420/930110と補完Block | 057〜062、補助図complement-* | 合格。対応パスのContent-Type制約、正本Speech raw本文、非本文4面の補完、CRS全検査と同等ではない点を維持。図には固定文書本文も明記。 |
| 専用入力検査と意味内容 | 063〜067 | 合格。WAF通過をPDF/音声のデコード後の意味検査と扱わず、APIM契約・ガードを維持。正規/攻撃対照と境界の確認を含む。 |
| 図と台詞 | route-*、scope-*、complement-* | 合格。通常HTTP APIの経路、単一適用の優先、raw/非本文の分担が台詞と一致。SVGの版違いは赤枠だけを変え、同一問答の配置を保持。 |

任意の明確化案（親へ連絡済み、技術上の阻害ではない）：

- turn058の「正規のContent-Typeが一致する920420」は「正規のContent-Typeが許可外と判定される920420」とすると、MIMEが正規でも管理ルールに不適合と判断される意味が明確になる。
- turn056の「集計規則を外して結果だけ消すこともしません」は「集計規則949110は無効化できません」とすると、運用方針だけでなく製品の必須規則という制約まで伝えられる。現状も無効化を提案しておらず、重大な誤りはない。

本監査は原稿を直接変更していない。採用する場合はauthor.pyとscript.jsonの生成元/結果を合わせ、未生成なら既存IDのまま確定できる。生成済みのspeech/styleを変更する場合は制作手順の新IDによる再録規則に従う。
