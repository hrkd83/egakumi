// Local, self-contained visual layers for the egakumi study series.
// Existing portraits and official logos are preserved. No network or lip sync.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {renderHologramFrame} from './hologram-visual.mjs';
import {renderSettingsContent} from './settings-visual.mjs';

const HERE=path.dirname(fileURLToPath(import.meta.url));
const require=createRequire(import.meta.url);
const sharp=require('sharp');
const W=1600,H=900;
const PEOPLE=[
  {id:'mako',name:'MAKO',role:'Moderator',cx:302,color:'#79f4f0'},
  {id:'manabu',name:'MANABU',role:'Product Manager',cx:800,color:'#80f6c4'},
  {id:'ikumi',name:'IKUMI',role:'Solution Architect',cx:1298,color:'#8bcaff'},
];
const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const n=v=>Number(v.toFixed(3));
const clamp=v=>Math.min(1,Math.max(0,v));
const smooth=v=>{v=clamp(v);return v*v*(3-2*v);};
const units=value=>[...String(value)].reduce((sum,ch)=>sum+(/[\u0000-\u007f]/.test(ch)? .56:1),0);
const text=(x,y,v,size=24,color='#e7f9ff',weight=500,attrs='')=>`<text x="${n(x)}" y="${n(y)}" fill="${color}" font-size="${size}" font-weight="${weight}" ${attrs}>${esc(v)}</text>`;
const wrap=(value,max=30)=>{
  const out=[];let line='';
  for(const ch of String(value??'')){if(ch==='\n'||[...line].length>=max){out.push(line);line=ch==='\n'?'':ch;}else line+=ch;}
  if(line)out.push(line);return out;
};
const lines=(x,y,values,size=24,gap=36,color='#b4d2df',attrs='')=>values.map((v,i)=>text(x,y+i*gap,v,size,color,500,attrs)).join('');
const assets=(async()=>{
  const png=async(relative,width)=>`data:image/png;base64,${(await sharp(await fs.readFile(path.join(HERE,relative))).resize({width,withoutEnlargement:true}).png().toBuffer()).toString('base64')}`;
  return {symbol:await png('assets/brand/egakumi-symbol.webp',540),wordmark:await png('assets/brand/egakumi-wordmark.webp',850),people:await Promise.all(PEOPLE.map(async p=>({...p,image:await png(`assets/portraits/${p.id}.webp`,440)})))};
})();
const root=(body,description='')=>`<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="1600" height="900" viewBox="0 0 1600 900"><title>egakumi 基盤管理者向け勉強会</title><desc>${esc(description)}</desc><defs><style>text{font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,sans-serif}.latin{font-family:-apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif}</style><radialGradient id="bg"><stop stop-color="#102e43"/><stop offset="1" stop-color="#030710"/></radialGradient><linearGradient id="glass" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#76d7ed" stop-opacity=".11"/><stop offset="1" stop-color="#071c2b" stop-opacity=".36"/></linearGradient><linearGradient id="beam" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#83eafa" stop-opacity="0"/><stop offset="1" stop-color="#83eafa" stop-opacity=".16"/></linearGradient><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="context-stroke"/></marker><clipPath id="stage-clip"><rect x="30" y="185" width="1540" height="557" rx="18"/></clipPath></defs>${body}</svg>`;
const background=(time=0)=>{
  let out='<rect width="1600" height="900" fill="#030710"/><ellipse cx="800" cy="420" rx="1030" ry="650" fill="url(#bg)" opacity=".74"/>';
  for(let i=0;i<24;i++){const x=40+(i*359)%1520,y=145+(i*127)%565;out+=`<circle cx="${x}" cy="${y}" r="${i%3===0?1.1:.6}" fill="#92e6f4" opacity="${n(.10+(Math.sin(time*.4+i)+1)*.045)}"/>`;}
  return out;
};
const image=(data,x,y,w,h,attrs='')=>`<image x="${n(x)}" y="${n(y)}" width="${w}" height="${h}" preserveAspectRatio="xMidYMid meet" xlink:href="${data}" ${attrs}/>`;
const normalizedLayout=layout=>({'hologram':'opening','network-overview':'overview','vnet-structure':'structure','address-spaces':'addresses','subnet-map':'subnets','api-flow':'flow','check':'quiz'}[layout]??layout??'overview');
const focused=(visual,id)=>{
  const focus=visual?.focus??visual?.highlight??[];
  return (Array.isArray(focus)?focus:[focus]).map(String).some(v=>v===id||v.toLowerCase().includes(id));
};
const node=(x,y,w,h,label,detail,id,visual,color='#76d7ef')=>{
  const active=focused(visual,id);
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="12" fill="${active?'#153b4c':'#081928'}" stroke="${active?color:'#244b61'}" stroke-width="${active?2.8:1.4}"/>${active?`<path d="M${x+15} ${y}H${x+w-15}" stroke="${color}" stroke-width="4"/>`:''}${text(x+w/2,y+(detail?35:h/2+9),label,Math.min(27,(w-24)/Math.max(units(label),5)),'#e3f8fc',650,'text-anchor="middle"')}${detail?text(x+w/2,y+65,detail,18,'#91b3c4',500,'text-anchor="middle"'):''}`;
};
const arrow=(points,color='#7be6b4',dash=false,width=3)=>`<path d="${points}" fill="none" stroke="${color}" stroke-width="${width}" ${dash?'stroke-dasharray="8 6"':''} marker-end="url(#arrow)"/>`;

function header(a,options){
  const chapter=typeof options.chapter==='object'?options.chapter: {title:options.chapter??''};
  const title=options.title??'egakumiのネットワーク全体像';
  const heading=options.visual?.heading||chapter.title||title;
  const episodeLabel=options.episodeLabel??`第${options.episodeNumber??1}回`;
  const subtitle=options.seriesLabel??(title===heading&&Number(options.episodeNumber??1)===1?'NETWORK OVERVIEW':title);
  return `${image(a.symbol,48,24,47,47)}${image(a.wordmark,105,24,165,55)}${text(1550,58,'基盤管理者向け勉強会',20,'#83a9bc',500,'text-anchor="end"')}${text(55,121,heading,Math.min(36,1450/Math.max(units(heading),1)),'#e8fbff',700)}${text(55,158,`${episodeLabel} / ${subtitle}`,18,'#7fcacb',500)}<path d="M55 178H1545" stroke="#387088" stroke-opacity=".45"/>`;
}
function captions(options){
  const person=PEOPLE.find(p=>p.id===options.speaker);
  let rows=options.subtitleLines??[];
  if(!Array.isArray(rows))rows=wrap(String(rows),40);
  if(rows.length>2)throw new Error('Use at most two subtitle lines per cue.');
  const max=Math.max(1,...rows.map(v=>[...v].length));
  const size=Math.min(32,Math.max(24,1370/max));
  return `<rect x="40" y="758" width="1520" height="124" rx="10" fill="#071827" stroke="#2f586b"/><path d="M64 758H${person?240:410}" stroke="${person?.color??'#6bced7'}" stroke-width="3"/>${text(68,785,person?`${person.name} / ${person.role}`:'MAKO / MANABU / IKUMI',16,'#8bd9df',650)}${rows.length===1?text(800,840,rows[0],size,'#f0fcff',600,'text-anchor="middle"'):rows.map((v,i)=>text(800,825+i*39,v,size,'#f0fcff',600,'text-anchor="middle"')).join('')}`;
}
function speakerRail(a,speaker){
  let out='<path d="M1315 204V728" stroke="#2a5067" stroke-opacity=".55"/>';
  a.people.forEach((p,i)=>{const y=213+i*170,active=p.id===speaker;out+=`<rect x="1342" y="${y}" width="200" height="151" rx="12" fill="${active?'#103140':'#07131f'}" stroke="${active?p.color:'#203d50'}" stroke-width="${active?2:1}"/>${image(p.image,1377,y+2,130,112,`opacity="${active?1:.62}"`)}${text(1442,y+137,p.name,21,active?'#eaffff':'#8aafc1',700,'class="latin" text-anchor="middle" letter-spacing="2"')}`;});
  return out;
}

function overview(visual={}){
  let out='<rect x="237" y="209" width="730" height="493" rx="18" fill="#071724" stroke="#3e758a" stroke-width="2"/>'+text(261,244,'VNet / 6つのサブネット',23,'#88cbd4',650);
  out+=node(60,320,148,92,'利用者','公開HTTPS','user',visual);
  out+=node(270,320,184,92,'AppGW','公開入口','agw',visual);
  out+=node(505,320,184,92,'Firewall','通信の制御','fw',visual,'#80edbd');
  out+=node(740,320,184,92,'APIM','APIの実行窓口','apim',visual);
  out+=node(270,513,184,92,'管理VM','配備・管理作業','vm',visual);
  out+=node(505,513,184,92,'Container Apps','Portal・Broker等','aca',visual);
  out+=node(740,513,184,92,'PE','サービスへの接続口','pe',visual,'#8bcaff');
  out+=node(1028,513,205,92,'Foundry','サービス本体','foundry',visual,'#a6bbd3');
  out+=arrow('M208 366H270','#91acbd');out+=arrow('M454 366H505');out+=arrow('M689 366H740');out+=arrow('M832 412V513','#85cafa',true);out+=arrow('M924 559H1028','#a4bbc8');
  if(focused(visual,'aca')||focused(visual,'portal'))out+=arrow('M598 412V513');
  if(focused(visual,'vm'))out+=arrow('M454 559H740','#85cafa',true);
  out+=text(261,665,'PEはVNet内の接続口。サービス本体と区別する。',21,'#87b1c4');
  out+=text(1018,642,'VNetの外',21,'#a4b5c7');
  return out;
}
function structure(visual={},layout='structure'){
  const slot=(x,y,w,h,cidr,label,id,reserve=false)=>{
    const active=focused(visual,id)||focused(visual,'address');
    return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10" fill="${reserve?'#081522':'#0b2635'}" stroke="${active?'#83e4dd':'#30566a'}" ${reserve?'stroke-dasharray="8 6"':''}/>${text(x+18,y+30,cidr,23,'#8ad7de',650)}${label?text(x+18,y+67,label,25,'#e4f8fc',600):''}`;
  };
  let out='<rect x="65" y="204" width="1207" height="511" rx="18" fill="#061724" stroke="#468899" stroke-width="2"/>'+text(87,239,'1つのVNet / 4つのアドレス空間',26,'#c9f3f6',650)+text(1248,238,'公開設定例',18,'#7a9dad',500,'text-anchor="end"');
  out+=slot(88,263,558,105,'10.40.0.0/24','Application Gateway','space0');
  out+=slot(668,263,580,105,'10.40.1.0/24','Private Endpoint','space1');
  out+=slot(88,389,1160,191,'10.40.2.0/24','','space2');
  const names=[['Firewall','10.40.2.0/26','fw'],['APIM','10.40.2.64/26','apim'],['管理VM','10.40.2.128/26','vm'],['Container Apps','10.40.2.192/26','aca']];
  names.forEach(([label,cidr,id],i)=>{out+=node(108+i*281,442,262,111,label,layout==='addresses'?cidr:['FW専用','内部API処理','配備・保守','Portal等'][i],id,visual);});
  out+=slot(88,601,1160,87,'10.40.3.0/24','拡張用の予備 / サブネットは作成しない','space3',true);
  return out;
}
function subnets(visual={}){
  const cards=[['agw','Application Gateway','公開入口','要求を内部へ中継する'],['pe','Private Endpoint','サービスへの閉域接続口','サービス本体はVNetの外'],['fw','Azure Firewall','通信制御','経路上の通信を判定する'],['apim','API Management','APIの実行窓口','認証・制約・下流への中継'],['vm','管理VM','配備・管理作業','公開IPを持たない'],['aca','Container Apps','Portal・Broker等','アプリとJobの実行基盤']];
  let out='';cards.forEach(([id,name,role,detail],i)=>{const x=70+(i%3)*403,y=219+Math.floor(i/3)*244,active=focused(visual,id);out+=`<rect x="${x}" y="${y}" width="375" height="216" rx="14" fill="${active?'#123748':'#081a29'}" stroke="${active?'#8be4df':'#2a4f64'}" stroke-width="${active?2.7:1.3}"/>${text(x+22,y+38,String(i+1).padStart(2,'0'),19,'#71cacf',650)}${text(x+22,y+81,name,26,'#e5f9fd',650)}${text(x+22,y+124,role,24,'#9edee4',600)}${lines(x+22,y+167,wrap(detail,18),21,30,'#91b0c1')}`;});return out;
}
function flow(visual={}){
  const returning=visual.direction==='return';
  let out='<rect x="240" y="251" width="800" height="307" rx="17" fill="#071c2a" stroke="#376c7e"/>'+text(260,288,'VNet内',21,'#7eb6c8',600);
  const nodes=[['user','利用者','HTTPS',50],['agw','AppGW','公開入口',251],['fw','Firewall','中継の制御',452],['apim','APIM','API実行',653],['pe','Foundry PE','接続口',854],['foundry','Foundry','サービス本体',1055]];
  nodes.forEach(([id,label,detail,x])=>{out+=node(x,345,170,109,label,detail,id,visual,id==='fw'?'#80edbd':id==='pe'?'#8bcaff':'#76d7ef');});
  out+=`<g opacity="${returning?.22:1}">`;
  for(let i=0;i<5;i++){out+=arrow(`M${220+i*201} 399H${251+i*201}`,i===1||i===2?'#80edbd':i===3?'#8bcaff':'#91aebb',i===3);}
  out+='</g>';
  if(returning){
    // Distinct ports keep the inbound and outbound response legs visible.
    // PE returns directly to APIM; APIM and AppGW return via the same Firewall.
    out+=arrow('M939 454V481H775V454','#8bcaff',true,3.3);
    out+=arrow('M725 454V510H560V454','#80edbd',false,3.3);
    out+=arrow('M515 454V538H335V454','#80edbd',false,3.3);
    out+=text(1052,485,'応答：PE → APIM',20,'#9acff1',600);
    out+=text(1052,520,'APIM → FW → AppGW',19,'#93d8bd',600);
  }
  out+=text(617,606,'AppGW → Firewall → APIM の往復をそろえる',25,'#93d8bd',600,'text-anchor="middle"');
  out+=text(617,650,'APIM → PE は、Firewallを通らない直接経路',25,'#9acff1',600,'text-anchor="middle"');
  out+=text(1074,returning?555:511,'サービス本体はVNet外',19,'#9bb2c4');
  return out;
}
function directPaths(visual={}){
  const rows=[['APIM','Foundry PE','Foundry','閉域サービスへの直接経路','#8bcaff'],['Container Apps','SQL PE','Azure SQL','アプリからデータベースへ','#8bcaff'],['APIM・管理VM・ACA','Firewall','公開依存先','具体的な経路がない公開宛て','#80edbd']];
  let out='';rows.forEach(([source,middle,dest,note,color],i)=>{const y=241+i*157;out+=`<rect x="65" y="${y-22}" width="1207" height="139" rx="13" fill="#081927" stroke="#264c60"/>`;out+=node(88,y+3,300,83,source,'',i===0?'apim':i===1?'aca':'egress',visual,color);out+=node(500,y+3,265,83,middle,'',i===2?'fw':'pe',visual,color);out+=node(877,y+3,367,83,dest,'',i===1?'sql':'foundry',visual,color);out+=arrow(`M388 ${y+45}H500`,color,i<2);out+=arrow(`M765 ${y+45}H877`,i<2?'#9fb7c5':color);out+=text(680,y+106,note,19,'#8fb2c4',500,'text-anchor="middle"');});return out;
}
function purpose(visual={}){
  const supplied=Array.isArray(visual.bullets)?visual.bullets:[];
  const cards=supplied.length?supplied.slice(0,3):['公開入口と内部の処理を分ける','管理作業とサービス接続を分ける','経路と通信の許可を確認しやすくする'];
  let out='';cards.forEach((value,i)=>{const x=70+i*403;out+=`<rect x="${x}" y="270" width="375" height="320" rx="16" fill="#0a2231" stroke="#386a7c"/>${text(x+25,318,`0${i+1}`,25,'#7edbd9',650)}${lines(x+25,391,wrap(value,10),28,48,'#def8fb')}`;});
  out+=text(670,670,'配置の分離と、通信制御を結びつける',29,'#97cfdf',600,'text-anchor="middle"');return out;
}
function quiz(visual={},layout='quiz'){
  const bullets=visual.bullets?.length?visual.bullets:layout==='summary'?['どこに何を置くか：VNetと6つのサブネット','どこを通るか：往路・復路と直接経路','何を許可するか：NSGとFirewallの判断']:['6つのサブネット間は、自動的に全部遮断される？','公開入口→APIMはFW経由、APIM→PEは直接経路？','SQL PEがある区画には、SQL本体も配置される？'];
  let out='';bullets.slice(0,4).forEach((value,i)=>{const y=250+i*115;out+=`<rect x="70" y="${y}" width="1200" height="91" rx="12" fill="#0a2231" stroke="#356377"/>${text(100,y+53,layout==='summary'?String(i+1).padStart(2,'0'):`Q${i+1}`,25,'#82dfd8',650)}${text(183,y+53,value,Math.min(28,1045/Math.max(units(value),1)),'#e4f9fc',600)}`;});return out;
}

