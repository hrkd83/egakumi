/** A self-contained, educational reconstruction of the Azure Portal NSG blades.
 * No subscription is queried; no external fonts, images, scripts or HTML are used.
 * Coordinates returned by renderPortalScene are in the native 1600 × 680 surface.
 */
const W=1600,H=680;
const C={nav:'#172938',ink:'#242424',muted:'#616161',border:'#d6d6d6',blue:'#0078d4',pale:'#eff6fc',bg:'#f4f5f7',green:'#107c10',red:'#a4262c'};
const esc=x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const str=x=>Array.isArray(x)?x.map(str).join(', '):String(x??'');
const units=x=>[...str(x)].reduce((n,c)=>n+(/[\x00-\x7f]/.test(c)?.54:1),0);
const tx=(x,y,s,size=19,color=C.ink,weight=400,extra='')=>`<text x="${x}" y="${y}" font-size="${size}" fill="${color}" font-weight="${weight}" ${extra}>${esc(s)}</text>`;
const box=(x,y,w,h,fill='#fff',stroke=C.border,r=0)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}"/>`;
const path=(d,color=C.muted,width=1.5,extra='')=>`<path d="${d}" fill="none" stroke="${color}" stroke-width="${width}" ${extra}/>`;
function wrap(value,maxUnits=32){
 const out=[];let row='',n=0;
 for(const c of str(value)){const u=/[\x00-\x7f]/.test(c)?.54:1;if(c==='\n'||n+u>maxUnits){out.push(row);row=c==='\n'?'':c;n=c==='\n'?0:u;}else{row+=c;n+=u;}}
 if(row||!out.length)out.push(row);return out;
}
function ellipsis(value,maxUnits){if(units(value)<=maxUnits)return str(value);let o='',n=0;for(const c of str(value)){const u=/[\x00-\x7f]/.test(c)?.54:1;if(n+u>maxUnits-1)return o+'…';o+=c;n+=u;}return o;}
const paragraph=(x,y,s,width=430,size=20,gap=29,color=C.ink,maxLines=12)=>wrap(s,width/size).slice(0,maxLines).map((v,i)=>tx(x,y+i*gap,v,size,color)).join('');
const rect=(id,x,y,width,height,label)=>({id,x,y,width,height,...(label?{label}: {})});
function shield(x,y,size=34){return `<g transform="translate(${x} ${y}) scale(${size/34})"><path d="M17 1L31 6V17C31 25 23 31 17 34C11 31 3 25 3 17V6Z" fill="#0078d4"/><path d="M10 11H24V22H10ZM13 11V8H21V11" fill="none" stroke="#fff" stroke-width="2"/><circle cx="17" cy="16" r="1.7" fill="#fff"/><path d="M17 17V20" stroke="#fff" stroke-width="2"/></g>`;}
function toolbar(spec,full=false){
 const add=spec.kind==='subnets'?'Subnet':'Add';
 const y=173;let s=`${tx(267,y+2,'＋',25,C.blue)}${tx(297,y,add,18,C.blue)}${tx(395,y,'⟳',24,C.blue)}${tx(427,y,'Refresh',18,C.blue)}${tx(553,y,'⇩',21,C.blue)}${tx(584,y,'Export',18,C.blue)}`;
 if(full)s+=`${tx(725,y,'▤',19,C.blue)}${tx(753,y,'Columns',18,C.blue)}`;
 return s+path('M248 188H1580',C.border,1);
}
function chrome(spec){
 const resource=str(spec.resource||'Network security group'),direction=spec.direction==='Outbound'?'Outbound':'Inbound',isVnet=spec.kind==='subnets'||resource.startsWith('vnet-');
 const active=spec.kind==='subnets'?'Subnets':`${direction} security rules`;
 let s=`<rect width="1600" height="680" fill="${C.bg}"/><rect width="1600" height="48" fill="${C.nav}"/>`;
 for(const [i,color] of ['#f25022','#7fba00','#00a4ef','#ffb900'].entries())s+=box(19+(i%2)*10,15+Math.floor(i/2)*10,8,8,color,color);
 s+=tx(49,32,'Microsoft Azure',21,'#fff',600);
 s+=box(425,9,650,31,'#f3f4f6','#b5bec7',3)+`<circle cx="443" cy="23" r="6" fill="none" stroke="#5f6368" stroke-width="1.7"/>`+path('M447 27L452 32','#5f6368')+tx(463,30,'Search resources, services, and docs',17,'#4d545b');
 s+=tx(1183,31,'☷',24,'#eef6fc')+tx(1232,31,'◷',22,'#eef6fc')+tx(1285,31,'⚙',22,'#eef6fc')+tx(1338,31,'?',22,'#eef6fc')+tx(1390,30,'Learn',17,'#eef6fc')+`<circle cx="1557" cy="24" r="16" fill="#d7eaff"/>`+tx(1545,30,'EG',15,'#17416a',650);
 s+=box(0,48,225,632,'#fff','#dadada');
 s+=path('M18 71H36M18 77H36M18 83H36','#5b5b5b',2)+tx(51,82,'Resource menu',17,'#4a4a4a',600);
 s+=box(13,100,197,31,'#fff','#a3a3a3',2)+tx(26,121,'Filter menu',16,'#777');
 const nsgMenu=[['Overview','◉'],['Activity log','▤'],['Access control (IAM)','♙'],['Tags','◇'],['Diagnose and solve problems','⌕'],['SETTINGS',''],['Inbound security rules','⇥'],['Outbound security rules','⇤'],['Network interfaces','▣'],['Subnets','▦'],['Properties','▤'],['Locks','▱'],['MONITORING',''],['Insights','◷'],['Diagnostic settings','▤']];
 const menu=isVnet?[['Overview','◉'],['Activity log','▤'],['Access control (IAM)','♙'],['Tags','◇'],['Diagnose and solve problems','⌕'],['SETTINGS',''],['Address space','▦'],['Subnets','▦'],['DNS servers','▤'],['Peerings','⇄'],['Properties','▤'],['Locks','▱'],['MONITORING',''],['Insights','◷'],['Diagnostic settings','▤']]:nsgMenu;
 menu.forEach(([name,icon],i)=>{const y=158+i*32;if(y>654)return;if(name==='SETTINGS'||name==='MONITORING'){s+=tx(20,y,name,13,'#767676',650);return;}const selected=name===active;if(selected)s+=`<rect x="0" y="${y-22}" width="225" height="31" fill="#e5f1fb"/><rect x="0" y="${y-22}" width="4" height="31" fill="${C.blue}"/>`;s+=tx(18,y,icon,19,selected?C.blue:'#6a6a6a')+tx(46,y,name, name.length>23?13.8:16,selected?'#005a9e':'#444',selected?600:400);});
 s+=tx(249,76,'Home',16,C.blue)+tx(302,76,'›',18,'#888')+tx(322,76,isVnet?'Virtual networks':'Network security groups',16,C.blue)+tx(530,76,'›',18,'#888')+tx(551,76,ellipsis(resource,52),16,'#616161');
 s+=shield(252,93,35)+tx(300,115,ellipsis(resource,67),27,C.ink,600)+tx(300,137,isVnet?'Virtual network':'Network security group',16,'#666')+tx(1543,107,'×',26,'#666');
 return s;
}
const columnSpecs=[['priority','Priority'],['name','Name'],['destinationPort','Port'],['protocol','Protocol'],['source','Source'],['destination','Destination'],['access','Action']];
// Ports carry the decision values: keep every number visible rather than ellipsizing.
function portCell(x,y,value,width,narrow){
 const v=str(value),available=width-18,base=narrow?17:18.5;
 if(units(v)*base<=available)return tx(x+9,y+27,v,base);
 if(!narrow)return tx(x+9,y+27,v,Math.min(16,available/Math.max(1,units(v))));
 const font=14.3,maxUnits=available/font;
 let lines;
 if(/[,・]/.test(v)){
  const parts=v.split(/[,・]\s*/);lines=[''];
  for(const part of parts){const i=lines.length-1,candidate=lines[i]?lines[i]+','+part:part;if(units(candidate)<=maxUnits||!lines[i])lines[i]=candidate;else lines.push(part);}
 }else if(v.includes('-')){const at=v.indexOf('-');lines=[v.slice(0,at),v.slice(at)];}
 else lines=wrap(v,maxUnits);
 if(lines.length>2){const at=Math.ceil(v.length/2);lines=[v.slice(0,at),v.slice(at)];}
 const fitted=Math.min(font,...lines.map(line=>available/Math.max(1,units(line))));
 return lines.map((line,i)=>tx(x+9,y+(lines.length===1?27:15+i*18),line,fitted)).join('');
}
function normalizeRule(raw={}){return {...raw,name:str(raw.name),priority:str(raw.priority),source:str(raw.source||'Any'),sourcePort:str(raw.sourcePort||'*'),destination:str(raw.destination||'Any'),destinationPort:str(raw.destinationPort||'*'),protocol:str(raw.protocol||'Any'),access:str(raw.access||'Allow')};}
function ruleTable(spec,area,registry){
 const {x,y,width,height}=area,narrow=width<1100,headerY=y+43,rowY=headerY+35,rowH=41,maxRows=Math.max(1,Math.floor((height-97)/rowH));
 const rows=(spec.rows||[]).map(normalizeRule),selected=spec.rule?normalizeRule(spec.rule):null;
 const requested=(spec.focus||[]).filter(x=>x.startsWith('row:')).map(x=>x.slice(4));
 const targetIndex=rows.findIndex(r=>requested.includes(r.name)||(selected&&r.name===selected.name));
 let offset=0;if(rows.length>maxRows&&targetIndex>=maxRows)offset=Math.min(rows.length-maxRows,Math.max(0,targetIndex-Math.floor(maxRows/2)));
 const visible=rows.slice(offset,offset+maxRows);
 const weights=narrow?[.10,.22,.10,.10,.16,.21,.11]:[.07,.25,.10,.10,.17,.21,.10];
 const starts=[x];for(let i=0;i<weights.length;i++)starts.push(starts[i]+width*weights[i]);
 let s=box(x,y,width,height,'#fff','#d9d9d9');
 s+=box(x+14,y+10,Math.min(425,width-28),29,'#fff','#b7b7b7',2)+tx(x+26,y+30,'Filter by rule name or IP address',16,'#7b7b7b');
 s+=`<rect x="${x}" y="${headerY}" width="${width}" height="35" fill="#fafafa"/>`;
 columnSpecs.forEach(([key,label],i)=>{const cx=starts[i],cw=width*weights[i];s+=tx(cx+9,headerY+24,label,narrow?16.5:18,'#3b3b3b',600);registry.set(`column:${key}`,rect(`column:${key}`,cx+2,headerY,cw-4,35+visible.length*rowH,label));});
 visible.forEach((rule,i)=>{
  const ry=rowY+i*rowH,sel=selected?.name===rule.name,requestedRow=requested.includes(rule.name);
  if(sel||requestedRow)s+=`<rect x="${x}" y="${ry}" width="${width}" height="${rowH}" fill="#e9f3fc"/>`;
  s+=path(`M${x} ${ry+rowH}H${x+width}`,'#ececec',1);
  columnSpecs.forEach(([key],ci)=>{
   const cx=starts[ci],cw=width*weights[ci],value=rule[key];let color=key==='name'?C.blue:C.ink;
   if(key==='access')color=rule.access==='Deny'?C.red:C.green;
   const size=narrow?17:18.5;
   if(key==='destinationPort')s+=portCell(cx,ry,value,cw,narrow);
   else s+=tx(cx+9,ry+27,ellipsis(value,(cw-18)/size),size,color,key==='priority'?500:400);
  });
  const r=rect(`row:${rule.name}`,x+2,ry+1,width-4,rowH-2,rule.name);registry.set(r.id,r);
 });
 if(!rows.length)s+=tx(x+26,rowY+60,'No rule rows in this view',19,'#777');
 const detail=rows.length>visible.length?`Showing ${offset+1}–${offset+visible.length} of ${rows.length} rules`:`${rows.length} security rules`;
 s+=tx(x+15,y+height-14,detail,16,'#6c6c6c');
 if(rows.length>visible.length){s+=box(x+width-9,rowY,5,maxRows*rowH,'#f0f0f0','#f0f0f0',2);s+=box(x+width-9,rowY+(offset/Math.max(1,rows.length-visible.length))*(maxRows*rowH-90),5,90,'#b4b4b4','#b4b4b4',2);}
 registry.set('table',rect('table',x,y,width,height,'Security rules'));
 return s;
}
function bladeHeader(x,title,subtitle=''){
 return box(x,145,1600-x,535,'#fff','#c7c7c7')+`<rect x="${x}" y="145" width="3" height="535" fill="#e5e5e5"/>`+tx(x+24,177,title,24,C.ink,600)+tx(1560,176,'×',25,'#666')+(subtitle?tx(x+24,201,subtitle,16,'#666'):'')+path(`M${x} 213H1600`,'#e7e7e7',1);
}
function inputField(x,y,label,value,key,registry,opts={}){
 const width=476,lw=159,controlX=x+lw,controlW=width-lw,height=opts.height||42;
 const labelRows={'Source port ranges':['Source port','ranges'],'Destination port ranges':['Destination port','ranges'],'Network security group':['Network security','group'],'Network policy for private endpoints':['Private endpoint','network policy']};
 const labellines=labelRows[label]||wrap(label,9).slice(0,2);let s=labellines.map((l,i)=>tx(x,y+(labellines.length>1?14+i*15:21),l,labellines.length>1?14.5:16.5,'#484848')).join('');
 s+=box(controlX,y+2,controlW,height-8,'#fff','#8e8e8e',1);
 const vals=(str(value).includes(' / ')?str(value).split(' / ').flatMap(v=>wrap(v,(controlW-20)/18)):wrap(value,(controlW-20)/18)).slice(0,2),font=18;
 s+=vals.map((v,i)=>tx(controlX+9,y+24+i*21,v,font,C.ink)).join('');
 registry.set(`field:${key}`,rect(`field:${key}`,controlX-3,y,controlW+6,height-4,label));
 return {svg:s,height};
}
function ruleBlade(spec,registry){
 const x=1078,rule=normalizeRule(spec.rule||(spec.rows||[])[0]||{});let s=bladeHeader(x,'Security rule',ellipsis(rule.name,44)),y=222;
 const fields=[['Source',rule.source,'source',53],['Source port ranges',rule.sourcePort,'sourcePort',39],['Destination',rule.destination,'destination',53],['Destination port ranges',rule.destinationPort,'destinationPort',43],['Protocol',rule.protocol,'protocol',39],['Action',rule.access,'access',39],['Priority',rule.priority,'priority',39],['Name',rule.name,'name',53]];
 for(const [label,value,key,height] of fields){const f=inputField(x+23,y,label,value,key,registry,{height});s+=f.svg;y+=f.height;}
 s+=tx(x+24,607,'Description',16.5,'#484848')+tx(x+183,607,'Not captured',16,'#666');
 s+=path(`M${x} 626H1600`,'#ddd',1)+box(x+24,638,78,29,C.blue,C.blue,2)+tx(x+45,658,'Save',16,'#fff',600)+box(x+113,638,89,29,'#fff','#888',2)+tx(x+130,658,'Discard',16,'#444');
 registry.set('detail',rect('detail',x+6,216,510,427,'Security rule details'));
 return s;
}
function auxiliaryPane(spec,registry){
 const x=1078,w=522,kind=spec.kind;let title=kind==='config'?'リポジトリ設定':kind==='flow'?'通信の追跡（説明図）':kind==='quiz'?'確認問題':'設定の読み方';
 let s=bladeHeader(x,title,kind==='config'?'Azure Portalの設定項目とは分けて表示':'教材に基づく説明・確認用の補助ペイン');
 if(kind==='config'){
  const cfg=spec.config||{},filenameRows=str(cfg.filename||'Repository configuration').split(/\s[+＋]\s/).flatMap(v=>wrap(v,25)).slice(0,2),filenameHeight=Math.max(35,filenameRows.length*23+13),codeY=229+filenameHeight+12;
  s+=box(x+20,229,w-40,filenameHeight,'#f0f3f7','#d9dfe5',2)+filenameRows.map((v,i)=>tx(x+32,253+i*23,v,17.5,'#333',600)).join('');
  const raw=Array.isArray(cfg.lines)?cfg.lines:[str(cfg.lines||spec.note||'')];
  const allLines=raw.flatMap((line,index)=>wrap(line,22).map((text,part)=>({text,index,part})));
  const requestedLine=(spec.focus||[]).map(v=>/^line:(\d+)$/.exec(v)).find(Boolean);
  const targetLine=requestedLine?allLines.findIndex(v=>v.index===Number(requestedLine[1])-1):-1;
  const offset=targetLine>9?Math.min(Math.max(0,allLines.length-12),targetLine-5):0;
  const lines=allLines.slice(offset,offset+(filenameRows.length>1?11:12));
  s+=box(x+20,codeY,w-40,Math.max(100,lines.length*25+24),'#f8f9fb','#d9dfe5',2);
  lines.forEach((line,i)=>{
   s+=tx(x+31,codeY+26+i*25,line.part?'':String(line.index+1).padStart(2,'0'),14,'#a2a2a2')+tx(x+63,codeY+26+i*25,line.text,17.2,'#23445d',400,'class="mono"');
   const key=`line:${line.index+1}`,old=registry.get(key);
   registry.set(key,old?{...old,height:old.height+25}:rect(key,x+57,codeY+6+i*25,w-86,25,str(raw[line.index])));
  });
  if(cfg.note)s+=paragraph(x+24,626,cfg.note,w-72,17,22,'#666',2);
  registry.set('config',rect('config',x+17,225,w-34,415,'Repository configuration'));
  registry.set('field:config',registry.get('config'));
 }else if(kind==='flow'){
  const fl=spec.flow||{},nodes=fl.nodes||[],count=Math.min(nodes.length,4),nh=count>3?67:83,gap=count>3?19:28,top=235;
  nodes.slice(0,4).forEach((n,i)=>{
   const yy=top+i*(nh+gap),status=str(n.status),deny=/deny|拒否|block/i.test(status),ok=/allow|許可|success|応答/i.test(status),stroke=deny?'#c4314b':ok?'#4b9151':'#76a8ca';
   s+=box(x+40,yy,w-80,nh,'#f7fafc',stroke,5)+tx(x+58,yy+27,n.label||n.name||`Step ${i+1}`,21,C.ink,600);
   s+=paragraph(x+58,yy+53,n.detail||status,w-118,17,20,'#5e646b',nh>75?2:1);
   if(i<count-1)s+=path(`M${x+w/2} ${yy+nh+3}V${yy+nh+gap-4}`,C.blue,2,'marker-end="url(#arrow)"');
   const rr=rect(`node:${i}`,x+35,yy-3,w-70,nh+6,n.label);registry.set(rr.id,rr);registry.set(`node:${n.id||n.label}`,rr);registry.set(`flow:${i}`,rr);
  });
  if(fl.explanation)s+=paragraph(x+27,Math.min(620,top+count*(nh+gap)+5),fl.explanation,w-54,17,23,'#525e69',2);
  registry.set('flow',rect('flow',x+20,225,w-40,421,'Communication explanation'));
 }else{
  const question=str(spec.question||spec.title||'この通信は、どの規則に一致しますか？');
  s+=paragraph(x+27,252,question,w-54,22,33,C.ink,6);
  const questionLines=Math.min(6,wrap(question,(w-54)/22).length),resultY=270+questionLines*33;
  if(spec.result){s+=box(x+24,resultY,w-48,90,'#eff7ef','#87b38a',4)+paragraph(x+40,resultY+31,str(spec.result),w-80,21,29,'#225c29',2);}
  const note=spec.note||spec.explanation||'';if(note)s+=paragraph(x+27,Math.min(580,resultY+126),note,w-54,19,28,'#5a626b',4);
  registry.set('question',rect('question',x+20,223,w-40,Math.min(254,questionLines*33+33),'Question'));
  registry.set('result',rect('result',x+21,resultY-3,w-42,96,'Result'));
  registry.set('concept',rect('concept',x+17,220,w-34,420,'Explanation'));
 }
 return s;
}
function subnetScene(spec,registry){
 const subs=spec.subnets||spec.rows||[],selected=spec.subnet||subs.find(n=>(spec.focus||[]).includes(`row:${n.name}`))||subs[0]||{},x=248,y=202,w=812,rowH=48;
 let s=toolbar(spec)+tx(265,203,'Subnets',20,C.ink,600)+box(x,219,w,422,'#fff','#ddd');
 const columns=[['name','Subnet',.36],['prefix','Address range',.27],['nsg','Network security group',.37]],positions=[x,x+w*.36,x+w*.63];
 columns.forEach(([key,label,f],i)=>{s+=tx(positions[i]+12,248,label,16.5,C.ink,600);registry.set(`column:${key}`,rect(`column:${key}`,positions[i],221,w*f,385,label));});
 subs.slice(0,7).forEach((n,i)=>{const yy=263+i*rowH,sel=n.name===selected.name;if(sel)s+=box(x+1,yy,w-2,rowH,'#e9f3fc','#e9f3fc');s+=path(`M${x} ${yy+rowH}H${x+w}`,'#ececec',1);const values=[n.name,n.prefix||n.addressPrefix,n.nsg??n.networkSecurityGroup??'None'];values.forEach((v,j)=>s+=tx(positions[j]+12,yy+30,ellipsis(v,(w*columns[j][2]-25)/18),18,j===0?C.blue:C.ink));registry.set(`row:${n.name}`,rect(`row:${n.name}`,x+1,yy+1,w-2,rowH-2,n.name));});
 s+=tx(x+16,628,`${subs.length} subnets`,16,'#777');
 s+=bladeHeader(1078,'Edit subnet',ellipsis(selected.name||'Subnet association',44));
 const fields=[['Virtual network',spec.vnet||'vnet-<stem>','vnet',48],['Name',selected.name||spec.resource,'name',48],['Address range',selected.prefix||selected.addressPrefix||'—','prefix',48],['Network security group',selected.nsg??selected.networkSecurityGroup??'None','nsg',61],['Network policy for private endpoints',selected.privateEndpointNetworkPolicies==='NetworkSecurityGroupEnabled'?'Network security groups':selected.privateEndpointNetworkPolicies||'Disabled','privateEndpointNetworkPolicies',68]];
 let yy=227;for(const [label,value,key,height] of fields){const f=inputField(1101,yy,label,value,key,registry,{height});s+=f.svg;yy+=height;}
 s+=box(1100,yy+12,475,92,'#f3f7fb','#ccdce9',3)+tx(1113,yy+36,'リポジトリ定義との対応',17,'#3f5d70',600)+paragraph(1113,yy+60,selected.privateEndpointNetworkPolicies?`privateEndpointNetworkPolicies:
${selected.privateEndpointNetworkPolicies}`:spec.note||'NSGの関連付けと、PEへ適用する設定を区別する。',448,17,22,'#4f6373',2);
 s+=path('M1078 626H1600','#ddd',1)+box(1102,638,78,29,C.blue,C.blue,2)+tx(1123,658,'Save',16,'#fff',600)+box(1191,638,89,29,'#fff','#888',2)+tx(1208,658,'Discard',16,'#444');
 registry.set('table',rect('table',x,219,w,422,'Subnets'));
 return s;
}
function teachingMemo(spec,area,registry){
 const {x,y,width,height}=area,title=str(spec.title||'通信条件と一致する規則を確認'),note=str(spec.note||'');
 let s=box(x,y,width,height,'#f0f7fc','#b9d6ec',3);
 s+=tx(x+15,y+23,'教材メモ',15,'#35627e',650);
 const titleRows=wrap(title,(width-135)/19).slice(0,2);
 titleRows.forEach((row,i)=>s+=tx(x+111,y+25+i*24,row,19,'#174d70',600));
 const bodyY=y+52+(titleRows.length-1)*24,maxLines=Math.max(1,Math.floor((height-(bodyY-y)-9)/23)+1);
 s+=paragraph(x+16,bodyY,note,width-32,18,23,'#3d5565',maxLines);
 registry.set('note',rect('note',x+3,y+2,width-6,height-4,'Teaching note'));
 return s;
}
function stableKey(spec){
 const clean={resource:spec.resource||'nsg',kind:spec.kind||'rules',direction:spec.direction||'Inbound'};
 if(spec.kind==='subnets'){
  clean.subnets=(spec.subnets||spec.rows||[]).map(v=>({name:v.name,prefix:v.prefix||v.addressPrefix,nsg:v.nsg??v.networkSecurityGroup??'None',privateEndpointNetworkPolicies:v.privateEndpointNetworkPolicies}));
  clean.subnet=spec.subnet?.name||clean.subnets[0]?.name||null;
 }else{
  clean.rows=(spec.rows||[]).map(r=>{const n=normalizeRule(r);return columnSpecs.map(([key])=>n[key]);});
  if(spec.kind==='detail')clean.rule=spec.rule?.name||(spec.rows||[])[0]?.name||null;
  if(spec.kind==='config')clean.filename=spec.config?.filename||null;
  if(spec.kind==='flow')clean.nodes=(spec.flow?.nodes||[]).map(n=>[n.id||n.label,n.status]);
  if(spec.kind==='quiz')clean.question=str(spec.question||'');
 }
 const raw=JSON.stringify(clean);let h=2166136261;
 for(let i=0;i<raw.length;i++){h^=raw.charCodeAt(i);h=Math.imul(h,16777619);}
 return `${spec.resource||'nsg'}:${spec.kind||'rules'}:${spec.direction||'Inbound'}:${(h>>>0).toString(16)}`;
}
export function renderPortalScene(input={}){
 const spec={...input,kind:input.kind||'rules'},registry=new Map();let body=chrome(spec);
 registry.set('body',rect('body',241,146,1339,516,'Azure Portal content'));
 if(spec.kind==='subnets')body+=subnetScene(spec,registry);
 else{
  const aux=spec.kind!=='rules',width=aux?812:1332;
  body+=toolbar(spec,!aux);
  body+=ruleTable(spec,{x:248,y:201,width,height:aux?322:333},registry);
  body+=teachingMemo(spec,{x:248,y:aux?535:546,width,height:aux?132:121},registry);
  if(spec.kind==='detail')body+=ruleBlade(spec,registry);
  else if(aux)body+=auxiliaryPane(spec,registry);
 }
 const aliases={sourcePortRange:'sourcePort',sourcePortRanges:'sourcePort',destinationPortRange:'destinationPort',destinationPortRanges:'destinationPort',port:'destinationPort',action:'access',networkSecurityGroup:'nsg',addressPrefix:'prefix'};
 const wanted=(Array.isArray(spec.focus)?spec.focus:[spec.focus]).filter(Boolean);
 const focusRects=[];
 for(const id of wanted){let value=registry.get(id);if(!value&&id.includes(':')){const [prefix,key]=id.split(':');value=registry.get(`${prefix}:${aliases[key]||key}`);}if(!value&&id.startsWith('pane:'))value=registry.get(id.slice(5));if(!value&&id==='note')value=registry.get('concept')||registry.get('config')||registry.get('detail');if(value)focusRects.push({...value,id});}
 if(!focusRects.length)focusRects.push(registry.get('body'));
 const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><defs><style>text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Hiragino Kaku Gothic ProN","Yu Gothic",sans-serif}.mono{font-family:Menlo,Consolas,monospace}</style><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10Z" fill="${C.blue}"/></marker></defs>${body}</svg>`;
 return {svg,width:W,height:H,focusRects,viewKey:stableKey(spec)};
}
