// Episode-local Azure Portal teaching replicas. No live Azure data is used.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url));
const W=1207,H=511;
const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const text=(x,y,v,size=17,fill='#242424',weight=400,extra='')=>`<text x="${x}" y="${y}" font-size="${size}" fill="${fill}" font-weight="${weight}" ${extra}>${esc(v)}</text>`;
const rect=(x,y,w,h,fill='#fff',stroke='none',r=0)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}"/>`;
const line=(x,y,x2,y2,color='#e0e0e0')=>`<path d="M${x} ${y}H${x2}" stroke="${color}"/>`;
const box=(x,y,width,height,label,zoom=1.16)=>({x,y,width,height,label,zoom});
const link=(x,y,v,size=17)=>text(x,y,v,size,'#0067b8');
const navLabel=value=>{let out='',u=0;for(const ch of value){const width=/[\u0000-\u007f]/.test(ch)?.56:1;if(u+width>9.8)return out+'…';out+=ch;u+=width;}return out;};
const status=(x,y,v)=>`<circle cx="${x+6}" cy="${y-5}" r="6" fill="#107c10"/>${text(x+21,y,v,17)}`;
function radio(x,y,label,on=false){return `<circle cx="${x+9}" cy="${y-6}" r="9" fill="#fff" stroke="${on?'#0078d4':'#616161'}" stroke-width="1.5"/>${on?`<circle cx="${x+9}" cy="${y-6}" r="5" fill="#0078d4"/>`:''}${text(x+28,y,label,18)}`;}
function input(x,y,w,value){return `${rect(x,y-24,w,36,'#fff','#8a8886',2)}${text(x+12,y,value,18)}`;}
function toggle(x,y,on=true){return `<rect x="${x}" y="${y-20}" width="43" height="23" rx="12" fill="${on?'#0078d4':'#fff'}" stroke="${on?'#0078d4':'#666'}"/><circle cx="${x+(on?31:12)}" cy="${y-8.5}" r="8" fill="${on?'#fff':'#666'}"/>${text(x+56,y,on?'Enabled':'Disabled',18)}`;}
function info(y,rows,{fill='#f3f9fd',stroke='#b5d7ee'}={}){let s=rect(228,y,953,rows.length*25+22,fill,stroke,2)+text(242,y+25,'ⓘ',19,'#0078d4');rows.forEach((v,i)=>s+=text(272,y+25+i*25,v,16,'#3b3a39'));return s;}
function table(columns,rows,{y=202,rowHeight=39,selected=-1}={}){
 let s=rect(225,y,960,34,'#fafafa')+line(225,y+34,1185,y+34);
 columns.forEach(c=>s+=text(c.x,y+23,c.label,16,'#424242',600));
 rows.forEach((row,i)=>{const top=y+34+i*rowHeight;if(i===selected)s+=rect(225,top,960,rowHeight,'#edf5fc');s+=line(225,top+rowHeight,1185,top+rowHeight);row.forEach((v,j)=>s+=text(columns[j].x,top+rowHeight/2+6,v,17,j===0?'#0067b8':'#242424'));});
 return s;
}
function shell({title,type,section='Overview',nav,actions=['Refresh'],body,crumb='Home > Resource groups > rg-egakumi-demo',tabs=[]}){
 const menu=nav??['Overview','Activity log','Access control (IAM)','Tags','Settings','Properties'];
 let s=rect(0,0,W,H,'#fff');
 s+=rect(0,0,W,38,'#0078d4');
 s+='<path d="M15 12H31M15 19H31M15 26H31" stroke="#fff" stroke-width="2"/>';
 s+=text(47,26,'Microsoft Azure',20,'#fff',600);
 s+=rect(251,6,533,26,'#fff','none',2)+text(263,25,'⌕',21,'#5a5a5a')+text(289,24,'Search resources, services, and docs',16,'#606060');
 s+=rect(946,6,245,26,'#005a9e','none',2)+text(1068,25,'教材用の再現画面',16,'#fff',600,'text-anchor="middle"');
 s+=text(17,62,crumb,16,'#0067b8');
 s+=rect(0,76,202,408,'#f7f7f7')+`<path d="M202 76V484" stroke="#d6d6d6"/>`;
 s+=rect(12,87,178,30,'#fff','#b3b3b3',2)+text(22,108,'Search',16,'#737373');
 menu.forEach((v,i)=>{const top=131+i*31;const active=v===section;if(active)s+=rect(0,top,202,31,'#e4eff9')+rect(0,top,4,31,'#0078d4');s+=text(19,top+22,navLabel(v),16,v==='Settings'?'#616161':'#242424',active?600:400);});
 s+=`<path d="M231 88L245 81L259 88V104L245 111L231 104Z" fill="#0078d4"/><path d="M231 88L245 95L259 88M245 95V111" fill="none" stroke="#8fd6ff"/>`;
 s+=text(274,103,title,25,'#242424',600)+text(274,126,type,16,'#616161');
 s+=line(219,139,1192,139);
 actions.forEach((v,i)=>{const x=231+i*139;s+=text(x,163,i===0?'↻':'+',18,'#0078d4')+text(x+24,162,v,16,'#242424');});
 s+=line(219,176,1192,176);
 if(tabs.length){tabs.forEach((v,i)=>{s+=text(232+i*150,197,v,16,i?'#616161':'#0078d4',i?400:600);if(!i)s+=`<path d="M231 204H349" stroke="#0078d4" stroke-width="3"/>`;});}
 s+=body;
 s+=rect(202,484,1005,27,'#f6f8fa')+line(202,484,1207,484)+text(220,503,'説明用の名前・IP（稼働環境の取得値ではありません）',16,'#616161');
 return `<svg xmlns="http://www.w3.org/2000/svg" width="1207" height="511" viewBox="0 0 1207 511"><defs><style>text{font-family:"Segoe UI",-apple-system,BlinkMacSystemFont,"Hiragino Kaku Gothic ProN","Yu Gothic",sans-serif}</style></defs>${s}</svg>`;
}

