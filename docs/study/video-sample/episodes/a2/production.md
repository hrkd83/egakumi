# A2 ハブ接続後のDNS・通信制御と切替 — 設定メニュー版 制作記録

改訂日：2026-11-02（JST）。状態：改訂版完成。画面基準：`teaching-settings-v2`。

## 依頼と今回の差分

A1に続き、最新CONTENT-PLAN.mdとSETTINGS-VIEW.mdへ合わせてA2を再制作する。テーマ・到達点・参照範囲は初稿と同じ。図/カード中心だった本編を教材専用の設定メニューへ更新し、質問から回答まで同じviewId・構図・縮尺を保つ。赤枠は説明対象に合わせ、確認問題の質問中に正解を先取りしない。

自動zoom/pan、赤枠pulse、模擬ポインター、台詞/話者/字幕chunkごとの画面リセットを使わない。設定と必要な補助図を同じ画面へ置き、静止画に加えて40〜70秒の連続した問答で確認する。Azureのブランドバー/検索欄などを模倣しない。

## 到達点と範囲

中央DNS・Private DNS・NSG/Firewall許可・出口IPを整合させ、段階切替、新規接続での検証、切戻し、IaC/再配備を説明する。主例は中央DNSへの切替から公開宛て通信の確認・再配備まで。資料は01ネットワークの制御50・51・52・54節と対応IaC/配備定義。稼働Azureへの変更適用・実測は行わない。

A1で扱った経路を土台に、VNetとFWのDNS、8ゾーンの回答/参照/所有、実送信元と許可、Core側の切戻しと共有資源、次回配備と撤去責任を結び付ける。次回は計画どおり第9回WAFへつなぐ。

## 初稿の保存

- [初稿の台本・設定・制作記録・検証](revisions/initial-v1/)
- [初稿MP4](../../output/lesson-a2-gemini/revisions/initial-v1/egakumi-study-a2.mp4)
- [保存マニフェスト](revisions/initial-v1/archive-manifest.json)

初稿MP4 SHA-256：`58795f0b4254eae84cb8384ff9f18cf2399ab4dde6d77d0bc18c0b772cd2a12d`。成功済み原音・送信request・履歴はcanonical出力内に保持する。

## 準備と固定仕様

[事前検査](preproduction-verification.json)：passed。必要ツール/sharp、共通ファイル・素材のハッシュを確認。空き容量5.14 GiB。変更はepisodes/a2/とoutput/lesson-a2-gemini/のみ。共有ファイル・A1/他回・他のロック/サーバーは変更しない。

Gemini 3.8 Flash TTS、MAKO=Aoede / Moderator、MANABU=Charon / Product Manager、IKUMI=Orus / Solution Architect。全員通常速度1.0倍、生成後の速度・ピッチ変換なし。冒頭のホログラムと滑らかな話者カメラ、ロゴ4秒、本編BGMの2秒クロスフェード/ダッキング、ブランド曲63.8〜71.8秒の8秒closingを維持する。本編のカメラは固定。

本文・caption・字幕・delivery・ID・章・間を保ち、prepare --replace→キャッシュ結び直し→build --reuse-audio trueで再合成する。全82候補と成功済みWAV/送信requestハッシュを初稿と照合済み。prepare --replace後のgenerateはcomplete、attemptedThisRun=0。追加API送信・認証取得なし。

## 改訂した設定ビューと問答の保持

23の固定viewId（通常設定19＋図併記4、SVG15 variants）へ整理。全78本編turnをsettingsまたは同じsettings shell内の図併記へ移した。本文・字幕・delivery・ID・章・出典・順序・間は初稿と同一。元の図/枠への言及に対応する補助図を併記し、質問・回答の間で構図を保つ。

