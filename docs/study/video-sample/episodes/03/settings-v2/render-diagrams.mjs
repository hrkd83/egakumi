// Episode 03 only. Fixed teaching settings views; no live Azure screen or camera motion.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {renderSettingsShell,renderSettingsSvg} from '../../../settings-visual.mjs';
const here=path.dirname(fileURLToPath(import.meta.url));
const episode=path.dirname(here);
const script=JSON.parse(await fs.readFile(path.join(episode,'script.json'),'utf8'));
const original=JSON.parse(await fs.readFile(path.join(episode,'versions/diagram-v1/script.json'),'utf8'));
const E=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const C={ink:'#20323e',muted:'#647984',accent:'#087f8c',red:'#de343b',line:'#d5e0e5',pale:'#e9f2f5'};
const font='Hiragino Sans,Yu Gothic,Meiryo,sans-serif';
const units=s=>[...String(s)].reduce((n,c)=>n+(/[\u0000-\u007f]/.test(c)?.56:1),0);
const rect=(x,y,w,h,fill='#fff',stroke=C.line,r=4)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}"/>`;
const txt=(x,y,s,size=18,color=C.ink,weight=400,anchor='start')=>`<text x="${x}" y="${y}" font-family="${font}" font-size="${size}" fill="${color}" font-weight="${weight}" text-anchor="${anchor}">${E(s)}</text>`;
function textBlock(x,y,w,h,s,size=18,color=C.ink,max=2){
  const parts=[];let line='';
  for(const c of String(s)){if(c==='\n'){parts.push(line);line='';continue;}if(line&&units(line+c)*size>w){parts.push(line);line=c;}else line+=c;}parts.push(line);
  if(parts.length>max)throw Error(`Text overflow ${s}: ${parts.length} lines in ${w} × ${h}`);
  if(parts.length*(size+4)>h+8)throw Error(`Text height overflow ${s}`);
  const first=y+h/2+size*.34-(parts.length-1)*(size+4)/2;
  return parts.map((p,i)=>txt(x,first+i*(size+4),p,size,color)).join('');
}
const range=(a,b)=>Array.from({length:b-a+1},(_,i)=>a+i);
const base=(id,title,section,more={})=>({viewId:id,areaLabel:'ネットワーク設定 / NSG',heading:title,title,kind:'NSGによる通信制御',section,menu:['関連付け','受信規則','送信規則','状態追跡','構築入力','確認問題'],subtitle:'公開設定例と教材の代表通信を対応付ける',footer:'教材用の設定ビュー / 稼働環境の取得値ではありません',...more});
const groups=[];
function native(id,ns,title,section,data,focus={},qa={}){
  const v={layout:'settings',...base(id,title,section),...data};
  groups.push({id,ns,v,focus,qa});
}
function composite(id,ns,title,section,build,focus={},qa={}){
  const targets={};const register=(id,x,y,w,h)=>{if(targets[id])throw Error(`Duplicate target ${id}`);targets[id]={x,y,w,h};};
  const content=build(register);
  groups.push({id,ns,v:{layout:'svg',...base(id,title,section)},content,targets,focus,qa});
}
function tablePanel(reg,id,x,y,w,title,columns,rows,{rowH=43,titleH=29,fontSize=18,fieldStyle=false}={}){
  let out=rect(x,y,w,titleH,C.pale)+txt(x+12,y+20,title,17,C.ink,650);
  const widths=columns.map(c=>c[1]);if(Math.abs(widths.reduce((a,b)=>a+b,0)-w)>1)throw Error(`Panel widths ${id}`);
  let yy=y+titleH;
  if(!fieldStyle){let xx=x;out+=rect(x,yy,w,26,'#f1f5f7','#e0e6ec',0);columns.forEach(([label,width])=>{if(units(label)*15>width-20)throw Error(`Column heading overflow ${id}/${label}`);out+=txt(xx+10,yy+18,label,15,C.muted,650);xx+=width;});yy+=26;}
  rows.forEach((r,i)=>{out+=rect(x,yy,w,rowH,'#fff','#e0e6ec',0);let xx=x;
    r[1].forEach((s,j)=>{out+=textBlock(xx+10,yy,widths[j]-20,rowH,s,fontSize,fieldStyle?(j?C.accent:C.muted):(j===0?C.accent:C.ink),2);xx+=widths[j];});
    reg(`${id}:${r[0]}`,x+2,yy+2,w-4,rowH-4);yy+=rowH;
  });return out;
}
function fieldPanel(reg,id,x,y,w,title,rows,{rowH=42,labelW=135,size=18}={}){
  return tablePanel(reg,id,x,y,w,title,[['項目',labelW],['値',w-labelW]],rows,{rowH,fontSize:size,fieldStyle:true});
}
function node(reg,id,x,y,w,h,title,detail){reg(id,x,y,w,h);return rect(x,y,w,h,'#fff','#a8c8ce',6)+txt(x+w/2,y+27,title,21,C.accent,650,'middle')+textBlock(x+12,y+31,w-24,h-31,detail,17,C.muted,2);}
function arrow(x1,y1,x2,y2,label,{dashed=false,color='#5a8794',labelY}={}){
 const dx=x2-x1,dy=y2-y1,len=Math.hypot(dx,dy),ux=dx/len,uy=dy/len;
 return `<path d="M${x1} ${y1}L${x2} ${y2}" fill="none" stroke="${color}" stroke-width="2.5"${dashed?' stroke-dasharray="7 5"':''}/><path d="M${x2-ux*10-uy*5} ${y2-uy*10+ux*5}L${x2} ${y2}L${x2-ux*10+uy*5} ${y2-uy*10-ux*5}" fill="none" stroke="${color}" stroke-width="2.5"/>`+(label?txt((x1+x2)/2,labelY??(y1+y2)/2-10,label,17,C.muted,500,'middle'):'');
}
const smallNote=(x,y,w,s)=>rect(x,y,w,36,'#edf5fc','#c3dce9')+textBlock(x+10,y,w-20,36,s,17,'#315a70',1);
const tbl=(labels,widths,rows)=>({columns:labels.map((label,i)=>({label,width:widths[i]})),rows:rows.map(([id,...cells])=>({id,cells}))});
const f=(id,label,value)=>({id,label,value});

