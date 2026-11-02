// Episode 06 teaching-settings-v2 authoring. Speech and timing are preserved.
import fs from 'node:fs/promises';
import {renderSettingsShell,renderSettingsSvg} from '../../settings-visual.mjs';

const here=new URL('./',import.meta.url),read=async n=>JSON.parse(await fs.readFile(new URL(n,here),'utf8'));
const source=await read('revisions/diagram-v1/script.json');
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const clone=v=>structuredClone(v);
const MENU=['通信の許可','宛先の生成','出口とSNAT','期待値と実測'];
const views=new Map(),groups=[];
function frame(id,title,section,notice=''){
 return {layout:'settings',viewId:id,areaLabel:'第6回 / 外向き通信の設定',heading:title,title,kind:'ネットワーク',section,menu:MENU,subtitle:'設定と通信の対応を確認する教材ビュー',footer:'教材・リポジトリ定義・公開設定例に基づく。稼働環境の取得画面ではない。',...(notice?{notice}:{})};
}
function table(id,title,section,rows,notice='',columns=[{label:'設定・確認対象',width:320},{label:'値・対応する通信',width:652}]){
 const v={...frame(id,title,section,notice),table:{columns,rows:rows.map(([id,...cells])=>({id,cells}))},highlightRows:[]};
 renderSettingsSvg(v);views.set(id,v);return id;
}
function fields(id,title,section,rows,notice=''){
 const v={...frame(id,title,section,notice),fields:rows.map(([id,label,value])=>({id,label,value})),highlightFields:[]};
 renderSettingsSvg(v);views.set(id,v);return id;
}
function assign(from,to,id,selected={}){
 groups.push({viewId:id,from,to});
 for(let n=from;n<=to;n++){
  const v=clone(views.get(id));if(!v)throw new Error('Unknown view '+id);
  const h=selected[n]??[];
  if(v.layout==='svg'){
   const suffix=h.length?h.join('-'):'base';v.svg=`diagrams/${id}--${suffix}.svg`;v.highlightRows=h;
   const item=svgDefinitions.get(id);pendingSvg.set(v.svg,{...item,highlights:h});
  }else v[v.table?'highlightRows':'highlightFields']=h;
  source.turns[n-1].visual=v;
 }
}

// Custom contents use the shared shell and the same fixed content rectangle.
// Only red outlines differ among variants of one viewId.
const svgDefinitions=new Map(),pendingSvg=new Map();
const R=(x,y,w,h,fill='#fff',stroke='#d8e2e7',sw=1)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="4" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
const T=(x,y,s,size=18,color='#20323e',anchor='start')=>`<text x="${x}" y="${y}" fill="${color}" font-size="${size}" text-anchor="${anchor}" font-family="Hiragino Sans,Yu Gothic,Meiryo,sans-serif">${esc(s)}</text>`;
const O=(x,y,w,h)=>R(x,y,w,h,'none','#de343b',2.8);
const A=(x1,y1,x2,y2,back=false)=>`<path d="M${x1} ${y1}H${x2}" stroke="${back?'#647984':'#087f8c'}" stroke-width="2.6" fill="none" marker-end="url(#arrow)" ${back?'stroke-dasharray="6 4"':''}/>`;
const defs='<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#087f8c"/></marker></defs>';
function smallTable(rows,{top=268,rowHeight=43,labelWidth=315}={}){
 let body=R(214,top,972,25,'#edf1f5')+T(228,top+18,'設定・確認対象',15,'#586875')+T(228+labelWidth,top+18,'値・教材注釈',15,'#586875');
 const bounds={};
 rows.forEach(([id,label,val],i)=>{const y=top+25+i*rowHeight;body+=R(214,y,972,rowHeight)+T(228,y+rowHeight/2+6,label,17,'#087f8c')+T(228+labelWidth,y+rowHeight/2+6,val,17);bounds[id]=[216,y+2,968,rowHeight-4];});
 return {body,bounds};
}
function flowNodes(labels,{top=156,details=[],returnLabel=null}={}){
 let body='';const xs=[231,565,899],w=270,h=70,bounds={};
 labels.forEach(([id,label],i)=>{body+=R(xs[i],top,w,h,'#f0f8f8','#7da4ab')+T(xs[i]+w/2,top+28,label,21,'#20323e','middle');if(details[i])body+=T(xs[i]+w/2,top+53,details[i],15,'#647984','middle');bounds[id]=[xs[i]+2,top+2,w-4,h-4];if(i<2)body+=A(xs[i]+w,top+35,xs[i+1]-5,top+35);});
 if(returnLabel){body+='<path d="M1034 226V247H366V226" stroke="#647984" stroke-width="2" stroke-dasharray="6 4" fill="none" marker-end="url(#arrow)"/>'+T(700,246,returnLabel,14,'#647984','middle');}
 return {body,bounds};
}
function svg(id,title,section,make){
 const meta=frame(id,title,section);delete meta.layout;
 views.set(id,{layout:'svg',viewId:id,heading:title,svg:`diagrams/${id}--base.svg`,highlightRows:[]});
 svgDefinitions.set(id,{meta,make});return id;
}
function composite(flow,tab){return {body:flow.body+tab.body,bounds:{...flow.bounds,...tab.bounds}};}

