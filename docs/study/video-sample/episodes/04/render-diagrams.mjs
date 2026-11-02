// Episode 04: fixed teaching settings views. No network, animation or camera.
// Rebuild: node docs/study/video-sample/episodes/04/render-diagrams.mjs
import fs from 'node:fs/promises';
import {renderSettingsShell} from '../../settings-visual.mjs';

const ROOT=new URL('./',import.meta.url),OUT=new URL('./diagrams/',ROOT);
const script=JSON.parse(await fs.readFile(new URL('script.json',ROOT),'utf8'));
const esc=v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const U=s=>[...s].reduce((n,c)=>n+(c.charCodeAt(0)<128?.58:1),0);
const R=(x,y,w,h,fill='#fff',stroke='#d5e0e5',r=4)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}"/>`;
const T=(x,y,s,size=17,color='#20323e',weight=400,anchor='start')=>`<text x="${x}" y="${y}" font-size="${size}" fill="${color}" font-weight="${weight}" text-anchor="${anchor}">${esc(s)}</text>`;
const WRAP=(s,w,size=17,max=2)=>{
  let lines=[],line='';
  for(const c of String(s)){if(c==='\n'){lines.push(line);line='';}else if(line&&U(line+c)*size>w){lines.push(line);line=c;}else line+=c;}
  if(line)lines.push(line);
  if(lines.length>max)throw Error(`Episode 04 text exceeds ${max} lines: ${s}`);
  return lines;
};
const BLOCK=(x,y,w,h,s,size=17,color='#20323e',weight=400,anchor='start')=>{
  const lines=WRAP(s,w,size),cy=y+h/2+size*.34-(lines.length-1)*(size+4)/2;
  return lines.map((l,i)=>T(x,cy+i*(size+4),l,size,color,weight,anchor)).join('');
};
const MENU=['概要','ルート設定','経路選択','往復経路','閉域と公開','管理通信','運用確認','確認問題'];
const views=new Map();
function view(id,title,section,subtitle){
  const v={id,title,section,subtitle,body:'',marks:new Map()};views.set(id,v);
  v.mark=(key,x,y,w,h)=>{v.marks.set(key,[x,y,w,h]);};
  v.add=s=>{v.body+=s;};
  return v;
}
function table(v,{x=214,y=183,w=550,widths=[240,310],heads=['設定項目','値'],rows,h=48,title='代表設定・候補経路'}){
  if(widths.reduce((a,b)=>a+b)!==w)throw Error('Table widths do not fit');
  v.add(T(x+1,y-14,title,18,'#365864',650)+R(x,y,w,29,'#edf1f5'));
  let cx=x;
  heads.forEach((s,i)=>{v.add(BLOCK(cx+11,y,widths[i]-22,29,s,15,'#637780',600));cx+=widths[i];});
  rows.forEach(([id,...cells],i)=>{
    const top=y+29+i*h;v.add(R(x,top,w,h));cx=x;
    cells.forEach((s,j)=>{v.add(BLOCK(cx+11,top,widths[j]-22,h,s,j?17:17,j?'#20323e':'#087f8c',j?400:550));cx+=widths[j];});
    if(cells.length!==heads.length)throw Error('Wrong cell count '+id);
    v.mark(id,x+2,top+2,w-4,h-4);
  });
}
function note(v,s,x=215,y=466,w=550){v.add(BLOCK(x,y-26,w,29,s,15,'#617a85'));}
function panel(v,title){v.add(R(782,147,404,328,'#eef5f6')+T(798,173,title,18,'#365864',650));}
function box(v,id,x,y,w,h,label,detail=''){
  v.add(R(x,y,w,h,'#fff','#8eafb6',6));
  if(detail){v.add(BLOCK(x+w/2,y+3,w-16,h*.51,label,18,'#087f8c',650,'middle')+BLOCK(x+w/2,y+h*.49,w-16,h*.48,detail,14,'#617983',400,'middle'));}
  else v.add(BLOCK(x+w/2,y,w-16,h,label,18,'#087f8c',600,'middle'));
  if(id)v.mark(id,x-2,y-2,w+4,h+4);
}
function arrow(v,id,points,label='',{color='#567e8b',dashed=false,labelX,labelY}={}){
  v.add(`<path d="${points.map(([x,y],i)=>`${i?'L':'M'}${x} ${y}`).join('')}" fill="none" stroke="${color}" stroke-width="2.3" ${dashed?'stroke-dasharray="6 4"':''} marker-end="url(#arrow)"/>`);
  if(label){let a=points[0],b=points.at(-1);v.add(T(labelX??(a[0]+b[0])/2,labelY??(a[1]+b[1])/2-8,label,14,color,500,'middle'));}
  if(id){const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);v.mark(id,Math.min(...xs)-5,Math.min(...ys)-17,Math.max(...xs)-Math.min(...xs)+10,Math.max(...ys)-Math.min(...ys)+32);}
}
function chain(v,labels,{y=234,ids=[],details=[],label=''}={}){
  labels.forEach((l,i)=>box(v,ids[i],800+i*130,y,112,59,l,details[i]??''));
  arrow(v,null,[[912,y+30],[930,y+30]]);arrow(v,null,[[1042,y+30],[1060,y+30]]);
  if(label)v.add(T(984,y-13,label,15,'#617983',500,'middle'));
}
function two(v,a,b,{y=224,ids=[],details=[],label=''}={}){
  box(v,ids[0],810,y,145,60,a,details[0]??'');box(v,ids[1],1020,y,145,60,b,details[1]??'');
  arrow(v,null,[[955,y+30],[1020,y+30]],label,{labelY:y+20});
}