native('nsg-associations',range(5,7),'NSGを関連付ける四つの区画','関連付け',{
 notice:'名前解決の次に、適用する通信制御を選ぶ',
 table:tbl(['区画','関連付けるNSG','役割'],[230,335,407],[
  ['agw','Application Gateway','AppGW用NSG','公開入口と内部中継'],['apim','API Management','APIM用NSG','API処理・基盤通信'],['pe','Private Endpoint','PE用NSG','サービス別のprivate接続'],['vm','管理VM','管理VM用NSG','管理作業と依存先']])
},{7:['agw','apim','pe','vm']});

native('six-subnets',range(8,14),'六つの区画とNSGの関連付け','関連付け',{
 table:tbl(['区画','公開paramsの範囲','NSG','教材注釈'],[230,240,190,312],[
 ['agw','AppGW','10.40.0.0/24','あり','公開入口'],['pe','Private Endpoint','10.40.1.0/24','あり','実PE IP群は作成後取得'],['apim','APIM','10.40.2.64/26','あり','専用区画を規則で指定'],['vm','管理VM','10.40.2.128/26','あり','単体の実IPとは区別'],['aca','Container Apps','10.40.2.192/26','なし','接続先NSGなども確認'],['fw','Azure Firewall','10.40.2.0/26','なし','Firewallポリシーで制御']])
},{9:['agw','pe','apim'],10:['vm','aca','fw'],11:['pe','vm'],13:['aca','fw'],14:['aca','fw']});

composite('two-directions',range(15,18),'一つの接続を、出る側と入る側で見る','関連付け',r=>{
 let s=node(r,'apim',245,165,320,82,'APIM','10.40.2.64/26');s+=node(r,'pe',835,165,320,82,'Foundry PE','作成後に取得する実PE IP群');s+=arrow(575,207,825,207,'新規 TCP 443');
 s+=fieldPanel(r,'out',233,270,458,'APIM側の規則',[['direction',['方向','送信 / Outbound']],['nsg',['関連付け','APIM用NSG']]],{rowH:47,labelW:120});
 s+=fieldPanel(r,'in',711,270,458,'PE側の規則',[['direction',['方向','受信 / Inbound']],['policy',['NSG policy','NSGを有効化']]],{rowH:47,labelW:140});
 s+=smallNote(233,412,936,'関連付けに加えて、PEへNSGを適用するネットワークポリシーも確認');return s;
},{16:['out:direction','in:direction'],17:['out:direction','in:direction'],18:['in:policy']});

