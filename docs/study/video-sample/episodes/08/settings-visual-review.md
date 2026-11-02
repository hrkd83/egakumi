# 第8回 設定ビューの静止プレビューレビュー

2026-11-02（JST）。画面基準：`teaching-settings-v2`。結果：**合格**。

対象台本のSHA-256：`3d0fdc4bbdfc10c2cfce85bf6a642bc4c0b79bd47a58fdd66dc555bf3f731ba2`。

## 確認範囲

更新済み`output/lesson-08-gemini/previews/`から全20個の`viewId`の代表画面と、問答・赤枠の追加状態を目視した。代表20枚、追加12枚の計32枚を`output/lesson-08-gemini/settings-qa/settings-sheet-01.png`〜`08.png`へ整理し、台本と各プレビューのハッシュを同フォルダーの`review-manifest.json`へ保存した。

3種類のSVG図の赤枠版（012・050・067番）と、修正後のSSH保護設定（046〜048番）は1600×900の原寸画像でも確認した。

| viewId | 対象台詞 |
| --- | --- |
| mgmt-vm-overview | 005〜008 |
| mgmt-ssh-stages | 009〜011 |
| mgmt-private-path | 012〜017 |
| mgmt-nic-output | 018〜021 |
| mgmt-operator-rules | 022〜029 |
| mgmt-operator-port | 030〜032 |
| mgmt-auth-overview | 033〜034 |
| mgmt-ssh-key | 035〜039 |
| mgmt-entra-ssh | 040〜045 |
| mgmt-ssh-os | 046〜048 |
| mgmt-management-execution | 049〜057 |
| mgmt-execution-result | 058〜060 |
| mgmt-downstream | 061〜067 |
| mgmt-ssh-triage | 068〜070 |
| mgmt-command-triage | 071〜072 |
| mgmt-check-private | 073〜075 |
| mgmt-check-empty | 076〜078 |
| mgmt-check-build | 079〜081 |
| mgmt-summary | 082〜084 |
| mgmt-next-a1 | 085〜086 |

## 視認性

スレート色の見出し、淡いメニュー、白い設定欄・表、青緑の値、赤枠の組み合わせを確認した。教材用設定メニューの表記と、資料・定義に基づく説明である旨のフッターがある。設定欄、本文図、外側の題名、右の話者欄、下の字幕はそれぞれの領域に収まり、確認した画面で文字の切れ・枠外へのはみ出し・領域同士の重なりは残っていない。

SSH保護設定の`disablePasswordAuthentication`は、初回に末尾の`n`だけが次行へ回っていた。著者の修正後、`disablePassword`と`Authentication`の境界で2行へ分かれ、046・047・048番の原寸プレビューで単独文字の孤立が解消した。赤枠の有無による行位置の変化もない。

## 質問と回答

073→074、076→077、079→080を前後で比較した。質問073・076・079の本文に正解を示す赤枠はない。回答074ではPublic IPなし・到達経路、077では二つの配列・公開鍵・Managed Run Commandの各行、080では実行状態・成果物・下流接続・調査範囲に赤枠が出る。各前後でメニュー、表の並び、行位置、構図は保たれている。

## 台詞と補助図

- `mgmt-private-path`：運用端末を左、管理VMを右へ置き、企業側の経路・受信NSGを間に並べている。下段に復路と代表設定を併記し、012番の左右への言及と図が一致する。013番の問いと017番の回答も同じ配置である。
- `mgmt-management-execution`：上にSSH、下にAzure管理API→VM agent→VM内処理を並べ、二つの入口と権限・作業例を同じ画面で読める。050・054・056番の赤枠は、その説明対象を示している。
- `mgmt-downstream`：上は管理VM→Firewall→公開依存先、下は管理VM→Private Endpoint→対象サービス。067番の「上」「下」への言及と経路図が一致し、DNS・送信NSG/UDR・Firewall・利用権限の代表行も併記されている。赤枠が矢印やノードの意味を隠していない。

## 検証の区別

本記録は静止プレビューの目視確認。本文・字幕・間・styleの不変性、同一viewの構造、SVGの強調以外の同一性は`settings-verification.json`の機械検証を参照する。完成MP4の実ピクセル・字幕境界での画面保持・連続した質問回答の再生・聴感は、制作担当の完成後検証で記録する。