const screens={};
function add(id,title,svg,focuses){screens[id]={file:`diagrams/portal-${id}.svg`,title,focuses};screens[id]._svg=svg;}
const peNav=['Overview','Activity log','Access control (IAM)','Tags','Settings','DNS configuration','Properties'];
const dnsNav=['Overview','Activity log','Access control (IAM)','Tags','DNS Management','Recordsets','Virtual network links'];

add('resources','4つのPrivate Endpointを確認',shell({title:'Private endpoints',type:'Private Link',section:'Private endpoints',nav:['Overview','Private endpoints','Private link services','Pending connections'],actions:['Refresh','Create'],body:table([{x:240,label:'Name'},{x:569,label:'Resource group'},{x:806,label:'Connection status'},{x:1016,label:'Location'}],[['pe-egakumi-foundry','rg-egakumi-demo','Approved','Japan East'],['pe-egakumi-kv','rg-egakumi-demo','Approved','Japan East'],['pe-egakumi-acr','rg-egakumi-demo','Approved','Japan East'],['pe-egakumi-sql','rg-egakumi-demo','Approved','Japan East']],{rowHeight:48,selected:1})}),{
 list:box(231,232,951,193,'4つの接続口',1.1),foundry:box(237,238,492,37,'Foundry PE'),keyvault:box(237,286,492,37,'Key Vault PE'),acr:box(237,334,492,37,'ACR PE'),sql:box(237,382,492,37,'SQL PE'),status:box(802,237,188,182,'接続状態',1.12)
});

let vnetBody=text(234,209,'DNS servers',21,'#242424',600);
vnetBody+=text(234,244,'DNS server settings',17,'#424242')+radio(235,281,'Default (Azure-provided)',true)+radio(235,322,'Custom',false);
vnetBody+=info(365,['Default uses Azure-provided DNS.','Custom DNS requires the server to resolve public and private names.']);
add('vnet-dns','VNetが問い合わせるDNS',shell({title:'vnet-egakumi-demo | DNS servers',type:'Virtual network',section:'DNS servers',nav:['Overview','Activity log','Access control (IAM)','Tags','Settings','Address space','DNS servers','Peerings'],actions:['Save','Discard'],body:vnetBody}),{
 mode:box(229,259,423,78,'Azure提供DNS / 独自DNS'),azure:box(229,259,423,35,'Azure提供DNS'),custom:box(229,301,423,35,'独自DNS'),explanation:box(229,366,947,68,'独自DNSには名前解決の準備も必要',1.1)
});

