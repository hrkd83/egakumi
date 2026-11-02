// Episode 10 teaching diagrams. Fixed, self-contained and repository-based.
import fs from 'node:fs/promises';
import {renderSettingsShell} from '../../settings-visual.mjs';
import sharp from 'sharp';

const out = new URL('./diagrams/', import.meta.url);
const preview = new URL('../../output/lesson-10-gemini/diagram-previews/', import.meta.url);
const ink='#20323e', teal='#087f8c', muted='#647984', red='#bc3741';
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const text=(x,y,s,size=22,color=ink,weight=500,anchor='start')=>`<text x="${x}" y="${y}" font-family="Hiragino Sans,Yu Gothic,Meiryo,sans-serif" font-size="${size}" font-weight="${weight}" fill="${color}" text-anchor="${anchor}">${esc(s)}</text>`;
const rect=(x,y,w,h,fill='#fff',stroke='#d2e0e5',r=6)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}"/>`;
const box=(x,y,w,h,label,fill='#edf7f7',size=21)=>rect(x,y,w,h,fill,'#b6d6d6')+text(x+w/2,y+h/2+7,label,size,teal,600,'middle');
const line=(points,color=teal,dashed=false)=>`<path d="M${points.map(p=>p.join(' ')).join(' L')}" fill="none" stroke="${color}" stroke-width="2.5" ${dashed?'stroke-dasharray="6 4"':''} marker-end="url(#arrow)"/>`;
const defs=`<defs><marker id="arrow" viewBox="0 0 10 10" refX="8.7" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M1 1L9 5L1 9Z" fill="${teal}"/></marker></defs>`;
const shell=(id,title,section,content,subtitle='教材注釈：公開定義から検査の位置と範囲を整理')=>`<svg xmlns="http://www.w3.org/2000/svg" width="1207" height="511" viewBox="0 0 1207 511">${defs}${renderSettingsShell({viewId:id,areaLabel:'Firewall / IDPS の設定',title,kind:'Firewall Policy',section,menu:['動作モード','経路と暗号化','方向分類'],subtitle,footer:'公開設定例に基づく教材用の設定ビュー・稼働環境の実測ではありません'},content)}</svg>`;

let content=rect(214,147,972,158,'#fff');
content+=text(233,175,'本番定義：Alert',23,teal,650)+text(458,174,'通常規則とIDPS検知は並列',20,ink);
content+=box(234,207,144,43,'通信')+line([[378,228],[408,228],[408,214],[437,214]])+line([[408,228],[408,271],[437,271]]);
content+=box(439,194,245,40,'通常の通信規則')+line([[684,214],[729,214]])+box(732,194,423,40,'Allow / Deny で許可・拒否');
content+=box(439,251,245,40,'IDPS 検知')+line([[684,271],[729,271]])+box(732,251,423,40,'署名一致を記録・規則Denyは覆さない','#fff3f1',19);
content+=rect(214,317,972,158,'#fff')+text(233,345,'機能説明：Deny',23,teal,650)+text(458,344,'現行本番の配備状態ではない',20,muted);
content+=box(234,365,250,48,'通常規則が Allow')+line([[484,389],[523,389]])+box(526,365,240,48,'IDPS / inline')+line([[766,389],[804,389]])+box(808,365,347,48,'転送 / 遮断対象の署名でdrop','#edf7f7',19);
content+=text(234,452,'規則のAllowログがあっても、IDPSが遮断する場合がある',21,red,600);
const modes=shell('idps-modes','通信規則の判定とIDPSの動作位置','動作モード',content);

content='';
const coverageRows=[
  {y:147,title:'通常API：AppGW → APIM',dest:'APIM',protocol:'HTTP / TCP 80',result:'平文区間',detail:'署名の条件に従って検査',fw:true},
  {y:256,title:'Portal：AppGW → Portal',dest:'Portal',protocol:'HTTPS / TCP 443',result:'TLS復号設定なし',detail:'暗号化された本文は読めない',fw:true},
  {y:365,title:'APIM → Foundry のPE',dest:'Foundry',protocol:'PEへの直接経路',result:'Firewallを通らない',detail:'このFWのIDPSは作用しない',fw:false},
];
for(const row of coverageRows){
  content+=rect(214,row.y,972,102,'#fff')+text(233,row.y+26,row.title,21,ink,650);
  const first=row.fw?'AppGW':'APIM',middle=row.fw?'Firewall':'PE';
  content+=box(233,row.y+39,158,43,first)+line([[391,row.y+60],[432,row.y+60]])+box(435,row.y+39,158,43,middle)+line([[593,row.y+60],[634,row.y+60]])+box(637,row.y+39,174,43,row.dest);
  content+=text(832,row.y+52,row.result,20,row.fw?teal:red,650)+text(832,row.y+79,row.detail,17,ink);
  content+=text(539,row.y+97,row.protocol,15,muted,500,'middle');
}
const coverage=shell('coverage-paths','経路・暗号化によって、検査できる範囲が変わる','経路と暗号化',content);

content=rect(214,147,972,51,'#e8f4ff','#b6d9f3')+text(234,180,'idpsPrivateRanges = []  →  独自指定なし・既定 RFC 1918',22,'#17496b',600);
const directions=[['公開IP','private IP','Inbound',214],['private IP','公開IP','Outbound',288],['private IP','private IP','Internal',362]];
for(const [from,to,mode,y] of directions){
  content+=rect(214,y,972,63,'#fff')+box(234,y+10,220,43,from)+line([[454,y+31],[615,y+31]])+box(620,y+10,220,43,to);
  content+=text(1011,y+40,mode,24,teal,650,'middle');
}
content+=text(234,456,'Firewallが見るIPで分類・元の利用者がInternet側かとは区別',20,muted,600);
const direction=shell('direction','IDPSの方向分類は送信元・宛先IPから決まる','方向分類',content,'教材注釈：private範囲は検査対象一覧ではなく方向分類に使う');

await fs.mkdir(out,{recursive:true});
await fs.mkdir(preview,{recursive:true});
for(const [name,svg] of [['idps-modes',modes],['coverage-paths',coverage],['direction',direction]]){
  await fs.writeFile(new URL(`${name}.svg`,out),svg);
  await sharp(Buffer.from(svg)).png().toFile(new URL(`${name}.png`,preview).pathname);
}
console.log(JSON.stringify({diagrams:3,output:out.pathname,previews:preview.pathname}));