native('rule-conditions',range(19,26),'AppGW受信：一行の条件をそろえる','受信規則',{
 notice:'全行 Allow / 送信元ポート * / 表示するポートは宛先',
 table:tbl(['優先度','送信元','宛先','Protocol','宛先ポート'],[105,280,225,130,232],[
 ['web','100','0.0.0.0/0','10.40.0.0/24','TCP','80, 443'],['manager','110','GatewayManager','*','TCP','65200–65535'],['lb','120','AzureLoadBalancer','*','Any','*']])
},{20:['web'],21:['web'],22:['manager','lb'],23:['web','manager','lb'],24:['web'],26:['web']});

composite('priority-order',range(27,34),'小さい優先度から、最初の一致で決める','受信規則',r=>{
 let s=tablePanel(r,'priority',229,151,480,'例：個別許可 → 独自拒否 → 既定',[['優先度',110],['条件と判定',370]],[
 ['allow',['100','個別条件に一致 → Allow']],['deny',['4096','任意 → Deny（終端拒否）']],['default',['65000〜','Azureの既定ルール']]],{rowH:60,fontSize:19});
 s+=tablePanel(r,'directions',729,151,442,'区画ごとの独自4096',[['区画',190],['受信',126],['送信',126]],[
 ['agw',['AppGW','あり','なし']],['apim',['APIM','あり','あり']],['pe',['PE','あり','なし']],['vm',['管理VM','あり','あり']]],{rowH:45,fontSize:18});
 s+=smallNote(229,413,942,'各NSGの同じ方向で評価し、最初の一致より後ろへは進まない');return s;
},{28:['priority:allow'],29:['priority:allow'],31:['priority:deny'],33:['priority:deny','priority:default'],34:['directions:agw','directions:apim','directions:pe','directions:vm']});

function pairedRules(r,{leftTitle,rightTitle,leftRule,rightRule,src,dst,port,note}){
 let s=node(r,'src',238,155,310,74,src[0],src[1]);s+=node(r,'dst',854,155,310,74,dst[0],dst[1]);s+=arrow(560,191,842,191,port);
 const rows=rule=>[['name',['規則名',rule.name]],['priority',['優先度・判定',`${rule.priority} / ${rule.access}`]],['source',['送信元',rule.source]],['dest',['宛先',rule.dest]]];
 s+=fieldPanel(r,'left',229,246,466,leftTitle,rows(leftRule),{rowH:39,labelW:128,size:18});
 s+=fieldPanel(r,'right',713,246,466,rightTitle,rows(rightRule),{rowH:39,labelW:128,size:18});
 s+=smallNote(229,438,950,note);return s;
}
composite('agw-apim-http',range(35,39),'AppGW → APIM：TCP 80を両端で照合','受信規則',r=>pairedRules(r,{
 src:['AppGW','10.40.0.0/24'],dst:['APIM','10.40.2.64/26'],port:'新規 TCP 80',
 leftTitle:'AppGW / 送信 Outbound',rightTitle:'APIM / 受信 Inbound',
 leftRule:{name:'allow-apim-http',priority:100,access:'Allow',source:'10.40.0.0/24',dest:'10.40.2.64/26'},
 rightRule:{name:'allow-agw-api',priority:100,access:'Allow',source:'10.40.0.0/24',dest:'10.40.2.64/26'},
 note:'二つの100は別NSGの規則。同じ通信条件へ、それぞれ一致する'}),
 {36:['left:name','left:priority','left:source','left:dest'],37:['right:priority'],38:['right:name','right:priority','right:source','right:dest'],39:['left:priority','right:priority']});

native('agw-outbound',range(40,42),'AppGWの送信規則と既定許可','送信規則',{
 notice:'AppGWは送信4096なし。ACA向けは経路上のFirewallも確認',
 table:tbl(['優先度','宛先','Protocol / 宛先Port','判定'],[125,345,312,190],[
 ['apim','100','APIM区画','TCP / 80','Allow'],['kv','110','Key Vault PE実IP群','TCP / 443','Allow'],['grpc','120','APIM区画','TCP / 443','Allow'],['internet','200','Internet','Any / *','Allow'],['vnet','65000','VirtualNetwork','Any / *','Allow（既定）']])
},{40:['kv','grpc','internet'],41:['internet'],42:['vnet']});