// Generic per-episode content occupies only the diagram area. Branding,
// presenter rail, caption layout, portraits and opening/closing stay shared.
const BODY={x:65,y:204,width:1207,height:511};
const finite=(v,name)=>{if(!Number.isFinite(Number(v)))throw new Error(`Invalid ${name}`);return Number(v);};
const colorValue=value=>/^#[0-9a-f]{3,8}$/i.test(value??'')?value:'#80dce3';
function cards(visual={}){
  if(!Array.isArray(visual.cards)||!visual.cards.length||visual.cards.length>6)throw new Error('cards requires one to six topic cards');
  const cols=visual.cards.length<=2?visual.cards.length:3,rows=Math.ceil(visual.cards.length/cols),gap=20;
  const w=(BODY.width-gap*(cols-1))/cols,h=(BODY.height-gap*(rows-1))/rows;
  return visual.cards.map((card,i)=>{
    const x=BODY.x+(i%cols)*(w+gap),y=BODY.y+Math.floor(i/cols)*(h+gap),body=Array.isArray(card.body)?card.body:wrap(card.body??'',Math.floor((w-48)/23));
    if(body.length>Math.floor((h-100)/32))throw new Error('Card body is too long; split the visual across turns');
    return `<rect x="${n(x)}" y="${n(y)}" width="${n(w)}" height="${n(h)}" rx="14" fill="#0a2231" stroke="${focused(visual,card.id)?'#8be4df':'#386a7c'}" stroke-width="${focused(visual,card.id)?2.7:1.3}"/>${text(x+24,y+48,card.title??'',Math.min(28,(w-48)/Math.max(units(card.title),1)),'#def8fb',650)}${lines(x+24,y+96,body,23,32,'#9dc6d6')}`;
  }).join('');
}
function diagram(visual={}){
  if(!Array.isArray(visual.nodes)||!visual.nodes.length||visual.nodes.length>16)throw new Error('diagram requires one to sixteen nodes');
  const nodes=new Map();
  for(const raw of visual.nodes){
    if(!/^[a-zA-Z0-9_-]+$/.test(raw.id??'')||nodes.has(raw.id))throw new Error('Diagram node IDs must be unique');
    const value={...raw,x:finite(raw.x,'node.x'),y:finite(raw.y,'node.y'),w:finite(raw.width??200,'node.width'),h:finite(raw.height??100,'node.height')};
    if(value.w<80||value.h<70||value.x<0||value.y<0||value.x+value.w>BODY.width||value.y+value.h>BODY.height)throw new Error('Diagram node exceeds the 1207x511 content area');
    nodes.set(raw.id,value);
  }
  const port=(a,b)=>{
    const cx=a.x+a.w/2,cy=a.y+a.h/2,dx=b.x+b.w/2-cx,dy=b.y+b.h/2-cy;
    if(!dx&&!dy)throw new Error('Diagram edge cannot connect overlapping centers');
    const scale=Math.min(dx?Math.abs(a.w/2/dx):Infinity,dy?Math.abs(a.h/2/dy):Infinity);
    return [cx+dx*scale,cy+dy*scale];
  };
  let out='';
  for(const edge of visual.edges??[]){
    const from=nodes.get(edge.from),to=nodes.get(edge.to);if(!from||!to)throw new Error('Diagram edge names an unknown node');
    const points=edge.points??[port(from,to),port(to,from)];
    if(!Array.isArray(points)||points.length<2||points.some(p=>!Array.isArray(p)||p.length!==2||!p.every(Number.isFinite)||p[0]<0||p[0]>BODY.width||p[1]<0||p[1]>BODY.height))throw new Error('Invalid diagram edge points');
    const d=points.map(([x,y],i)=>`${i?'L':'M'}${n(BODY.x+x)} ${n(BODY.y+y)}`).join('');
    out+=arrow(d,colorValue(edge.color),edge.dashed===true,3);
    if(edge.label){const mid=points[Math.floor(points.length/2)],before=points[Math.floor((points.length-1)/2)];out+=text(BODY.x+(mid[0]+before[0])/2,BODY.y+(mid[1]+before[1])/2-12,edge.label,19,'#a9d4de',500,'text-anchor="middle"');}
  }
  for(const value of nodes.values())out+=node(BODY.x+value.x,BODY.y+value.y,value.w,value.h,value.label??'',value.detail??'',value.id,visual,colorValue(value.color));
  return out;
}
function customSvg(visual={}){
  if(!/^data:image\/svg\+xml;base64,[A-Za-z0-9+/=]+$/.test(visual.svgDataUri??''))throw new Error('Custom SVG must be loaded and validated by build-lesson');
  return image(visual.svgDataUri,BODY.x,BODY.y,BODY.width,BODY.height);
}

