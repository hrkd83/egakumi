# 第8回 teaching-settings-v2への画面改修

2026-11-02 JST。最新の `docs/study/AGENTS.md`、`CONTENT-PLAN.md`、`PRODUCTION-GUIDE.md`、`SETTINGS-VIEW.md`、`production-profile.json`、`settings-visual.mjs`、書式見本と、担当回の `settings-source-review.md` を参照した。

本編82台詞を20個の問答単位へ再編集した。設定メニューの60台詞は共有 `layout:settings`、図と設定を併記する22台詞は `renderSettingsShell()` を用いた自己完結SVG。冒頭001〜004のホログラムは維持する。トップレベルへ `visualStyle: teaching-settings-v2` を設定した。

## 問答ごとの画面保持

| 台詞 | viewId | 構成と強調の意図 |
| --- | --- | --- |
| 005〜008 | mgmt-vm-overview | 5行のVM概要。用途・OS・private IPv4・Public IPなし・役割の境界 |
| 009〜011 | mgmt-ssh-stages | 到達・NSG・OSログインと資料範囲を保持 |
| 012〜017 | mgmt-private-path | 左の運用端末から右の管理VMまでの図、復路、企業側の準備とNSGの役割を併記 |
| 018〜021 | mgmt-nic-output | Dynamic / IPv4と、実IPは対象VM・NICの出力を確認する旨 |
| 022〜029 | mgmt-operator-rules | 空配列・指定時・最後の受信拒否を同じ6行の表で比較 |
| 030〜032 | mgmt-operator-port | Tcp / ["22"] と、OSの待受の担当を区別 |
| 033〜034 | mgmt-auth-overview | 二つの認証方式と共通の到達前提を保持 |
| 035〜039 | mgmt-ssh-key | 公開例のユーザー・公開鍵ファイルとOS配置先、秘密鍵の保持、鍵方式の権限 |
| 040〜045 | mgmt-entra-ssh | 拡張・割当要素・二つのロール・VM scope・空割当と既存権限を6行で保持 |
| 046〜048 | mgmt-ssh-os | パスワード禁止・root禁止・公開鍵維持と、構文・実効設定の確認 |
| 049〜057 | mgmt-management-execution | SSHと管理APIの二つの経路に、処理例・権限・空の運用元配列の意味を併記 |
| 058〜060 | mgmt-execution-result | API受付・管理状態・実行状態・終了コード・成果物を同じ5行で確認 |
| 061〜067 | mgmt-downstream | 上は公開宛てFW経由、下はPE直接経路。DNS・送信NSG/UDR・FW・サービス権限を同じ画面に併記 |
| 068〜070 | mgmt-ssh-triage | SSHの到達・NSG・待受・認証の確認先を保持 |
| 071〜072 | mgmt-command-triage | 管理操作・agent・下流・サービス認証・結果の確認先を保持 |
| 073〜075 | mgmt-check-private | 確認①。073は赤枠なし、074で宛先・経路、075で通信・OSを強調 |
| 076〜078 | mgmt-check-empty | 確認②。076は赤枠なし、077で各方式、078で二つの入力の役割を強調 |
| 079〜081 | mgmt-check-build | 確認③。079は赤枠なし、080で実行結果・下流、081で結果と必要な通信を強調 |
| 082〜084 | mgmt-summary | SSH・各権限・管理実行・下流と結果を同じ6行で振り返る |
| 085〜086 | mgmt-next-a1 | ハブ接続の採用範囲と維持するローカル経路、往復の整合を予告 |

同じviewIdの設定行・メニュー・見出し・構図・縮尺は固定し、説明に応じた赤枠だけを変える。台詞・話者・字幕chunkは本文の切替理由にしない。本編にズーム・パン・pulse・ポインター・アニメーションを追加しない。Azureのブランドバー、検索、疑似操作ボタンを模倣しない。

## 図の生成と検査

`render-diagrams.mjs` を実行すると、担当回の `script.json` のvisualと `diagrams/settings-v2/` のSVGだけを更新する。共有レンダラーへの変更や、第1回の回専用ツールへの依存はない。

```sh
node docs/study/video-sample/episodes/08/render-diagrams.mjs
```

SVGは `viewBox="0 0 1207 511"`、外部参照・script・foreignObject・アニメーションなし。設定用の共通殻に図と代表行を置く。赤枠は `<g id="explanation-highlights">` 内の単独rectのみで、`fill="none" stroke="#de343b" stroke-width="2.8"`。このgを除くと同じviewId内のSVGは文字・座標・線・設定行を含め同一になる。

共有settings描画関数で全60設定turnの内容を検査し、メニュー8項目以内、fields/table6行以内、存在する強調IDと文字の収まりを確認した。3種類の補助図を原寸PNGへレンダリングして目視し、文字や図の大きな衝突・はみ出しは認めなかった。連続動画の画面保持と最終動画の検証は、完成書き出し後にrootで実施する。

## 音声を再利用できる根拠

保存済みの `revisions/diagram-v1/script.json` と新しい `script.json` から、トップのvisualStyleと各turnのvisualだけを除外して比較した。**すべての非visualフィールドが完全一致**した。86台詞のID、speaker、chapterId、speech、caption、subtitleLines、subtitleChunks、delivery、gapAfterSeconds、章構成、速度、キャスト指定は変更していない。statusも `ready-for-generation` を維持した。

非visualのJSON（キー順を維持したcompact JSON）のSHA-256は `98d005ea5ed7cb3e92b185fe550168187141243d7e4b91b5a946316d49d9123f`。

さらに現行の `prepare-gemini-lesson.py` の `prepare_document()` をメモリー上だけで実行し、保存済み旧 `generation.json` と照合した。**全86件のcandidateレコード、globalStyle、castSha256が完全一致**した。本文、直前台詞の文脈、人物・章・deliveryから組み立てるstyle、声、通常速度、表示本文を含む全生成入力が同じである。

86件のcandidate配列（ensure_ascii=False、compact JSON）のSHA-256は `d054c2b145c0d7028595a40b3e01c8cd445f4080d57c3b86450cb11ff61017f9`。この検査でAPI呼出し、認証読取り、generation.jsonへの書込みは行っていない。

音声の原本・BGM・ロゴ・共通クロージング・共有ファイルは変更していない。台本全体のハッシュは画面変更により変わるため、rootで制作手順のprepare --replace → generateの検証済みキャッシュ再結合を行い、追加API 0件を確認してから --reuse-audio true で合成する。