| viewId | 台詞番号 | 画面 |
| --- | --- | --- |
| a2-completion | 005〜008 | 名前・経路・許可・管理元を一組で確認 |
| a2-dns-settings | 009〜015 | VNet用DNSとFirewall自身のDNSを分けて確認 |
| a2-dns-reach | 016〜018 | 中央DNSへの経路・許可とサービス反映 |
| a2-eight-zones | 019〜022 | Private Link 6ゾーン ＋ APIM・ACA 2ゾーン |
| a2-dns-resolution | 023〜025 | 中央DNSの転送と、private IPまでの回答を確認 |
| a2-zone-ownership | 026〜028 | ゾーンの配置と、更新・削除の管理元 |
| a2-public-names | 029〜029 | API公開GatewayとPortalの公開AはAppGWへ |
| a2-observed-source | 030〜033 | NSGが観測する送信元で、必要な許可を追加 |
| a2-virtualnetwork-tag | 034〜035 | VirtualNetworkタグの範囲を再確認 |
| a2-preserve-controls | 036〜037 | 基盤・入口・private接続の制御を維持 |
| a2-firewall-rules | 038〜039 | ハブFirewallの依存先と評価順を確認 |
| a2-snat | 040〜043 | SNATの方式と、公開出口の実送信元を照合 |
| a2-egress-expectation | 044〜046 | 出口の実測と、許可元・期待値を対応付ける |
| a2-before-cutover | 047〜050 | 現状保存と事前準備を、切替前にそろえる |
| a2-cutover-stages | 051〜054 | 許可・経路を先に準備し、段階的に切り替える |
| a2-new-connection | 055〜059 | 新しい接続を開始し、同じ通信で照合 |
| a2-observability | 060〜061 | 中央側の診断・通知・担当も確定する |
| a2-rollback | 062〜064 | 保存したCore側の変更範囲へ戻す |
| a2-redeployment | 065〜068 | 既存経路の保持と、再配備・撤去の管理 |
| a2-quiz-dns | 069〜071 | 確認①：VNetだけ中央DNSなら、FWも同じ？ |
| a2-quiz-source | 072〜074 | 確認②：ハブCIDR全体の許可へ置き換える？ |
| a2-quiz-up | 075〜077 | 確認③：手動UDRは、次のupでも同じ？ |
| a2-summary | 078〜082 | 名前・許可・送信元と、切替後の管理をそろえる |

009〜015は同じDNS比較表で未指定/[]/IP配列/Proxyを説明する。023〜025は、上段にDNS問い合わせの転送経路、下段にSQL公開名→Private Link名→PE private IPの回答連鎖を分け、同じ構図へ併記した。SQL名はplaceholder例とし、実値と区別。8ゾーンは役割別4群で同じ画面に置く。

確認問題069/072/075は条件と選択肢のみ、質問中は赤枠なし。回答から正しい選択肢を強調し、設定行や図の配置・縮尺は変えない。

[技術再監査](source-review.md)：合格。SQL FQDN例を個別サービス名のplaceholder付きにし、expectedEgressIpの参照元をローカルFW公開IP（期待値）、自動実測なし/ハブIPへ自動追従なしと明示した。036/037の表示値は意味を保って短縮し、1字だけの折返しを解消。読み上げ本文は変更していない。

[設定画面の構造QA](structural-qa-v2.json)：issues/warnings 0。全82台詞の非visualデータと章・出典・順序・間が初稿と完全一致。23view内のsettings base、およびSVGからhighlight矩形を除いた全XMLが同一。

[静止画QA](visual-review-v2.md)：全73visualを13コンタクトシートで確認、重点/修正画面を1600×900で個別確認し合格。SQL FQDN例・出口期待値・1字折返しを整理した。最終表示編集後にprepare --replaceとgenerateを再実行し、82候補が旧版と一致、追加API送信0でキャッシュ結び直し完了。

共有ビルダーの --reuse-audio true で再合成完了。元の声・style・間・BGMミックスを保持する。

## 再合成・全編検証

完成尺 **16分16.375秒**、23,433フレーム、1600×900 / 24fps、H.264/AAC。82台詞・字幕118区間を初稿と同じ時刻で採用。

[全編自動検証](verification.json)：passed、warnings 0。全編のA/V decode、生成証跡・原音/設定ハッシュ、字幕/台詞対応、通常速度、冒頭カメラ、BGMと8秒closingを確認。音量 **−16.0 LUFS / −1.5 dBTP**。

初稿と今回のaudio-cache.jsonは完全一致し、PCM音源SHA-256を保持した：

- lesson-narration.wav：`45291aeadd3d86c64f1b097f67d0ff7c57b80cce24f24b03043367eb7f16a828`
- lesson-mix.wav：`2397716299515979ec41dd792efefb419962d5741fa0e7e069f77ddc1a80db4a`