table('egress-reading','外向き通信は、許可・経路・送信元をそろえて読む','通信の許可',[
 ['permit','送信元・用途・宛先','APIM / 管理VM / ACA のWeb依存先'],
 ['path','経路','公開宛てはFirewall経由。PE直接経路は別'],
 ['source','送信元','接続先から見えるIPと期待値を確認'],
 ['basis','この教材の根拠','定義・公開設定例。稼働Azureの実測ではない']
]);
assign(5,9,'egress-reading',{5:['permit'],7:['permit','path','source'],8:['basis'],9:['permit','source']});

fields('application-conditions','Applicationルールで照合する条件','通信の許可',[
 ['source','送信元','APIM区画 / 管理VM区画 / ACA区画'],
 ['protocol','プロトコル・ポート','HTTP 80 / HTTPS 443'],
 ['fqdn','宛先','設定されたFQDN'],
 ['evaluation','適用する前提','Firewall経由。Networkで未一致のWeb通信']
]);
assign(10,12,'application-conditions',{10:['source','protocol','fqdn'],12:['fqdn','evaluation']});

table('apim-vm-web','APIMと管理VMのWeb依存先を分ける','通信の許可',[
 ['apim-cert','APIM / 証明書','証明書チェーン・失効情報の取得先'],
 ['apim-update','APIM / 管理基盤の更新','Windows更新先・基盤FQDN'],
 ['vm-cert','管理VM / 証明書','証明書検証に必要な取得先'],
 ['vm-package','管理VM / Linuxパッケージ','Ubuntu・Microsoftの配布先'],
 ['wildcard','宛先の書式','固定ホスト / *.windowsupdate.com 等']
],'表の用途説明は教材注釈。送信元ごとの規則を確認する');
assign(13,17,'apim-vm-web',{13:['apim-cert','apim-update'],15:['apim-update','vm-cert','vm-package'],16:['apim-cert','vm-cert'],17:['wildcard']});

svg('portal-build-web','実行基盤・ビルド・Portal試用の依存先','通信の許可',()=>{
 const tab=smallTable([
 ['aca','ACA / 実行基盤','起動・認証 / api.ipify.org'],
 ['build','管理VM / ビルド','GitHub・npm・Docker・Python・OS配布先'],
 ['gateway','ACA / 公開Gateway','gatewayOrigin のホスト1個 / HTTPS 443'],
 ['pe','ACRのPE接続','公開Web許可とは別の直接経路']
 ],{top:151,rowHeight:40,labelWidth:315});
 const f=flowNodes([['portal','Portal / ACA'],['fw','Firewall'],['agw','公開Gateway']],{top:366,details:['新しいサーバー間要求','ACAの外向き既定経路','選択環境の入口']});
 return composite(f,tab);
});
assign(18,22,'portal-build-web',{19:['aca','build'],20:['build','pe'],21:['gateway'],22:['portal']});