// 005–007: a setting menu distinguishes routing from permission.
{
 const v=view('udr-role','UDR・NSG・Firewallの分担','概要','教材注釈：経路を用意することと、通信を許可することを分ける');
 table(v,{rows:[['udr','UDR','宛先範囲 → 次の転送先'],['nsg','NSG','通信の許可・拒否'],['fw','Firewall','経路上の通信をルールで判定']],h:63});
 panel(v,'経路と判定を対応付ける');two(v,'経路選択','許可の判定',{y:235,ids:['route','permit'],details:['どこへ送るか','通ってよいか']});
 v.add(BLOCK(804,335,360,94,'UDRを追加しても、NSGやFirewallの許可は別に必要。',20,'#365864'));
}
// 008–009: the application endpoint remains APIM while next hop is Firewall.
{
 const v=view('udr-next-hop','最終宛先と次ホップ','ルート設定','代表設定：AppGW用 rt-<stem>-agw の APIM宛て経路');
 table(v,{rows:[['destination','宛先プレフィックス','APIMの実内部VIP /32'],['type','次ホップの種類','VirtualAppliance'],['hop','次ホップIP','Firewallの実private IP'],['final','アプリの接続先','APIMの内部VIPのまま']],h:54});
 panel(v,'宛先はAPIM、途中でFirewallへ');chain(v,['AppGW','Firewall','APIM'],{ids:['source','firewall','apim'],details:['送信元','次ホップ','最終宛先']});
 v.add(BLOCK(802,340,363,99,'VirtualApplianceは、この教材では同じVNetのAzure Firewall。',19,'#365864'));
}
// 010–013: six subnets, four route tables. Counts describe authored UDRs only.
{
 const v=view('udr-associations','サブネットとルートテーブル','ルート設定','標準の関連付け。独自テーブルがない区画にもシステム経路はある');
 table(v,{widths:[210,241,99],heads:['送信元区画','ルートテーブル','UDR数'],rows:[['agw','AppGW','rt-<stem>-agw','2'],['apim','APIM','rt-<stem>-apim','3'],['vm','管理VM','rt-<stem>-vm','1'],['aca','ACA','rt-<stem>-aca','2'],['pep','PE','関連付けなし','—'],['firewall','AzureFirewallSubnet','関連付けなし','—']],h:43});
 panel(v,'教材の標準経路と実環境を分ける');box(v,'count',810,207,345,74,'標準の独自UDR：合計8本');
 v.add(BLOCK(807,307,355,103,'実効経路には、システム経路・伝搬経路・保持した既存経路も関係する。',19,'#365864'));
 v.add(T(809,447,'実環境の全経路を8本と扱わない',17,'#617983'));
}
// 014–015: name resolution precedes route lookup. This is a conceptual sequence.
{
 const v=view('udr-resolved-ip','名前解決後の宛先IPから調べる','経路選択','教材注釈：サービス名だけでFirewall経由かどうかを判断しない');
 table(v,{rows:[['source','確認する送信元','対象リソースのサブネット'],['resolved','名前解決後の宛先IP','公開IP または PEの実IP'],['route','一致する候補経路','宛先プレフィックスを比較']],h:63});
 panel(v,'確認の順番');box(v,null,844,204,285,53,'送信元で名前解決');box(v,'resolved-ip',844,302,285,53,'回答された宛先IP');
 arrow(v,null,[[986,257],[986,302]],'確認',{labelX:1037,labelY:284});
 v.add(T(987,412,'宛先IPに一致する経路を比較',18,'#365864',550,'middle'));
}
// 016–018: neutral examples remain in place during the default-route question.
{
 const v=view('udr-prefix','宛先範囲の具体性を比べる','経路選択','基本：一致する経路のうちプレフィックス長が長いものを選ぶ');
 table(v,{widths:[170,380],heads:['プレフィックス','表す範囲'],rows:[['ip32','/32','IPアドレス1個'],['subnet24','/24','/32より広いネットワーク範囲'],['default','0.0.0.0/0','すべてのIPv4宛先に一致']],h:63});
 panel(v,'同じ宛先IPを含む候補を比較');
 v.add(R(812,206,344,176,'#fff','#8eafb6',6)+T(828,235,'/0：最も広い範囲',18,'#087f8c',600));
 v.add(R(846,255,276,103,'#eaf3f4','#9cb8bf',5)+T(861,282,'/24：より具体的',18,'#365864',550)+R(879,307,210,38,'#fff','#8dabb3',5)+T(984,332,'/32：IP 1個',18,'#087f8c',600,'middle'));
 v.mark('specific',877,305,214,42);v.add(T(984,434,'既定経路は「最後の候補」',18,'#365864',550,'middle'));
}
// 019–021: the real VIP is deliberately not guessed.
{
 const v=view('udr-specific','AppGW → APIMの経路候補','経路選択','実IPを仮定せず、作成後のAPIM内部VIPを使う');
 table(v,{widths:[263,127,160],heads:['一致する宛先','種類','次ホップ'],rows:[['vip','APIMの実内部VIP /32','UDR','Firewall'],['vnet','10.40.2.0/24','System','VNet内']],h:67});
 v.add(R(214,385,550,65,'#eaf3f4')+BLOCK(229,386,520,63,'VIP /32 は、サービス作成後の実出力から設定する。',19,'#365864'));v.mark('actual',216,387,546,61);
 panel(v,'AppGWから同じ宛先へ');chain(v,['AppGW','Firewall','APIM'],{ids:['source','firewall','apim'],details:['送信元','次ホップ','実内部VIP']});
 v.add(BLOCK(806,342,357,93,'候補 /32 と /24 は、同じAPIMのIPに一致する。',19,'#365864'));
}
// 022–024: same-prefix comparison stays visible through the answer and recap.
{
 const v=view('udr-equal-prefix','APIM → AppGWの復路候補','経路選択','公開設定例：宛先 10.40.0.0/24 が同じときは、経路の種類を比べる');
 table(v,{widths:[263,127,160],heads:['一致する宛先','種類','次ホップ'],rows:[['udr','10.40.0.0/24','UDR','Firewall'],['system','10.40.0.0/24','System','VNet内']],h:67});
 panel(v,'経路を選ぶ順番');box(v,null,810,208,345,65,'1. 最も具体的な宛先');box(v,'order',810,326,345,65,'2. 同じ範囲なら種類を比較');arrow(v,null,[[982,273],[982,326]]);
 v.add(T(984,442,'同じ範囲では UDR が System より優先',16,'#617983',500,'middle'));
}
// 025–026: display real UDR inputs rather than an invented priority/port setting.
{
 const v=view('udr-route-conditions','UDRが指定する条件','ルート設定','教材注釈：NSGの数値優先度やポート条件を、UDRへ持ち込まない');
 table(v,{rows:[['destination','宛先プレフィックス','IPの範囲・サービス タグ'],['type','次ホップの種類','VirtualAppliance など'],['ip','次ホップIP','種類に応じて指定'],['port','優先順位・ポート条件','UDRには指定欄なし']],h:54});
 panel(v,'同じVIPなら、経路は同じ');box(v,null,805,220,354,77,'HTTP / gRPC → APIM VIP');
 v.add(BLOCK(806,335,356,99,'ポート別の許可は、NSGやFirewallの規則で判定する。',19,'#365864'));
}
// 027–032: one 56-second question/answer unit. Both representative routes and
// both directions remain in the identical composition for the entire unit.
{
 const v=view('udr-appgw-roundtrip','AppGWとAPIMの往復経路','往復経路','代表UDRと往復の補助図を同じ画面で照合する');
 table(v,{widths:[152,248,150],heads:['送信元RT','宛先','次ホップ'],rows:[['forward','AppGW用','APIM実VIP /32','Firewall'],['return','APIM用','10.40.0.0/24','同じFirewall']],h:70,title:'往路・復路に置く代表ルート'});
 v.add(R(214,392,550,59,'#eaf3f4')+BLOCK(229,393,518,57,'次ホップ種別：VirtualAppliance\nIP：Firewallの実private IP',18,'#365864'));
 panel(v,'同じ接続を、同じFirewallで往復');chain(v,['AppGW','Firewall','APIM'],{y:236,ids:['agw','fw','apim']});
 arrow(v,null,[[1102,295],[1102,335],[1015,335],[1015,295]],'',{color:'#087f8c'});
 arrow(v,null,[[960,295],[960,375],[856,375],[856,295]],'復路',{labelX:910,labelY:401,color:'#087f8c'});
 v.mark('return-flow',850,292,258,88);
 v.mark('firewall',923,232,123,67);v.add(T(984,439,'往路 /32 と、復路 /24 を一組にする',17,'#617983',500,'middle'));
}
// 033–036: keep the hypothetical asymmetry plus the two settings in one view.
{
 const v=view('udr-asymmetry','復路が別経路になる場合を考える','往復経路','教材上の比較：往路だけをFirewallへ向けた場合');
 table(v,{rows:[['outbound','往路のUDR','AppGW → Firewall → APIM'],['asym','復路UDRがない場合','VNet内を直接戻る経路が候補'],['nsg','NSGの状態追跡','応答を許可する'],['return','復路のUDR','応答の次ホップをFirewallへ']],h:54});
 panel(v,'仮に復路だけが直接戻ると');chain(v,['AppGW','Firewall','APIM'],{y:234,ids:['agw','fw','apim']});
 arrow(v,'direct-return',[[1116,293],[1116,350],[856,350],[856,293]],'復路：Firewallを通らない',{labelY:379,dashed:true,color:'#607989'});
 v.add(BLOCK(808,412,355,46,'応答の許可は、転送先を変えない。',17,'#365864'));
}
// 037–040: Portal uses the same two-leg design; AppGW's default is untouched.
{
 const v=view('udr-portal-roundtrip','AppGWとPortal入口の経路','往復経路','作成後のACA環境static IPを使い、往復とも同じFirewallへ送る');
 table(v,{widths:[135,265,150],heads:['送信元RT','宛先','次ホップ'],rows:[['forward','AppGW用','ACA実static IP /32','Firewall'],['return','ACA用','10.40.0.0/24','同じFirewall']],h:70});
 v.add(R(214,387,550,65,'#eaf3f4')+BLOCK(228,389,521,61,'AppGW用：Firewall向けの独自既定経路は追加しない。',19,'#365864'));v.mark('agw-scope',216,389,546,61);
 panel(v,'内部入口を往復する区間');chain(v,['AppGW','Firewall','ACA入口'],{y:236,ids:['agw','fw','aca']});
 arrow(v,null,[[1102,295],[1102,335],[1015,335],[1015,295]],'',{color:'#087f8c'});
 arrow(v,null,[[960,295],[960,375],[856,375],[856,295]],'復路',{labelX:910,labelY:402,color:'#087f8c'});
 v.mark('portal-return',850,292,258,88);
 v.add(T(984,439,'通常Internet出口・Azure制御経路は維持',16,'#617983',500,'middle'));
}
// 041–045: selected /32 and PE network policy share the same view.
{
 const v=view('udr-pe-direct','APIMからPEへ向かう経路','閉域と公開','PEの実IPへの具体的なシステム経路と、NSGのみ有効なPEポリシー');
 table(v,{widths:[205,345],rows:[['system','PE実IP /32','System → PEへ直接'],['default','0.0.0.0/0','UDR → Firewall'],['policy','PEポリシー','NetworkSecurityGroupEnabled'],['rt','PE区画の独自RT','関連付けなし'],['nsg','通信の許可','送信元側・PE受信側など']],h:49});
 panel(v,'接続口とサービス本体を分ける');chain(v,['APIM','PE','Foundry'],{y:237,ids:['source','pe','service'],details:['送信元','VNet内','VNet外']});
 v.add(BLOCK(807,350,354,90,'PEへの接続経路と、サービス本体の全アウトバウンドは別の範囲。',19,'#365864'));
}
// 046–048: management VM and ACA are compared left/right, not on alternations.
{
 const v=view('udr-vm-aca-compare','管理VMとACAの送信経路を比べる','閉域と公開','名前解決後の宛先IPで、公開宛てとPE宛てを分ける');
 for(const [key,x,label,nsg]of[['vm',214,'管理VM','送信元にNSGあり'],['aca',706,'ACA','送信元NSGなし']]){
  table(v,{x,y:183,w:480,widths:[170,310],heads:['確認項目','標準構成'],rows:[[key+'-default','公開宛て','0.0.0.0/0 → Firewall'],[key+'-pe','PE宛て','PE実IP /32 → 直接'],[key+'-nsg','送信元NSG',nsg],[key+'-permit','PE受信NSG','必要な接続元・ポートを許可']],h:45,title:label});
  box(v,key+'-source',x+13,415,127,47,label);box(v,key+'-target',x+302,415,164,47,'PE');arrow(v,null,[[x+140,438],[x+302,438]],'具体的な経路',{labelY:429});
 }
}
// 049–050: internal ACA IP retains the VNet system route, not default /0.
{
 const v=view('udr-internal-direct','APIM → ACA内部IPの経路','閉域と公開','Brokerなどの認証窓口への内部通信を、公開宛ての既定経路と分ける');
 table(v,{widths:[263,127,160],heads:['一致する宛先','種類','次ホップ'],rows:[['system','10.40.2.0/24','System','VNet内'],['default','0.0.0.0/0','UDR','Firewall']],h:67});
 note(v,'標準構成：VNet内 /24 は、既定経路 /0 より具体的。',215,442);
 panel(v,'内部の接続先へ直接');two(v,'APIM','ACA内部入口',{y:239,ids:['apim','aca'],details:['送信元','認証窓口など'],label:'VNet内'});
 v.add(BLOCK(807,351,354,88,'Firewallに許可規則があっても、それだけで経由とは判断しない。',19,'#365864'));
}
// 051–053: default routes plus the downstream permission check.
{
 const v=view('udr-public-egress','公開依存先への既定経路','閉域と公開','ほかに具体的な経路がない公開宛て通信を、同じFirewallへ集約する');
 table(v,{widths:[150,245,155],heads:['送信元RT','宛先','次ホップ'],rows:[['apim','APIM用','0.0.0.0/0','Firewall'],['vm','管理VM用','0.0.0.0/0','同じFirewall'],['aca','ACA用','0.0.0.0/0','同じFirewall']],h:63});
 panel(v,'経路に届いた後で許可を判定');chain(v,['送信元','Firewall','公開依存先'],{y:236,ids:['source','fw','public'],details:['APIM等','規則で判定','認証・監視等']});
 v.add(BLOCK(807,350,354,92,'経路の存在 ≠ 許可\n許可の存在 ≠ 経由の証拠',19,'#365864'));
}
// 054–055: public Gateway request and subsequent internal relay coexist.
{
 const v=view('udr-portal-public','Portalから公開Gatewayを呼ぶ','閉域と公開','ACAからの公開宛て要求と、入口後の内部中継を区間で分ける');
 table(v,{rows:[['request','ACAの接続先','公開GatewayのIP'],['default','ACA用UDR','0.0.0.0/0 → Firewall'],['ingress','公開AppGWの入口','NSG・リスナーで受信'],['inside','入口後の内部中継','AppGW → Firewall → APIM']],h:54});
 panel(v,'同じ基盤でも、公開宛ての要求');chain(v,['ACA','Firewall','公開AppGW'],{y:217,ids:['source','fw','public-gw']});
 chain(v,['AppGW','Firewall','APIM'],{y:366,ids:['agw','same-fw','apim']});
 v.add(T(984,330,'受信後は、内部入口の中継区間へ',16,'#617983',500,'middle'));
}
// 056–061: management ingress/response and Portal's ARM request remain distinct.
{
 const v=view('udr-apim-management','APIM管理系の直接経路','管理通信','代表UDR：apim-management-direct。サービス・タグを宛先にした管理系の復路');
 table(v,{widths:[205,345],rows:[['destination','宛先プレフィックス','ApiManagement'],['type','次ホップの種類','Internet'],['hop','次ホップIP','指定なし'],['nsg','管理受信NSG','ApiManagement / TCP 3443'],['arm','Portal → ARMのAPI','アプリからの公開宛て通信']],h:49});
 panel(v,'Azure管理系からの要求と応答');two(v,'Azure管理系','APIM',{y:207,ids:['azure','apim'],label:'管理受信'});
 box(v,null,810,346,145,59,'APIM');box(v,null,1020,346,145,59,'Azure管理系');arrow(v,'response',[[955,375],[1020,375]],'直接の復路',{labelY:362});
 v.add(T(984,446,'Internet は次ホップ種別。ポート条件ではない',15,'#617983',500,'middle'));
}
// 062–063: DNS proxy setting alone is not a traffic route.
{
 const v=view('udr-dns-context','DNS設定と経路の確認を分ける','経路選択','公開設定例：VNetはAzure提供DNS。FirewallのDNS proxy有効化と混同しない');
 table(v,{rows:[['vnet','VNet dnsServers','[] → Azure 提供DNS'],['proxy','Firewall DNS proxy','enableProxy: true'],['ip','名前解決の確認結果','PEのIP または 公開IP'],['route','経路の確認','宛先IPに一致する次ホップ']],h:54});
 panel(v,'名前と接続経路は別に確かめる');box(v,null,833,204,306,57,'名前 → 宛先IP');box(v,null,833,331,306,57,'宛先IP → 次ホップ');arrow(v,null,[[986,261],[986,331]]);
 v.add(T(984,442,'proxy有効だけで全DNS経由とは言えない',16,'#617983',500,'middle'));
}
// 064–066: a concrete communication and its source definitions.
{
 const v=view('udr-inspection-source','対象通信を決めて定義と照合する','運用確認','教材の標準8経路を、実環境の全経路や疎通結果として扱わない');
 table(v,{rows:[['source','送信元','サブネットと関連ルートテーブル'],['ip','宛先','名前解決後のIP'],['effective','実際の選択','有効な次ホップ'],['permission','必要な許可','NSG・Firewallを照合']],h:54});
 panel(v,'宣言から実IPをたどる');box(v,'network',809,202,350,60,'network.bicep','関連付け・静的経路・伝搬');box(v,'bindings',809,291,350,61,'network-bindings.bicep','APIM VIP /32 を追加');box(v,'portal-bindings',809,381,350,61,'portal-network-bindings.bicep','ACA入口IP /32 を追加');
}
// 067–069: existing route preservation is not a freeze of declared properties.
{
 const v=view('udr-redeploy-preserve','再配備で保持する既存経路','運用確認','lifecycle.py が4テーブルを読み、親テーブル更新へ経路を引き継ぐ');
 table(v,{rows:[['existing','既存経路の読み取り','4テーブルから取得'],['preserve','親テーブルの更新','既存経路を引き継ぐ'],['declared','宣言済みの同名経路','今回のIaCを再適用'],['propagation','伝搬設定','今回のconfigを再適用']],h:54});
 panel(v,'保持と再適用を区別する');box(v,null,817,207,336,57,'既存経路を読み取る');box(v,null,817,332,336,57,'親テーブル更新へ引き継ぐ');arrow(v,null,[[985,264],[985,332]]);
 v.add(T(984,441,'保持しても、何も変わらないわけではない',16,'#617983',500,'middle'));
}
// 070–071: current learning sequence places required hub lessons after #8.
{
 const v=view('udr-bgp-propagation','ゲートウェイ経路伝搬の設定','運用確認','公開例の伝搬設定と、企業接続の存在を区別する');
 table(v,{widths:[240,155,155],heads:['対象RT','config値','Azure値'],rows:[['core','AppGW / APIM / 管理VM','伝搬 true','disable false'],['aca','ACA','切替なし','disable true']],h:68});
 v.add(R(214,386,550,65,'#eaf3f4')+BLOCK(230,387,518,63,'Azure値は disableBgpRoutePropagation。\n利用者の「伝搬 true」を反転して指定。',17,'#365864'));v.mark('inverse',216,388,546,61);
 panel(v,'独立VNetには伝搬元がない');box(v,'gateway',809,207,350,67,'伝搬元Gateway：なし');
 v.add(BLOCK(808,306,355,74,'伝搬有効だけでVPNやExpressRouteは作られない。',19,'#365864'));
 v.add(BLOCK(808,404,355,58,'企業接続は第8回の後の\n必修 A1・A2 で確認する。',18,'#365864'));
}
// 072: the final operations checklist remains a settings view.
{
 const v=view('udr-effective-check','実効経路と往復を確かめる','運用確認','稼働環境の取得画面ではなく、調査で照合する項目の教材チェックリスト');
 table(v,{rows:[['source','1. 送信元','サブネット・ルートテーブル'],['destination','2. 宛先','名前解決後のIP'],['next','3. 次ホップ','有効な経路で確認'],['return','4. 復路','戻る送信元側からも確認'],['permission','5. 許可・疎通','NSG / Firewall / 新しい接続']],h:49});
 panel(v,'同じ通信について往復を追う');chain(v,['送信元','次ホップ','接続先'],{y:236});arrow(v,'return-flow',[[1116,295],[1116,351],[856,351],[856,295]],'復路も確認',{labelY:377,color:'#087f8c'});
}
// 073–075: quiz stays neutral until MANABU's answer. Both candidates are shown.
{
 const v=view('udr-quiz-pe','確認1：PEのIP宛ての経路候補','確認問題','APIMの既定UDRとPEのシステム経路。どちらの候補を選ぶ？');
 table(v,{widths:[265,125,160],heads:['一致する宛先','種類','次ホップ'],rows:[['pe-route','PE実IP /32','System','PEへ直接'],['default-route','0.0.0.0/0','UDR','Firewall']],h:75,title:'同じ宛先に一致する2候補'});
 panel(v,'候補を比べる');box(v,null,807,278,130,63,'APIM');box(v,'pe',1033,207,130,63,'PE');box(v,'fw',1033,369,130,63,'Firewall');
 arrow(v,null,[[937,296],[984,296],[984,238],[1033,238]],'/32',{labelX:978,labelY:222});arrow(v,null,[[937,321],[984,321],[984,400],[1033,400]],'/0',{labelX:978,labelY:438});
 note(v,'宛先に一致する範囲の具体性を比べよう。',215,435);
}
// 076–078: candidate route designs, no answer/preselected needed/not-needed state.
{
 const v=view('udr-quiz-return','確認2：NSGの応答許可と復路UDR','確認問題','往路は AppGW → Firewall → APIM。復路に何を指定する？');
 table(v,{widths:[245,305],heads:['復路の候補','考える設定'],rows:[['direct','復路UDRを置かない','VNet内のシステム経路'],['via-fw','復路UDRを置く','AppGW区画 → Firewall'],['nsg','NSGの状態追跡','接続に対する応答の許可']],h:65,title:'応答の許可と次ホップを分けて比較'});
 panel(v,'復路の候補');chain(v,['AppGW','Firewall','APIM'],{y:236,ids:['agw','fw','apim']});
 arrow(v,'via-fw-flow',[[1116,295],[1116,327],[986,327],[986,295]],'Firewallへ',{labelX:1065,labelY:354});arrow(v,null,[[1116,296],[1116,405],[856,405],[856,296]],'VNet内を直接',{labelY:435,dashed:true,color:'#607989'});
}
// 079–081: the exact route parameters are visible, while its purpose is a neutral
// three-choice list. Only the selected purpose gets a red outline at the answer.
{
 const v=view('udr-quiz-management','確認3：このルートの用途は？','確認問題','宛先 ApiManagement、次ホップ Internet の意味を確認する');
 table(v,{widths:[88,462],heads:['候補','用途'],rows:[['api','A','API利用者への応答すべて'],['arm','B','PortalがARMのAPIを呼ぶ通信'],['management','C','Azure APIM管理系への応答']],h:65,title:'三つの用途から考える'});
 panel(v,'rt-<stem>-apim の代表経路');
 box(v,null,808,205,353,64,'宛先：ApiManagement');box(v,null,808,306,353,64,'次ホップ：Internet');
 v.add(T(984,438,'教材注釈：設定から用途を考える',17,'#617983',500,'middle'));
}
// 082–084: settings and route classes recap the three learning objectives.
{
 const v=view('udr-summary','宛先で選び、区間で分け、往復で確かめる','概要','宣言・実IP・実効経路・必要な許可を、同じ通信について照合する');
 table(v,{widths:[250,300],heads:['通信の区間','標準構成の考え方'],rows:[['selection','経路選択','具体的な範囲を優先'],['roundtrip','AppGW ↔ APIM・ACA','同じFirewallで往復'],['direct','PE・APIM→ACA内部IP','具体的な直接経路'],['management','Azure APIM管理系','必要な直接復路を維持']],h:54});
 panel(v,'設定と実際の通信を結び付ける');box(v,null,810,206,346,57,'送信元と宛先を一つ決める');box(v,null,810,332,346,57,'往復の次ホップと許可を照合');arrow(v,null,[[983,263],[983,332]]);
 v.add(T(984,443,'既定経路 ≠ 全通信の強制経路',17,'#617983',500,'middle'));
}
// 085–087: a stable closing topic, not a new application UI or a diagram detour.
{
 const v=view('udr-next-firewall','次回：Firewallは何を判断するか','概要','第5回は、経路に届いた通信へのルール判定を確認する');
 table(v,{rows:[['network','Networkルール','IP・ポートなどの条件'],['application','Applicationルール','Webの宛先などの条件'],['order','次回確認すること','使い分け・評価順・必要な許可']],h:63});
 panel(v,'経路から、判定の内容へ');two(v,'経路の選択','規則の判定',{y:242});v.add(BLOCK(805,350,360,80,'内部中継や基盤通信に、なぜその許可を置くのか。',19,'#365864'));
}

