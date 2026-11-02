import fs from 'node:fs/promises';
import {renderSettingsShell} from '../../settings-visual.mjs';

// This episode-only diagram keeps one geometry for the question and both answers.
// Arrows describe policy execution, not network forwarding.
const folder = new URL('./diagrams/', import.meta.url);
const esc = value => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const text = (x,y,label,size=20,color='#20323e',weight=400,anchor='middle') =>
  `<text x="${x}" y="${y}" font-size="${size}" fill="${color}" font-weight="${weight}" text-anchor="${anchor}">${esc(label)}</text>`;
const nodes = [
  {id:'base', x:232, y:181, n:'1', label:'Operation inbound', detail:'<base /> で継承'},
  {id:'common', x:462, y:181, n:'2', label:'API共通制約', detail:'契約・サイズ等を検査'},
  {id:'limits', x:692, y:181, n:'3', label:'利用枠', detail:'判定・計数の実行位置'},
  {id:'auth', x:922, y:181, n:'4', label:'下流認証準備', detail:'業務呼出とは区別'},
  {id:'accepted', x:232, y:329, n:'5', label:'accepted', detail:'API inbound 末尾'},
  {id:'document', x:577, y:329, n:'6', label:'文書の形式検査', detail:'Operation固有ガード'},
  {id:'backend', x:922, y:329, n:'7', label:'業務呼出', detail:'下流の解析処理へ'},
];
const width=205, height=80;
const arrows = [
  'M437 221H456', 'M667 221H686', 'M897 221H916',
  'M1024.5 261V291H334.5V323',
  'M437 369H571', 'M782 369H916',
].map(d=>`<path d="${d}" fill="none" stroke="#6b8a94" stroke-width="2.5" marker-end="url(#arrow)"/>`).join('');

for (const [suffix,selected] of [['question',null],['accepted','accepted'],['reject','document']]) {
  let content = `<defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="5.8" refY="3.5" orient="auto"><path d="M0 0L7 3.5L0 7Z" fill="#6b8a94"/></marker></defs>`;
  content += `<g font-family="Hiragino Sans,Yu Gothic,Meiryo,sans-serif">`;
  content += text(231,166,'文書Operationの実行順（簡略図）',16,'#52727d',600,'start');
  content += arrows;
  content += text(680,315,'継承したAPI共通処理の続き',16,'#647984');
  content += text(507,350,'Operationへ戻る',14,'#647984');
  for (const node of nodes) {
    content += `<rect x="${node.x}" y="${node.y}" width="${width}" height="${height}" rx="7" fill="#ffffff" stroke="#b6cdd4" stroke-width="1.5"/>`;
    content += `<circle cx="${node.x+16}" cy="${node.y+17}" r="11" fill="#e4f1f3"/>`;
    content += text(node.x+16,node.y+22,node.n,14,'#087f8c',650);
    content += text(node.x+width/2,node.y+37,node.label,node.id==='base'?18:21,'#087f8c',650);
    content += text(node.x+width/2,node.y+64,node.detail,16,'#526a75');
    if(selected===node.id) content += `<rect x="${node.x-3}" y="${node.y-3}" width="${width+6}" height="${height+6}" rx="8" fill="none" stroke="#de343b" stroke-width="3"/>`;
  }
  content += text(231,444,'番号順に、記録が残る位置と最終応答を照合する。',19,'#20323e',600,'start');
  content += text(231,468,'矢印はポリシーの処理順。ネットワークの通信経路を表すものではない。',16,'#647984',400,'start');
  content += '</g>';
  const frame = renderSettingsShell({
    viewId:'lesson13-document-order', areaLabel:'要求の調査・実行位置',
    title:'文書API：acceptedと形式検査の実行位置', kind:'APIM', section:'実行位置',
    menu:['到達段階','相関キー','実行位置','変更と検証'],
    subtitle:'公開定義に基づく教材図。稼働環境の取得値・実測結果ではない。',
    footer:'出典：02 外部攻撃への対策 19・22節 / 文書APIのOperationを簡略化',
  }, content);
  await fs.mkdir(folder,{recursive:true});
  await fs.writeFile(new URL(`document-order-${suffix}.svg`,folder),`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1207 511">${frame}</svg>\n`);
}
console.log('Wrote three fixed-geometry execution-order SVGs.');