native('apim-subnet-target',range(43,44),'APIM宛先はVIP一個ではなく専用区画','受信規則',{
 fields:[f('destination','宛先アドレス','10.40.2.64/26（APIM専用区画）'),f('source','通常APIの送信元','10.40.0.0/24（AppGW区画）'),f('port','通常APIのProtocol / Port','TCP / 80'),f('purpose','教材注釈：区画を使う理由','内部LB背後のノードも含める')]
},{44:['destination','purpose']});

native('vm-apim-comparison',range(45,47),'管理VM → APIM：80と443を比較','受信規則',{
 notice:'標準例・追加管理規則なし・gRPC有効 / 送信元は管理VM',
 table:tbl(['通信','管理VMの送信','APIMの受信','判定の根拠'],[165,275,275,257],[
 ['http','TCP 80','4096 / Deny','該当許可なし','内部同士でも拒否'],['grpc','TCP 443','120 / Allow','150 / Allow','gRPC比較用接続']])
},{46:['http'],47:['grpc']});

native('apim-inbound',range(48,49),'APIM受信：API・管理・基盤通信を分ける','受信規則',{
 table:tbl(['優先度','送信元','Protocol / 宛先Port','用途'],[110,310,260,292],[
 ['api','100','AppGW区画','TCP / 80','通常API'],['management','110','ApiManagement','TCP / 3443','基盤管理'],['lb','120','AzureLoadBalancer','TCP / 6390','正常性確認'],['sync','130','APIM区画','UDP / 4290','カウンター同期'],['agwgrpc','140','AppGW区画','TCP / 443','gRPC'],['vmgrpc','150','管理VM実IP','TCP / 443','gRPC比較用']])
},{48:['management','lb','sync'],49:['api','agwgrpc','vmgrpc']});

composite('apim-foundry',range(50,54),'APIM → Foundry PE：実IP群を両端で指定','受信規則',r=>pairedRules(r,{
 src:['APIM','10.40.2.64/26'],dst:['Foundry PE','NICから取得した実IP群'],port:'新規 TCP 443',
 leftTitle:'APIM / 送信 Outbound',rightTitle:'PE / 受信 Inbound',
 leftRule:{name:'allow-private-backends',priority:100,access:'Allow',source:'10.40.2.64/26',dest:'Foundry PE実IP群'},
 rightRule:{name:'allow-apim-foundry',priority:100,access:'Allow',source:'10.40.2.64/26',dest:'Foundry PE実IP群'},
 note:'PE区画にはFoundry・Key Vault・ACR・SQL。区画全体への許可とは分ける'}),
 {51:['left:priority','right:priority'],53:['left:dest','right:dest'],54:['dst']});

native('agw-pe-comparison',range(55,58),'AppGW → PE：同じ443でも宛先で異なる','受信規則',{
 notice:'AppGWには送信終端4096なし / 受信側の一致まで確認',
 table:tbl(['接続先','AppGWの送信','PEの受信','TCP 443の判定'],[215,265,265,227],[
 ['foundry','Foundry PE実IP群','65000 / Allow（既定）','4096 / Deny','拒否'],['kv','Key Vault PE実IP群','110 / Allow','110 / Allow','許可（証明書取得）']])
},{56:['foundry'],57:['kv'],58:['kv']});

composite('pe-service-rules',[...range(59,63),117],'PE受信：接続元・サービス・ポートを分ける','受信規則',r=>{
 let s=tablePanel(r,'pe',225,151,950,'代表受信規則（全行Allow / 送信元ポート *）',[['優先度',88],['送信元',268],['宛先PE実IP群',318],['Protocol / Port',276]],[
 ['kv',['110','AppGW・APIM区画','Key Vault','TCP / 443']],['maintenance',['140','管理VMの実IP','Foundry・Key Vault','TCP / 443']],['acr',['160','ACA・管理VM区画','ACR','TCP / 443']],['sql',['175','ACA区画','SQL','TCP / 1433']],['brokerkv',['177','ACA区画','Key Vault','TCP / 443']]],{rowH:37,fontSize:18});
 s+=smallNote(225,409,950,'管理VM送信100 → Foundry / KV TCP443、170 → ACR TCP443');
 s+=txt(238,471,'教材注釈：ACAにNSGなし。管理VM → SQL TCP1433の標準許可なし。',17,C.muted);
 r('vm-out',225,409,950,36);return s;
},{59:['pe:kv','pe:maintenance'],60:['pe:acr','pe:sql','pe:brokerkv'],61:['pe:sql'],62:['pe:sql'],63:['pe:acr','vm-out'],117:['pe:maintenance','vm-out']});

