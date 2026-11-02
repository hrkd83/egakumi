# 本編の設定ビューと編集方法

2026-11-02に第1回の設定メニュー版を採用した、本編デザインの正本です。スタイルIDは`teaching-settings-v2`。新規セッションは[CONTENT-PLAN.md](../CONTENT-PLAN.md)の担当回に、この文書と[制作手順](PRODUCTION-GUIDE.md)を適用します。声・役割・通常速度・ロゴ・ホログラム・BGM・クロージングは[制作プロファイル](production-profile.json)の指定を維持します。

## 見せ方

本編は教材専用の設定メニューから説明を始めます。スレート色の見出し、淡い背景のメニュー、白い設定欄・表、青緑色の値、赤い対象枠を使います。共有の[settings-visual.mjs](settings-visual.mjs)が描画し、外側の番組ヘッダー・話者・字幕は[lesson-visual.mjs](lesson-visual.mjs)が維持します。

Azure Portalの画面を再現したとは扱いません。Microsoft Azureのブランドバー、検索欄、実装と異なる疑似操作ボタンを飾りとして足しません。実画面を使うよう追加指定された場合は、その回で確認した画面と出典を明記します。教材の説明文を実際の製品の設定項目名と混同させないよう、用途・役割などの補足は教材注釈と分かる表記にします。

画面に出す設定値は対応する資料・定義・公開設定例から取ります。稼働環境の取得値とは区別し、実IPや実効経路を推測して確定値にしません。第1回のIP、設定項目、台詞、24話題という分割数を他の回の内容へ流用しません。

## 会話に合わせる

- 本編は固定カメラを標準とします。自動ズーム・パン、台詞ごとの拡大リセット、赤枠の脈動、疑似マウスポインターを加えません。冒頭の話者へ移るホログラムカメラは別の採用済み演出です。
- 一つの質問から回答・確認までを、一つの`visual.viewId`でまとめます。同じ話題ではメニュー、行の並び、構図、縮尺を保ち、話者や字幕chunkの変更を画面切替の理由にしません。
- 赤枠はその場で説明する行・値に対応させます。確認問題の質問中に正解行を先取りせず、回答で明らかになる時点から強調します。質問と回答の間に無関係な設定画面へ戻しません。
- 設定と全体図を短い間隔で往復させる必要があるなら、設定の代表行と小さい補助図を同じ画面へ併記します。配置の枠・矢印・色の線に言及する台詞には、該当する図を実際に置きます。
- 動きが必要という具体的な追加指定がある場合は、説明に必要な区間に限って設計し、移動後もその対象について説明が続く長さを確保します。既定で動きを追加しません。

採用版では、包含関係の問答は同じ構造図、アドレス空間の問答は同じ4行の表、管理VMとACAは左右比較、UDRの説明は小図と代表ルートの併記にしました。この「会話のまとまりを保つ」判断を各回の題材へ適用します。

## 共有レイアウトの使い方

`script.json`のトップレベルへ`"visualStyle":"teaching-settings-v2"`を置きます。本編turnの`visual.layout`は通常`settings`です。書式見本は[settings-view-examples.json](settings-view-examples.json)。新しい回のひな型も、このレイアウトを使います。

設定欄の例です。同じ問答で`viewId`と`fields`を保ち、質問時は`highlightFields:[]`、回答時は`["dns"]`へ切り替えます。以下の内容はDNSの書式見本なので、担当回の到達点に合わせて置き換えます。

```json
{
  "layout": "settings",
  "viewId": "dns-vnet",
  "areaLabel": "ネットワーク設定",
  "title": "vnet-prod-egakumi",
  "kind": "仮想ネットワーク",
  "section": "DNS サーバー",
  "menu": ["概要", "サブネット", "DNS サーバー"],
  "notice": "VNetクライアントの名前解決先を確認する",
  "fields": [
    {"id":"dns", "label":"DNS サーバー", "value":"Azure 提供"}
  ],
  "highlightFields": ["dns"]
}
```

表は`fields`を次に置き換えます。列幅を省略すれば残りを均等配分し、すべて指定する場合の合計は972pxです。

```json
{
  "table": {
    "columns": [{"label":"設定項目","width":420},{"label":"値","width":552}],
    "rows": [
      {"id":"entry","cells":["公開入口","Application Gateway"]},
      {"id":"execution","cells":["APIの処理","API Management"]}
    ]
  },
  "highlightRows": ["execution"]
}
```

