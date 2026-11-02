# 第8回 基盤管理者の接続と作業実行

出典は `docs/study/CONTENT-PLAN.md` の第8回。管理VMへのprivate接続、NSG、SSH認証、Managed Run Commandの分担を説明できることを到達点とする。教材の主範囲は [41〜43節](../../../01-network-control.html#maintenance-network)。

共通仕様は [制作手順](../../PRODUCTION-GUIDE.md)、[制作プロファイル](../../production-profile.json)、[現行キャスト](../../podcast-cast-gemini.json)、[Podcast形式](../../PODCAST-FORMAT.md)、[設定ビュー仕様](../../SETTINGS-VIEW.md) に従う。画面基準は `teaching-settings-v2`。

## 内容の構成

| 章 | 台詞番号 | 目的・代表図 | 教材 |
| --- | --- | --- | --- |
| 今回の問い | 001〜004 | 公開IPがない管理VMへ、鍵だけで入れるか。独自のホログラム掛け合い | 41・43節 |
| 管理VMの目的 | 005〜011 | 証明書・内部保守・イメージビルド。三段階の確認と参照範囲 | 41節 |
| 到達経路 | 012〜021 | 運用端末→企業側の既存接続経路→NSG→VMの実private IP | 41節 |
| NSG | 022〜032 | 空配列のDeny/4080、指定時Allow/100、最終Deny/4096。ポートとOS待受を区別 | 41節 |
| SSH認証 | 033〜048 | 公開鍵とEntra SSHの二つの入口。空の割り当て、OS保護、人・配備主体・VMの権限 | 42節 |
| 管理実行 | 049〜060 | 配備主体→Azure管理API→VM agent→VM内処理。API受付と実行結果・ビルド成果物を分離 | 43節・image_build.py |
| 下流と切り分け | 061〜072 | VM→FW→公開依存先、VM→PE→サービスの例。接続条件と処理先の認証 | 43節、補助図45節と整合 |
| 確認問題 | 073〜081 | 公開IPを許可、二つの空配列、Run Command後のビルド失敗の3問 | 41〜43節 |
| まとめ | 082〜086 | 接続と実行を役割で説明し、必修A1「ハブ＆スポーク接続と往復経路」へつなぐ | 41〜43節・CONTENT-PLAN |

## 本編の設定メニューと補助図

本編82台詞を20個の問答単位の `viewId` にまとめる。60台詞は共有 `layout:settings` の設定欄・表、22台詞は設定の代表行と補助図を併記した自己完結SVGを使う。SVGも `renderSettingsShell()` による同じ教材用設定メニューを持ち、共有フレームの1207×511領域へ配置する。冒頭001〜004のホログラムは維持する。

| 台詞番号 | viewId | 保持する設定・補助図 |
| --- | --- | --- |
| 005〜008 | `mgmt-vm-overview` | 管理VMの用途・OS・private IPv4・Public IPなし |
| 009〜011 | `mgmt-ssh-stages` | 到達・NSG・OSログインの三段階と参照範囲 |
| 012〜017 | `mgmt-private-path` | 左の運用端末から右の管理VMへの経路・復路と設定行を併記 |
| 018〜021 | `mgmt-nic-output` | NICのDynamic / IPv4と実出力の確認 |
| 022〜029 | `mgmt-operator-rules` | 空配列・指定時・最終拒否を同じ受信NSG表で比較 |
| 030〜032 | `mgmt-operator-port` | `Tcp` / `["22"]` とOS待受の分担 |
| 033〜034 | `mgmt-auth-overview` | 公開鍵・Entra SSHと共通前提 |
| 035〜039 | `mgmt-ssh-key` | 初期ユーザー、公開鍵入力とOS配置先、秘密鍵の保持 |
| 040〜045 | `mgmt-entra-ssh` | 拡張・割当要素・ロール・VM scope・空割当 |
| 046〜048 | `mgmt-ssh-os` | SSH保護設定と構文・実効設定の確認 |
| 049〜057 | `mgmt-management-execution` | SSHと管理APIの入口図、処理例・権限・空の運用元配列の意味を併記 |
| 058〜060 | `mgmt-execution-result` | API受付・管理状態・実行状態・終了コード・成果物 |
| 061〜067 | `mgmt-downstream` | 上のFW経由と下のPE直接経路に、DNS・送信NSG/UDR・FW・サービス権限を併記 |
| 068〜070 | `mgmt-ssh-triage` | SSH接続で止まった段階の確認先 |
| 071〜072 | `mgmt-command-triage` | 管理実行の開始と作業中の失敗の確認先 |
| 073〜075 | `mgmt-check-private` | 確認①：公開IPの許可元追加と到達前提 |
| 076〜078 | `mgmt-check-empty` | 確認②：二つの空配列の担当範囲 |
| 079〜081 | `mgmt-check-build` | 確認③：API受付後の実行結果と下流接続 |
| 082〜084 | `mgmt-summary` | 接続・権限・管理実行・下流と結果の振り返り |
| 085〜086 | `mgmt-next-a1` | ハブ接続の採用範囲・維持する経路・往復の整合 |

一つの問いから回答・反応まで、メニュー、行順、構図、縮尺を保持する。台詞・話者・字幕chunkの切替で本文画面をリセットせず、説明対象の赤枠だけを変える。確認問題の073・076・079は赤枠なしとし、回答で関係項目を強調する。設定と経路図を短い間隔で往復させず、図に言及する012と067には対応する左右図・上下図を同じ画面へ置く。

本編は固定カメラとし、自動ズーム・パン・赤枠のpulse・模擬ポインターを入れない。Azureのブランドバーや検索欄を部分的に模倣しない。メニュー8項目以内、設定欄・表6行以内とし、実IPは推測せず対象VM/NICの実出力を確認する旨を示す。詳しい移行内容と音声再利用の根拠は [settings-migration.md](settings-migration.md) に記録する。

## 連続プレビュー

完成動画から、質問の頭から回答・反応の終わりまでを途中で切らず、通常速度で取り出した。2本とも元の声とBGMを連続して保持し、フレーム数・尺・全編デコードの検査はpassed。

| 対象 | 台詞番号 | 元動画の範囲（秒） | 尺 | 確認する画面保持 |
| --- | --- | --- | --- | --- |
| [private接続の問答](../../output/lesson-08-gemini/continuous-previews/private-route/conversation-preview.html) | 013〜017 | 127.000000〜182.791667 | 55.791667秒 | `mgmt-private-path` の左右図・設定行を保持し、企業側の準備とNSGの分担を説明 |
| [管理実行の問答](../../output/lesson-08-gemini/conversation-preview.html) | 053〜057 | 584.000000〜644.500000 | 60.5秒 | `mgmt-management-execution` の二つの入口図を保持し、鍵・管理権限・受信NSGの指定を区別 |


## 技術上の編集判断

- 到達経路・受信NSG・OS待受と認証を、それぞれ満たす条件として説明する。現在のinfraがVPN Gateway、Bastion、VNetピアリングを構築する説明はしない。
- 管理VMはDynamic private IPv4のみ。宛先の固定IPやNSGが観測する送信元IPを推測しない。
- 公開鍵方式にEntra VMログインロールを重ねて必須としない。`loginAssignments=[]` はロールを追加しない指定で、鍵ログインの無効化や継承権限不存在の証明ではない。
- `operatorAddressPrefixes=[]` は受信NSGの指定で、Managed Run Commandを停止するフラグではない。
- Run Commandの管理操作権限、Entra SSHのVMログイン権限、VMのサービス利用権限を区別する。どちらの入口も下流通信を無条件許可しない。
- API受付や管理リソースの状態だけでビルド成功としない。`instanceView.executionState` と `exitCode`、ソースhashとimage digestの照合を具体例にする。
- 稼働中Azureへの接続・測定は行っていない。教材とリポジトリ定義・公開設定例の説明として扱う。

## 台本・音声・字幕

台本は9章86台詞、読み上げ本文5,872字、表示本文5,626字。尺は通常速度の生成音声から確定する。画面改修でも本文・字幕・delivery・間は維持し、86件の原音を追加API 0件で再利用する。Gemini 3.8 Flash TTS、MAKO=Aoede / MANABU=Charon / IKUMI=Orus。全員1.0倍、生成後の速度・ピッチ変更なし。

読み用カタカナと字幕の製品名を分離し、演技指示はdeliveryへ置く。字幕は全文を文単位のchunkを基本に短い相づちを隣接文とまとめ、各cue最大2行、1行40全角相当以下（句の切れ目と禁則を優先）とする。字幕chunkのcaptionとlinesを結合すると元のcaptionに一致する。

出典の正本は `script.json` の `sourceReferences` と章ごとの `sourceRefs`。そのfileはvideo-sample基準。関連実装は `infra/modules/management.bicep`、`network-bindings.bicep`、`network.bicep`、`management-audit.bicep`、`certificate.bicep`、`guest/configure-audit.sh`、公開config/params、`scripts/deployment/helpers/{image_build,portal_image,credential_image}.py`。

制作状況・完成尺・検証・再生先は担当回の `production.md` に記録する。
