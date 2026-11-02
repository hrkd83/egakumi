// A1-specific, static settings views. Shared branding/camera/renderers are unchanged.
import fs from 'node:fs/promises';
import {renderSettingsShell,renderSettingsBody} from '../../settings-visual.mjs';
const HERE=new URL('./',import.meta.url);
const scriptFile=new URL('script.json',HERE);
const script=JSON.parse(await fs.readFile(scriptFile,'utf8'));
const clone=v=>JSON.parse(JSON.stringify(v));
const esc=v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const txt=(x,y,s,size=20,color='#20323e',weight=400,anchor='start')=>'<text x="'+x+'" y="'+y+'" font-size="'+size+'" fill="'+color+'" font-weight="'+weight+'" text-anchor="'+anchor+'">'+esc(s)+'</text>';
const box=(x,y,w,h,fill='#fff',stroke='#c7d9df',rx=5)=>'<rect x="'+x+'" y="'+y+'" width="'+w+'" height="'+h+'" rx="'+rx+'" fill="'+fill+'" stroke="'+stroke+'"/>';
const red=(x,y,w,h,id)=>'<rect class="highlight" data-target="'+id+'" x="'+x+'" y="'+y+'" width="'+w+'" height="'+h+'" rx="5" fill="none" stroke="#de343b" stroke-width="2.8"/>';
const arrow=(d,label,x,y,color='#087f8c',both=false)=>'<path d="'+d+'" fill="none" stroke="'+color+'" stroke-width="2.6" marker-end="url(#arr)"'+(both?' marker-start="url(#arr)"':'')+'/>'+ (label?txt(x,y,label,17,color,500,'middle'):'');
const menu=['採用する範囲','ピアリング','公開出口','入口の往復','区画別の経路','Gateway共有','経路伝搬','確認問題'];
const common=(id,title,section,subtitle)=>({viewId:id,areaLabel:'ハブ＆スポーク接続の確認',title,kind:'接続案の設定（教材）',section,menu,subtitle:subtitle??'教材・現行定義に基づく接続案。稼働環境の取得値ではない',footer:'設定値は設計時の確認対象。実IP・実効経路・許可は対象環境で照合する'});
const tableView=(id,title,section,columns,rows,extra={})=>({layout:'settings',...common(id,title,section),table:{columns,rows:rows.map(([id,...cells])=>({id,cells}))},highlightRows:[],...extra});
const fieldView=(id,title,section,rows,extra={})=>({layout:'settings',...common(id,title,section),fields:rows.map(([id,label,value])=>({id,label,value})),highlightFields:[],...extra});
const views={
 scope:tableView('a1-scope','共有する機能を、個別に選ぶ','採用する範囲',[{label:'採用する機能',width:320},{label:'選択と目的',width:652}],[
  ['private','社内・ハブへのprivate接続','出口とDNSを維持する構成も選べる'],
  ['egress','公開宛て出口の集約','対象区画ごとに採用する'],
  ['dns','中央DNSの利用','名前解決の設定・転送を別途整える'],
  ['gateway','VPN / ExpressRouteの共有','ハブGatewayを実際に使う場合に採用']]),
 ownership:tableView('a1-ownership','企業側とCore側の管理範囲を合わせる','採用する範囲',[{label:'確認する対象',width:350},{label:'教材上の整理',width:622}],[
  ['target','接続先VNet・CIDR','相手の識別情報と重複を確認'],
  ['owner','共有する設備・変更担当','両側の担当と責任を先に確定'],
  ['iac','現行IaCが作らないもの','ハブ・ピアリング・共有Gateway・中央Resolver']]),
 preserve:fieldView('a1-preserve','既存の役割を保つ接続案','採用する範囲',[
  ['subnets','既存の構成','6サブネットと既存サービスを維持'],
  ['local','ローカルFirewall','AppGWとAPIM / ACAの入口検査を継続'],
  ['pe','Private Endpoint','各サービスのprivate接続を維持'],
  ['change','撤去・公開入口の中央化','送信元・制御も含む別の再設計']]),
 addressing:tableView('a1-address-plan','接続先全体で、アドレス重複を確認','ピアリング',[{label:'照合する範囲',width:350},{label:'確認すること',width:622}],[
  ['core','Coreの全VNet CIDR','1つの範囲だけで判断しない'],
  ['hub','企業ハブ・他スポーク','重複と経路衝突を接続前に確認'],
  ['prem','接続先の社内・オンプレミス','重複時は再採番等の影響を設計']]),
 peer:fieldView('a1-peering','両側のピアリングを確認する','ピアリング',[
  ['spoke','Core → ハブ','対ハブのピアリングを作成'],
  ['hub','ハブ → Core','対Coreのピアリングを作成'],
  ['state','VNetアクセスと接続状態','アクセス許可・両側Connectedを確認'],
  ['policy','個別通信の成立','NSG・UDR・Firewall・復路も別途確認']]),
 specific:tableView('a1-specific-routes','0/0と、宛先に合う具体的な経路','公開出口',[{label:'宛先の種類',width:310},{label:'経路選択で確認すること',width:662}],[
  ['default','公開宛ての既定経路','出口集約を採用したAPIMはハブFWへ'],
  ['pe','PEのprivate IP','より具体的な直接経路を確認'],
  ['vnet','VNet内・ピアリング宛て','0/0だけで全宛先を上書きしない']]),
 agw:fieldView('a1-agw-default','通常のAppGW v2の経路制約を守る','入口の往復',[
  ['default','0/0 → ハブFW / NVA','一律に追加しない'],
  ['control','Azure制御系の通信','直接通信とサービスの制約を維持'],
  ['internal','内部入口への具体経路','実APIM VIP /32・実ACA入口IP /32はローカルFWへ']]),
 subnets:tableView('a1-subnet-routes','区画ごとに、変更と保持を選ぶ','区画別の経路',[{label:'対象',width:300},{label:'設定・確認する内容',width:672}],[
  ['vm','管理VM','採用時はdefault-egressをハブFWへ。運用元への復路も確認'],
  ['aca','ACA','採用時は公開出口をハブFWへ。agw-returnはローカルFW'],
  ['private','PE・APIM → ACA','private接続・直接経路を公開出口と分けて確認'],
  ['pe','PEサブネット','現行は独自RTなし。一律にハブ向けRTを追加しない'],
  ['fw','AzureFirewallSubnet','現行は独自RTなし。ローカルFWの動作を維持']]),
 propagation:tableView('a1-propagation','Gateway経路伝搬：利用者設定とARM属性','経路伝搬',[{label:'対象',width:268},{label:'bgpRoutePropagation',width:290},{label:'disableBgpRoutePropagation',width:414}],[
  ['agw','AppGW / agw','true（既定）','false：伝搬する'],
  ['apim','APIM / apim','true（既定）','false：伝搬する'],
  ['vm','管理VM / management','true（既定）','false：伝搬する'],
  ['stop','上記3区画で停止','false','true：伝搬しない'],
  ['aca','ACA','公開切替キーなし','trueに固定：伝搬しない']],{
   subtitle:'入力とARM属性は真偽が反転する。伝搬停止は既定経路だけを除く指定ではない',
   footer:'伝搬停止前に、必要な社内・中央DNS宛ての明示経路と復路を準備する'}),
 agwPropagation:tableView('a1-agw-propagation','AppGWへの伝搬と、実効経路を確認','経路伝搬',[{label:'確認対象',width:300},{label:'教材上の要点',width:672}],[
  ['default','Gateway由来の0/0','AppGWを強制トンネルする経路にならないか確認'],
  ['stop','agwの伝搬を止める場合','必要な社内・DNS宛ての明示経路と復路を先に準備'],
  ['priority','経路選択の基本','最長一致。同一prefixはUDR → BGP → システム'],
  ['exception','優先順位の例外','VNet・ピアリング等のシステム経路に注意'],
  ['peering','伝搬停止の作用範囲','ピアリング経路を削除する操作とは別']]),
 q1:fieldView('a1-quiz-peering','確認①：Connectedなら、全通信がハブFW経由？','確認問題',[
  ['given','前提','両側のピアリングがConnectedになった'],
  ['a','選択肢 A','すべての通信がハブFWを通る'],
  ['b','選択肢 B','通る装置は、宛先・経路などで決まる']],{notice:'条件と選択肢を見て考える'}),
 q2:fieldView('a1-quiz-return','確認②：AppGWへの復路もハブ向けに変える？','確認問題',[
  ['given','前提','APIMの公開宛て0/0をハブFWへ変更した'],
  ['a','選択肢 A','AppGWへの復路もハブFWへ変更する'],
  ['b','選択肢 B','AppGWへの復路はローカルFWへ維持する']],{notice:'この接続案で、入口への応答をどう返すか考える'}),
 q3:fieldView('a1-quiz-propagation','確認③：伝搬停止で、消えるのは既定経路だけ？','確認問題',[
  ['given','前提','AppGWへのGateway経路伝搬を無効にする'],
  ['a','選択肢 A','消えるのは既定経路だけ'],
  ['b','選択肢 B','必要な社内・DNS宛て経路も対象になり得る']],{notice:'止める前に、必要な到達性をどう残すか考える'})
};
const svgMeta={
 forward:{...common('a1-forwarded','転送を受ける側の許可と、中継する経路','ピアリング'),heading:'転送トラフィックとピアリングの設定'},
 egress:{...common('a1-public-egress','APIMの公開出口：設定と往復を一緒に確認','公開出口'),heading:'APIMの公開宛ては、ハブFWの往復'},
 local:{...common('a1-local-return','内部入口：設定と往復を一緒に確認','入口の往復'),heading:'AppGWへの応答は、ローカルFWへ返す'},
 gateway:{...common('a1-gateway-sharing','ハブGatewayの共有設定と、社内との往復','Gateway共有'),heading:'Gateway共有は、必要な場合だけ採用する',subtitle:'社内側にもCore CIDRへの復路が必要。図は接続案の教材整理'},
 summary:{...common('a1-summary','接続案の設定と往復を振り返る','採用する範囲'),heading:'共有する範囲と、維持する経路を合わせる'}
};
const configRow=(y,label,value,id,high)=>box(214,y,972,45)+txt(231,y+29,label,18,'#586875')+txt(570,y+29,value,18,'#087f8c',500)+(high.has(id)?red(217,y+3,966,39,id):'');
const node=(x,y,label,detail,id,high,w=240,h=68)=>box(x,y,w,h,'#fff','#9dbac4')+txt(x+w/2,y+29,label,22,'#20323e',650,'middle')+(detail?txt(x+w/2,y+53,detail,17,'#647984',400,'middle'):'')+(high.has(id)?red(x-2,y-2,w+4,h+4,id):'');
const path3=(names,ids,high,{y=181,back=true}={})=>{
 let out=node(240,y,names[0][0],names[0][1],ids[0],high)+node(580,y,names[1][0],names[1][1],ids[1],high)+node(920,y,names[2][0],names[2][1],ids[2],high);
 out+=arrow('M480 '+(y+25)+' H580','往路',530,y+17)+arrow('M820 '+(y+25)+' H920','往路',870,y+17);
 if(back)out+=arrow('M1040 '+(y+68)+' V'+(y+116)+' H728 V'+(y+68),'復路',884,y+107)+arrow('M672 '+(y+68)+' V'+(y+116)+' H360 V'+(y+68),'復路',516,y+107);
 return out;
};
function svgContent(key,targets){
 const high=new Set(targets);let content='';
 if(key==='egress'){
  content=path3([['APIM','要求を開始する側'],['ハブFW','許可・SNATと状態'],['公開依存先','応答する側']],['apim','hub','public'],high);
  content+=configRow(355,'default-egress','0.0.0.0/0 → ハブFW private IP','route',high)+configRow(405,'ハブ側の事前準備','依存先の許可・SNAT・Coreへの復路','prepare',high);
 }else if(key==='local'){
  content=path3([['AppGW','公開入口'],['ローカルFW','入口の検査'],['APIM / ACA','内部入口']],['agw','local','backend'],high,{y:168});
  content+=configRow(319,'APIMの復路','AppGW区画 → ローカルFW','return',high)+configRow(366,'AppGWの具体経路','実APIM VIP /32・実ACA入口IP /32 → ローカルFW','vip',high)+configRow(413,'APIMの管理経路','ApiManagementタグ → Internet','management',high);
 }else if(key==='gateway'){
  content=path3([['社内・オンプレミス','要求・応答の両端'],['ハブGateway','VPN / ExpressRoute'],['Core / スポーク','共有Gatewayを利用']],['prem','gateway','spoke'],high,{y:163});
  content+=configRow(314,'ハブ側のピアリング','allowGatewayTransit','hubSetting',high)+configRow(361,'スポーク側のピアリング','useRemoteGateways','spokeSetting',high)+configRow(408,'採用・共有の制約','実利用時のみ / 自身GW併用不可 / リモート利用1ピアリング','conditions',high);
 }else if(key==='forward'){
  content=node(240,171,'社内・他スポーク','元の送信元','other',high)+node(580,171,'ハブFW / NVA','中継する装置','hub',high)+node(920,171,'Core / スポーク','転送を受け入れる側','spoke',high);
  content+=arrow('M480 204 H580','通信',530,195)+arrow('M820 204 H920','転送',870,195);
  content+=configRow(294,'Core側の対ハブピアリング','allowForwardedTraffic：転送を受ける方向で許可','forward',high)+configRow(341,'逆方向も転送を受ける場合','ハブ側にも転送許可を設定','reverse',high)+configRow(388,'別途そろえるもの','中継装置・UDR・NSG / FW許可・復路','routes',high);
 }else if(key==='summary'){
  content+=txt(240,165,'公開宛ての接続',17,'#647984',500);
  content+=node(240,176,'APIM','','apim',high,240,49)+node(580,176,'ハブFW','','hub',high,240,49)+node(920,176,'公開依存先','','public',high,240,49);
  content+=arrow('M480 201 H580','',0,0,'#087f8c',true)+arrow('M820 201 H920','',0,0,'#087f8c',true);
  content+=txt(240,259,'公開入口の接続',17,'#647984',500);
  content+=node(240,270,'AppGW','','agw',high,240,49)+node(580,270,'ローカルFW','','local',high,240,49)+node(920,270,'APIM / ACA','','backend',high,240,49);
  content+=arrow('M480 295 H580','',0,0,'#087f8c',true)+arrow('M820 295 H920','',0,0,'#087f8c',true);
  content+=configRow(351,'個別に採用する範囲','private接続・公開出口・中央DNS・Gateway共有','scope',high)+configRow(402,'A2でつなげる運用','名前解決・許可・出口IP・段階切替と再配備','next',high);
 }
 return '<defs><marker id="arr" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto-start-reverse"><path d="M0 0L7 3.5L0 7Z" fill="#087f8c"/></marker></defs><g font-family="Hiragino Sans,Yu Gothic,Meiryo,sans-serif">'+content+'</g>';
}
const mapping=new Map();
function assign(first,last,key,high={}){
 for(let n=first;n<=last;n++)mapping.set(n,{key,targets:high[n]??[]});
}
assign(5,7,'scope',{5:['private','egress'],7:['private','egress','dns','gateway']});
assign(8,10,'ownership',{8:['target','owner'],10:['iac']});
assign(11,13,'preserve',{11:['subnets','local'],12:['pe','change']});
assign(14,16,'addressing',{15:['core','hub','prem'],16:['hub','prem']});
assign(17,19,'peer',{17:['spoke','hub'],19:['state','policy']});
assign(20,23,'forward',{20:['spoke','forward'],21:['forward'],22:['reverse','routes'],23:['routes']});
assign(24,32,'egress',{25:['route'],26:['route'],27:['apim','hub','public'],28:['prepare'],29:['hub','prepare'],31:['hub','prepare'],32:['prepare']});
assign(33,34,'specific',{34:['pe','vnet']});
assign(35,41,'local',{36:['agw','local','backend'],37:['local','backend'],38:['return'],40:['return','management'],41:['vip']});
assign(42,43,'agw',{43:['default','control','internal']});
assign(44,51,'subnets',{45:['vm'],46:['vm'],47:['aca'],49:['private'],51:['pe','fw']});
assign(52,58,'gateway',{53:['conditions'],55:['hubSetting','spokeSetting'],56:['conditions'],57:['prem','spoke'],58:['prem','gateway','spoke']});
assign(59,65,'propagation',{60:['agw','apim','vm'],61:['agw','apim','vm','stop'],62:['stop'],63:['stop'],64:['stop'],65:['aca']});
assign(66,69,'agwPropagation',{67:['default','stop'],68:['priority','exception'],69:['peering']});
assign(70,72,'q1',{71:['b'],72:['b']});
assign(73,75,'q2',{74:['b'],75:['b']});
assign(76,78,'q3',{77:['b'],78:['b']});
assign(79,82,'summary',{79:['scope'],80:['apim','hub','local','backend'],81:['next'],82:['next']});
const diagrams=new URL('diagrams/settings-v2/',HERE);await fs.mkdir(diagrams,{recursive:true});
const written=new Set();
for(let i=0;i<script.turns.length;i++){
 const n=i+1,t=script.turns[i];if(t.chapterId==='opening')continue;
 const map=mapping.get(n);if(!map)throw new Error('Missing turn mapping '+n);
 if(views[map.key]){
  const v=clone(views[map.key]);v.heading=v.title;
  if(v.table)v.highlightRows=map.targets;else v.highlightFields=map.targets;
  renderSettingsBody(v);t.visual=v;
 }else{
  const meta=svgMeta[map.key],tag=map.targets.length?map.targets.join('-'):'base';
  const filename=meta.viewId+'-'+tag+'.svg';
  if(!written.has(filename)){
   const source='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1207 511">'+renderSettingsShell(meta,svgContent(map.key,map.targets))+'</svg>';
   await fs.writeFile(new URL(filename,diagrams),source);written.add(filename);
  }
  t.visual={layout:'svg',viewId:meta.viewId,heading:meta.heading,svg:'diagrams/settings-v2/'+filename,highlightTargets:map.targets};
 }
}
script.visualStyle='teaching-settings-v2';
await fs.writeFile(scriptFile,JSON.stringify(script,null,2)+'\n');
console.log(JSON.stringify({turns:script.turns.length,bodyTurns:mapping.size,settingsViews:Object.keys(views).length,diagramViews:Object.keys(svgMeta).length,svgVariants:written.size,viewGroups:[...new Set(script.turns.filter(t=>t.chapterId!=='opening').map(t=>t.visual.viewId))]}));