今回の追加音声API送信は **0件**。既存82原音とミックスを再利用した。初稿のモデル報告使用量（prompt 28,710 / candidates 23,270 / total 51,980 tokens）は過去の使用量であり、今回の追加量ではない。

## 連続した質問・回答の確認

[共通ツール](../../make-conversation-preview.py)で009 MAKOの問いから014 MANABUの反応までを連続抽出。

- 元動画：**89.375〜154.000秒**
- 抜粋：**64.625秒 / 1,551フレーム / 24fps**
- 対象：009〜014（VNet/FW DNSの問い→二つの設定→FW未指定/[]の問いと回答→PM反応）
- 同一viewId：`a2-dns-settings`
- 内部カットなし、通常速度1.0、速度/ピッチ変更なし、元の声/BGMを連続維持
- [プレビュー検証・時刻記録](../../output/lesson-a2-gemini/conversation-preview.json)：passed、warnings 0、全編A/V decode合格
- [プレビュー再生](../../output/lesson-a2-gemini/conversation-preview.html)

静止画の検証と、連続動画の時系列/本文差分・ブラウザー通常速度再生を区別して記録する。

## 完成ファイル

- [設定メニュー版MP4](../../output/lesson-a2-gemini/egakumi-study-a2.mp4)
- [全編の再生ページ](../../output/lesson-a2-gemini/index.html)
- [字幕](../../output/lesson-a2-gemini/lesson-captions.vtt)
- [台本](../../output/lesson-a2-gemini/lesson-transcript.md)
- [完成版から抽出した78代表画面](../../output/lesson-a2-gemini/final-review-v2/manifest.json)

完成MP4 SHA-256：`efda01133acc670f24c18fdf0305d1fc4499dd3b63c5bef398aa641764a90e2c`。サイズ：51841680 bytes。

一時再生URL：<http://127.0.0.1:56699/>。連続確認用：<http://127.0.0.1:56699/conversation-preview.html>。

localhostは一時的な閲覧用で、MP4が成果物の正本。サーバー再開は `python3 -u docs/study/video-sample/episodes/a2/serve.py`（127.0.0.1の空きポートを自動選択）。再生ページの「動画を保存」から保存できる。

この環境では音声内容の聴取・認識ができないため、発音・用語・本文の欠落・演技指示の発声・会話の自然さの聴感確認は未実施。本文/送信style/原音ハッシュの一致や波形/音量検査とは区別する。

ブラウザーの連続再生確認：[実行記録](browser-continuous-replay-v2.json)。playbackRate=1、途中seek/停止なしで0.009→32.751→64.507→64.625秒まで再生。終端でended=true/paused=true/errorなし。32.751秒の実表示でも設定表の位置・行順・縮尺が保たれ、説明対象の赤枠のみ変化することを視認した。音声内容の聴感判定ではない。

連続再生後、全編ページへ移動し、16:16、字幕/動画保存リンク、全11章を確認。成果物タブとして残した。

## 最終映像・連続性QA

[映像QA](visual-review-v2.md)：合格。完成MP4の78代表画面・7コンタクトシートと最終previewを照合し一致。設定表/図/赤枠/字幕/3人表示、SQLのFQDN例、期待値参照、8秒closingを確認した。

[連続性の機械検査](continuity-qa-v2.json)と[確認後の判定](continuity-reviewed-v2.json)：全1,551フレームを検査、5つの台詞境界の強調切替は想定時刻どおり。境界外10.416667秒の小差分1件は、frame250のkey_frame=1/Iへの圧縮更新と照合した。前後同寸画像・拡大差分・0px優位の位置照合から、文字/表/赤枠の位置は変わらず量子化由来と確認。機械検出の閾値とraw結果を保持し、hash固定の確認済みcodec更新としてレビュー判定を記録した。実際のpan/zoom、字幕chunkごとのリセットはない。

連続clipの時系列8点でも同じviewId・設定行・構図・縮尺を確認。ブラウザーでの64.625秒の無seek・通常速度の完走結果も上記に保存した。クロージングの直前・開始後・終端も確認済み。未解決の技術・表示指摘なし。聴感確認は上記のとおり未実施。
