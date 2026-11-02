# 第8回 基盤管理者の接続と作業実行

出典は `docs/study/CONTENT-PLAN.md` の第8回。管理VMへのprivate接続、NSG、SSH認証、Managed Run Commandの分担を説明できることを到達点とする。教材の主範囲は [41〜43節](../../../01-network-control.html#maintenance-network)。

共通仕様は [制作手順](../../PRODUCTION-GUIDE.md)、[制作プロファイル](../../production-profile.json)、[現行キャスト](../../podcast-cast-gemini.json)、[Podcast形式](../../PODCAST-FORMAT.md) に従う。

## 構成と図

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

独自の図は台本内の `diagram` / `cards` / `quiz` / `summary` で定義する。共有フレームの1207×511領域へ配置し、各場面の対象を強調する。第1回の図の既定値は使わない。

## 技術上の編集判断

- 到達経路・受信NSG・OS待受と認証を、それぞれ満たす条件として説明する。現在のinfraがVPN Gateway、Bastion、VNetピアリングを構築する説明はしない。
- 管理VMはDynamic private IPv4のみ。宛先の固定IPやNSGが観測する送信元IPを推測しない。
- 公開鍵方式にEntra VMログインロールを重ねて必須としない。`loginAssignments=[]` はロールを追加しない指定で、鍵ログインの無効化や継承権限不存在の証明ではない。
- `operatorAddressPrefixes=[]` は受信NSGの指定で、Managed Run Commandを停止するフラグではない。
- Run Commandの管理操作権限、Entra SSHのVMログイン権限、VMのサービス利用権限を区別する。どちらの入口も下流通信を無条件許可しない。
- API受付や管理リソースの状態だけでビルド成功としない。`instanceView.executionState` と `exitCode`、ソースhashとimage digestの照合を具体例にする。
- 稼働中Azureへの接続・測定は行っていない。教材とリポジトリ定義・公開設定例の説明として扱う。

## 台本・音声・字幕

初稿は9章86台詞、読み上げ本文5,872字、表示本文5,626字。最終尺は通常速度の生成音声から確定する。Gemini 3.8 Flash TTS、MAKO=Aoede / MANABU=Charon / IKUMI=Orus。全員1.0倍、生成後の速度・ピッチ変更なし。

読み用カタカナと字幕の製品名を分離し、演技指示はdeliveryへ置く。字幕は全文を文単位のchunkを基本に短い相づちを隣接文とまとめ、各cue最大2行、1行40全角相当以下（句の切れ目と禁則を優先）とする。字幕chunkのcaptionとlinesを結合すると元のcaptionに一致する。

出典の正本は `script.json` の `sourceReferences` と章ごとの `sourceRefs`。そのfileはvideo-sample基準。関連実装は `infra/modules/management.bicep`、`network-bindings.bicep`、`network.bicep`、`management-audit.bicep`、`certificate.bicep`、`guest/configure-audit.sh`、公開config/params、`scripts/deployment/helpers/{image_build,portal_image,credential_image}.py`。

制作状況・完成尺・検証・再生先は担当回の `production.md` に記録する。
