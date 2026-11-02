import fs from 'node:fs/promises';
import {renderSettingsShell} from '../../settings-visual.mjs';
const here=new URL('./',import.meta.url),data=JSON.parse(await fs.readFile(new URL('settings-diagrams.json',here),'utf8'));
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const txt=(x,y,s,size=17,fill='#20323e',weight=400)=>`<text x="${x}" y="${y}" font-size="${size}" fill="${fill}" font-weight="${weight}">${esc(s)}</text>`;
const rect=(x,y,w,h,fill='#fff',stroke='#d5e0e5',sw=1)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="5" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
const arrow=(x1,y1,x2,y2)=>`<path d="M${x1} ${y1}L${x2} ${y2}" fill="none" stroke="#087f8c" stroke-width="2.2" marker-end="url(#arrow07)"/>`;
function graph(v){
 const g=v.graph,focus=new Set(v.graphFocus??[]);let body='';
 const node=(id,x,y,w,label,detail)=>rect(x,y,w,65,'#f2f8f8',focus.has(id)?'#de343b':'#7ba3aa',focus.has(id)?2.8:1)+txt(x+12,y+27,label,21,'#174b55',600)+txt(x+12,y+50,detail,14,'#52727d');
 if(['u1','roundtrip'].includes(g)){
  body+=node('agw',235,156,230,'AppGW','バックエンドへの新規接続')+node('fw',589,156,230,'Firewall','Networkルール')+node('apim',943,156,230,'APIM','受信と応答');
  body+=arrow(465,179,589,179)+arrow(819,179,943,179)+txt(488,169,'TCP 80',14)+txt(842,169,'TCP 80',14);
  if(g==='roundtrip')body+=arrow(589,208,465,208)+arrow(943,208,819,208)+txt(490,242,'復路も同じFW',14)+txt(848,242,'往復を照合',14);
  else body+=txt(235,247,'U1：利用者 → AppGW とは別のネットワーク接続',17,'#52727d');
 }else if(g==='dns'){
  body+=node('source',235,156,230,'送信元 AppGW','失敗した場所のDNS設定')+node('dns',589,156,230,'参照するDNS','転送・ゾーンのリンク')+node('answer',943,156,230,'APIM内部IP','今回の宛先として照合');
  body+=arrow(465,190,589,190)+arrow(819,190,943,190)+txt(235,247,'端末でのDNS成功を、AppGWでの確認結果に置き換えない',17,'#52727d');
 }else if(g==='direct'){
  body+=node('apim',235,156,230,'APIM','送信側NSG')+node('pe',589,156,230,'Foundry PE','受信側NSG')+node('fw',943,156,230,'Firewall','D2では経由しない');
  body+=arrow(465,190,589,190)+txt(480,170,'TCP 443',14)+txt(235,247,'標準D2：PEへの具体的な直接経路。FWログ不在だけで遮断とは判断しない',17,'#52727d');
 }else if(g==='stores'){
  body+=node('fw',235,156,230,'Firewall診断','Network / Application')+node('law',589,156,230,'LAW','Dedicated形式・検索')+node('storage',943,156,230,'監査Storage','監査収集を有効にした構成');
  body+=arrow(465,180,589,180)+`<path d="M350 221V240H1058V221" fill="none" stroke="#087f8c" stroke-width="2.2" marker-end="url(#arrow07)"/>`+txt(525,258,'監査収集の条件と、到着状況も確認',16,'#52727d');
 }else if(g==='notify'){
  body+=node('signal',235,156,230,'数値・管理操作','メトリック / Activity')+node('rule',589,156,230,'アラート条件','対象・評価窓・閾値')+node('mail',943,156,230,'Action Group','登録された運用者へメール');
  body+=arrow(465,190,589,190)+arrow(819,190,943,190)+txt(235,247,'ログの保存と、条件に一致した通知は別の設定',17,'#52727d');
 }else throw new Error('Unknown graph: '+g);
 return body;
}
await fs.mkdir(new URL('diagrams/settings-v2/',here),{recursive:true});
for(const [name,v]of Object.entries(data)){
 const rows=v.table.rows,top=274,h=Math.floor(197/rows.length),selected=new Set(v.highlightRows??[]);
 let content=`<defs><marker id="arrow07" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#087f8c"/></marker></defs><g font-family="Hiragino Sans,Yu Gothic,Meiryo,sans-serif">`+graph(v);
 for(const [i,row]of rows.entries()){
  const y=top+i*h,on=selected.has(row.id);content+=rect(214,y,972,h,on?'#eef6f8':'#fff','#d5e0e5');
  content+=txt(228,y+h/2+6,row.cells[0],16,'#586875',500)+txt(438,y+h/2+6,row.cells[1],17,'#087f8c');
  if(on)content+=rect(216,y+2,968,h-4,'none','#de343b',2.8);
 }
 content+='</g>';
 await fs.writeFile(new URL('diagrams/settings-v2/'+name,here),`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1207 511">${renderSettingsShell(v,content)}</svg>\n`);
}
console.log(JSON.stringify({svgVariants:Object.keys(data).length}));
