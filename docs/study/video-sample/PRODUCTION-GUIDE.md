# 番号を指定して勉強会動画を制作する

この文書は、新しいCodexセッションが`docs/study/CONTENT-PLAN.md`とコンテンツ番号だけで、完成動画まで制作するための手順です。題材・到達点・対応する資料の正本は[CONTENT-PLAN.md](../CONTENT-PLAN.md)、共通仕様の機械可読な正本は[production-profile.json](production-profile.json)、人物・音声の設定は[podcast-cast-gemini.json](podcast-cast-gemini.json)です。演出と会話の方針は[PODCAST-FORMAT.md](PODCAST-FORMAT.md)、本編の画面設計は[SETTINGS-VIEW.md](SETTINGS-VIEW.md)を読みます。画面基準は`teaching-settings-v2`です。制作プロファイルのIDは互換性のため`study-series-v1`を維持し、画面基準と区別します。

## セッションへ渡す指示

例えば、次だけを渡します。

```text
docs/study/CONTENT-PLAN.md の第2回を制作してください。
```

担当セッションは本手順を読み、題材の確認、台本・図の執筆、音声生成、映像合成、検証、完成ファイルの提示まで進めます。ユーザーが役割・音声モデル・素材を再指定する必要はありません。別の番号は別セッションで並行して制作できます。

この制作一式を含むコミットから作成した別worktreeで、同じ手順を使えます。資料・ツール・共通素材・依存関係のロックファイルはGitで引き継ぎ、生成済みの`output/`、`node_modules/`、認証情報は引き継ぎません。worktreeごとに担当番号を分け、相手の作業場所にあるファイルを実行時の依存にしません。

## worktreeの初回準備

Node.js、Python 3、`ffmpeg`、`ffprobe`、Google Cloud CLIを使います。同じMacの別worktreeでは既存のgcloudユーザー認証を利用できるため、新しい認証キーやCloudプロジェクトは作りません。別の端末では、その端末で必要なユーザー認証を行います。

制作ツールの`sharp`は、Portalから独立した`video-sample/package.json`と`package-lock.json`で固定します。新規worktreeで未導入の場合は、リポジトリルートから一度実行します。

```sh
npm ci --prefix docs/study/video-sample --include=optional --no-audit --no-fund
python3 docs/study/video-sample/check-production.py --episode 2
```

セッションは依存関係が不足していればこの準備を実行してから制作を続けます。同じworktreeで他の生成・合成が動いている間に、`npm ci`で依存関係を入れ替えません。日本語フォントは第1回と同じmacOS環境を基準にし、異なるOS・フォントでは文字の収まりを目で確認します。Node.jsの版は制作ツールの`package.json`の`engines`も参照します。

## 共通に固定するもの

| 項目 | 採用仕様 |
| --- | --- |
| MAKO | Moderator / Aoede / 読みはマコ |
| MANABU | Product Manager / Charon / 読みはマナブ |
| IKUMI | Solution Architect / Orus / 読みはイクミ |
| 音声 | Google Cloud `gemini-3.8-flash-tts`、全員通常速度、生成後の速度・ピッチ変換なし |
| 接続 | `egakumi-example` / `global` / Agent Platform API、既存`developer@example.com`のgcloudユーザー認証 |
| 話し方 | 共通・人物別・場面別の自然言語指示を`speechMetadata.style`へ渡す。本文と分ける |
| 映像 | 1600×900、24fps、H.264 / AAC、黒に近い未来的な空間 |
| 冒頭 | 共通ロゴ4秒 → 3人の顔・名前・役割の引き → その回に合う新しい掛け合い |
| 冒頭のカメラ | 話者が中央へ来る約0.85秒の滑らかな移動。人物の位置は入れ替えない |
| 本編 | `teaching-settings-v2`の教材用設定メニューと必要な補助図。共通フレーム、話者表示、最大2行の字幕。WebVTTも保存 |
| 本編のカメラ | 固定。質問から回答まで同じ`viewId`・構図・縮尺を保持し、自動ズーム・パン・台詞ごとのリセットを使わない |
| 音楽 | `assets/music/brand-theme.mp3`と`main-bgm.mp3`。本編は2秒クロスフェードと会話中のダッキング |
| 終了 | まとめ・次回の問い → 共通ロゴ8秒。ブランド曲63.8〜71.8秒、先頭1秒と末尾2秒をフェード |
| 音量 | 最終ミックス約−16 LUFS、True Peak約−1.5 dBTP |

