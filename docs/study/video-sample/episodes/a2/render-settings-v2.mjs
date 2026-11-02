// A2-local static teaching views. Spoken content and shared renderers stay unchanged.
import fs from 'node:fs/promises';
import {renderSettingsShell,renderSettingsBody} from '../../settings-visual.mjs';
const HERE=new URL('./',import.meta.url),file=new URL('script.json',HERE);
const script=JSON.parse(await fs.readFile(file,'utf8'));
const clone=v=>JSON.parse(JSON.stringify(v));
const esc=v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const txt=(x,y,s,size=19,color='#20323e',weight=400,anchor='start')=>`<text x="${x}" y="${y}" font-size="${size}" fill="${color}" font-weight="${weight}" text-anchor="${anchor}">${esc(s)}</text>`;
const box=(x,y,w,h,fill='#fff',stroke='#c7d9df')=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="5" fill="${fill}" stroke="${stroke}"/>`;
const red=(x,y,w,h,id)=>`<rect class="highlight" data-target="${id}" x="${x}" y="${y}" width="${w}" height="${h}" rx="5" fill="none" stroke="#de343b" stroke-width="2.8"/>`;
const arrow=(d,label,x,y)=>`<path d="${d}" fill="none" stroke="#087f8c" stroke-width="2.6" marker-end="url(#arr)"/>`+(label?txt(x,y,label,16,'#087f8c',500,'middle'):'');
const menu=['切替の範囲','DNS設定','Private DNS','通信の許可','送信元と出口','切替と検証','切戻しと再配備','確認問題'];
const common=(viewId,title,section,extra={})=>({viewId,areaLabel:'ハブ接続後の設定と切替',title,kind:'接続案の設定（教材）',section,menu,subtitle:'教材・現行定義に基づく確認項目。稼働環境の取得値ではない',footer:'実FQDN・IP・経路・許可は対象環境で照合し、設定とレコードの管理元を確定する',...extra});
const fields=(id,title,section,rows,extra={})=>({layout:'settings',...common(id,title,section),fields:rows.map(([id,label,value])=>({id,label,value})),highlightFields:[],...extra});
const table=(id,title,section,columns,rows,extra={})=>({layout:'settings',...common(id,title,section),table:{columns,rows:rows.map(([id,...cells])=>({id,cells}))},highlightRows:[],...extra});
const views={
 purpose:fields('a2-completion','名前・経路・許可・管理元を一組で確認','切替の範囲',[
  ['name','名前の回答','必要な公開名・内部名を正しい接続先へ解決'],
  ['path','維持する経路','ローカルFWの入口検査・PE直接接続を維持'],
  ['owner','対象と担当','出口は区画ごとに採用。企業側とCore側の担当を合意'],
  ['process','完了までの流れ','保存 → 準備 → 段階切替 → 検証 → 復旧 → 再配備']]),
 dns:table('a2-dns-settings','VNet用DNSとFirewall自身のDNSを分けて確認','DNS設定',[
  {label:'設定対象',width:340},{label:'入力・値',width:260},{label:'意味・確認すること',width:372}],[
  ['vnet-ip','network.dnsServers','IP配列','VNetと対応NSGのDNS宛先を変更'],
  ['vnet-empty','network.dnsServers','未指定 / []','Azure提供DNS'],
  ['fw-inherit','network.firewall.dnsServers','未指定','VNet側の値を継承'],
  ['fw-empty','network.firewall.dnsServers','明示 []','FW自身はAzure提供DNS'],
  ['fw-ip','network.firewall.dnsServers','IP配列','FW上流DNSを個別に指定'],
  ['proxy','DNS Proxy / enableProxy','現行 true','VNetの宛先は自動変更しない']]),
 dnsReach:fields('a2-dns-reach','中央DNSへの経路・許可とサービス反映','DNS設定',[
  ['path','DNSへの到達','往復経路とTCP / UDP 53を用意'],
  ['custom','現行custom-dnsの送信元','APIM・管理VMだけ。AppGW・ACAは含まない'],
  ['additional','AppGW / ACAがFW経由なら','必要な送信元・宛先・53番の許可を追加設計'],
  ['apply','反映方法と保守時間','APIM構成適用・AppGW停止/開始・VM DNS再取得等']]),
 ownership:fields('a2-zone-ownership','ゾーンの配置と、更新・削除の管理元','Private DNS',[
  ['local','現在のゾーンを維持','中央DNSの転送先・解決用VNetリンクを整備'],
  ['central','中央ゾーンへ移管','zone group参照先とAPIM / ACA実IPレコードを変更'],
  ['iac','現行IaCの境界','既存中央ゾーンIDの公開入力なし。コード・配備も対応'],
  ['owner','レコードの管理','手動で二重管理せず、更新・削除の担当を確定']]),
 publicNames:fields('a2-public-names','API公開GatewayとPortalの公開AはAppGWへ','Private DNS',[
  ['gateway','APIの公開Gateway名','公開A → AppGWの公開IPを維持'],
  ['portal','Portalの公開名','公開A → AppGWの公開IPを維持'],
  ['scope','内部DNS集約の範囲','公開名を一括してprivate IPへ変える設計とは別']]),
 nsg:table('a2-observed-source','NSGが観測する送信元で、必要な許可を追加','通信の許可',[{label:'照合する設定',width:310},{label:'変更・維持の判断',width:662}],[
  ['source','送信元CIDR・宛先IP・ポート','中継やSNAT後にNSGが観測する送信元を確認'],
  ['operator','管理VMの運用元','運用元設定と実際の送信元を照合'],
  ['pe','PEの許可','各サービスの実PE IPへ限定する'],
  ['deny','終端拒否','4NSGの受信、APIM / 管理VMの送信終端拒否を維持'],
  ['changed','新しい宛先','中央DNSなど変更した宛先へ必要な許可を追加']]),
 tag:fields('a2-virtualnetwork-tag','VirtualNetworkタグの範囲を再確認','通信の許可',[
  ['range','タグが含む範囲','自VNetだけでなく、ピアリング先等も含む'],
  ['effective','接続後の実効NSG','既定許可も含めて照合'],
  ['focus','特に確認する区画','独自の送信終端拒否がないAppGW・PE']]),
 preserve:fields('a2-preserve-controls','基盤・入口・private接続の制御を維持','通信の許可',[
  ['platform','Azure基盤・APIM管理','AppGW基盤通信、APIM管理通信と直接復路を維持'],
  ['local','内部入口の検査','AppGW ↔ ローカルFW ↔ APIM / ACAを維持'],
  ['direct','直接通信','PEへの接続・APIM → ACAを公開出口と区別'],
  ['auth','到達性と認証','Public Network Access/IAMを一括緩和しない。SSH認証維持']]),
 rules:table('a2-firewall-rules','ハブFirewallの依存先と評価順を確認','通信の許可',[{label:'規則・確認点',width:320},{label:'用途と注意',width:652}],[
  ['network','Networkルール','IP・サービス・タグ・ポート。先に一致すると評価終了'],
  ['application','Applicationルール','Web宛てのFQDNを扱う'],
  ['wide','広いTCP 443許可','FQDN制御の代わりにしない'],
  ['central','中央ポリシー','対象区画の実効依存先、既定拒否と企業ルールを照合']]),
 expected:table('a2-egress-expectation','出口の実測と、許可元・期待値を対応付ける','送信元と出口',[{label:'確認対象',width:320},{label:'現行の参照・意味',width:310},{label:'ハブ出口採用時の対応',width:342}],[
  ['observer','allow-certificate-observer','ローカルFW公開IPを参照','新しい実送信元と許可を照合'],
  ['expected','expectedEgressIp','ローカルFW公開IP（期待値）','自動実測・ハブIPへの自動追従なし'],
  ['portal','Portal → 公開Gateway','公開呼出しの実送信元','接続先・公開AppGWの許可元と照合']]),
 baseline:table('a2-before-cutover','現状保存と事前準備を、切替前にそろえる','切替と検証',[{label:'段階・対象',width:300},{label:'保存・準備する内容',width:672}],[
  ['network','現状の構成','CIDR・peering・4RTの全経路と伝搬'],
  ['settings','名前と制御','VNet / FW DNS、NSG / FW、ゾーン・リンク・レコード'],
  ['criteria','出口と復旧条件','出口IP・対象と担当・合格条件・切戻し条件'],
  ['central','中央側の準備','許可・復路、DNS転送・参照、ログ検索先'],
  ['query','切替前の直接照会','中央DNSへ問い合わせ、公開名・内部名の回答を確認'],
  ['operator','保守経路と権限','両側peering、管理VMへの到達と作業権限']]),
 verify:fields('a2-new-connection','新しい接続を開始し、同じ通信で照合','切替と検証',[
  ['dns','名前の回答','8ゾーンの実FQDN・公開名・外部名を確認'],
  ['route','実効経路','宛先ごとに入口・公開出口・社内・中央DNSへの往復を確認'],
  ['allow','許可とログ','NSG判定・Firewallログを照合'],
  ['source','実送信元','公開先が見たSNAT元を確認'],
  ['services','機能と保守の範囲','API / Portal / PE・管理・SSH・証明書・イメージ・監視']],{notice:'古いDNS回答や既存接続の成功に依存しない'}),
 observe:fields('a2-observability','中央側の診断・通知・担当も確定する','切替と検証',[
  ['logs','ログの収集と検索先','中央Firewall・DNS・peeringの診断を確認'],
  ['alerts','異常の通知','アラート条件・通知先・対応担当を合意'],
  ['scope','Core既存通知の範囲','共有ハブ資源まで自動で覆うとは扱わない']]),
 rollback:fields('a2-rollback','保存したCore側の変更範囲へ戻す','切戻しと再配備',[
  ['scope','切戻す対象','合意したCore側のDNS・出口・伝搬・許可・参照等'],
  ['shared','共有資源の扱い','共有ハブ全体や他スポークの設定は巻き戻さない'],
  ['verify','復旧後の確認','同じ通信を新しい接続で再確認'],
  ['persist','継続採用する設定','config・IaC・配備処理へ反映し、次回upの値を確認']]),
 redeploy:table('a2-redeployment','既存経路の保持と、再配備・撤去の管理','切戻しと再配備',[{label:'管理するもの',width:310},{label:'現行の挙動と確認する責任',width:662}],[
  ['keep','4RTの既存ルート','親RT更新へ引き継ぐ。恒久化の保証とは別'],
  ['apply','Bicepの同名ルート・伝搬','宣言値を再適用。出口がローカルFWへ戻り得る'],
  ['zone','中央ゾーンとレコード','PE / zone group更新・削除の影響と管理元を合意'],
  ['down','down時の解除担当','両側peering・中央ゾーン参照・FW許可・Gateway共有']]),
 q1:fields('a2-quiz-dns','確認①：VNetだけ中央DNSなら、FWも同じ？','確認問題',[
  ['given','前提','VNetは中央DNS。FW側dnsServersは明示 []'],
  ['a','選択肢 A','Firewall自身も中央DNSを使う'],
  ['b','選択肢 B','Firewall自身はAzure提供DNSを使う']],{notice:'設定対象と入力の意味を考える'}),
 q2:fields('a2-quiz-source','確認②：ハブCIDR全体の許可へ置き換える？','確認問題',[
  ['given','前提','Coreを企業ハブへ接続した'],
  ['a','選択肢 A','NSGの許可元をハブCIDR全体へ置き換える'],
  ['b','選択肢 B','実送信元・宛先・ポートで必要な分を許可する']],{notice:'接続範囲と、通信許可の範囲を分けて考える'}),
 q3:fields('a2-quiz-up','確認③：手動UDRは、次のupでも同じ？','確認問題',[
  ['given','前提','default-egressを手動でハブFWへ変更した'],
  ['a','選択肢 A','既存ルート保持があるので、次のupも必ず維持'],
  ['b','選択肢 B','同名ルート・伝搬の再適用を確認し、正本へ反映']],{notice:'一度の疎通と、再配備での維持を区別する'}),
 summary:fields('a2-summary','名前・許可・送信元と、切替後の管理をそろえる','切替の範囲',[
  ['dns','DNSと8ゾーン','VNet / FWを分け、転送・参照・レコード管理元を確認'],
  ['policy','通信の許可と送信元','NSG / FW、SNAT・出口IPを同じ新規接続で照合'],
  ['process','切替と運用','保存・準備 → 段階切替 → 検証 → 復旧・監視'],
  ['iac','再配備と撤去','IaC・配備処理と、中央側を含む担当を確定'],
  ['next','次回 第9回','WAFの検査と誤検知への対応']])
};
const meta={
 zones:{...common('a2-eight-zones','8ゾーンの役割と、維持するレコード','Private DNS'),heading:'Private Link 6ゾーン ＋ APIM・ACA 2ゾーン'},
 resolve:{...common('a2-dns-resolution','問い合わせの転送と、回答の名前の連鎖を区別','Private DNS'),heading:'中央DNSの転送と、private IPまでの回答を確認'},
 snat:{...common('a2-snat','Firewall前後の送信元・復路とSNAT方式','送信元と出口'),heading:'SNATの方式と、公開出口の実送信元を照合'},
 stages:{...common('a2-cutover-stages','準備した範囲ごとに切り替え、別通信として検証','切替と検証'),heading:'許可・経路を先に準備し、段階的に切り替える'}
};
const node=(x,y,w,h,label,details,id,high)=>box(x,y,w,h)+txt(x+w/2,y+25,label,20,'#20323e',650,'middle')+(details??[]).map((s,i)=>txt(x+w/2,y+47+i*19,s,16,'#647984',400,'middle')).join('')+(high.has(id)?red(x-2,y-2,w+4,h+4,id):'');
const row=(y,label,value,id,high,h=42)=>box(214,y,972,h)+txt(230,y+27,label,17,'#586875')+txt(562,y+27,value,17,'#087f8c',500)+(high.has(id)?red(217,y+3,966,h-6,id):'');
function diagram(key,targets){
 const h=new Set(targets);let c='';
 if(key==='zones'){
  const panel=(x,y,w,height,label,lines,id)=>box(x,y,w,height)+txt(x+16,y+29,label,19,'#20323e',650)+lines.map((s,i)=>txt(x+16,y+57+i*25,s,17,i===lines.length-1?'#647984':'#087f8c')).join('')+(h.has(id)?red(x-2,y-2,w+4,height+4,id):'');
  c+=panel(214,151,475,165,'Foundry PE / 3ゾーン',['privatelink.cognitiveservices.azure.com','privatelink.openai.azure.com','privatelink.services.ai.azure.com','zone group → 実PE IP'],'foundry');
  c+=panel(709,151,477,165,'Key Vault・ACR・SQL PE / 3ゾーン',['privatelink.vaultcore.azure.net','privatelink.azurecr.io','privatelink.database.windows.net','zone group → 実PE IP'],'other');
  c+=panel(214,330,475,142,'APIM内部 / 1ゾーン',['実Gateway FQDNと同名','@ A → 実内部VIP','PEとは別の内部入口'],'apim');
  c+=panel(709,330,477,142,'ACA内部 / 1ゾーン',['実defaultDomainと同名','* A → 実static IP','現行はapex @ Aの定義なし'],'aca');
 }else if(key==='resolve'){
  c+=txt(226,164,'① DNS問い合わせの転送経路',16,'#647984',600);
  c+=node(226,178,250,65,'問い合わせ元',['サービスの公開名'],'client',h)+node(570,178,250,65,'中央DNS',['公開ゾーンから転送'],'central',h)+node(914,178,250,65,'Azure側Resolver等',['ゾーン参照して解決'],'resolver',h);
  c+=arrow('M476 208H570','照会',523,199)+arrow('M820 208H914','転送',867,199);
  c+=txt(226,280,'② 回答：SQL公開名からCNAME、private IPへの解決',16,'#647984',600);
  c+=node(226,292,280,74,'SQL公開名（例）',['<SQL名>.','database.windows.net'],'sql',h)+node(568,292,280,74,'Private Link名（例）',['<SQL名>.privatelink.','database.windows.net'],'cname',h)+node(910,292,254,74,'最終回答',['対象PEのprivate IP'],'pe',h);
  c+=arrow('M506 328H568','CNAME',537,317)+arrow('M848 328H910','A',879,317);
  c+=row(379,'解決に使うVNet','ゾーンリンクを整備。Azure DNS直接利用側も維持','link',h,40)+row(427,'内部名も別途参照','APIM実FQDN / ACA実defaultDomain','internal',h,40);
 }else if(key==='snat'){
  c+=node(230,168,245,70,'対象区画',['要求を開始する側'],'source',h)+node(574,168,245,70,'Firewall',['SNAT・許可と復路'],'fw',h)+node(918,168,245,70,'接続先',['観測した送信元を確認'],'target',h);
  c+=arrow('M475 203H574','通信',524,194)+arrow('M819 203H918','送信元',868,194);
  c+=row(288,'Networkルール','宛先・SNAT除外private rangesにより確認','network',h)+row(339,'Applicationルール','常にSNAT。Networkの除外指定とは別','application',h)+row(390,'公開出口をハブへ変更','新規接続で公開SNAT元・接続先の許可元を照合','public',h);
  c+=txt(232,462,'FW両側で何が見えるか、反対側がどこへ返すかを同じ通信で確認',17,'#647984');
 }else if(key==='stages'){
  c+=node(230,168,245,70,'事前準備',['許可・復路・名前解決'],'prepare',h)+node(574,168,245,70,'対象範囲の切替',['DNS・出口UDR・伝搬'],'change',h)+node(918,168,245,70,'新規接続で検証',['変更結果を対応付ける'],'verify',h);
  c+=arrow('M475 203H574','先に準備',524,194)+arrow('M819 203H918','反映後',868,194);
  c+=row(288,'切替単位','影響範囲ごとに分け、合格を判断できる単位にする','unit',h)+row(339,'サービスへの反映','対象サービスの手順と保守時間を確認','apply',h)+row(390,'別の通信として追う','DNS問い合わせとAPIMの公開宛て要求を区別','separate',h);
 }
 return '<defs><marker id="arr" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0 0L7 3.5L0 7Z" fill="#087f8c"/></marker></defs><g font-family="Hiragino Sans,Yu Gothic,Meiryo,sans-serif">'+c+'</g>';
}
const mapping=new Map();
function assign(first,last,key,high={}){for(let n=first;n<=last;n++)mapping.set(n,{key,targets:high[n]??[]});}
assign(5,8,'purpose',{5:['name','owner'],7:['path'],8:['process']});
assign(9,15,'dns',{10:['vnet-ip','fw-ip'],11:['vnet-ip','vnet-empty'],13:['fw-inherit','fw-empty'],14:['vnet-ip','fw-ip'],15:['proxy']});
assign(16,18,'dnsReach',{17:['path','custom','additional'],18:['apply']});
assign(19,22,'zones',{20:['foundry','other'],21:['apim'],22:['aca']});
assign(23,25,'resolve',{24:['central','resolver','link'],25:['sql','cname','pe','internal']});
assign(26,28,'ownership',{27:['central','iac'],28:['owner']});
assign(29,29,'publicNames',{29:['gateway','portal','scope']});
assign(30,33,'nsg',{31:['source','operator'],32:['pe'],33:['deny','changed']});
assign(34,35,'tag',{35:['range','effective','focus']});
assign(36,37,'preserve',{36:['platform','local','direct'],37:['auth']});
assign(38,39,'rules',{38:['network','application','central'],39:['network','wide','central']});
assign(40,43,'snat',{41:['network','application'],42:['fw','target'],43:['public']});
assign(44,46,'expected',{45:['observer','expected'],46:['expected','portal']});
assign(47,50,'baseline',{48:['network','settings','criteria'],49:['criteria'],50:['central','query','operator']});
assign(51,54,'stages',{52:['unit'],53:['prepare','change','apply'],54:['separate']});
assign(55,59,'verify',{56:['dns'],57:['dns','route','allow'],58:['dns','route','allow','source'],59:['services']});
assign(60,61,'observe',{61:['logs','alerts','scope']});
assign(62,64,'rollback',{63:['scope','shared','verify'],64:['persist']});
assign(65,68,'redeploy',{66:['keep','apply'],67:['zone'],68:['down']});
assign(69,71,'q1',{70:['b'],71:['b']});
assign(72,74,'q2',{73:['b'],74:['b']});
assign(75,77,'q3',{76:['b'],77:['b']});
assign(78,82,'summary',{78:['dns'],79:['policy'],80:['process','iac'],81:['next'],82:['next']});
const dir=new URL('diagrams/settings-v2/',HERE);await fs.mkdir(dir,{recursive:true});const written=new Set();
for(let i=0;i<script.turns.length;i++){
 const t=script.turns[i];if(t.chapterId==='opening')continue;
 const m=mapping.get(i+1);if(!m)throw new Error('Unmapped turn '+(i+1));
 if(views[m.key]){
  const v=clone(views[m.key]);v.heading=v.title;if(v.table)v.highlightRows=m.targets;else v.highlightFields=m.targets;
  renderSettingsBody(v);t.visual=v;
 }else{
  const v=meta[m.key],tag=m.targets.length?m.targets.join('-'):'base',name=v.viewId+'-'+tag+'.svg';
  if(!written.has(name)){await fs.writeFile(new URL(name,dir),'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1207 511">'+renderSettingsShell(v,diagram(m.key,m.targets))+'</svg>');written.add(name);}
  t.visual={layout:'svg',viewId:v.viewId,heading:v.heading,svg:'diagrams/settings-v2/'+name,highlightTargets:m.targets};
 }
}
script.visualStyle='teaching-settings-v2';
await fs.writeFile(file,JSON.stringify(script,null,2)+'\n');
console.log(JSON.stringify({turns:script.turns.length,bodyTurns:mapping.size,settingsViews:Object.keys(views).length,diagramViews:Object.keys(meta).length,svgVariants:written.size,viewIds:[...new Set(script.turns.filter(t=>t.chapterId!=='opening').map(t=>t.visual.viewId))]}));
