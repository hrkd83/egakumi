# 第7回 teaching-settings-v2 への対応案

2026-11-02 JST。最新の `AGENTS.md`、`CONTENT-PLAN.md`、`SETTINGS-VIEW.md`、書式見本JSON、`settings-visual.mjs`、第7回台本と元資料38〜40・46節を読んだ。画面の設計案であり、実環境の設定取得や疎通試験ではない。台本本体・共通ファイルは変更していない。

## 更新する範囲

第7回のテーマ・到達点と対応節は変わっていない。今回の主な差分は、本編の採用基準が `teaching-settings-v2` になったこと。旧版のカードと独立図を、教材用の設定メニュー・表・設定欄を入口とする画面へ置き換える。ロゴ・声・本編BGM・冒頭ホログラムは維持する。

`script.json` のトップレベルに `visualStyle: "teaching-settings-v2"` を加える。本編は `layout:"settings"` を基本とし、設定表と補助図の併記が必要な場面は担当回のSVGと `renderSettingsShell()` を使う。質問から回答・反応まで `visual.viewId`、メニュー、行順、図の位置、縮尺を保つ。話者や字幕chunkの切替では画面を変えない。赤枠は出し入れのみで、pulse・ズーム・パン・疑似ポインターを加えない。

既存の本文・声・styleは技術的な修正を必要としていない。映像の更新だけなら `prepare ... --replace` → `generate ...` のキャッシュ結び直し → `build ... --reuse-audio true` でAPIの追加送信を避けられる。台詞ID、本文、delivery、章のtitle、採用集合や直前の発言を変えるとstyleや文脈が変わる場合があるので、今回の画面改修では維持する。

## 会話ごとの viewId 案

以下の番号は `lesson07-turn-NNN-...`。080/083は採用中の `080r2` / `083r2` を指す。079/082は省略したままとする。