let customBody=text(234,209,'DNS servers',21,'#242424',600)+text(234,244,'DNS server settings',17,'#424242');
customBody+=radio(235,280,'Default (Azure-provided)',false)+radio(235,320,'Custom',true)+text(263,357,'DNS server',17,'#424242')+input(263,393,453,'10.60.0.4');
customBody+=text(263,445,'Private names must be resolvable by this DNS server.',17,'#616161');
add('vnet-dns-custom','独自DNSを指定する場合',shell({title:'vnet-egakumi-demo | DNS servers',type:'Virtual network',section:'DNS servers',nav:['Overview','Activity log','Access control (IAM)','Tags','Settings','Address space','DNS servers','Peerings'],actions:['Save','Discard'],body:customBody}),{
 custom:box(229,298,494,36,'独自DNSを選択'),address:box(258,367,463,42,'独自DNSのIP'),explanation:box(258,423,838,32,'このDNSからPrivate DNSを解決できるか',1.1)
});

const property=(y,name,value,{blue=false,green=false}={})=>text(234,y,name,17,'#616161')+(green?status(520,y,value):(blue?link(520,y,value,18):text(520,y,value,18)));
let peOverview=text(234,205,'Essentials',20,'#242424',600);
peOverview+=property(242,'Resource group','rg-egakumi-demo',{blue:true})+property(279,'Location','Japan East')+property(316,'Connection status','Approved',{green:true})+property(353,'Network interface','nic-pe-egakumi-kv',{blue:true})+property(390,'Private link resource','kv-egakumi-demo',{blue:true})+property(427,'Target sub-resource','vault')+property(464,'Virtual network / subnet','vnet-egakumi-demo / snet-egakumi-pep',{blue:true});
add('pe-overview','PEの接続先とNICを開く',shell({title:'pe-egakumi-kv',type:'Private endpoint',nav:peNav,body:peOverview}),{
 connection:box(513,293,315,35,'PEの接続状態'),nic:box(513,330,400,35,'実NICへのリンク'),resource:box(513,367,480,35,'接続先サービス本体'),subresource:box(513,404,315,35,'接続の種類'),subnet:box(513,441,655,35,'PE区画',1.1)
});

const nicBody=text(233,204,'IP configurations',21,'#242424',600)+table([{x:240,label:'Name'},{x:480,label:'IP version'},{x:670,label:'Private IP address'},{x:970,label:'Allocation'}],[['ipconfig1','IPv4','10.40.1.6','Dynamic'],['ipconfig2','IPv4','10.40.1.7','Dynamic']],{y:225,rowHeight:52,selected:0})+info(390,['nic-pe-egakumi-acr / snet-egakumi-pep','The IP configurations shown here are illustrative examples.']);
add('nic-ipconfigs','NICの全IP構成を確認',shell({title:'nic-pe-egakumi-acr | IP configurations',type:'Network interface',section:'IP configurations',nav:['Overview','Activity log','Access control (IAM)','Settings','IP configurations','DNS servers','Properties'],body:nicBody}),{
 all:box(231,257,949,105,'IP構成をすべて確認',1.1),first:box(663,263,270,43,'レジストリ用IP'),second:box(663,315,270,43,'データ用IP'),allocation:box(963,263,210,95,'割り当て方法',1.1)
});