共通素材は`assets/`から使います。`output/`にある過去の試作や、別リポジトリ・別サービスから取り直す素材を制作依存にしません。既存の顔・身体・ロゴを新たに生成しません。旧DragonHDのBree/Vance/Tylerや`enhancePronunciation`は現行仕様に適用しません。

表示は**MAKO / MANABU / IKUMI**、読み上げは**マコ／マナブ／イクミ**です。技術用語の読みは本文またはstyleに記録します。相づち・フィラーは実際に話す本文へ、演技はstyleへ置きます。Azureの角括弧タグを直接読ませず、必要な笑い・息継ぎの意図を自然言語へ移します。毎行へ同じフィラーや笑いを足しません。

## 並列制作で編集できる範囲

番号は`2`→`02`、`A1`→`a1`、`B1`→`b1`に正規化します。1タスクに1番号を割り当てます。

A1・A2は第8回の後に制作する必修のハブ＆スポーク対応、B1〜B3は第18回の後に制作するPortalのデータ設計・実装編、A3・A4は応用編です。番号は制作IDなので、受講順に並べ替えて他の回の番号を変更しません。B1の入力は`episodes/b1/`、出力は`output/lesson-b1-gemini/`です。

B1〜B3の題材と参照コードはコンテンツ計画を読みます。B1はER図、B2はフォルダーツリーとファイル、B3は短い実コードと呼び出し図を主画面にし、専用SVGなどで説明対象を示します。共通外枠・字幕・登場人物・音声・BGM・冒頭と終了、問いから回答までの固定した構図は維持します。アプリやSQLを変更せず、参照したコミット・パス・関数名を制作記録へ残します。

```text
docs/study/video-sample/
  episodes/02/
    brief.md             # 計画表から抽出した到達点と参照範囲
    script.json          # 完成した対話・字幕・図の指示
    generation.json      # 台本から作る音声生成設定
    diagrams/            # 必要な場合の回専用SVG
    production.md        # 出典、編集判断、完成尺、検証、再生先
  output/lesson-02-gemini/
    generation.json      # APIの実行結果。上の設定ファイルとは別
    requests-sent/       # 実際に送った本文と話し方
    *-raw.wav            # 生成原音と対応する*-raw.json
    egakumi-study-02.mp4
    lesson-captions.vtt
    lesson-transcript.md
    lesson-timeline.json
    lesson-export.json
    index.html
```

担当回の`episodes/<key>/`と`output/lesson-<key>-gemini/`のみ変更します。計画表、共有README、キャスト、プロファイル、音源、ロゴ、肖像、共有プログラムは各回のタスクから変更しません。共通修正が必要なら、担当回の`production.md`へ具体的に記録して調整します。完成状況も担当回の記録に書き、共有一覧の集約は別に行います。

同じ番号を2セッションへ同時に割り当てません。生成と合成は回ごとのロックで重複実行を拒否します。使用中のロックを削除して続行しません。正常終了・異常終了の記録とプロセス状態を確認してから対処します。

## 1. 準備と題材の確認

以下はすべてリポジトリルートから実行します。例は第2回です。

```sh
python3 docs/study/video-sample/check-production.py --episode 2
python3 docs/study/video-sample/new-episode.py --episode 2
```

最初のコマンドは共通設定・素材のハッシュと必要ツールを確認し、認証情報を読まずAPIも呼びません。2番目は計画表から担当回のbriefと**未完成の台本ひな型**を作ります。既存フォルダーは上書きしません。途中から再開するときは、既存の原稿と生成記録を読みます。

確認結果には空き容量も表示します。台本・図・音声の制作は回別に進められますが、動画書き出しには各回の一時PNG・WAVが必要です。同じマシンでは容量と負荷を見て書き出しの同時数を抑え、他の回の一時ファイルを削除して空きを作りません。

対応HTMLの指定範囲と関係するリポジトリ定義を読み、到達点、代表例、誤解しやすい点を整理します。章の全文読み上げや全設定値の列挙へ広げません。関連回との境界は計画表で確認し、他の未完成動画に制作を依存させません。稼働環境を調査していない内容を、稼働Azureの実測値として説明しません。

## 2. 台本・字幕・図を執筆する

