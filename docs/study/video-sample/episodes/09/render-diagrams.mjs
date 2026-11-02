import fs from 'node:fs/promises';
import {renderSettingsShell} from '../../settings-visual.mjs';

const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const text=(x,y,s,size=20,color='#d9efef',weight=400)=>`<text x="${x}" y="${y}" fill="${color}" font-size="${size}" font-weight="${weight}" font-family="Hiragino Sans,Yu Gothic,Meiryo,sans-serif">${esc(s)}</text>`;
const rect=(x,y,w,h,fill='#123949',stroke='#63979f',radius=7)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${radius}" fill="${fill}" stroke="${stroke}"/>`;
const red=(x,y,w,h)=>`<rect data-highlight="true" x="${x}" y="${y}" width="${w}" height="${h}" rx="7" fill="none" stroke="#de343b" stroke-width="3"/>`;
const arrow=(x1,y,x2)=>`<path d="M${x1} ${y}H${x2}" fill="none" stroke="#91c9cd" stroke-width="2.5"/><path d="M${x2-7} ${y-5}L${x2} ${y}L${x2-7} ${y+5}" fill="none" stroke="#91c9cd" stroke-width="2.5"/>`;
const base=rect(214,147,972,328,'#0b2633','#325d69');
const shell=(key,title,section,body)=>`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1207 511" width="1207" height="511">${renderSettingsShell({viewId:'waf09-'+key,areaLabel:'WAF / 教材用設定メニュー',title,kind:'Application Gateway WAF',section,menu:['入口と役割','管理ルール','本文と上限','適用ポリシー','限定した除外','特殊形式','確認問題'],subtitle:'リポジトリ定義と公開設定例を読み解く'},body)}</svg>`;
const files=[];
let route=base;
for(const [i,title,detail] of [[0,'AppGW / WAF','HTTPS終端・HTTP検査'],[1,'Firewall','通信規則で許可・拒否'],[2,'内部 APIM','利用許可・入力契約'],[3,'バックエンド','APIの処理']]){
  const x=237+i*237;
  route+=rect(x,178,210,100)+text(x+15,210,title,22,'#e7fbf9',600)+text(x+15,244,detail,16);
  if(i<3)route+=arrow(x+212,226,x+234);
}
route+=text(244,312,'通常HTTP API：AppGW → APIMはHTTP / TCP 80',20,'#a7dfe1',600);
route+=rect(237,331,922,54,'#183d4b','#567e88')+text(254,365,'WAFで拒否 → その要求をFW・APIMへ業務転送しない',21);
route+=text(244,420,'確認：時刻・URI・Action・原因規則と、後段へ届いた範囲',20);
route+=text(244,452,'403だけでは原因を断定しない。APIMも別の理由で403を返せる。',18,'#b9cfd5');
for(const target of ['none','waf','apim']){
  const extra=target==='waf'?red(234,175,216,106):target==='apim'?red(708,175,216,106):'';
  files.push([`route-${target}.svg`,shell('route','通常HTTP API：どの層で要求が止まったか','入口と役割',route+extra)]);
}

let scope=base+text(244,178,'より具体的なポリシーを優先して適用する',20,'#a7dfe1',600);
for(const [i,title,detail] of [[0,'1  URI別','要求パスに関連付け'],[1,'2  リスナー別','その入口に関連付け'],[2,'3  Gateway全体','個別指定のない要求']]){
  const x=237+i*315;
  scope+=rect(x,201,292,105)+text(x+16,235,title,22,'#e7fbf9',600)+text(x+16,274,detail,19);
  if(i<2)scope+=arrow(x+294,254,x+312);
}
scope+=text(244,345,'一致する最も具体的な1つを適用。親と子の二重検査ではない。',21);
scope+=rect(237,365,922,77,'#183d4b','#567e88')+text(254,396,'Coreの実装：sharedWafPropertiesから専用ポリシーを作る',20)+text(254,426,'本文検査・上限・ログマスク・追加規則を明示的に合成',19);
for(const target of ['none','uri'])files.push([`scope-${target}.svg`,shell('scope','ポリシーの適用：具体的な設定が全体を置き換える','適用ポリシー',scope+(target==='uri'?red(234,198,298,111):''))]);

let complement=base;
complement+=rect(237,169,442,210)+rect(705,169,454,210);
complement+=text(254,203,'raw本文の限定した調整',23,'#e7fbf9',600)+text(254,240,'正本SpeechのSSML・WAV',20)+text(254,274,'固定文書APIの本文',20)+text(254,309,'正常な ../ → 930110を調整',20,'#a7dfe1')+text(254,348,'他の管理ルール・本文検査を維持',18);
complement+=text(722,203,'非本文：4本のカスタムBlock',22,'#e7fbf9',600)+text(722,240,'URI  /  query',21)+text(722,274,'ヘッダー  /  Cookie',21)+text(722,309,'不審なパストラバーサルを拒否',20,'#a7dfe1')+text(722,348,'930100など他の規則も維持',19);
complement+=rect(237,398,922,59,'#183d4b','#567e88')+text(254,425,'4本のBlockは、CRSの全検査・全正規化を再現するものではない。',20)+text(254,448,'raw本文を許容する限定した判断として、残る検査範囲を把握する。',17,'#b9cfd5');
for(const target of ['none','block','limit'])files.push([`complement-${target}.svg`,shell('raw-complement','raw本文の調整と、URI・query・ヘッダー・Cookieの保護','特殊形式',complement+(target==='block'?red(702,166,460,216):target==='limit'?red(234,395,928,65):''))]);

await fs.mkdir(new URL('./diagrams/',import.meta.url),{recursive:true});
for(const [name,svg] of files)await fs.writeFile(new URL('./diagrams/'+name,import.meta.url),svg);
console.log(JSON.stringify({created:files.map(x=>x[0])}));
