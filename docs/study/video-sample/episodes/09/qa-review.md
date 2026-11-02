# 第9回 独立した台本・画面構造QA

状態：合格。台本80台詞、読み上げ5233字、本編26固定viewId。音声生成前の静的監査であり、発音・読み落とし・会話の自然さの聴感確認ではない。

## 機械検査

[qa-audit.json](qa-audit.json)：errors 0。実際の共有settings rendererで全settings turnの行数・列幅・文字折返し・ハイライトIDを検査した。字幕chunkは全文と一致し最大2行、固定字幕領域の幅に収まる見積り。出典ファイル/HTML anchorを確認。全viewIdの基底visualはハイライト指定とSVG variant名以外同一。

[qa-svg.json](qa-svg.json)：3群8variantすべて合格。data-highlight=trueの赤い矩形だけを除いた正規化XMLが、同一viewId内で完全一致。route/scope/raw-complement各群で図・文字・構図・縮尺が変わらないことを検証した。qa-audit.jsonのSVG実視警告3件に対応する追加の機械的根拠であり、実画像の読みやすさ確認を置き換えない。

## 内容と会話保持の独立レビュー

- 005〜008は同じ通常API経路の図。403だけでWAF原因を断定せず、AppGW/WAF・Firewall・APIMの到達段階を区別。009〜012の別入口比較はPortal要求をAPIM経由と説明せず、gRPCのHTTP WAF対象外も正確。
- 020〜022は異常スコアの同一表を保持し、Warning=3、Critical=5、合計5以上、サイズ・custom Blockとの別判断を区別。023〜024はMatched/Blocked/原因規則949110の表を保持。
- 025〜027は受入/検査2000 KiBの同一表。028〜030は省略既定128 KiB・明示config・実適用の区別。031〜033はfilename付きmultipart4 MiBとraw本文を同じ表で比較。
- 034〜036のURI/リスナー/全体の優先を同じ図で説明。037〜039はsharedWafPropertiesの合成と空配列の範囲を同じ表で確認。040〜041のexistingWafPolicyIdの問答も固定表を保持。
- 045〜048はPortal callback完全パス、RequestArgValues/Equals/code、原因2規則942430/942440、残すstate等を同じ表で示す。049〜051はHTTP方式、同名query、case-insensitive、Portal callback認証・入力条件/API側APIMの境界を同じ表で確認。
- 054〜056でcustom Allowの後続検査停止と原因規則へ絞る判断を説明。949110無効化不可を明示し、広いAllowを解決策として提案しない。
- 057〜062はraw形式の調整と非本文4面custom Blockを区別し、CRS全体と等価な復元とは説明しない。063〜065は文書APIのWAF1025 KiB/APIM1 MiBと意味内容検査の違いを区別。
- 確認問題068/071/074は条件とA/B選択肢だけを表示し、highlightRowsは空。正解は069/072/075から赤枠を表示する。条件・選択肢の並びを回答時も変更せず、質問から補足まで同一viewIdを保持する。
- 次回はCONTENT-PLANどおり第10回Firewall/IDPSへ接続。稼働Azureの取得や実機試験結果と語る箇所はない。

未解決の構造・内容指摘なし。静止画・完成映像の視認、40〜70秒連続問答の再生は親担当の後続確認。音声の聴感は別記する。

台本SHA-256：`acfda6f5f9039284159c26ccf3db1023f3969dfb5ca7dadd67eab68d9b97b9b7`。

字幕改善後の再監査：139 chunks、本文speech/caption維持。ASCII単語・識別子・6桁規則番号が字幕行の境界で分割されないことを追加確認。読み上げ桁読みのdelivery追記は自然言語style指示であり、本文・表示字幕へ演技指示を混ぜない。


## 最終wrap版の後半静止画QA

最終再生成されたqa-preview/sheet-08〜14、計39台詞（042〜080）の代表画面を実視し合格。旧wrap版とは区別し、[qa-preview-review.json](qa-preview-review.json)へシートSHA-256と対象台詞を記録した。対象は各台詞の代表first chunkであり、139全chunkを実視したという意味ではない。

- 新しい字幕改行は下部固定領域に収まり、設定表・補助図・人物・字幕に重なりや欠けなし。ASCII単語/パス/識別子/6桁規則番号の不自然な途中分割を認めなかった。
- Portal callbackの制約欄は「callbackはPortal側、API入力はAPIM側で制約を維持」と表内に収まり、HTTP方式・同名query・大文字小文字の行と一緒に読める。051を1600×900でも個別確認した。
- 058を1600×900で個別確認。Content-Type制約920420の設定行と赤枠が対応し、2行字幕の920420は分断されず読める。059/061も表または補助図と規則IDが対応する。
- 確認問題の質問068/071/074は赤枠なし。回答069/072/075で選択肢Bへ赤枠が出る。条件/選択肢の位置や構図は維持。
- raw補助図の左右比較と下の限定範囲の注記、文書1025 KiB/1 MiB比較、まとめと第10回予告まで読みやすさに未解決指摘なし。

これは静止画の視認結果。連続問答の動画再生、全編A/V decode、音声聴感の確認とは分ける。


## 原音の機械検証

80/80原音のPCM・hash・波形監査完了。read/PCM/hashエラー0、長無音/極端レート候補0。短いfull-scale端点ピークの30候補は警告を維持し、[qa-clipping-review.md](qa-clipping-review.md)へ精査値・素材由来の制約を記録した。聴感・認識は未実施。原音合計847.04秒、完成尺の計画値14:49.542。
