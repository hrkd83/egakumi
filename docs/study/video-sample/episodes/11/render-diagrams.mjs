// Episode 11 only: fixed teaching diagrams in the shared settings shell.
import fs from 'node:fs/promises';
import {renderSettingsShell} from '../../settings-visual.mjs';

const esc = value => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const font = 'Hiragino Sans,Yu Gothic,Meiryo,sans-serif';
const t = (x,y,label,size=18,color='#20323e',weight=400,anchor='start') => `<text x="${x}" y="${y}" fill="${color}" font-family="${font}" font-size="${size}" font-weight="${weight}" text-anchor="${anchor}">${esc(label)}</text>`;
const r = (x,y,w,h,fill='#fff',stroke='#c8d7df',sw=1) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="7" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
const line = d => `<path d="${d}" fill="none" stroke="#16818d" stroke-width="2.7" marker-end="url(#arrow)"/>`;
const defs = '<defs><marker id="arrow" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="#16818d"/></marker></defs>';
const shell = (visual,content) => `<svg xmlns="http://www.w3.org/2000/svg" width="1207" height="511" viewBox="0 0 1207 511">${renderSettingsShell(visual,defs+content)}</svg>`;
const output = new URL('./diagrams/',import.meta.url);
await fs.mkdir(output,{recursive:true});
const steps = [
  {x:236,y:165,title:'標準キー検証',detail:'状態・APIの利用範囲を確認'},
  {x:551,y:165,title:'共通入口',detail:'ヘッダー・ID・入口証明'},
  {x:866,y:165,title:'API共通入力検査',detail:'Operation・model・profile'},
  {x:866,y:310,title:'利用枠・下流認証準備',detail:'計数・MI / CM等・accepted'},
  {x:551,y:310,title:'Operation固有検査',detail:'専用ガード・入力変換'},
  {x:236,y:310,title:'業務呼出し',detail:'通常RESTはbackendで転送'}
];
const orderVisual = {
  viewId:'lesson11-policy-order',areaLabel:'APIMポリシー',title:'通常RESTの実行順を追う',
  kind:'教材用の実行順',section:'実行順',menu:['入口認証','入力契約','実行順','専用ガード'],
  subtitle:'登録されたOperationから、継承先を実行して戻る',
  footer:'教材用の実行順／稼働環境の取得画面ではない'
};
for(let highlight=0;highlight<=6;highlight++){
  let content = r(214,147,972,328,'#f1f5f7','#d5e0e5');
  content += line('M506 209H541')+line('M821 209H856')+line('M1001 253V300')+line('M866 354H831')+line('M551 354H516');
  steps.forEach((s,i)=>{
    content += r(s.x,s.y,270,88,highlight===i+1?'#eaf4ff':'#fff');
    content += `<circle cx="${s.x+22}" cy="${s.y+25}" r="12" fill="#0a7c88"/>`+t(s.x+22,s.y+30,i+1,15,'#fff',650,'middle');
    content += t(s.x+43,s.y+32,s.title,18,'#126b77',650)+t(s.x+15,s.y+66,s.detail,16);
    if(highlight===i+1)content += r(s.x-3,s.y-3,276,94,'none','#de343b',3);
  });
  content += t(237,433,'専用検査で拒否されても、計数・準備が済む場合がある',20,'#993a37',600);
  content += t(237,460,'SSML宣言ガードなど、Operationの継承前に動く例外もある',17,'#526e7a');
  await fs.writeFile(new URL(highlight?`policy-order-${highlight}.svg`:'policy-order.svg',output),shell(orderVisual,content));
}

const inheritanceVisual = {
  viewId:'lesson11-inheritance',areaLabel:'APIMポリシー',title:'<base /> の位置で親の処理を取り込み、戻る',
  kind:'inboundの継承',section:'継承位置',menu:['入口認証','入力契約','継承位置','専用ガード'],
  subtitle:'左から右へ継承先に入り、右から左へ戻って続きを実行する',
  footer:'教材用の継承関係／標準キー検証はカスタムポリシーより先'
};
let content=r(214,147,972,328,'#f1f5f7','#d5e0e5');
for(const [x,title] of [[234,'Operation inbound'],[555,'API inbound'],[876,'Service inbound']]){
  content+=r(x,164,280,254)+r(x,164,280,37,'#e0edf0','#c8d7df')+t(x+15,190,title,20,'#126b77',650);
}
// Calls are drawn on dedicated horizontal lanes between the three scopes.
content+=t(250,231,'① 専用前処理（SSML等）',17)+t(250,272,'② <base />',19,'#087f8c',650)+t(250,385,'⑥ Operation固有処理',17);
content+=t(571,272,'③ <base />',19,'#087f8c',650)+t(571,348,'⑤ APIの続き',18,'#087f8c',650)+t(571,376,'契約・利用枠・認証準備',16)+t(571,399,'accepted trace',16);
content+=t(892,272,'④ 共通入口',19,'#087f8c',650)+t(892,304,'認証完了マーカーを設定',16);
content+=line('M485 265H545')+line('M806 265H866');
content+=t(518,247,'継承',13,'#526e7a','500','middle')+t(839,247,'継承',13,'#526e7a','500','middle');
content+=line('M891 329H846V337H818')+line('M570 366H534V378H494');
content+=t(850,358,'戻る',13,'#526e7a')+t(520,399,'戻る',13,'#526e7a');
content+=t(237,446,'<base /> より前の処理 → 親スコープ → <base /> より後の処理',20,'#20323e',600);
content+=t(237,468,'継承先は完了マーカーでも確認する。ない場合は503で停止する。',16,'#526e7a');
await fs.writeFile(new URL('inheritance.svg',output),shell(inheritanceVisual,content));