| 採用turn | viewId案 | 設定・補助図 | 赤枠の対象 |
| --- | --- | --- | --- |
| 007〜009 | `triage-connection` | 教材の調査対象欄：U1、AppGW→APIM、TCP80。隣にAppGW→FW→APIMの小図 | 質問007はなし。008で対象接続、009で接続区間の分離 |
| 010〜014 | `connection-identity` | 調査票：失敗時刻/TZ、通信元、宛先名、DNS回答IP、Protocol/Port、症状。数値は推測で埋めず「実値を確認」 | 011で条件群、013〜014で時刻/TZと検索対象 |
| 015〜017 | `source-ip` | 送信元IPと利用者情報の区別。AppGWで接続が分かれる小図と、NSG/FWが見る送信元の欄 | 015は問いの条件のみ、016でAppGWからの接続、017で調査の順序 |
| 018〜021 | `dns-source-answer` | 問い合わせ元、参照DNS、APIM名、内部IP回答、ゾーン/リンク/転送。端末成功とAppGW確認の小さい左右比較 | 018は回答強調なし、019で問い合わせ元、020で確認場所、021で回答とゾーン |
| 022〜027 | `effective-return-route` | **往路/復路の代表UDR表とAppGW↔FW↔APIMの小図を併記**。実APIM内部IPに対する往路、AppGW区画に対する復路、取得可能な実効情報 | 022はなし、023で宛先/次ホップ、024〜025で往復、026で設定と実効情報、027で経路と許可の役割 |
| 028〜031 | `u1-nsg-rules` | 同じU1図を小さく添え、AppGW送信 `allow-apim-http` / APIM受信 `allow-agw-api` / FW `agw-to-apim-http` の表。NSG宛先はAPIM区画、FW/UDR宛先は実VIPという粒度を混同しない | 028はなし、029で両側NSG、030で向き/条件と保持されるAppGW IP、031で三層の分担 |
| 032〜036 | `firewall-log-type` | ログ種別表：Network=`AZFWNetworkRule`、Application=`AZFWApplicationRule`、それぞれの照合項目。Network一致→Applicationへ進まない注記 | 032はなし、033でNetwork、034〜035でApplicationと評価順、036で記録の限界 |
| 037〜041 | `network-log-columns` | 検索条件/Action/ActionReason/Policy・RCG・Collection・Ruleの5行程度。実ログを捏造せず列名と確認方法として表示 | 037で対象条件とAction、038は質問に答える欄を強調しない、039〜040でActionReason/Default Action、041で適用条件 |
| 042〜043 | `allow-scope` | FWでの判定、受信側、復路、認証/認可、処理結果を区別する確認欄 | 042はなし、043でFWの許可と未確認の層 |
| 044〜047 | `missing-log-direct-pe` | ログ検索条件と収集状態の欄に、APIM→Foundry PEの直接経路小図を同じ画面へ併記。FWを経由しないことを説明できる構成 | 044はなし、045で条件/収集、046で直接経路、047でDNS/両側NSG/サービスログ |
| 048〜055 | `triage-u1-example` | 演習用調査票とU1小図。値は「演習上の仮定」と明示。DNS/往復/両側NSG、Deny/Default Action、適用ポリシー/ルールを同じ構図内に配置 | 各turnでその観測と確認先だけを強調。最初からDenyを確定した実測ログとして表示しない |
| 056〜059 | `allow-next-evidence` | 受信側/復路、APIM記録、AppGW Accessの時刻/status/backend/TransactionId、WAFとの区別、引継ぎ欄 | 056はなし、057で次の確認先、058でAppGWの列とログ種類、059で引継ぎ |
| 060〜062 | `diagnostic-destinations` | 診断設定表：Network/Application→LAW Dedicated、監査有効時→日本側Storage。保存先の分岐小図を併記 | 060はなし、061で保存先、062で設定と到着の違い |
| 063〜065 | `telemetry-scope` | 通信ログ/メトリック/Activityの目的の表。Flow Logs/Packet Captureは現行定義なしという注記 | 063で収集外、064は問いに合わせ、065で数値/管理操作の役割 |
| 066〜070 | `alert-condition-action` | **通知の評価と配線を1画面に保持**。上部に状態/管理操作→条件→Action Groupの小図、下部にAppGW/FWメトリック2行と運用者メールの行。メトリックだけ別画面へ切り替えて戻さない | 066はなし、067で条件/配線、068で2つの閾値、069で評価窓、070で通知先と自動処理なし |
| 071〜073 | `activity-alert-scope` | `network-config-changed` の対象ID/操作名/結果/対象外を表にする。ACA別RT・PE・Private DNSがこの変更通知の一覧外 | 071はなし、072で対象範囲、073で収集/通知の区別とFailed |
| 074〜076 | `log-alert-flag` | `enableLogAlerts`、本番false/テストtrue、3本のKQL通知、別設定の診断/メトリック/Activity。FW Deny全件通知ではない注記 | 074は問いの設定値のみ、075で切替範囲、076で別設定の区別 |
| 077〜078 | `check-pe-no-log` | 条件＝APIM→Foundry PE/TCP443/FWログなし。判定欄は質問中「未確認」、回答で確認順を表示。行と位置は同じ | 077で答えの赤枠なし、078で直接経路と確認先 |
| 080r2〜081 | `check-fw-allow` | 条件＝同じ通信のFW Allow。API認証/処理の確認結果は質問中「未確認」。回答でFWの許可とAPI結果を区別 | 080r2で答えなし、081で未確認範囲 |
| 083r2〜085 | `check-deny-notification` | 条件＝Deny保存あり/メールなし/本番flag=false。通知定義と配線の判定欄は質問時「未確認」 | 083r2で答えなし、084から通知条件と配線、085で保存/評価/送信先 |
| 086〜091 | `triage-summary` | 代表調査票の確認順と記録・通知の区別を一覧にする。最終行に第8回のprivate接続/NSG/SSH/Run Command | 086〜089で振り返る対象、090〜091で次回行 |

## 答えの先取りを避ける