const peDns=text(234,205,'Private DNS integration',20,'#242424',600)+table([{x:240,label:'Private DNS zone'},{x:922,label:'DNS zone group'}],[['privatelink.vaultcore.azure.net','default']],{y:220,rowHeight:43})+text(234,330,'Custom DNS records',20,'#242424',600)+table([{x:240,label:'FQDN'},{x:922,label:'IP addresses'}],[['kv-egakumi-demo.vault.azure.net','10.40.1.4']],{y:345,rowHeight:46});
add('pe-dns-config','PEとDNSゾーンの連携',shell({title:'pe-egakumi-kv | DNS configuration',type:'Private endpoint',section:'DNS configuration',nav:peNav,body:peDns}),{
 zone:box(233,256,670,43,'Private DNSゾーン'),group:box(913,256,267,43,'DNS zone group'),name:box(234,382,665,43,'通常の接続先FQDN'),ip:box(915,382,265,43,'PEの実private IP'),integration:box(231,218,952,82,'PEとDNSレコードの連携',1.1)
});

const zones=['privatelink.cognitiveservices.azure.com','privatelink.openai.azure.com','privatelink.services.ai.azure.com','privatelink.vaultcore.azure.net','privatelink.azurecr.io','privatelink.database.windows.net','apim-egakumi-demo.azure-api.net','demo-env.japaneast.azurecontainerapps.io'];
add('dns-zones','Private DNSゾーンを一覧で確認',shell({title:'Private DNS zones',type:'DNS',section:'Private DNS zones',nav:['Overview','Private DNS zones','Public DNS zones'],actions:['Refresh','Create'],body:table([{x:240,label:'Name'},{x:922,label:'Resource group'}],zones.map(n=>[n,'rg-egakumi-demo']),{y:199,rowHeight:30,selected:3})}),{
 foundry:box(234,232,669,88,'同じFoundry PEに3ゾーン',1.1),keyvault:box(234,322,669,28,'Key Vault'),acr:box(234,352,669,28,'ACR'),sql:box(234,382,669,28,'SQL'),internal:box(234,411,669,59,'APIM・ACAの内部名',1.1),all:box(231,232,951,239,'6つのPrivate Linkゾーン＋内部2ゾーン',1.08)
});

const recordColumns=[{x:240,label:'Name'},{x:467,label:'Type'},{x:565,label:'TTL'},{x:687,label:'Value'},{x:1038,label:'Auto registered'}];
function records(zone,name,ip){return shell({title:`${zone} | Recordsets`,type:'Private DNS zone',section:'Recordsets',nav:dnsNav,actions:['Refresh','Record set'],body:table(recordColumns,[['@','SOA','3600','…','False'],[name,'A','300',ip,'False']],{y:215,rowHeight:52,selected:1})});}
add('dns-records-keyvault','Key Vault名からPEのIPへのAレコード',records('privatelink.vaultcore.azure.net','kv-egakumi-demo','10.40.1.4'),{
 name:box(234,301,218,45,'Private Link名のレコード'),type:box(458,301,92,45,'Aレコード'),ip:box(678,301,343,45,'Key Vault PEのprivate IP'),record:box(233,300,792,47,'名前とIPの対応',1.1),autoreg:box(1030,301,149,45,'VM名の自動登録とは別',1.12)
});

const linkBody=table([{x:240,label:'Link name'},{x:603,label:'Link status'},{x:782,label:'Virtual network'},{x:1034,label:'Auto registration'}],[['vnet-egakumi-demo-link','Completed','vnet-egakumi-demo','Disabled']],{y:220,rowHeight:61,selected:0})+info(356,['Virtual network links define where a private zone can be resolved.','Auto registration controls VM host-name registration.']);
add('dns-vnet-links','名前解決の範囲とVM自動登録',shell({title:'privatelink.vaultcore.azure.net | Virtual network links',type:'Private DNS zone',section:'Virtual network links',nav:dnsNav,actions:['Refresh','Add'],body:linkBody}),{
 vnet:box(775,254,245,56,'このVNetへリンク'),autoreg:box(1026,254,154,56,'VM名の自動登録は無効',1.12),link:box(233,254,946,56,'ゾーンとVNetのリンク',1.1)
});