`table`か`fields`のどちらか一つを使います。表は2〜5列・1〜6行、設定欄は1〜6行、メニューは最大8項目です。行の`id`は一意にし、赤枠は存在するIDで指定します。セルは収まる場合だけ2行まで折り返します。長文・行数超過はエラーになるので、文字を極端に小さくせず内容を分けます。`notice`は短い教材の補足です。

描画位置は共通本文の`(65,204)`、大きさは`1207×511`です。回ごとに人物・字幕・番組ロゴを作り直しません。`settings`に`zoom`・`camera`・`motion`を渡すと、固定画面の指定違反として停止します。

## 補助図を併記する

複雑な図は担当回の`diagrams/`へ自己完結SVGを保存し、`visual:{layout:"svg",viewId:"dns-flow",svg:"diagrams/dns-flow.svg"}`で使います。`renderSettingsShell()`で、共通の設定ビューの内側へ図を置けます。本文用SVGは`viewBox="0 0 1207 511"`、設定ビュー内の図の領域は`x:214,y:147,width:972,height:328`です。

担当回の`render-diagrams.mjs`の最小例です。出力先はその回のフォルダーだけにします。

```js
import fs from 'node:fs/promises';
import {renderSettingsShell} from '../../settings-visual.mjs';

const content = `
  <rect x="214" y="147" width="972" height="328" rx="8" fill="#0a2432"/>
  <g font-family="Hiragino Sans,Yu Gothic,Meiryo,sans-serif" fill="#e0f4f2">
    <text x="244" y="195" font-size="22">設定と通信を対応付ける</text>
    <text x="244" y="255" font-size="20">担当回の図と、説明する設定の代表行をここへ配置する。</text>
  </g>`;
const frame = renderSettingsShell({
  viewId:'dns-flow', areaLabel:'ネットワーク設定', title:'名前解決の流れ',
  kind:'DNS', section:'通信元と回答', menu:['通信元と回答']
}, content);
await fs.mkdir(new URL('./diagrams/', import.meta.url), {recursive:true});
await fs.writeFile(new URL('./diagrams/dns-flow.svg', import.meta.url),
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1207 511">${frame}</svg>`);
```

これは配置の書式例で、完成した教材図ではありません。担当回の資料と会話に沿って内容を執筆します。SVGに外部参照、スクリプト、`foreignObject`、アニメーションを入れません。必要に応じて既存の`diagram`・`cards`・`quiz`・`summary`も補助に使えますが、設定の確認箇所との対応と固定画面の方針を維持します。

## 確認と引き継ぎ

標準の`build-lesson.mjs --preview true`で全場面の文字・赤枠・図を確認します。その後、40〜70秒程度の連続した質問・回答を動画で確認します。数秒ずつを集めた抜粋だけでは、画面の往復やカメラのリセットを判断できません。確認用の抜粋は質問の頭から回答・反応の終わりまでを含め、途中のカットや速度変更を加えません。

全編の書き出し後は共通ツールで連続プレビューを作れます。以下の台詞IDは、完成した担当回の質問の先頭と回答・反応の終わりに置き換えます。既存プレビューを意図的に更新するときだけ`--replace`を追加します。

```sh
python3 docs/study/video-sample/make-conversation-preview.py --episode 2 --from-turn lesson02-turn-027-mako --through-turn lesson02-turn-031-manabu
```

担当回の出力先へ`conversation-preview.mp4`・`conversation-preview.json`・`conversation-preview.html`を保存します。元の動画や音声は変えず、連続したフレーム範囲を抽出し、尺・フレーム数・全編デコードを検査します。40〜70秒を外れても質問と回答を途中で切る理由にせず、会話のまとまりを優先します。

担当回の`production.md`には、どの台詞を同じ`viewId`で保持したか、赤枠を出す時点、補助図との対応、連続プレビューの開始・終了・再生先を記録します。字幕の切替だけで本文画面が変わらないこと、質問中に答えが強調されないことも確認します。機械的な検証と視聴による判断は分けて記録します。

共通デザインはこの文書、`settings-visual.mjs`、書式見本JSONで独立して再現できます。新規worktreeで、第1回の`episodes/01/`、旧試作のジェネレーター、生成済み`output/`、localhostの起動状態を実行時の依存にしません。[旧第1回記録](LESSON-01.md)は音声・台本形式の参考として残しますが、本編画面の採用基準はこの文書です。