svg('generated-fqdns','入力の空配列を、最終許可と同一視しない','宛先の生成',()=>{
 const f=flowNodes([['input','入力JSON'],['generate','設定生成・API公開'],['deployed','配備後の設定']],{details:['公開params例','選択した設定から追加','Azure上のルールと照合']});
 const tab=smallTable([
 ['apim','apimAllowedFqdns','公開params例：[]'],
 ['vm','managementAllowedFqdns','公開params例：[]'],
 ['reflect','反映を確認する対象','生成された設定 / 配備後のルール']
 ],{top:267,rowHeight:48});
 return composite(f,tab);
});
assign(23,25,'generated-fqdns',{24:['generate','reflect'],25:['input','reflect']});

table('certificate-generation','証明書の選択設定から管理VMの許可先を生成','宛先の生成',[
 ['ca','ACME CA','production / staging の選択に対応'],
 ['dns','DNS API','Cloudflare / Route53 / Azure から選択'],
 ['powershell','PowerShell配布先','cdn.oneget.org / *.powershellgallery.com'],
 ['entry','公開ホストの検証','公開Gateway / Portal のホスト']
],'全DNS事業者のAPIを一括許可するという意味ではない');
assign(26,29,'certificate-generation',{26:['ca','dns','powershell'],28:['ca','dns'],29:['entry']});

table('api-publication-fqdns','API公開でホストを追加し、既存許可を保持する','宛先の生成',[
 ['backend','外部バックエンド','API / Operation のHTTPS・WSS / TLS 443'],
 ['identity','本人接続','management.azure.com / identityEndpoint'],
 ['append','API公開時の更新','未登録のDNSホストを追加。既存宛先を保持'],
 ['delete','APIの削除・変更','旧FQDNの許可は自動削除しない'],
 ['retain','次回基盤配備への引継ぎ','所有・APIM送信元・HTTPS境界を検査']
],'対象は明示DNSホスト。URLパスや本人認証の判断とは別');
assign(30,35,'api-publication-fqdns',{31:['backend'],32:['identity'],34:['append','delete'],35:['delete','retain']});

table('additional-rules','追加ルールの種別と、変更するグループを確認','宛先の生成',[
 ['network','追加Networkルール','一致した通信はApplicationへ進まない'],
 ['application','追加Applicationルール','送信元と必要なWeb FQDNを絞る'],
 ['portal','Portal公開Gateway用','rcg-portal-gateway を更新'],
 ['base','基盤用のグループ','rcg-portal を置き換えない']
]);
assign(36,36,'additional-rules',{36:['network','portal','base']});

svg('vm-explicit-egress','管理VMの公開IP・既定経路・暗黙出口を分ける','出口とSNAT',()=>{
 const f=flowNodes([['vm','管理VM'],['fw','Firewall'],['dest','公開依存先']],{details:['Public IPなし','明示した共通出口','許可されたWeb宛先']});
 const tab=smallTable([
 ['route','default-egress','0.0.0.0/0 → Firewall'],
 ['implicit','defaultOutboundAccess','false：暗黙の出口に依存しない'],
 ['allow','通信の許可 / SNAT','UDR・暗黙出口属性とは別に確認'],
 ['subnets','属性を明示する5区画','AppGW / APIM / PE / 管理VM / ACA']
 ],{top:258,rowHeight:43});
 return composite(f,tab);
});
assign(37,42,'vm-explicit-egress',{38:['route'],39:['vm','fw'],41:['implicit','allow'],42:['subnets']});

table('public-ip-resources','AppGWの入口IPと、Firewallの出口IPは別','出口とSNAT',[
 ['ingress','AppGW / pip-agw-<stem>','利用者向けの公開フロントエンド'],
 ['egress','Firewall / pip-afw-<stem>','Standard / IPv4 / Static。公開宛ての送信元'],
 ['route','APIM・管理VM・ACA','既定UDR：0.0.0.0/0 → Firewall'],
 ['direct','具体的な経路・管理例外','PE / VNet内の直接経路等を優先'],
 ['scope','この教材の標準Bicep','NAT Gateway・LB outboundRulesの定義なし']
],'実IPは環境の出力から取得。実環境の追加資源は未調査');
assign(43,47,'public-ip-resources',{44:['ingress','egress'],45:['direct'],46:['route','direct'],47:['scope']});