let fwBody=text(234,205,'DNS settings',21,'#242424',600)+text(234,240,'DNS servers',18,'#424242',600);
fwBody+=radio(235,275,'Default (Azure-provided)',true)+radio(235,313,'Custom',false)+line(231,337,1185,337)+text(234,370,'DNS proxy',18,'#424242',600)+toggle(520,371,true);
fwBody+=info(409,['Clients must use the firewall private IP to query the DNS proxy.']);
add('firewall-dns','Firewallポリシーの上流DNSとproxy',shell({title:'afwp-egakumi-demo | DNS settings',type:'Firewall policy',section:'DNS settings',nav:['Overview','Activity log','Settings','Rules','DNS settings','Threat intelligence'],actions:['Save','Discard'],body:fwBody}),{
 upstream:box(229,250,490,79,'Firewall自身の上流DNS'),azure:box(229,253,490,35,'Azure提供DNS'),custom:box(229,290,490,35,'独自の上流DNS'),proxy:box(513,345,385,42,'DNS proxyを有効化'),client:box(229,409,947,46,'クライアント側のDNS指定は別',1.1)
});

add('dns-records-apim','APIMのFQDN自体をゾーンにする',records('apim-egakumi-demo.azure-api.net','@','10.40.2.68'),{
 apex:box(234,301,218,45,'ゾーン頂点の@ A'),ip:box(678,301,343,45,'APIMの実内部VIP'),ttl:box(556,301,113,45,'TTL 300秒'),record:box(233,300,792,47,'APIM名 → 内部VIP',1.1),zone:box(270,78,747,35,'APIM自身のFQDNがゾーン名',1.1)
});

const apimBody=text(234,205,'Virtual network',21,'#242424',600)+text(234,244,'Virtual network type',17,'#424242')+radio(235,280,'None',false)+radio(418,280,'External',false)+radio(638,280,'Internal',true)+property(337,'Virtual network','vnet-egakumi-demo',{blue:true})+property(383,'Subnet','snet-egakumi-apim',{blue:true})+info(422,['Internal access exposes the API gateway through an internal VIP.']);
add('apim-network','APIMのInternal VNet構成',shell({title:'apim-egakumi-demo | Network',type:'API Management service',section:'Network',nav:['Overview','Activity log','APIs','Subscriptions','Network','Properties'],actions:['Save','Discard','Apply network configuration'],body:apimBody}),{
 mode:box(630,254,224,38,'Internal VNet'),vnet:box(513,313,563,36,'接続するVNet'),subnet:box(513,359,563,36,'APIM用サブネット'),apply:box(503,141,317,31,'ネットワーク構成を適用',1.12)
});

let acaJson=text(234,205,'JSON View',20,'#242424',600)+rect(228,221,953,236,'#fafafa','#e5e5e5');
const jsonLines=['{','  "name": "cae-egakumi-demo",','  "type": "Microsoft.App/managedEnvironments",','  "properties": {','    "defaultDomain": "demo-env.japaneast.azurecontainerapps.io",','    "staticIp": "10.40.2.196",','    "vnetConfiguration": { "internal": true }','  }','}'];
jsonLines.forEach((v,i)=>{acaJson+=text(238,247+i*24,String(i+1),16,'#8a8886')+text(271,247+i*24,v,17,i===4||i===5?'#005a9e':'#3b3a39',400,'font-family="Menlo,Consolas,monospace"');});
add('aca-json','ACA環境の実ドメインと内部IP',shell({title:'cae-egakumi-demo | JSON View',type:'Container Apps environment',section:'Overview',nav:['Overview','Activity log','Access control (IAM)','Settings','Networking','Properties'],body:acaJson}),{
 domain:box(267,325,905,26,'環境のdefaultDomain',1.1),ip:box(267,349,570,26,'環境のstaticIp'),internal:box(267,373,814,26,'internal: true',1.1)
});

add('dns-records-aca','ACAの子アプリ名をワイルドカードで解決',records('demo-env.japaneast.azurecontainerapps.io','*','10.40.2.196'),{
 wildcard:box(234,301,218,45,'子アプリ名を扱う* A'),ip:box(678,301,343,45,'環境の実staticIp'),ttl:box(556,301,113,45,'TTL 300秒'),noApex:box(233,249,792,99,'@はSOAのみ、この定義に@ Aはない',1.1),record:box(233,300,792,47,'アプリ名 → ACA環境の内部IP',1.1)
});