composite('stateful-connection',range(64,71),'状態追跡：同じ接続への応答と新規接続','状態追跡',r=>{
 let s=node(r,'apim',245,163,300,78,'APIM','接続を開始する側');s+=node(r,'pe',857,163,300,78,'Foundry PE','応答を返す側');
 s+=arrow(555,184,847,184,'① 新規接続 TCP 443',{labelY:167});s+=arrow(847,224,555,224,'② 同じ接続への応答',{labelY:249});
 s+=tablePanel(r,'state',229,273,942,'接続の扱いを区別する',[['通信・確認',320],['評価の考え方',622]],[
 ['response',['許可済み接続への応答','状態追跡で扱う']],['new',['逆向きに開始する別の接続','その方向の規則で評価']],['change',['ルール変更後の確認','既存接続に加え、新規接続でも検証']]],{rowH:49,fontSize:19});return s;
},{65:['state:response'],66:['state:response'],68:['state:new'],70:['state:change'],71:['state:change']});

native('apim-platform-storage',range(72,75),'APIM：API以外の保存・認証・管理通信','送信規則',{
 notice:'送信規則の送信元はAPIM区画 / APIと基盤の依存を区別',
 table:tbl(['方向・優先度','相手','Protocol / 宛先Port','用途'],[180,310,260,222],[
 ['management','受信 110','ApiManagement','TCP / 3443','基盤管理'],['storage','送信 200','Storage','TCP / 443','保存'],['sql','送信 210','Sql','TCP / 1433','基盤SQL'],['kv','送信 220','AzureKeyVault','TCP / 443','基盤Key Vault'],['entra','送信 240','AzureActiveDirectory','TCP / 443','認証'],['cm','送信 250','AzureConnectors','TCP / 443','Credential Manager']])
},{73:['management'],74:['storage','sql','kv'],75:['entra','cm']});

native('apim-platform-monitor',range(76,79),'APIM：監視・ログ・Web・時刻・同期','送信規則',{
 notice:'送信元はAPIM区画 / AzureMonitorとEventHubは別のタグ',
 table:tbl(['優先度','宛先','Protocol / 宛先Port','用途'],[110,285,320,257],[
 ['monitor','230','AzureMonitor','TCP / 1886, 443','監視'],['event','235','EventHub','TCP / 443, 5671, 5672','監査ログ'],['web','260','Internet','TCP / 80, 443','Web依存'],['ntp','270','Internet','UDP / 123','時刻同期'],['sync','280','APIM区画','UDP / 4290','カウンター同期']])
},{77:['monitor','event'],78:['web','ntp','sync'],79:['monitor','event']});

native('service-tags',range(80,83),'サービス・タグとFirewallの分担','送信規則',{
 fields:[f('tag','サービスタグ','Azureが管理するIP範囲のまとまり'),f('scope','教材注釈：表していないもの','リソース一個やFQDN一覧ではない'),f('web','Internet / TCP 443','NSG Allowに加え、経路上のFWの規則も確認'),f('smtp','APIM・管理VM → Internet / TCP 25','標準許可なし → 送信4096 / Deny')]
},{81:['tag','scope'],82:['web'],83:['smtp']});

native('common-dns',range(84,87),'共通DNSの設定と基盤通信の例外','送信規則',{
 fields:[f('tcp','送信400 / Protocol・Port','TCP / 53 → 設定したDNSサーバー'),f('udp','送信410 / Protocol・Port','UDP / 53 → 設定したDNSサーバー'),f('where','規則を追加する区画','AppGW・APIM・管理VM（PEには追加しない）'),f('input','公開入力 dnsServers','[] → 168.63.129.16（Azure提供DNS）'),f('exception','教材注釈：基盤通信','Azure提供DNS等は通常のNSG処理に例外あり')]
},{85:['tcp','udp','where'],86:['exception'],87:['input','tcp','udp']});

