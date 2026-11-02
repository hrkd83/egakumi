# 第6回 最新計画への内容照合（設定ビュー版）

2026-11-02（JST）確認。判定：既存87台詞の本文・字幕・話し方を再利用できる。最新教材・定義との重大な矛盾、用語のずれ、到達点の欠落は見つからなかった。

## 計画の変更と本回への適用

最新CONTENT-PLAN.mdではA1・A2を必修にし、本編画面をteaching-settings-v2へ統一している。第6回のテーマ「外向き通信と出口IP」、到達点、対応範囲（01の31〜33・36〜37節）は前回と同じ。第6回から第7回の予告も現在の推奨順と整合する。本文のハブ集約出口への言及は観測差の一例に留まり、必修A1・A2の切替手順へ範囲を広げていない。

今回必要な修正は、教材用設定メニュー、固定カメラ、同じ問いと回答のviewId保持、説明対象の赤枠、連続した問答による確認という画面・編集の要件である。声・通常速度・BGM・ホログラムは引き継ぐ。

## 現行資料・実装への照合

| 論点 | 現在の根拠と判定 |
| --- | --- |
| Web依存先 | 01の31〜32節、network.bicepのApplicationルール、portal-network.bicep、credential-broker-network.bicep、gateway-egress.bicep。送信元と用途を分け、Network一致後にApplicationの許可を重ねて求めない説明は正確。公開Web許可とACR PE通信を区別している。 |
| 生成されるFQDN | 33節、configuration.py、api_publication.py、lifecycle.py。CA・選択DNS事業者API・PowerShell・公開入口名の追加、HTTPS/WSS・443・明示DNS名の抽出、既存保持と追加、旧FQDNをAPI削除のみで消さない説明は正確。既存許可の引継ぎは所有・送信元・HTTPS境界の検査に限定。 |
| 明示的な出口 | 36節、network.bicep。5サブネットのdefaultOutboundAccess:falseは属性の明示として説明し、管理VM等の暗黙出口への依存回避をUDR・許可・SNATと区別。PaaSへの機能効果を一律に一般化していない。 |
| 見える送信元 | 37節とsnat.privateRanges定義。公開宛ての標準出口、AppGW private IPを保持するNetwork/private通信、ApplicationのSNAT、PE直接接続の違いが整合。HTTPヘッダーの利用者情報とNSGの送信元も区別。 |
| 期待値と実測 | main.bicepのexpectedEgressIp出力、deploy_portal.py、application.bicepのEXPECTED_EGRESS_IP、portal-network.bicepのapi.ipify.org許可を確認。Portalアプリの実装内には当変数/確認先を用いる自動実測照合がなく、台本の未実装説明は現在も正確。対象環境・実行場所から測る必要と観測範囲の限界も説明している。 |
| 確認3問 | 空の入力配列を最終許可先と決めない、非SNAT範囲をAllowと取り違えない、期待値・確認先の設定を自動実測済みと取り違えない、という問いと回答は全て正確。 |

既存の[技術根拠](source-review.md)で確認した5節の説明と、今回読んだ最新HTML・実装は整合していた。稼働中Azureへの問い合わせ、疎通、出口IP実測は行っていない。公式仕様の再ブラウズも今回は実施せず、現行教材と実装の比較として記録する。

## 音声再利用の境界

基準はrevisions/diagram-v1/script.json。87台詞のid・話者・章・speech・caption・delivery・間・字幕本文/改行/重みが変更されていないことを確認した。音声の生成APIへの再送は必要ない。変更したvisualと台本ハッシュは、prepare --replaceとgenerateの検証済み原音再利用で結び直す。

本文等の比較用SHA-256：`94d26b6b50c1501ddc485110a63905500f88dd5cd41aaa236b93fb0dd70400e6`。

設定ビューの表・注釈、問答の保持、正解強調の時点は作者の改修後に別途確認する。上記内容レビューは完成動画の自動検証・視覚確認・聴感確認を代替しない。

## teaching-settings-v2 設定内容と問答保持の独立レビュー

設定ビュー改修後のscript.jsonを確認した。判定：合格。主要設定・注釈と教材31〜33・36〜37節との重大な矛盾なし。83本編台詞を21 viewIdへまとめ、設定メニューと必要な補助図を併記している。実IPを推測した値はなく、公開設定例・教材注釈であることをフッター等へ示している。

- 設定表は、APIMと管理VMのWeb依存先、FQDN追加工程、送信元境界を持つ既存許可の引継ぎ、defaultOutboundAccessとUDR/許可/SNATの分担、AppGW入口IPとFirewall出口IP、Network/Application/PE直接経路、期待値の配布と実測の違いを正しく区別する。
- 問答範囲は5〜9、10〜12、13〜17、18〜22、23〜25、26〜29、30〜35、36、37〜42、43〜47、48〜50、51〜56、57〜62、63〜66、67〜69、70〜73、74〜76、77〜79、80〜82、83〜85、86〜87。全範囲でviewIdは連続しており、問いから回答・補足まで同じ画面を保持する。
- settingsの同viewId内は赤枠対象以外の属性が同一。SVGの全variantも、赤枠rectを除いたXMLが同一で、設定表・補助図・縮尺のリセットはない。台詞/話者の変更による図の往復もない。zoom/camera/motion/azureMotionの指示はなく、SVGにもanimate/script/foreignObjectはない。
- 確認問題074/077/080の質問時は赤枠なし。回答075/078/081から該当する設定項目を強調し、質問の段階で正解を赤枠で先取りしない。
- Portal試用の公開Gateway経路、管理VMの明示出口、公開SNAT、private Networkの往復、ルール種別/直接経路の比較は、同一設定メニューの中へ補助図を置いている。画面内の説明対象と本文の内容は整合する。
- speech/caption/delivery/間/字幕などvisual以外の全turn属性は、archiveの旧版87台詞から変更されていない。

軽微な表記改善候補として、certificate-generationのPowerShell配布先『cdn.oneget.org / powershellgallery等』は実FQDNの例『cdn.oneget.org / *.powershellgallery.com』等へすると、値欄が省略ホスト名であるという誤読をさらに減らせる。現状は『等』を付けた要約であり、内容上の重大な誤りではない。rootへ通知済み。

本レビューはJSON/SVGの内容と構造の確認。画面の実ピクセル上の文字収まり、40〜70秒程度の連続問答を通常速度で再生した印象、完成MP4の検証・聴感確認はroot側の制作検証へ引き継ぐ。