旧版の確認3問は質問時から「判断」カードに正解が表示される。新版では赤枠を外すだけでなく、質問中の断定的な正解文を置かない。条件はそのまま残し、「確認結果：未確認」など中立の値を示す。回答時に同じ行/同じ配置の値を説明内容へ更新し、そこで赤枠を出す。メニュー、行ID、順序、画面タイトル、図の位置、縮尺は変えない。

一般の質問でも、問答の途中に別画面へ戻る理由はない。設定内容を先に見せる必要がある場合は中立の確認対象として配置し、赤枠を回答の時点から使う。

## スキーマと寸法の注意

- `settings`は `fields` または `table` の一方だけ。`fields`/tableの行IDは一意で、赤枠には実在するIDのみを指定する。tableは `highlightRows`、fieldsは `highlightFields`。
- fields/tableは1〜6行。**noticeあり6行では行高が48px未満になり、2行折返しが使えない**。fieldsは45px、tableは41pxになる。2行にする場合はnoticeを外すか、5行以下へ減らす。エラーを避けるため極端に文字を小さくしない。
- tableは2〜5列、すべて幅指定する場合は合計972px。省略幅は残りを均等配分し、各列最低100px。列見出しは幅−24px/16pxで1行。
- fieldsはラベル302px/18px、値609px/20px。全角ならラベル16字程度、値30字程度が1行の目安。2行可能でも情報を短くする。
- メニューは最大8項目、各ラベル154px/16px（全角9字程度）。`section`がmenuにないと自動追加されるので、8項目を越えないよう注意する。
- `notice`は938px/19px、改行不可。title・subtitle・footerもそれぞれ1行。補足は「教材注釈」「公開設定例」「演習上の仮定」と分かる短い表記にする。
- `zoom`/`camera`/`motion`/`azureMotion`は渡さない。赤枠も静的な細枠とする。
- 補助SVGは `viewBox="0 0 1207 511"`、設定ビュー内の図の領域 `x:214,y:147,width:972,height:328`。担当回の `diagrams/` に自己完結で保存。外部参照、script、foreignObject、animationを含めない。
- 実IPや稼働リソース名を推測で埋めない。「送信元の実IP」「APIMの内部IP（確認値）」など、調査で得る値と設計上の値を区別する。実ログに見える架空のタイムスタンプ/行を作らない。

## 連続プレビュー候補

旧完成タイムラインで測定した範囲。画面のみの改修で音声と間を再利用するなら、時刻も同じになる見込み。最終版のタイムラインと共通ツールの出力で確定する。

| 優先 | from-turn | through-turn | 元動画の範囲 | 尺 | 見ること |
| --- | --- | --- | --- | --- | --- |
| 主 | `lesson07-turn-022-mako` | `lesson07-turn-027-manabu` | 205.750〜269.583秒 | 63.833秒 | 問い→宛先/次ホップ→往復→実効情報→役割の確認を同じ設定表+小図で保持する |
| 予備 | `lesson07-turn-032-mako` | `lesson07-turn-036-manabu` | 318.208〜372.042秒 | 53.833秒 | Network/Applicationの問答、行の赤枠のみを変えて画面を保持する |
| 予備 | `lesson07-turn-066-mako` | `lesson07-turn-070-ikumi` | 711.750〜768.333秒 | 56.583秒 | 条件→メトリック→評価窓→通知先まで、図と代表設定を併記した同じ画面を保持する |

主候補のコマンド：

```sh
python3 docs/study/video-sample/make-conversation-preview.py --episode 7 --from-turn lesson07-turn-022-mako --through-turn lesson07-turn-027-manabu
```

40〜70秒の連続会話で、字幕chunkや話者が変わるたびに本文が揺れないこと、表と補助図を往復させないこと、質問から回答まで枠/縮尺が保たれることを見る。静止画だけではこの確認の代わりにならない。全編デコードの合格、静止代表図の目視、連続会話の視聴による編集判断、音声の聴感は別々に記録する。