composite('vm-platform',range(88,91),'管理VM：作業・監視の依存先と接続条件','送信規則',r=>{
 let s=tablePanel(r,'vm',225,151,950,'管理VM送信 / 代表的な依存先',[['優先度',112],['宛先',352],['Protocol / Port',272],['用途',214]],[
 ['entra',['300','AzureActiveDirectory','TCP / 443','認証']],['arm',['310','AzureResourceManager','TCP / 443','管理API']],['monitor',['320','AzureMonitor','TCP / 443','監視']],['storage',['330','Storage','TCP / 443','エージェント保存']],['web',['350','Internet','TCP / 80, 443','Web依存']],['ntp',['360','Internet','UDP / 123','時刻同期']]],{rowH:31,fontSize:17});
 s+=smallNote(225,402,950,'DNS送信400・410の後、その他は4096 / Deny');r('deny',225,402,950,36);
 s+=txt(237,469,'接続成功まで：DNS → UDR → NSG / Firewall → サービス認証・権限',18,C.muted);r('conditions',225,446,950,30);return s;
},{89:['vm:entra','vm:arm','vm:monitor','vm:storage'],90:['vm:web','vm:ntp','deny'],91:['conditions']});

composite('grpc-switch',range(92,95),'gRPC：公開8443と内部443を一組で切り替える','構築入力',r=>{
 let s=fieldPanel(r,'input',225,151,950,'リポジトリの構築入力（Azureの製品設定名とは区別）',[
 ['enabled',['grpc.enabled','公開例 true / false時は同名規則Deny']],['ports',['ポート','公開 8443 / 内部 443']]],{rowH:46,labelW:210,size:19});
 s+=tablePanel(r,'rules',225,281,950,'対応するNSG規則',[['場所',280],['方向 / 優先度',250],['宛先ポート',190],['有効時',230]],[
 ['public',['AppGW','受信 / 150','8443','Allow（許可元あり）']],['relay',['AppGW・管理VM','送信 / 120','443','Allow']],['receive',['APIM','受信 / 140・150','443','Allow']]],{rowH:35,fontSize:18});
 s+=txt(236,469,'教材注釈：client配列が空なら公開gRPC受信はDeny。名前よりAccessを確認。',17,C.muted);return s;
},{93:['input:enabled'],94:['input:ports','rules:public','rules:relay','rules:receive'],95:['input:ports','rules:public']});

native('vm-operator',range(96,98),'管理VMへの受信：運用元とSSHを絞る','構築入力',{
 fields:[f('prefix','operatorAddressPrefixes','公開例 [] → 管理受信 Deny / 4080'),f('enabled','運用元を指定した場合','指定元 → 管理VM実IP / Allow / 100'),f('protocol','operatorProtocol / operatorPorts','Tcp / ["22"]'),f('additional','managementAdditionalSecurityRules','公開例 []（追加規則なし）'),f('conditions','教材注釈：追加確認','到達経路・追加規則の優先順位・SSH認証')]
},{96:['prefix','enabled'],98:['protocol','additional','conditions']});

native('certificate-probe',[118],'証明書監視194：設定値と通信条件','構築入力',{
 fields:[f('input','certificatePublicProbeEnabled','公開config true → Allow / false → 同名Deny'),f('priority','優先度 / 方向','194 / AppGW受信'),f('source','送信元','Firewall公開IPの /32'),f('dest','宛先','10.40.0.0/24（AppGW区画）'),f('port','Protocol / 宛先Port','TCP / 443')]
},{118:['input','priority','source','dest','port']});

const overlapData=(clientEmpty=false)=>({
 notice:'対象通信：Firewall公開IP /32 → AppGW区画 / 新規 TCP 443',
 table:tbl(['優先度','規則・送信元','宛先Port','Access'],[145,387,190,250],clientEmpty?[
 ['probe','194','監視 / FW公開IP /32','TCP 443','Allow'],['web','4080','Web / client配列が空','TCP 80, 443','Deny'],['deny','4096','その他 / *','Any / *','Deny']
 ]:[['web','100','Web / 0.0.0.0/0','TCP 80, 443','Allow'],['probe','194','監視 / FW公開IP /32','TCP 443','Deny'],['deny','4096','その他 / *','Any / *','Deny']])});