let agwBody=text(234,205,'Essentials',20,'#242424',600)+property(245,'Resource group','rg-egakumi-demo',{blue:true})+property(289,'Location','Japan East')+property(333,'Virtual network / subnet','vnet-egakumi-demo / snet-egakumi-agw',{blue:true})+property(377,'Frontend public IP address','203.0.113.10',{blue:true})+property(421,'Frontend public IP resource','pip-egakumi-agw',{blue:true});
add('public-dns','公開名が指すAppGWの公開入口',shell({title:'agw-egakumi-demo',type:'Application gateway',section:'Overview',nav:['Overview','Activity log','Access control (IAM)','Settings','Frontend IP configurations','Listeners','Backend pools'],body:agwBody}),{
 ip:box(513,353,440,37,'公開DNSのAレコードが指すIP'),resource:box(513,397,529,37,'AppGWの公開IPリソース'),subnet:box(513,309,659,37,'AppGWの配置区画',1.1)
});

let check=text(234,204,'RunShellScript',20,'#242424',600)+text(234,231,'Output (解説用の応答例)',16,'#616161')+rect(228,247,953,218,'#1e1e1e');
const terminal=[['$ hostname',''],['vm-egakumi-demo',''],['$ dig kv-egakumi-demo.vault.azure.net',''],[';; SERVER: 168.63.129.16#53',''],['kv-egakumi-demo.vault.azure.net. CNAME',''],['  kv-egakumi-demo.privatelink.vaultcore.azure.net.',''],['kv-egakumi-demo.privatelink.vaultcore.azure.net.',''],['  A  10.40.1.4','']];
terminal.forEach(([a,b],i)=>check+=text(244,273+i*24,a+(b?'  '+b:''),17,i<=1?'#9cdcfe':i===7?'#b5cea8':'#e8e8e8',400,'font-family="Menlo,Consolas,monospace"'));
add('dns-check','実際の送信元で問い合わせる',shell({title:'vm-egakumi-demo | Run command',type:'Virtual machine',section:'Run command',nav:['Overview','Activity log','Access control (IAM)','Operations','Run command','Connect'],body:check}),{
 source:box(237,251,654,54,'この管理VMが送信元'),query:box(237,303,895,30,'アプリが使う通常のFQDN',1.1),dns:box(237,327,723,30,'問い合わせたDNS'),cname:box(237,351,932,56,'CNAMEの案内先',1.1),ip:box(237,405,932,51,'対象PEのIPと照合',1.1),all:box(231,247,949,218,'送信元・DNS・FQDN・回答をそろえる',1.08)
});

function endpointDns(resource,zone,rows){return shell({title:`${resource} | DNS configuration`,type:'Private endpoint',section:'DNS configuration',nav:peNav,body:text(234,205,'Private DNS integration',20,'#242424',600)+table([{x:240,label:'Private DNS zone'},{x:922,label:'DNS zone group'}],[[zone,'default']],{y:220,rowHeight:43})+text(234,330,'Custom DNS records',20,'#242424',600)+table([{x:240,label:'FQDN'},{x:922,label:'IP addresses'}],rows,{y:345,rowHeight:37})});}
add('pe-dns-acr','ACRのレジストリとデータ側の名前',endpointDns('pe-egakumi-acr','privatelink.azurecr.io',[['acregakumidemo.azurecr.io','10.40.1.6'],['acregakumidemo.japaneast.data.azurecr.io','10.40.1.7']]),{
 zone:box(233,256,670,43,'ACR用Private DNSゾーン'),group:box(913,256,267,43,'DNS zone group'),registry:box(233,380,947,36,'レジストリ側のFQDNとIP',1.1),data:box(233,417,947,36,'データ側のFQDNとIP',1.1),ips:box(913,380,267,73,'複数のIPを確認',1.12),all:box(233,380,947,73,'接続先の名前を一つに決めつけない',1.1)
});
add('pe-dns-sql','SQLの通常名とPrivate Endpoint',endpointDns('pe-egakumi-sql','privatelink.database.windows.net',[['sql-egakumi-demo.database.windows.net','10.40.1.8']]),{
 zone:box(233,256,670,43,'SQL用Private DNSゾーン'),group:box(913,256,267,43,'DNS zone group'),name:box(233,380,670,36,'SQLの通常接続先名'),ip:box(913,380,267,36,'SQL PEのprivate IP'),all:box(233,380,947,36,'名前が解決できても認証・操作権限は別',1.1)
});