svg('public-snat','SNATで、外部から見える公開送信元が変わる','出口とSNAT',()=>{
 const f=flowNodes([['source','APIM・管理VM・ACA'],['fw','Firewall'],['dest','公開接続先']],{details:['送信元：private IP','SNATによる変換','送信元：Firewall公開IP']});
 const tab=smallTable([
 ['meaning','SNAT','通信の送信元IP等を変換'],
 ['ip','この標準経路の公開送信元','Firewallに関連付けた公開IP'],
 ['permit','接続先の許可元IP','対象環境の出力と経路から確認']
 ],{top:267,rowHeight:48});
 return composite(f,tab);
});
assign(48,50,'public-snat',{49:['fw','meaning','ip'],50:['dest','permit']});

svg('private-network-snat','Networkのprivate宛ては、送信元を保持する','出口とSNAT',()=>{
 const f=flowNodes([['agw','AppGW'],['fw','Firewall / Network'],['target','APIM・ACA']],{details:['新しい内部接続','private宛ては非SNAT','送信元：AppGW private IP'],returnLabel:'復路も同じFirewallへ（教材補助図）'});
 const tab=smallTable([
 ['ranges','snat.privateRanges','公開例：IANAPrivateRanges'],
 ['allow','通信許可 / 経路','非SNATの範囲とは別にAllow・往復UDRを確認'],
 ['idps','IDPSのprivateRanges','方向分類の設定。SNAT設定とは別']
 ],{top:267,rowHeight:48});
 return composite(f,tab);
});
assign(51,56,'private-network-snat',{52:['target'],53:['ranges'],54:['allow'],55:['idps'],56:['fw','allow']});

svg('snat-by-path','ルール種別と直接経路で、SNATの扱いを分ける','出口とSNAT',()=>{
 const rows=[
  ['network','Network / private宛て','AppGW → FW → APIM','非SNAT：privateRangesを適用'],
  ['application','Applicationルール','アプリ → FW → 公開先','透明プロキシ。private宛てでもSNAT'],
  ['pe','PEへの直接接続','通信元 → PE → サービス','Firewall非経由。FWによるSNATなし'],
  ['sender','見える送信元の照合','AppGWが新しい内部接続を作る','HTTPヘッダーの利用者情報とNSGは別']
 ];
 let body=R(214,151,972,26,'#edf1f5')+T(228,170,'経路・ルール種別',15,'#586875')+T(518,170,'教材補助図・接続',15,'#586875')+T(842,170,'送信元の扱い',15,'#586875');const bounds={};
 rows.forEach(([id,label,route,note],i)=>{const y=177+i*69;body+=R(214,y,972,69)+T(228,y+36,label,17,'#087f8c')+T(518,y+36,route,16)+T(842,y+36,note,15);bounds[id]=[216,y+2,968,65];});
 return {body,bounds};
});
assign(57,62,'snat-by-path',{58:['application'],59:['application'],61:['pe'],62:['sender']});

fields('expected-vs-measured','期待値の受渡しと、実測・照合を分ける','期待値と実測',[
 ['output','infra出力 expectedEgressIp','Firewallの公開IPを期待値として出力'],
 ['env','Portalの環境変数','EXPECTED_EGRESS_IP へ渡す'],
 ['probe','出口確認先の通信許可','api.ipify.org'],
 ['automatic','現在のPortal実装','この環境変数による自動実測・照合処理はない']
],'期待値・確認先があることと、実測結果があることは別');
assign(63,66,'expected-vs-measured',{64:['output','env'],65:['probe','automatic'],66:['output','automatic']});

fields('measurement-origin','対象の実行場所から、同じ条件で出口を測る','期待値と実測',[
 ['target','実行場所','Portalの出口は、対象Portalの実行場所から確認'],
 ['expected','期待値','対象環境の出力から取得したFirewall公開IP'],
 ['probe','確認先','許可された出口確認先へ対象から接続'],
 ['record','結果の記録','環境 / 実行場所 / 時刻 / 確認先 / 観測値']
],'端末からの測定は、その端末の出口を測るもの');
assign(67,69,'measurement-origin',{68:['target','expected','probe'],69:['record']});