native('probe-priority',[99,100],'監視194がDenyなら、必ず拒否できる？','受信規則',overlapData(false),{100:['web']},{question:[99],answer:[100]});
native('probe-empty-client',[101,102],'Web許可元が空でも、監視194を先に評価','受信規則',overlapData(true),{101:['probe','web'],102:['probe']});

native('key-vault-switch',[103,104],'Key Vault：PE通信と基盤・IAMを分ける','構築入力',{
 fields:[f('input','apimKeyVaultAccessEnabled','公開config true / 実効false時の変化は次の2行'),f('out','APIM送信110 / KV PE向け','false → 同名規則Deny'),f('in','PE受信110 / KV PE向け','false → 許可元からAPIMを除外'),f('platform','基盤向けAzureKeyVault / 送信220','このスイッチで一括変更する対象ではない'),f('iam','Key VaultのIAM権限','ネットワーク接続の許可とは別に確認')]
},{103:['input','out','in'],104:['platform','iam']});

native('broker-conditional',[105,119],'Broker：条件付き176と、常時作成175・177','構築入力',{
 fields:[f('input','credentialProfiles','空でない時だけAPIM送信176を作成'),f('broker','APIM送信176 / Allow','APIM区画 → ACA実内部IP / TCP 443'),f('sql','PE受信175 / Allow','ACA区画 → SQL PE実IP群 / TCP 1433'),f('kv','PE受信177 / Allow','ACA区画 → KV PE実IP群 / TCP 443'),f('residual','教材注釈：無効化後の確認','175・177は常時作成。176の許可残存は実規則確認')]
},{105:['input','broker'],119:['sql','kv','residual']});

native('quiz-vm-apim',range(106,108),'確認1：管理VM → APIM / 新規 TCP 80','確認問題',{
 notice:'標準例・追加管理規則なし・gRPC有効 / 両端の方向を選ぶ',
 table:tbl(['適用箇所','優先度','宛先Port','Access','条件の手がかり'],[210,110,190,132,330],[
 ['vmallow','管理VM / 送信','120','TCP 443','Allow','管理VM実IP → APIM区画'],
 ['vmdeny','管理VM / 送信','4096','Any / *','Deny','その他の送信'],
 ['apimallow','APIM / 受信','150','TCP 443','Allow','管理VM実IP → APIM区画'],
 ['apimdeny','APIM / 受信','4096','Any / *','Deny','その他の受信']])
},{108:['vmdeny','apimdeny']},{question:[106,107],answer:[108]});

composite('quiz-stateful',[109,110],'確認2：許可済みTCP 443への応答','確認問題',r=>{
 let s=node(r,'apim',245,163,300,78,'APIM','新規TCP443を開始');s+=node(r,'pe',857,163,300,78,'Foundry PE','両端で許可済み');
 s+=arrow(555,184,847,184,'新規接続',{labelY:165});s+=arrow(847,224,555,224,'同じ接続への応答',{labelY:249});
 s+=tablePanel(r,'question',229,278,942,'応答に、逆向きAllowを追加する？',[['確認する対象',360],['今回の前提・考えること',582]],[
 ['same',['PE → APIMの応答','許可済みの同じ接続へ返す']],['different',['逆向きに始める新規接続','今回の応答とは分けて考える']],['change',['ルール変更後の検証','既存接続・新規接続のどちらで確認する？']]],{rowH:47,fontSize:18});return s;
},{110:['question:same','question:change']},{question:[109],answer:[110]});

native('quiz-overlap',[111,112],'確認3：Web100と監視194、最初の一致は？','確認問題',overlapData(false),{112:['web']},{question:[111],answer:[112]});

