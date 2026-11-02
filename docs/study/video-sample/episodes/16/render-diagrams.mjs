import fs from 'node:fs/promises';
import {renderSettingsShell} from '../../settings-visual.mjs';

const dir=new URL('./diagrams/',import.meta.url);
await fs.mkdir(dir,{recursive:true});
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const text=(x,y,s,size=19,color='#20323e',weight=400)=>`<text x="${x}" y="${y}" font-size="${size}" fill="${color}" font-weight="${weight}">${esc(s)}</text>`;
const box=(x,y,w,h,title,lines=[])=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="7" fill="#fff" stroke="#b7cdd4"/>${text(x+16,y+30,title,21,'#087f8c',650)}${lines.map((s,i)=>text(x+16,y+59+i*25,s,17)).join('')}`;
const arrow=(x1,y1,x2,y2)=>`<path d="M${x1} ${y1}H${x2}" fill="none" stroke="#438d9a" stroke-width="3" marker-end="url(#arrow)"/>`;
const defs='<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#438d9a"/></marker></defs>';
const outline=([x,y,w,h])=>`<rect x="${x-3}" y="${y-3}" width="${w+6}" height="${h+6}" rx="8" fill="none" stroke="#de343b" stroke-width="2.8"/>`;

async function write(name,view,body,areas){
  for(const key of ['neutral',...Object.keys(areas)]){
    const svg=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1207 511"><g font-family="Hiragino Sans,Yu Gothic,Meiryo,sans-serif">${renderSettingsShell(view,defs+body+`<g id="highlights">${key==='neutral'?'':outline(areas[key])}</g>`)}</g></svg>`;
    await fs.writeFile(new URL(`${name}-${key}.svg`,dir),svg);
  }
}

await write('catalog',{
  viewId:'catalog-build-flow',areaLabel:'Portal / カタログ',title:'仕様が画面に届くまで',kind:'教材の処理図',section:'カタログ生成',
  menu:['カタログ生成','候補の表示','APIの試用'],subtitle:'元データ・生成処理・同梱ファイルを分けて確認する',footer:'リポジトリ実装に基づく教材図 / 稼働環境の取得画面ではありません'
},
  box(228,163,280,121,'元データ',['API定義・公開名','モデル能力情報'])+
  box(558,163,252,121,'ビルド前に生成',['prepare-model-catalog','同じ定義から整理'])+
  box(860,163,310,121,'Portalに同梱',['生成JSON → 画面へ','カテゴリ・提供元・仕様'])+
  arrow(509,221,548,221)+arrow(812,221,851,221)+
  box(228,315,450,119,'カタログ本体',['Portal同梱JSONとToolのアプリ内定義','SQLにカタログ本体を保存しない'])+
  box(704,315,466,119,'閲覧時のログイン確認',['本人のセッションをSQLで確認','カタログの保存先とは役割が異なる']),
  {source:[228,163,280,121],generated:[558,163,252,121],display:[860,163,310,121],storage:[228,315,450,119],login:[704,315,466,119]});

await write('execution',{
  viewId:'execution-flow',areaLabel:'Portal / API試用',title:'台帳の選択からGateway実行へ',kind:'教材の処理図',section:'APIの試用',
  menu:['カタログ生成','候補の表示','APIの試用'],subtitle:'ブラウザーから送る値と、サーバー内で解決する値を分ける',footer:'通常REST試用の代表経路 / Realtime・文書解析・MCPは方式に差があります'
},
  box(228,161,249,116,'ブラウザー',['台帳ID ＋ 入力','キー実値は指定しない'])+
  box(535,161,339,116,'Portalサーバー',['現在の権限・状態を再確認','ARM主キー → 要求に付与'])+
  box(932,161,238,116,'固定Gateway',['サービスの処理を実行','結果 → Portal → 画面'])+
  arrow(479,216,525,216)+arrow(877,216,922,216)+
  text(232,312,'サーバー内で参照を解決',17,'#647984',600)+
  box(228,326,249,100,'SQL台帳ID',['portalApiKey.id'])+
  box(535,326,339,100,'実APIMサブスクリプションID',['apimSubscriptionId'])+
  box(932,326,238,100,'秘密の主キー',['primaryKey'])+
  arrow(479,373,525,373)+arrow(877,373,922,373)+
  text(231,461,'入力・結果・キー実値を保存するPortal SQL表はありません。',17,'#52727d'),
  {request:[228,161,249,116],permission:[535,161,339,116],secret:[932,326,238,100],send:[932,161,238,116],result:[932,161,238,116],ledger:[228,326,249,100],subscription:[535,326,339,100]});
console.log('Episode 16 teaching diagrams created.');
