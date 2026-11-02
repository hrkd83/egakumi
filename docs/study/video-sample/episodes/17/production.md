# 第17回「発行後のキー管理」制作記録

更新日：2026-11-02（JST）。完成。第17回「発行後のキー管理」は16分04.46秒、75台詞、全員通常速度で書き出し、全編の映像・音声・字幕・音量検証に合格しました。聴感による発音・欠落・自然さの確認は未実施です。

## 到達点と範囲

[CONTENT-PLAN.md](../../../CONTENT-PLAN.md)の第17回に基づき、表示・停止・再開・移管・棚卸し・削除と、利用制限の設定が利用へ与える効果を説明できることを到達点とします。

元資料は[03 Portalのサービスと実装：キー管理](../../../03-subscription-management.html#key-operations)と[利用制限](../../../03-subscription-management.html#limits)です。Portalを操作の入口に、SQLを台帳・設定・監査の保存先に、APIMをAPIMサブスクリプションの管理・利用制限の適用先に置き、操作後の状態と確認先を結び付けます。第12回の制限判定そのものへ広げず、今回の中心は設定・保存・反映です。組織観測・SQL台帳・APIM実体の不一致を整える詳細は第18回へつなぎます。

説明は教材と現行リポジトリ定義に基づきます。稼働中Azureの取得値、実際のキーの操作、実機での制限反映を確認した結果ではありません。回中のチーム名と「60秒に60回・毎分30,000トークン・1日1,000,000トークン」は架空の説明例です。

## 原稿と構成

- [script.json](script.json)：75台詞。表示用caption合計5,305字、読み上げ用speech合計5,561字。`status: ready-for-generation`、省略台詞なし。
- [author.py](author.py)：第17回専用の原稿生成元。
- [generation.json](generation.json)：台本と採用キャストから作る音声生成設定。API実行結果の記録とは別です。
- 章順：今回の問い → 操作の入口と保存先 → キー表示・停止・再開 → 所有グループを移す → 棚卸しと削除 → 利用制限を保存し反映する → チーム引継ぎの具体例 → 三つの確認問題 → まとめと次回の問い。
- 約15分を目標に構成し、完成尺は16分04.46秒です。通常速度で生成した原音の実長から確定し、尺合わせの速度変更や長い無音の追加は行っていません。

## 内容の根拠と編集判断

| 操作・論点 | 今回説明する挙動と判断 | 主な実装根拠 |
| --- | --- | --- |
| キー表示 | 明示操作でAPIMの対象・製品スコープを照合し、`listSecrets`から実値を取得。SQLへは表示監査を保存し、キー実値を保存しない。画面は最大60秒で表示を閉じ、タブ非表示やページ離脱などで破棄する。コピー済みキーの失効とは区別する。 | `apps/portal/lib/api-keys/service.ts`、`apim.ts`、`apps/portal/components/api-keys/key-dialogs.tsx` |
| 停止・再開 | 同じAPIMサブスクリプションを`suspended`／`active`へ変更し、SQLの台帳状態と操作監査を対応させる。キー再発行や削除と混同しない。 | `service.ts`、`repository.ts`、`apim.ts` |
| 同時操作・途中失敗 | SQLロックで同じ対象の操作を順に進める。APIMのGETで取得したETagを変更時に照合する。画面読込時の版を比較する制限設定とは仕組みを分け、APIMとSQLを一つのSQLトランザクションで巻き戻せるとは説明しない。結果不明はAPIM再照会と台帳の照合へつなぐ。 | `service.ts`、`repository.ts`、`apim.ts` |
| 所有グループの移管 | 移管元への現在所属、両グループの適格性・承認、移管先の発行枠等を確認し、SQLの`groupId`と移管監査を保存。移管先への本人所属は必須ではない。同じAPIMサブスクリプションとキーを継続し、会社・利用制限も自動変更しない。移管は古いキーの失効を意味しない。 | `service.ts`、`repository.ts`、`apps/portal/sql/004_group_lifecycle.sql` |
| 棚卸し | APIMの実在・製品・状態を確認し、SQLへ確認日時・確認した本人・操作監査を保存。キー再発行・期限延長・停止解除ではない。 | `service.ts`、`repository.ts`、`apps/portal/sql/002_api_keys.sql` |
| 削除 | APIMで対象を確認してからSQLを`deleting`へ進め、DELETEを要求。202 Acceptedは受付中として扱い、削除完了を確認してから`deleted`と削除監査へ進む。台帳と履歴を保持する。 | `service.ts`、`apim.ts`、`repository.ts` |
| 制限の設定値 | 呼出回数と更新期間の秒数、毎分トークン、期間トークンと期間を保存する。上限を設けない項目は`null`。設定SQLは使用量・課金カウンターの保存先ではない。 | `apps/portal/lib/api-keys/limit-contracts.ts`、`apps/portal/sql/003_api_key_limits.sql` |
| 版を伴う保存 | 制限行が未作成なら版0・各項目上限なし。最初の保存では期待版0を確認して版1へ進む。版一致と所有・承認・状態を確認し、設定と変更前後・本人・版の監査を同じSQL処理で保存。古い版の要求は競合とし、最新値を確認して判断する。 | `limit-service.ts`、`limit-repository.ts`、`apps/portal/components/api-keys/limit-profile.tsx` |
| APIMへの反映 | profile連携が有効な場合、APIMが実APIMサブスクリプションIDと専用内部鍵でPortal内部APIを照会。SQL保存のたびにAPIMサブスクリプションやポリシーXMLを書き換えない。取得値はAPIMで30秒キャッシュするため、保存成功と利用時の適用を区別する。 | `gateway-profile.ts`、`limit-repository.ts`、`apps/gateway/apim/policies/subscription-profile.xml` |
| 取得不能・適用範囲 | 照会が必要な場面で取得障害が起きた場合は503で止め、未設定の上限なしへ読み替えない。既存キャッシュの再利用とは分ける。トークン制限は対応するChat／Responses操作が対象。主副キーは同じ枠を共有し、設定変更だけで使用量をリセットしない。 | `subscription-profile.xml`、`rate-limit.xml`、`token-limit.xml` |

表内で省略したPortalのTypeScriptファイルは`apps/portal/lib/api-keys/`、APIMポリシーは`apps/gateway/apim/policies/`です。台帳IDと実APIMサブスクリプションIDの対応は`apps/portal/sql/007_apim_subscription_identity.sql`も確認しています。

原稿レビューで、再開の説明につながらない相づち、画面の古さとAPIM ETagの競合検出を混同し得る問い、削除時のGETと`deleting`の順序を修正しました。

## 画面設計と補助図

本編は`teaching-settings-v2`です。教材用の設定メニュー・表・項目を使い、Azureのブランドバーや検索欄を模倣しません。質問から回答まで`viewId`・構図・縮尺を保つ設計です。本編の自動ズーム・パン、台詞や字幕chunkごとの画面リセット、赤枠のpulse、模擬ポインターを入れません。赤枠は説明対象だけに合わせ、確認問題の質問中は正答を強調しません。固定画面の構造検査、完成MP4から抽出した代表19画面、57.54秒の連続会話のブラウザー再生で確認しました。

回専用の補助図は2枚です。[management-flow.svg](diagrams/management-flow.svg)はPortalからSQL・APIMへの管理操作の分担、[limits-flow.svg](diagrams/limits-flow.svg)は設定保存とAPI利用時の内部照会・反映を示します。それぞれ007〜008、047〜048の連続した会話で同じ図を保持します。「この図」という台詞と表示する図を対応付けています。

| `viewId` | 台詞番号 | 内容 |
| --- | --- | --- |
| `lesson17-roles` | 004〜006 | Portal・SQL・APIMの分担 |
| `lesson17-management-flow` | 007〜008 | 管理の関係図 |
| `lesson17-authorize` | 009〜010 | 操作時の現在所属・承認 |
| `lesson17-reveal` | 011〜013 | キー表示と秘密の取得 |
| `lesson17-state` | 014〜016 | 停止・再開 |
| `lesson17-concurrency` | 017〜020 | SQLロック・ETag・結果不明 |
| `lesson17-transfer` | 021〜024 | 移管の条件と保存 |
| `lesson17-transfer-effect` | 025〜027 | 移管後の管理権限・継続するもの |
| `lesson17-review` | 028〜029 | 棚卸し |
| `lesson17-delete` | 030〜034 | 削除受付・完了・履歴 |
| `lesson17-limit-values` | 035〜038 | 利用制限の項目 |
| `lesson17-version` | 039〜041 | 未作成の版0と保存 |
| `lesson17-version-conflict` | 042〜044 | 二人の管理者による保存競合 |
| `lesson17-runtime-profile` | 045〜046、049〜053 | 内部照会・30秒キャッシュ・障害 |
| `lesson17-limits-flow` | 047〜048 | 保存と利用時反映の図 |
| `lesson17-example-limits` | 054〜055 | 架空の上限例・適用範囲 |
| `lesson17-case` | 056〜062 | 停止・移管・制限変更・再開の具体例 |
| `lesson17-check-transfer` | 063〜065 | 確認問題1：移管 |
| `lesson17-check-delete` | 066〜068 | 確認問題2：削除202 |
| `lesson17-check-limits` | 069〜071 | 確認問題3：制限反映 |
| `lesson17-summary` | 072〜075 | まとめ・第18回への接続 |

本編21種類の`viewId`を使用します。001〜003は冒頭ホログラムです。連続会話プレビューは台詞030〜034（削除の質問から回答・補足）です。全編の361.75〜419.291667秒、元フレーム8682〜10063を切れ目なく抽出した57.541667秒・1381フレーム。全会話で`lesson17-delete`を保持し、質問時は枠なし、回答で削除開始・受付中から完了・履歴へ赤枠を切り替えます。通常速度1.0でブラウザー再生し、最後まで到達したことを確認しました。`conversation-preview.json`と`browser-review.json`へ機械検査・観察を分けて記録しています。

字幕は句読点の節を保持して改行し、最大38字・2行に整理しました。語中で字幕chunkを切らず、読み上げ本文と字幕本文の内容は変えていません。

静的な[画面プレビューの一覧と対話全文](../../output/lesson-17-gemini/previews/index.html)とJSONは保持します。出力内の確認用PNGは完成後に整理済みのため、画像を表示する際は末尾の手順で再生成します。完成動画と57秒の連続プレビューはそのまま再生できます。

## 共通指定

[制作手順](../../PRODUCTION-GUIDE.md)、[制作プロファイル](../../production-profile.json)、[設定ビュー仕様](../../SETTINGS-VIEW.md)、[現行キャスト](../../podcast-cast-gemini.json)に従います。共有ファイルと共通素材は今回の担当範囲から変更しません。

| 項目 | 指定 |
| --- | --- |
| 音声モデル | Google Cloud `gemini-3.8-flash-tts` |
| MAKO | Moderator / Aoede |
| MANABU | Product Manager / Charon |
| IKUMI | Solution Architect / Orus |
| 速度 | 全員1.0倍。生成後の速度・ピッチ変換なし |
| 映像 | 1600×900、24fps、H.264 / AAC |
| 冒頭 | 共通ロゴ4秒と、その回の問いに合わせた3人のホログラム。話者への滑らかなカメラ移動を維持 |
| 本編 | 固定カメラ、共通フレーム・話者表示・最大2行の字幕。WebVTTを別保存 |
| 音楽 | 共通`main-bgm`を2秒クロスフェードでループし、会話中はダッキング |
| 終了 | 共通egakumiロゴ8秒。`brand-theme`の63.8〜71.8秒、先頭1秒・末尾2秒をフェード |
| 音量目標 | 実測−15.96 LUFS、True Peak −1.5 dBTP |

## 実行・検証の状態

| 工程 | 状態 |
| --- | --- |
| 企画範囲と実装根拠の照合 | 原稿作成時の照合・内容レビューを実施 |
| 台本・字幕・補助図・生成設定 | 作成済み。75台詞、caption 5,305字、speech 5,561字 |
| 静止画の最終確認 | 全75台詞＋3人の引きの76画面を出力。全場面のコンタクトシートと、修正した再開・削除・制限例の1600×900画面を目視確認。文字の収まり・図・赤枠・話者・字幕を確認 |
| 構造QA | `qa-structure.json`: passed、errors 0、warnings 0。75画像の再描画一致・21 viewIdの構図保持・138字幕chunk・3問の質問時ハイライトなし・出典・SVGを確認 |
| 音声生成 | 75/75台詞がHTTP 200で成功。原音合計924.68秒。再録・自動再送・速度変更なし。送信本文・style・声・PCM・hashの構造照合に不一致なし |
| 映像・BGM・字幕の全編合成 | 完成。1600×900・24fps、964.458333秒、23,147フレーム |
| `verify-lesson.py`による検証 | `verification.json`: passed、warningsなし。原音と生成証跡・通常速度・全字幕・カメラ・BGM・クロージングを照合 |
| 完成MP4の全編デコード・音量検査 | 全23,147フレームと全編音声のデコード合格。−15.96 LUFS、−1.5 dBTP |
| 完成画面の目視確認 | `final-frames/visual-review.json`: 完成MP4からの19枚を原寸確認し、修正を要する問題なし |
| 40〜70秒程度の連続会話プレビュー | 57.54秒を生成・全編デコード合格。ブラウザーで1.0倍速・最後まで再生確認 |
| 発音・欠落・演技指示の発声・会話の聴感確認 | 未実施 |

開始時は既存gcloud認証の更新が`Reauthentication failed`となり、API送信前（送信0件）で中断しました。ユーザーの「実行して」を受け、既存認証をブラウザーで更新する `gcloud auth login developer@example.com --force --no-activate` を実行。本人確認後に音声生成を再開し、HTTP 200の正常生成を確認しています。プロジェクト・アカウントの既定値やIAMは変更していません。トークンを成果物へ保存していません。

## 音声の検査と使用量

API返却使用量はpromptTokenCount 25,553、candidatesTokenCount 23,117、totalTokenCount 48,670です。請求額は未取得で、使用量から課金請求額を確定したとは扱いません。

`audio-audit.json`の構造照合は合格、errors 0です。波形スクリーニングでは44台詞にPCMのfull-scale到達を検出し、statusは`review_required`を保持しています。near-full-scaleは全22,192,320サンプル中865（0.003898%）、最長連続10サンプル（0.417ms）。無音・尺外れのフラグはありません。同じ基準で比較した既存第9回の比率0.003842%・最長0.458msに近い値です。詳細は`audio-review.json`に残します。原音の飽和を最終音量正規化で修復したとは説明せず、聴感上の歪み・発音・欠落・自然さの確認は未実施です。

## 完成ファイルと再生先

- [完成MP4](../../output/lesson-17-gemini/egakumi-study-17.mp4)：16分04.46秒、49,040,780 bytes（46.77 MiB）。
- [保存済み再生ページ](../../output/lesson-17-gemini/index.html)。一時的なローカル再生先：[http://127.0.0.1:63763/](http://127.0.0.1:63763/)。ページ内の「動画を保存」で保存できます。
- [連続会話プレビュー](../../output/lesson-17-gemini/conversation-preview.html)：57.54秒。
- [WebVTT字幕](../../output/lesson-17-gemini/lesson-captions.vtt)、[対話台本](../../output/lesson-17-gemini/lesson-transcript.md)。
- [タイムライン](../../output/lesson-17-gemini/lesson-timeline.json)、[書出し記録](../../output/lesson-17-gemini/lesson-export.json)。
- [検証記録](verification.json)、[構造QA](qa-structure.json)、[最終画面確認](final-frames/visual-review.json)、[連続再生確認](browser-review.json)。

MP4 SHA-256：`7fe51205c1797bbbfbf65c8ae9263a5bec63d92bca2bcb177a2e486c91f4818d`。

ファイルが成果物の正本です。localhostのURLは起動中のローカルサーバーでのみ使えます。

## 書き出し方法と再生成

一時PNGをディスクに蓄積する通常の書き出しは、同時作業中の容量不足で中断しました。担当回専用の[streaming-build.mjs](streaming-build.mjs)で、共通プログラムのhashを照合した派生処理を使い、各フレームをPNGのままFFmpegへ逐次送信して完成しました。描画・字幕・カメラ・フレーム数・音楽・エンコード指定を維持し、キャッシュは4場面に限定します。共通ファイルは変更せず、生成済み音声・ミックスをhash検証して再利用しています。途中の担当回一時PNGだけを削除し、他回のファイルと原音は保持しました。

整理後のWAV復元には、まず次のローカル復元コマンドを使います。保持した生成原音から再合成し、`audio-cache.json`に残した元のSHA-256との完全一致を検証します。追加の音声API送信は行いません。

```sh
node docs/study/video-sample/episodes/17/restore-audio.mjs
node docs/study/video-sample/episodes/17/streaming-build.mjs --reuse-audio true
python3 docs/study/video-sample/verify-lesson.py --script episodes/17/script.json --generation-config episodes/17/generation.json --cast podcast-cast-gemini.json --output lesson-17-gemini
python3 -u docs/study/video-sample/serve-episode.py --episode 17
```

追加の音声生成は不要です。詳しい描画方式は[render-mode.json](render-mode.json)を参照します。原音の微小な飽和のスクリーニング結果と、聴感未確認の範囲は上記の音声検査に記録しています。

## 再開時の原稿指紋

- `script.json` SHA-256: `f5b2cac0713fc5617f0bdf00ddf67aab830c0dd9f2a050c746d75c7cc79e0599`
- `generation.json` SHA-256: `908006261e671c8fb2bbbaf50ff068f1e646fef8e6686b961154bbb97bdd2d5c`

## 完成後のデータ整理

制作完了とデータ整理は別に記録します。`CONTENT-PLAN.md`の「完成後のデータ整理」を2026-11-02に確認し、同日17:04（JST）に第17回の整理を完了しました。

- 整理前に、完成MP4・書き出し記録・検証結果・連続再生記録のSHA-256と合格状態を照合。生成・修正・再書き出し・検証の進行中や再開待ちではなく、実行ロックがないことを確認しました。
- 別回・別版・比較用データからの参照を調べ、対象をこの回の出力内にある確認PNG76枚（32,122,867 bytes）と`lesson-narration.wav`／`lesson-mix.wav`（計277,764,156 bytes）に限定しました。
- 削除前に、78ファイルの一覧・容量・SHA-256と、保持する完成動画・原音・制作ソース・設定・共通素材等のパスとSHA-256を[cleanup-record.json](cleanup-record.json)へ保存しました。排他を取得し、削除直前の変更なしを再確認して対象ファイルだけを削除しました。
- 削除容量は309,887,023 bytes（295.53 MiB）。直後の空き容量増分は309,817,344 bytes（295.46 MiB）でした。空き容量は他の処理でも変動します。保持対象の存在とハッシュ一致を確認済みです。
- 完成MP4、連続会話MP4、共通クロージング、ポスター、再生ページ、字幕、台本、タイムライン、生成原音75本と証跡、生成設定・結果・送信要求・試行記録・`audio-cache.json`、整理前の検証記録を保持しています。`episodes/17/`の図・最終画面・QA画像・専用ツールも保持し、他回や共通素材は削除していません。

動画と連続会話プレビューは引き続き再生できます。WAVを使う音声単独再生・完全検証・`--reuse-audio true`は、WAVを復元してから利用してください。静止画ギャラリーの画像も再生成が必要です。整理前の検証記録を、整理後に再実行した結果へ書き換えてはいません。

### 音声単独再生・再編集・完全検証の前に復元する

```sh
node docs/study/video-sample/episodes/17/restore-audio.mjs
```

このローカル復元を整理前に実際に実行し、生成原音・設定・既存素材から、元のWAV2本とSHA-256が完全一致することを確認しました（[audio-restore-test.json](audio-restore-test.json)）。音声APIへ再送せず、動画・字幕・タイムラインを変更しません。復元後に上記のストリーミングビルダーや完全検証を利用できます。

### 出力内の確認画像を再作成する

```sh
node docs/study/video-sample/build-lesson.mjs --script episodes/17/script.json --output lesson-17-gemini --preview true
python3 docs/study/video-sample/episodes/17/make-preview-gallery.py
```

この再生成も追加の音声API送信は行いません。依存調査は[cleanup-dependencies.json](cleanup-dependencies.json)、実行ツールは[cleanup-completed.py](cleanup-completed.py)に残します。