native('summary',range(113,115),'一つの通信を、同じ順番で判定する','関連付け',{
 fields:[f('condition','1. 通信条件を固定','送信元・宛先・ポート・プロトコル'),f('direction','2. 適用箇所と方向を選ぶ','関連付けを確認し、送信側と受信側を見る'),f('priority','3. 最初の一致を探す','小さい優先度からAllow / Denyを判定'),f('state','4. 新規接続か応答かを分ける','変更後は新規接続でも確認'),f('layers','5. 接続に必要な他の条件を確認','DNS・UDR・Firewall・サービス認証・権限'),f('source','根拠と取得元','教材11〜19節・公開設定例・対象環境の入力と実規則')]
},{113:['condition','direction'],114:['direction','priority','state'],115:['source']});

native('next-udr',[116],'次回：許可した通信を、どこへ送るか','送信規則',{
 fields:[f('today','第3回：NSGの問い','その通信を許可するか'),f('next','第4回：UDRの問い','許可した通信を、どこへ送るか'),f('routes','代表経路','Firewall経由とPE直接経路'),f('return','往復の確認','送る経路と戻る経路をそろえる')]
},{116:['next','routes','return']});

await fs.mkdir(path.join(here,'diagrams'),{recursive:true});
const byN=new Map();const viewMap=[];const turnMap=[];
for(const g of groups){
 const turnIds=[];
 for(const n of g.ns){
  if(byN.has(n))throw Error(`Duplicate turn ${n}`);
  const turn=script.turns.find(t=>Number(t.id.match(/turn-(\d+)/)[1])===n);if(!turn)throw Error(`Missing ${n}`);
  const focus=g.focus[n]??[];let v={...g.v};
  if(v.layout==='settings'){
   if(v.fields)v.highlightFields=focus;else v.highlightRows=focus;
   renderSettingsSvg(v); // strict shared text/geometry validation before writing script
  }else{
   let marks='';for(const id of focus){const q=g.targets[id];if(!q)throw Error(`Unknown ${g.id}/${id}`);marks+=`<rect data-target="${id}" x="${q.x}" y="${q.y}" width="${q.w}" height="${q.h}" rx="4" fill="none" stroke="${C.red}" stroke-width="2.8"/>`;}
   const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="1207" height="511" viewBox="0 0 1207 511">${renderSettingsShell(v,g.content)}<g data-highlight="true">${marks}</g></svg>`;
   const file=`${g.id}-${focus.length?focus.join('_').replaceAll(':','-'):'neutral'}.svg`;
   await fs.writeFile(path.join(here,'diagrams',file),svg);
   v={layout:'svg',viewId:g.id,heading:g.v.heading,svg:`settings-v2/diagrams/${file}`};
  }
  turn.visual=v;turnIds.push(turn.id);byN.set(n,g.id);
  turnMap.push({id:turn.id,viewId:g.id,layout:v.layout,highlightTargets:focus});
 }
 viewMap.push({viewId:g.id,turnIds,layout:g.v.layout,questionTurnIds:(g.qa.question??[]).map(n=>script.turns.find(t=>Number(t.id.match(/turn-(\d+)/)[1])===n).id),answerTurnIds:(g.qa.answer??[]).map(n=>script.turns.find(t=>Number(t.id.match(/turn-(\d+)/)[1])===n).id),description:g.v.title});
}
script.visualStyle='teaching-settings-v2';
for(const t of script.turns)if(t.chapterId!=='opening'&&!byN.has(Number(t.id.match(/turn-(\d+)/)[1])))throw Error(`Unmapped ${t.id}`);
const strip=s=>({...s,visualStyle:undefined,turns:s.turns.map(({visual,...t})=>t)});
if(JSON.stringify(strip(script))!==JSON.stringify(strip(original)))throw Error('Non-visual script data changed');
await fs.writeFile(path.join(episode,'script.json'),JSON.stringify(script,null,2)+'\n');
await fs.writeFile(path.join(here,'view-map.json'),JSON.stringify({schemaVersion:1,visualStyle:'teaching-settings-v2',bodyCamera:'fixed',groups:viewMap,turns:turnMap,bodyTurnCount:turnMap.length,noSpeechOrStyleEdits:true,sourceBasis:'docs/study/01-network-control.html sections 11–19 and current repository/public configuration examples',createdAt:new Date().toISOString()},null,2)+'\n');
console.log(JSON.stringify({groups:groups.length,bodyTurns:turnMap.length,nativeSettings:turnMap.filter(t=>t.layout==='settings').length,compositeSettings:turnMap.filter(t=>t.layout==='svg').length,noSpeechEdits:true}));