/** Smooth virtual-camera coordinates. Actor positions never swap. */
export function getHologramCamera({speaker=null,previousSpeaker=null,turnTime=10,cameraFrom,cameraTo}={}){
  const target=PEOPLE.find(p=>p.id===speaker),previous=PEOPLE.find(p=>p.id===previousSpeaker);
  const from=typeof cameraFrom==='object'?cameraFrom:{focusX:previous?.cx??800,zoom:previous?1.10:1};
  const to=typeof cameraTo==='object'?cameraTo:{focusX:target?.cx??800,zoom:target?1.10:1};
  const p=smooth(turnTime/.85);
  return {focusX:from.focusX+(to.focusX-from.focusX)*p,zoom:from.zoom+(to.zoom-from.zoom)*p,progress:p};
}

async function opening(a,options){
  const camera=getHologramCamera(options),active=options.speaker;
  const t=options.time??0;
  let stage='';
  // A wide virtual stage is projected through the camera below. Header and
  // captions stay in screen space while people and their bases share a pan.
  for(let i=-12;i<=12;i++)stage+=`<path d="M800 525L${800+i*190} 750" stroke="#24647a" stroke-opacity=".24"/>`;
  for(let i=0;i<8;i++)stage+=`<path d="M-1500 ${550+i*i*4}H3100" stroke="#368399" stroke-opacity=".16"/>`;
  a.people.forEach((p,i)=>{const on=p.id===active,hover=Math.sin(t*.7+i)*2.8;stage+=`<path d="M${p.cx-173} 679L${p.cx-136} 230H${p.cx+136}L${p.cx+173} 679Z" fill="url(#beam)"/><ellipse cx="${p.cx}" cy="683" rx="186" ry="28" fill="#082738" stroke="${p.color}" stroke-opacity="${on?.65:.25}"/><ellipse cx="${p.cx}" cy="683" rx="156" ry="20" fill="none" stroke="${p.color}" stroke-opacity="${on?.65:.20}"/><g transform="translate(0 ${n(hover)})" opacity="${on?1:.77}"><path d="M${p.cx-179} 239L${p.cx+179} 227L${p.cx+188} 660L${p.cx-188} 670Z" fill="url(#glass)" stroke="${p.color}" stroke-opacity="${on?.7:.26}" stroke-width="${on?2:1}"/>${image(p.image,p.cx-174,233,348,365)}<path d="M${p.cx-149} 601H${p.cx+149}" stroke="${p.color}" stroke-opacity=".5"/>${text(p.cx,642,p.name,34,'#dcfbff',750,'class="latin" text-anchor="middle" letter-spacing="3"')}${text(p.cx,675,p.role,20,'#a4d3df',600,'text-anchor="middle"')}</g>`;});
  const transform=`translate(800 450) scale(${n(camera.zoom)}) translate(-${n(camera.focusX)} -450)`;
  return `${background(t)}${header(a,{...options,visual:{heading:options.title??'egakumiのネットワーク全体像'}})}<g clip-path="url(#stage-clip)"><g transform="${transform}">${stage}</g></g>${captions(options)}`;
}
async function closing(a,options){
  const t=Math.max(0,options.sceneTime??options.turnTime??0),length=options.closingDuration??8;
  const p=smooth(t/1.15),fade=1-smooth((t-(length-1.15))/1.15),scale=.94+.06*p;
  let out=background(t);
  for(let i=-8;i<=8;i++)out+=`<path d="M800 580L${800+i*190} 900" stroke="#28657a" stroke-opacity=".22"/>`;
  out+=`<g opacity="${n(fade)}"><ellipse cx="800" cy="603" rx="270" ry="43" fill="#0a2b3b" stroke="#64cddc" stroke-opacity=".4"/><path d="M540 603L660 220H940L1060 603Z" fill="url(#beam)"/><g opacity="${n(p)}" transform="translate(800 420) scale(${n(scale)}) translate(-800 -420)">${image(a.symbol,630,180,340,340)}${image(a.wordmark,540,510,520,165)}</g>${text(800,723,'また次の勉強会で。',28,'#c4eaf0',500,'text-anchor="middle" letter-spacing="2"')}</g>`;
  return out;
}