let kvNetwork=text(234,207,'Firewalls and virtual networks',20,'#0078d4',600)+text(643,207,'Private endpoint connections',18,'#616161')+`<path d="M233 218H560" stroke="#0078d4" stroke-width="3"/>`;
kvNetwork+=text(234,255,'Public network access',19,'#242424',600)+radio(235,294,'Allow public access from all networks',false)+radio(235,335,'Allow public access from specific virtual networks and IP addresses',false)+radio(235,376,'Disable public access',true);
kvNetwork+=info(414,['A private endpoint and a public network access setting are separate controls.']);
add('kv-networking','サービス本体の公開アクセスを別に制御',shell({title:'kv-egakumi-demo | Networking',type:'Key vault',section:'Networking',nav:['Overview','Activity log','Access control (IAM)','Settings','Access configuration','Networking','Private endpoint connections'],actions:['Save','Discard'],body:kvNetwork}),{
 access:box(229,350,679,40,'サービス本体の公開アクセスを無効化'),public:box(229,350,679,40,'サービス本体の公開アクセスを無効化'),privateEndpoints:box(635,184,525,34,'PE接続の確認とは別の項目',1.1),heading:box(229,230,612,37,'公開アクセス設定',1.12)
});

const nsgBody=text(234,204,'Outbound security rules',21,'#242424',600)+table([{x:236,label:'Priority'},{x:322,label:'Name'},{x:558,label:'Port'},{x:638,label:'Protocol'},{x:735,label:'Source'},{x:831,label:'Destination'},{x:1087,label:'Action'}],[['400','allow-dns-tcp','53','TCP','Any','168.63.129.16','Allow'],['410','allow-dns-udp','53','UDP','Any','168.63.129.16','Allow'],['4096','deny-other-outbound','*','Any','Any','Any','Deny']],{y:223,rowHeight:45,selected:0})+info(420,['DNS関連と終端規則のみ抜粋。実際の確認では、他の規則と経路も照合します。']);
add('nsg-dns-rules','DNSへのTCP・UDP 53の許可を確認',shell({title:'nsg-egakumi-apim | Outbound security rules',type:'Network security group',section:'Outbound security rules',nav:['Overview','Activity log','Access control (IAM)','Settings','Inbound security rules','Outbound security rules','Network interfaces','Subnets'],actions:['Refresh','Add'],body:nsgBody}),{
 tcp:box(232,257,950,44,'TCP 53の送信許可',1.08),udp:box(232,302,950,44,'UDP 53の送信許可',1.08),dns:box(232,257,950,89,'TCP・UDP 53の送信許可',1.08),ports:box(551,257,168,89,'TCP / UDP 53',1.15),destination:box(823,257,251,89,'問い合わせ先DNSを確認',1.15),priority:box(231,257,82,134,'優先順位を確認',1.12),deny:box(232,347,950,44,'後続の規則による拒否',1.08)
});

await fs.mkdir(path.join(here,'diagrams'),{recursive:true});
for(const [id,s] of Object.entries(screens)){await fs.writeFile(path.join(here,s.file),s._svg);delete s._svg;}
await fs.writeFile(path.join(here,'portal-screens.json'),JSON.stringify({schemaVersion:1,viewport:{width:W,height:H},illustrative:true,description:'Azure Portal teaching replicas. Names, IPs and output are illustrative; no live Azure inspection.',screens},null,2)+'\n');
console.log(JSON.stringify({screens:Object.keys(screens).length,files:Object.values(screens).map(s=>s.file)}));