ひな型のTODOをすべて置き換えます。全体を15分前後、読み上げ本文5,000〜6,000字・80〜100台詞程度から見積もり、生成後の実長で確定します。これはノルマではありません。原理と代表例を優先し、尺合わせの早送りや無音の追加を行いません。

- 冒頭30〜40秒程度は、その回ならではの素朴な問い、目的、仕組みの入口を3人で交わします。第1回の台詞や共通の自己紹介文を丸ごと使い回しません。
- 本編は目的 → 設定メニューと補助図で仕組み → 一つの具体例 → 誤解の確認 → まとめ、を題材に合わせて構成します。章の数や台詞数を第1回へ固定しません。
- MAKOが視聴者の疑問を確かめ、MANABUが目的・価値・判断を、IKUMIが設計・実装・確認方法を答えます。一人の長いナレーションを3人へ機械的に分割しません。
- 2〜3問程度の確認問題と、まとめ・次回の問いを含めます。最後の回や応用編では架空の次回を予告しません。
- 各章・重要な説明と元資料の節を対応付け、`sourceReferences`と担当回の`production.md`に出典を残します。

`script.json`は第1回のスキーマを使います。必須の基本情報は`episodeKey`、`episodeNumber`、`episodeId`、`topic`、`speed:1`、`chapters`、`turns`、`sourceReferences`です。各turnには一意な`id`、`speaker`、`chapterId`、読み上げ用`speech`、表示用`caption`、`subtitleLines`、`visual`を置きます。`delivery`には場面の意図を書けます。長い字幕は`subtitleChunks:[{caption,lines,weight}]`で最大2行ずつに分けます。原稿全体の意味と字幕を一致させ、読み上げのためのカタカナと表示用の製品名を分けます。

`sourceReferences[].file`と章の`sourceRefs[].file`は`video-sample/`基準です。例は`../01-network-control.html`、節は`anchor`で指定します。`firstCut.omitTurnIds`は省略する台詞IDだけを指定し、通常は空です。

### 設定ビューと画面を保つ単位

本編は[SETTINGS-VIEW.md](SETTINGS-VIEW.md)の共通設定ビューを使います。設定値、一覧、関連付け、状態を教材用のメニューとして表示し、配置や通信経路は必要な補助図で示します。Azure Portalのロゴ・ブランドバー・検索・IAMなどを部分的に模倣しません。実環境を取得した画面ではないことと、公開設定例に基づく値であることを制作記録に残します。

画面を保つ単位は一つの問いと回答です。台詞を書く段階で同じ話題のturnへ共通の`visual.viewId`を付け、同じ構図・表の並び・縮尺を維持します。話者交代や`subtitleChunks`の切替を、新しい画面やズームのきっかけにしません。対話中は赤枠の対象を変える程度にし、確認問題の質問中に答えの行を先取りしません。赤枠の繰り返すpulseや模擬ポインターは追加しません。

同じ話題で設定表と全体図を何度も往復させず、必要なら図と代表設定を同じ画面へ併記します。VMとACAなどの比較は左右に並べます。「この図」「枠の内側」「戻り道」などの台詞には、その対象を画面に置きます。第1回固有のネットワーク図・IP・設問を、題名だけ変えて流用しません。

本編は自動ズーム・パンを使わない固定カメラです。将来ユーザーが具体的な動きを指定した場面は、連続した会話の中で動きと保持時間を設計し、説明の対象・開始時点・次の画面への切替が自然につながるか確認します。冒頭ホログラムの話者カメラは従来どおり維持します。

補助図には次の共通レイアウトも使えます。

```json
{
  "layout": "diagram",
  "heading": "名前から接続先を調べる",
  "nodes": [
    {"id":"client","label":"送信元","detail":"今回の具体例","x":40,"y":170,"width":250,"height":100},
    {"id":"dns","label":"DNS","detail":"問い合わせ先","x":440,"y":170,"width":250,"height":100},
    {"id":"answer","label":"回答","detail":"資料に沿って執筆","x":840,"y":170,"width":250,"height":100}
  ],
  "edges": [{"from":"client","to":"dns"},{"from":"dns","to":"answer","label":"応答"}]
}
```

座標は本文図の領域`1207×511`内です。共有フレームが領域を画面の`(65,204)`へ配置するので、タイトル、右の話者、下の字幕を回ごとに作り直しません。`edges`は`dashed`、`color`、折れ線`points:[[x,y],...]`も指定できます。