/** One self-contained source SVG. Body frames are intentionally static/cached. */
export async function renderLessonFrame(options={}){
  const a=await assets,phase=options.phase??'body',visual=options.visual??{},layout=normalizedLayout(visual.layout);
  if(phase==='logo')return renderHologramFrame({...options,phase:'logo',sceneTime:options.sceneTime??options.time??0});
  if(phase==='closing'||phase==='outro')return root(await closing(a,options),'egakumiの共通クロージング');
  if(phase==='opening'||phase==='dialogue'&&layout==='opening'||layout==='opening')return root(await opening(a,options),'話者へ滑らかに移動するホログラムのカメラ');
  const renderers={settings:renderSettingsContent,cards,diagram,svg:customSvg,overview,structure:visual=>structure(visual,'structure'),addresses:visual=>structure(visual,'addresses'),subnets,flow,'direct-paths':directPaths,purpose,quiz,summary:visual=>quiz(visual,'summary')};
  if(!renderers[layout])throw new Error(`Unknown visual layout ${layout}; use settings/diagram/cards/quiz/summary/svg`);
  if(Number(options.episodeNumber??1)!==1&&['quiz','summary'].includes(layout)&&!visual.bullets?.length)throw new Error('Generic quiz/summary requires explicit topic bullets');
  const content=renderers[layout](visual);
  return root(`${background(0)}${header(a,options)}${content}${speakerRail(a,options.speaker)}${captions(options)}`,`${options.episodeLabel??`第${options.episodeNumber??1}回`} ${options.title??'egakumi'}の解説図`);
}

const pngCache=new Map();
/** PNG Buffer. For body use once per cue and hold it in the video encoder. */
export async function renderLessonRaster(options={}){
  const phase=options.phase??'body',layout=normalizedLayout(options.visual?.layout);
  const animated=['logo','closing','outro','opening'].includes(phase)||layout==='opening';
  const key=animated?null:JSON.stringify([phase,options.episodeNumber,options.episodeLabel,options.seriesLabel,options.speaker,options.chapter,options.title,options.visual,options.subtitleLines]);
  if(key&&pngCache.has(key))return pngCache.get(key);
  const rendered=renderLessonFrame(options).then(svg=>sharp(Buffer.from(svg)).png({compressionLevel:1}).toBuffer());
  if(key){pngCache.set(key,rendered);if(pngCache.size>220)pngCache.delete(pngCache.keys().next().value);}
  return rendered;
}