table('unexpected-egress','出口IPが違うときに、比較条件と経路を確かめる','期待値と実測',[
 ['origin','比較の条件','同じ環境・実行場所からの通信か'],
 ['path','実効経路・出口資源','FW / ハブ集約など、実際に通る出口は何か'],
 ['limit','観測の範囲','確認先1件の結果を全宛先へ一般化しない'],
 ['pe','PEへの直接経路','公開宛ての出口IP確認とは別'],
 ['evidence','そろえる根拠','対象環境の出力 / 実際の経路 / 観測結果']
]);
assign(70,73,'unexpected-egress',{71:['origin','path'],72:['limit','pe'],73:['evidence']});

table('quiz-generated','確認1：入力の空配列から、最終許可を判断できる？','宛先の生成',[
 ['input','公開params例','apimAllowedFqdns / managementAllowedFqdns：[]'],
 ['generate','設定生成・API公開','入力から配備する設定を組み立てる工程'],
 ['deployed','確認する最終状態','生成設定と、Azure上に配備したルール'],
 ['retained','旧FQDNの許可','API公開は既存宛先を保持する追加方式']
],'入力の状態と、生成・配備後の状態を比べる');
assign(74,76,'quiz-generated',{75:['generate','deployed'],76:['retained']});

fields('quiz-private-ranges','確認2：非SNATの範囲なら、Allow確認は不要？','出口とSNAT',[
 ['ranges','Network / snat.privateRanges','NetworkでSNATしない宛先範囲'],
 ['permit','通信許可・経路','NSG / FirewallのAllowと、往復経路'],
 ['application','Applicationの扱い','透明プロキシによるSNATはNetworkと別']
],'送信元変換と、通信を許可する判断を区別する');
assign(77,79,'quiz-private-ranges',{78:['ranges','permit','application'],79:['ranges','permit']});

fields('quiz-measured','確認3：期待値と確認先があれば、自動照合済み？','期待値と実測',[
 ['expected','Portalの環境変数','EXPECTED_EGRESS_IP'],
 ['probe','通信を許可する確認先','api.ipify.org'],
 ['automatic','自動実測・照合の実装','現在のPortalコードには処理なし'],
 ['evidence','一致を確認する根拠','対象の実行場所からの測定結果と期待値']
],'確認できた通信の範囲と、設定から分かることを分ける');
assign(80,82,'quiz-measured',{81:['automatic','evidence'],82:['expected','evidence']});

table('egress-summary','外向き通信を、設定から観測結果まで説明する','期待値と実測',[
 ['permit','1 / Web依存先','送信元・用途・FQDNをそろえて許可'],
 ['generate','2 / 生成される宛先','入力・設定生成・API公開・配備結果を照合'],
 ['snat','3 / 出口と送信元','経路・Network / Application / PEを区別'],
 ['measurement','4 / 出口IPの確認','期待値と、対象から測った実測を分ける']
]);
assign(83,85,'egress-summary',{83:['permit','generate'],84:['snat'],85:['snat','measurement']});

fields('next-troubleshooting','第7回：同じ通信の調査材料をそろえる','期待値と実測',[
 ['question','調査の起点','どこから / どこへ / どの経路で'],
 ['configuration','確認する設定・状態','DNS / 実効経路 / NSG'],
 ['logs','判断の記録','Firewallログと、対象の通信を照合']
]);
assign(86,87,'next-troubleshooting',{86:['configuration','logs'],87:['question']});

await fs.mkdir(new URL('diagrams/',here),{recursive:true});
for(const [file,{meta,make,highlights}] of pendingSvg){
 const {body,bounds}=make();let outlines='';
 for(const id of highlights){if(!bounds[id])throw new Error('Missing SVG outline target '+id);outlines+=O(...bounds[id]);}
 const svg=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1207 511">${defs}${renderSettingsShell(meta,body+outlines)}</svg>`;
 await fs.writeFile(new URL(file,here),svg);
}
source.visualStyle='teaching-settings-v2';
await fs.writeFile(new URL('script.json',here),JSON.stringify(source,null,2)+'\n');
console.log(JSON.stringify({bodyTurns:source.turns.filter(t=>t.chapterId!=='opening').length,views:groups.length,svgVariants:pendingSvg.size,groups},null,2));