短い論点は`visual:{layout:"cards",cards:[{title:"論点",body:["短い説明"]}]}`、確認問題とまとめは`layout:"quiz"`または`"summary"`と、その回の`bullets`を明記します。複雑な図は`layout:"svg",svg:"diagrams/example.svg"`を使えます。SVGは台本フォルダー基準、`viewBox="0 0 1207 511"`とし、外部参照、スクリプト、`foreignObject`を含めません。いずれも文字の収まりと意味を目で確認します。

```sh
node docs/study/video-sample/build-lesson.mjs --script episodes/02/script.json --output lesson-02-gemini --preview true
```

音声生成前に回専用の静止画プレビューを確認し、設定項目・図・字幕・役割・表記・台本を見直します。質問から回答まで画面が保たれること、赤枠が台詞の対象と一致すること、図や枠への言及に対応するものがあることを確認します。執筆と確認が終わったら`status`を`ready-for-generation`へ変更します。未完成の`draft`やTODOを含む台本は生成へ進めません。

## 3. 音声を生成する

```sh
python3 docs/study/video-sample/prepare-gemini-lesson.py --script episodes/02/script.json --config episodes/02/generation.json --output lesson-02-gemini
python3 docs/study/video-sample/generate-gemini-lesson.py --config episodes/02/generation.json --workers 1 --dry-run
python3 docs/study/video-sample/generate-gemini-lesson.py --config episodes/02/generation.json --workers 1
```

prepareは台本と共通キャストから生成設定を作るローカル処理、dry-runは入力・出力先などの検査です。最後のコマンドだけがGoogle Cloudへ送信します。並列セッションでは各タスク`workers=1`にして、同じGoogleプロジェクトのクォータを共有することを前提に進めます。

既存のgcloudユーザー認証を使います。プロジェクトやアカウントの既定値変更、新しいプロジェクト、APIキー、サービスアカウント、IAM設定の作成を各回で繰り返しません。認証が失効している場合は必要な本人確認だけをユーザーへ引き継ぎ、複数セッションで同時にログイン・失効処理をしません。トークンはメモリーで扱い、台本・設定・ログ・成果物へ保存しません。

成功済みの原音はリクエスト・音声ハッシュで再利用します。429などのエラーでは新規送信を止め、原因と記録を調べます。結果不明のリクエストは自動再送しません。既知の非200失敗だけを見直して再送する場合は、既存CLIの`--retry-failed-http <turn-id>`を指定します。

原稿を直して意図的に再録するときは、台詞に新しいID（例：`lesson02-turn-015r2-ikumi`）を付け、生成設定を`prepare ... --replace`で更新します。成功済みの原音や履歴を消して再送しません。文脈・話し方の指示が変わる後続台詞も対象を確認し、必要なIDを更新します。

## 4. 合成・検証・納品する

```sh
node docs/study/video-sample/build-lesson.mjs --script episodes/02/script.json --generation-config episodes/02/generation.json --cast podcast-cast-gemini.json --output lesson-02-gemini
python3 docs/study/video-sample/verify-lesson.py --script episodes/02/script.json --generation-config episodes/02/generation.json --cast podcast-cast-gemini.json --output lesson-02-gemini
python3 -u docs/study/video-sample/serve-episode.py --episode 2
```

共有ビルダーが音声実長から場面・字幕・カメラを確定し、ロゴ、BGM、クロージングまで合成します。`--write-script`を使わず、元台本をビルダーから書き換えません。

書き出し後は40〜70秒程度の連続した質問・回答を確認用プレビューにします。元の時刻範囲から、問いの始まりから回答・補足の終わりまでを途中で飛ばさず取り出し、通常速度で再生します。異なる場面を数秒ずつ寄せ集めた動画だけでは、会話と画面保持の自然さを確認したことにしません。説明中の画面往復、台詞・話者・字幕chunkでのリセット、答えを先取りする赤枠、図への言及と実際の図のずれを見直します。

生成後に図や表示字幕だけを修正した場合も、台本全体のハッシュが変わります。まず`prepare ... --replace`で設定を更新し、同じ`generate ...`を再実行して検証済みキャッシュと新しい設定を結び直します。実際の読み上げ本文・声・styleが同じなら、認証取得やAPI送信をせず原音を再利用し、表示字幕の注記だけを更新します。その後、音源の計画・間・ハッシュが変わっていなければビルダーに`--reuse-audio true`を追加できます。本文やstyleも変えた場合は前節の新しい台詞IDによる再録を使います。