// One unchanged base composition per question/answer unit. Only this highlight
// group varies. The caller can strip it to prove stable geometry and wording.
await fs.mkdir(OUT,{recursive:true});
function svgFor(v,highlight){
 const red=highlight.map(id=>{
  const b=v.marks.get(id);if(!b)throw Error(`${v.id}: unknown highlight ${id}`);
  return `<rect x="${b[0]}" y="${b[1]}" width="${b[2]}" height="${b[3]}" rx="4" fill="none" stroke="#de343b" stroke-width="2.8"/>`;
 }).join('');
 const shell=renderSettingsShell({viewId:v.id,areaLabel:'ネットワーク設定 / UDR',title:v.title,kind:'ルートテーブル',section:v.section,menu:MENU,subtitle:v.subtitle,footer:'公開設定例に基づく教材用の設定ビュー / 実効経路の取得画面ではない'},`<g font-family="Hiragino Sans,Yu Gothic,Meiryo,sans-serif">${v.body}<g id="highlights">${red}</g></g>`);
 return `<svg xmlns="http://www.w3.org/2000/svg" width="1207" height="511" viewBox="0 0 1207 511"><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="#567e8b"/></marker></defs>${shell}</svg>\n`;
}
const assignments=[
 [5,7,'udr-role',{5:['nsg'],7:['udr']}],
 [8,9,'udr-next-hop',{9:['destination','hop','apim']}],
 [10,13,'udr-associations',{10:['agw','apim','vm','aca'],12:['pep','firewall'],13:['count']}],
 [14,15,'udr-resolved-ip',{15:['resolved','route']}],
 [16,18,'udr-prefix',{16:['ip32','subnet24'],18:['default']}],
 [19,21,'udr-specific',{19:['vip','vnet'],20:['vip'],21:['actual']}],
 [22,24,'udr-equal-prefix',{23:['udr'],24:['order']}],
 [25,26,'udr-route-conditions',{26:['destination','port']}],
 [27,32,'udr-appgw-roundtrip',{28:['forward'],30:['return','return-flow'],31:['forward','return'],32:['firewall']}],
 [33,36,'udr-asymmetry',{34:['asym','direct-return'],36:['nsg','return']}],
 [37,40,'udr-portal-roundtrip',{37:['forward','return'],38:['forward','return','portal-return'],39:['agw-scope'],40:['agw-scope']}],
 [41,45,'udr-pe-direct',{42:['system'],43:['pe','service'],45:['policy','rt','nsg']}],
 [46,48,'udr-vm-aca-compare',{46:['aca-pe'],47:[],48:['aca-nsg','aca-permit','vm-pe']}],
 [49,50,'udr-internal-direct',{50:['system']}],
 [51,53,'udr-public-egress',{51:[],52:['apim','vm','aca'],53:['fw']}],
 [54,55,'udr-portal-public',{55:['default','fw','ingress']}],
 [56,61,'udr-apim-management',{56:[],57:['nsg','response'],58:['destination','type'],59:['type','hop'],60:['arm'],61:['arm']}],
 [62,63,'udr-dns-context',{63:['vnet','proxy']}],
 [64,66,'udr-inspection-source',{65:['source','ip','effective','permission'],66:['network','bindings','portal-bindings']}],
 [67,69,'udr-redeploy-preserve',{68:['existing','preserve','declared','propagation'],69:[]}],
 [70,71,'udr-bgp-propagation',{70:['core','aca','gateway'],71:['gateway']}],
 [72,72,'udr-effective-check',{72:['source','destination','next','return','permission']}],
 [73,75,'udr-quiz-pe',{74:['pe-route','pe'],75:['pe-route','pe']}],
 [76,78,'udr-quiz-return',{77:['via-fw','via-fw-flow'],78:['via-fw','via-fw-flow']}],
 [79,81,'udr-quiz-management',{80:['management'],81:['management']}],
 [82,84,'udr-summary',{82:['roundtrip'],83:['selection','roundtrip','direct'],84:['roundtrip','direct','management']}],
 [85,87,'udr-next-firewall',{85:['network','application'],86:['order'],87:[]}]
];
const rendered=new Set();
for(const turn of script.turns){
 const n=Number(turn.id.match(/turn-(\d+)/)?.[1]);if(n<=4)continue;
 const a=assignments.find(([start,end])=>n>=start&&n<=end);if(!a)throw Error('Missing view for turn '+n);
 const [, ,id,targets]=a,v=views.get(id),highlight=targets[n]??[];
 const suffix=highlight.length?highlight.join('_'):'base',name=`${id}--${suffix}.svg`;
 if(!rendered.has(name)){await fs.writeFile(new URL(name,OUT),svgFor(v,highlight));rendered.add(name);}
 turn.visual={layout:'svg',viewId:id,heading:v.title,svg:'diagrams/'+name,highlightRows:highlight,settingsStyle:'teaching-settings-v2',cameraPolicy:'fixed'};
}
script.visualStyle='teaching-settings-v2';
await fs.writeFile(new URL('script.json',ROOT),JSON.stringify(script,null,2)+'\n');
console.log(JSON.stringify({style:script.visualStyle,bodyTurns:script.turns.length-4,views:views.size,svgFiles:rendered.size,camera:'fixed'}));
