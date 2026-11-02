import fs from 'node:fs/promises';
import sharp from 'sharp';
import {renderSettingsShell} from '../../settings-visual.mjs';

// Episode 15: the SQL reservations and ARM calls are independent operations.
// These are teaching diagrams based on the repository, not production captures.
const dir = new URL('./diagrams/', import.meta.url);
const C = {ink:'#20323e', muted:'#5d7580', teal:'#087f8c', blue:'#3379a7'};
const esc = s => String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const text = (x,y,s,size=18,color=C.ink,weight=400) => `<text x="${x}" y="${y}" font-size="${size}" fill="${color}" font-weight="${weight}">${esc(s)}</text>`;
const rect = (x,y,w,h,fill='#fff',stroke='#c6d8de') => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="7" fill="${fill}" stroke="${stroke}" stroke-width="1.5"/>`;
const arrow = (x,y) => `<path d="M${x} ${y}h16" fill="none" stroke="${C.teal}" stroke-width="2.4" marker-end="url(#arrow)"/>`;
const defs = `<defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0L7 3.5L0 7Z" fill="${C.teal}"/></marker></defs>`;
const xs = [234,472,710,948];
function card(index,title,lines,fill='#fff') {
  const x=xs[index];
  return rect(x,212,218,157,fill)+text(x+14,244,title,20,C.teal,650)+lines.map((line,i)=>text(x+14,280+i*28,line,18)).join('');
}
const common = {
  areaLabel:'Portal のサービスと管理', kind:'APIキー発行の教材図',
  menu:['発行入力','許可の確認','SQL予約','APIM作成','確定と監査','再試行'],
  footer:'リポジトリ定義に基づく処理図。稼働環境の取得・実測結果ではない。',
};
const issue = `${defs}<g font-family="Hiragino Sans,Yu Gothic,Meiryo,sans-serif">
${rect(214,147,972,328,'#f8fbfc')}
${text(234,181,'予約を先に保存し、APIMの結果を照合してから利用可能にする',20,C.muted,500)}
${card(0,'① 入力と許可',['本人・入力','現在所属・製品承認','有効な会社'])}
${card(1,'② SQL 予約',['creating を保存','同じ依頼・発行枠','予約を commit'],'#e9f5f3')}
${card(2,'③ APIM',['GET で既存を照会','不在なら PUT','ID・製品・状態照合'],'#edf5fb')}
${card(3,'④ SQL 確定',['active に更新','create 監査を保存','一緒に commit'],'#e9f5f3')}
${xs.slice(0,3).map(x=>arrow(x+220,291)).join('')}
${text(234,405,'②の予約は独立して確定。③と④の前には排他を取り、現在所属・承認を再確認する。',18,C.muted)}
${rect(234,423,932,35,'#f1f4fa','#c9d5e4')}
${text(248,447,'SQLの取消で、成功済みAPIM操作を取り消すことはできない。',19,C.blue,600)}
</g>`;
const recovery = `${defs}<g font-family="Hiragino Sans,Yu Gothic,Meiryo,sans-serif">
${rect(214,147,972,328,'#f8fbfc')}
${text(234,181,'APIM作成後にSQL確定が失敗した例：保存済みの予約から再開する',20,C.muted,500)}
${card(0,'① 確定で失敗',['APIM作成は成功','SQL確定は失敗','SQLの確定を取消'])}
${card(1,'② 残る状態',['SQL：creating 予約','APIM：実体あり','両方を照合する'],'#edf5fb')}
${card(2,'③ 同じ依頼で再開',['依頼ID ＋ 同じ入力','APIMを GET 再照会','一致を確認する'])}
${card(3,'④ SQLで確定',['active に更新','create 監査を保存','一緒に commit'],'#e9f5f3')}
${xs.slice(0,3).map(x=>arrow(x+220,291)).join('')}
${text(234,405,'再試行でも現在所属・承認を再確認。既存APIMを上書きせず、結果が一致するかを調べる。',18,C.muted)}
${rect(234,423,932,35,'#f1f4fa','#c9d5e4')}
${text(248,447,'creating の予約だけでは、APIMの作成成功は判断できない。',19,C.blue,600)}
</g>`;

await fs.mkdir(dir,{recursive:true});
for (const [name,title,section,content] of [
  ['issue-flow','発行を4段階で追う','APIM作成',issue],
  ['recovery-flow','APIM成功後のSQL確定失敗から再開する','再試行',recovery],
]) {
  const viewId=`lesson15-${name}`;
  const shell=renderSettingsShell({...common,viewId,title,section,subtitle:'同じ画面のまま、処理の順番と保存される状態を確認する'},content);
  const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="1207" height="511" viewBox="0 0 1207 511">${shell}</svg>\n`;
  const file=new URL(`${name}.svg`,dir);
  await fs.writeFile(file,svg);
  await sharp(Buffer.from(svg)).png().toFile(new URL(`${name}.png`,dir).pathname);
  console.log(JSON.stringify({file:file.pathname,viewId}));
}