検証の標準出力を担当回の`verification.json`へ保存します。失敗したときの空ファイルや途中結果を「合格」と記録しません。検証では本文・生成証跡、音源と設定のハッシュ、通常速度、字幕、カメラ、BGM、共通クロージング、全編の映像・音声デコード、長さ、音量を確認します。プレビューと完成MP4から、3人の顔・名前・役割、話者の中央寄せ、代表図、字幕、ロゴ終了を目で確認します。名前・技術用語・相づち・冒頭と終盤を試聴し、読み上げの欠落や演技指示の発声も確認します。自動検証だけで発音や会話の自然さを確認済みとは書きません。

利用できるツールで音声の内容を聴取・認識できない場合は、機械的な検証と聴感の確認を分け、聴感の確認は未実施と記録して、試聴可能な完成動画を提示します。

再生サーバーは空きポートを自動で選び、ローカルだけへ公開します。既存セッションのサーバーを止めたり、固定8780などを奪ったりしません。出力ファイルが成果物の正本で、localhostのURLは一時的な閲覧用です。

`episodes/02/production.md`に、到達点、参照範囲、台本と図の対応、完成尺、音声モデル・声・速度、生成使用量、検証結果、MP4のSHA-256、ファイルへのリンク、一時再生URLを記録します。約15分から大きく外れるときは内容と対話を調整し、再録の範囲を管理します。共有計画表や共通READMEは更新しません。

画面基準`teaching-settings-v2`への適合、質問と回答を保つ`viewId`の単位、連続プレビューの元時刻範囲・尺・確認結果も制作記録へ残します。本編の固定カメラと冒頭のホログラムカメラを混同しません。

連続プレビューは[共通ツール](make-conversation-preview.py)で作れます。担当回の質問の先頭IDと回答・反応の末尾IDを指定する方法は[設定ビューの確認手順](SETTINGS-VIEW.md#確認と引き継ぎ)を参照します。生成するMP4・記録・HTMLは担当回の出力先に保存し、元動画を変更しません。

最終回答では、完成動画、尺、全員通常速度であること、再生・保存方法、検証結果の要点を伝えます。制作依頼を台本の提案や計画だけで終了せず、必要な認証・外部障害など具体的な未解決事項がある場合だけ、その状態を明記します。

## 進行中の回を新しい画面基準へ合わせる

すでに別タスクで制作中・完成済みの回も、まず担当回の`production.md`に、新しい画面基準との違いを記録します。担当範囲は既存の`episodes/<key>/`と出力先のままにし、他の回の原稿・進捗や共通ファイルを編集しません。

設定ビューと補助図を整理し、同じ問いのturnへ`visual.viewId`をそろえます。説明対象・図への言及・確認問題の回答時点を見直し、静止画と連続した会話の両方を確認します。声や台本を作り直すことを画面改修の前提にしません。

読み上げ本文・声・styleが同じ場合は、前節の`prepare ... --replace` → `generate ...`による検証済みキャッシュの結び直し → `build ... --reuse-audio true`を使い、追加API送信をせずに画面を更新できます。本文やstyleも変える場合は、その変更を明示し、新しい台詞IDで必要な部分だけを再録します。

## 参考にする完成例

画面・編集の正本は、第1回の採用内容を独立させた[設定ビュー仕様](SETTINGS-VIEW.md)と[書式見本](settings-view-examples.json)です。固定カメラ、質問から回答までの画面保持、設定と補助図の併記、赤枠の対象を確認します。新規worktreeでも、この共有仕様と`layout:"settings"`から同じ外観を作れます。

第1回の[完成記録](LESSON-01.md)、[元台本](lesson-01-script.json)、[生成設定](lesson-01-gemini-generation.json)で採用内容と音声・台本構造を確認できます。旧図中心版やAzure模倣版を最新の画面指定として扱いません。第1回だけのネットワーク図・確認問題・次回予告・87台詞という編集範囲は、他の回の内容へ引き継ぎません。

新しい回は`assets/`と共通ツールから制作し、第1回の`output/`、完成MP4、ブラウザーの起動状態、回専用ビルダーや過去の画面生成スクリプトを実行時の依存にしません。別worktreeに第1回の生成済み音声や動画をコピーする必要はありません。
