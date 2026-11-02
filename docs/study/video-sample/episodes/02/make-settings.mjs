// Episode 02 teaching-settings-v2 authoring. Shared renderers/assets stay unchanged.
import fs from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import crypto from 'node:crypto';
import {renderSettingsBody,renderSettingsShell} from '../../settings-visual.mjs';
const DIR=path.dirname(fileURLToPath(import.meta.url));
const source=JSON.parse(await fs.readFile(path.join(DIR,'versions/portal-v2/script.json'),'utf8'));
const script=structuredClone(source),groups=[],assigned=new Set();
const clone=structuredClone;
const sha=s=>crypto.createHash('sha256').update(s).digest('hex');
const base=(viewId,title,section,notice='')=>({layout:'settings',viewId,areaLabel:'DNSとPrivate Endpoint',title,kind:'ネットワーク設定',section,menu:['名前と接続口','Private DNS','DNSの問い合わせ先','送信元別の確認'],subtitle:'構築定義・公開設定例から確認する',footer:'教材用の設定ビュー / 稼働Azureから取得した値ではありません',...(notice?{notice}:{})});
const row=(id,label,value)=>({id,label,value});
const fld=(id,title,section,notice,fields)=>({...base(id,title,section,notice),fields});
const tbl=(id,title,section,notice,columns,rows)=>({...base(id,title,section,notice),table:{columns,rows}});
function group(ids,visual,highlights={},note=''){
  const baseline=clone(visual);delete baseline.files;
  if(visual.layout==='settings'){delete baseline.highlightFields;delete baseline.highlightRows;}
  groups.push({viewId:visual.viewId,turnNumbers:ids,turnIds:ids.map(n=>script.turns.find(t=>Number(t.id.match(/turn-(\d+)/)[1])===n).id),layout:visual.layout,baselineSha256:visual.layout==='svg'?visual.baselineSha256:sha(JSON.stringify(baseline)),note});
  for(const n of ids){const turn=script.turns.find(t=>Number(t.id.match(/turn-(\d+)/)[1])===n);if(!turn||assigned.has(n))throw Error(`bad turn ${n}`);assigned.add(n);turn.visual=clone(visual);const h=highlights[n]??[];if(visual.layout==='settings'){turn.visual[visual.fields?'highlightFields':'highlightRows']=h;renderSettingsBody(turn.visual);}else if(visual.layout==='svg'){turn.visual.svg=visual.files[h[0]??'none'];delete turn.visual.files;delete turn.visual.baselineSha256;}}
}
const C={ink:'#20323e',muted:'#617985',accent:'#087f8c',red:'#de343b',pale:'#edf5f6',line:'#bfd2d9'};
const esc=x=>String(x).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const rect=(x,y,w,h,fill='#fff',stroke=C.line,r=6,sw=1)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
const text=(x,y,s,size=19,color=C.ink,weight=400)=>`<text x="${x}" y="${y}" font-family="Hiragino Sans,Yu Gothic,Meiryo,sans-serif" font-size="${size}" fill="${color}" font-weight="${weight}">${esc(s)}</text>`;
const red=(x,y,w,h)=>`<g class="teaching-highlight">${rect(x,y,w,h,'none',C.red,6,2.8)}</g>`;
const arrow=(x1,y1,x2,y2,color=C.accent)=>`<path d="M${x1} ${y1}L${x2} ${y2}" stroke="${color}" stroke-width="2.4" fill="none"/><path d="M${x2+(x2>x1?-7:7)} ${y2-5}L${x2} ${y2}L${x2+(x2>x1?-7:7)} ${y2+5}" fill="none" stroke="${color}" stroke-width="2.4"/>`;
const down=(x,y1,y2)=>`<path d="M${x} ${y1}V${y2}m-5 -7l5 7l5 -7" fill="none" stroke="${C.accent}" stroke-width="2.4"/>`;
await fs.mkdir(path.join(DIR,'diagrams'),{recursive:true});
async function svgSet(view,variants,draw){const files={};for(const key of variants){const file=`diagrams/settings-${view.viewId}-${key}.svg`;files[key]=file;const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="1207" height="511" viewBox="0 0 1207 511">${renderSettingsShell(view,draw(key))}</svg>`;await fs.writeFile(path.join(DIR,file),svg);}const none=await fs.readFile(path.join(DIR,files.none),'utf8');return {...view,layout:'svg',files,baselineSha256:sha(none)};}

group([5,6,7,8],fld('name-to-ip','名前から接続先のIPを決める','名前と接続口','教材注釈：DNSの回答を、後続で調べる宛先とそろえる',[
 row('fqdn','アプリが使う名前','サービスの通常FQDN'),row('dns','DNSの役割','名前に対応するIPを回答'),row('data','データ通信','アプリ → 回答されたIP'),row('expected','最初に照合すること','期待した接続先IPか')]),{6:['data'],7:['fqdn','dns'],8:['expected']});
group([9,10],fld('lesson-goals','今回確認できるようになること','名前と接続口','教材6〜10節 / 構築定義と公開設定例を読む',[
 row('names','名前と接続先','通常名・内部名・PEのprivate IP'),row('source','確認する場所','問題の通信を開始する送信元'),row('basis','画面の値の根拠','構築定義・公開設定例'),row('actual','実測との区別','稼働Azureの取得結果ではない')]),{10:['basis','actual']});
const peView=base('pe-location','Key Vault：接続口とサービス本体を分ける','名前と接続口');
const peSvg=await svgSet(peView,['none','pe'],key=>{
 let out=rect(214,147,972,43,'#e8f4ff','#b6d9f3')+text(231,176,'接続方式：Private Endpoint / IPは実NICから確認する',20,'#17496b',600);
 out+=rect(230,219,605,219,C.pale)+text(248,249,'VNet内',19,C.accent,650);
 out+=rect(247,282,223,99)+text(265,317,'アプリ',22,C.ink,600)+text(265,348,'通常FQDNを使用',18,C.muted);
 out+=rect(585,282,228,99)+text(603,317,'Private Endpoint',21,C.accent,600)+text(603,348,'Key Vault PEの実IP',18,C.muted);
 out+=rect(914,282,254,99)+text(934,317,'Key Vault本体',22,C.ink,600)+text(934,348,'サービス側の認証・権限',17,C.muted);
 out+=arrow(473,330,578,330)+arrow(816,330,907,330)+text(248,417,'中央：VNet内の接続口',19,C.accent)+text(918,417,'右：サービス本体',19,C.muted);
 if(key==='pe')out+=red(579,276,240,111);return out;
});
group([12,13],peSvg,{13:['pe']},'中央のPE / 右のKey Vault本体という台詞の配置を保持');
group([15],tbl('pe-services','PE区画に作る4種類の接続口','名前と接続口','教材注釈：サービス本体をPE区画へ移す構成ではない',[
 {label:'Private Endpoint',width:245},{label:'接続先',width:245},{label:'主な用途',width:482}], [
 {id:'foundry',cells:['Foundry PE','Foundry','AIの各API群']},{id:'kv',cells:['Key Vault PE','Key Vault','証明書・秘密情報']},{id:'acr',cells:['ACR PE','Container Registry','イメージの取得・登録']},{id:'sql',cells:['SQL PE','共用SQL','Portal・認証窓口のデータ保存']}]),{15:['foundry','kv','acr','sql']});
group([16,17,18],fld('pe-ip-configs','PEの個数とIPの個数を分ける','名前と接続口','教材注釈：先頭のIPやサブネット名から推測しない',[
 row('count','作成するPE','4種類 / 1 PE = 1 IPとは限らない'),row('foundry','Foundry・ACR','用途別の名前・データ側の接続先も確認'),row('nic','構築処理の取得元','Azureが返したPEの実NIC ID'),row('ips','取得する範囲','実NICの全IP構成 → 整理・重複除去'),row('next','後段の制御へ','取得したIP群をNSGなどへ渡す')]),{17:['count','foundry'],18:['nic','ips','next']});
group([19,20],tbl('pe-responsibilities','PEだけで通信と操作を許可しない','名前と接続口','教材注釈：接続口・名前解決・通信許可・認証を分ける',[
 {label:'確認する設定',width:320},{label:'役割',width:652}], [
 {id:'pe',cells:['Private Endpoint','サービスへprivate IPで接続する入口']},{id:'dns',cells:['DNS','名前に対応する宛先IPを決める']},{id:'public',cells:['サービス本体の公開アクセス','Foundry・Key Vault・ACR・SQLは無効']},{id:'network',cells:['NSG・具体的な経路','その宛先へ到達できるか']},{id:'auth',cells:['サービス認証・操作権限','利用者が何を操作できるか']}]),{20:['public','network','auth']});

const chainView=base('kv-name-and-data','Key Vault：DNSの解決順とデータ通信','Private DNS');
const chainSvg=await svgSet(chainView,['none','normal','cname','a','dns','data'],key=>{
 let out=text(229,170,'DNS：名前の対応を調べる順番',20,C.accent,650)+text(822,170,'データ：得られたIPへ接続',19,C.accent,650);
 const nodes=[{y:185,title:'通常の接続先FQDN',val:'<vault>.vault.azure.net'},{y:283,title:'公開DNSのCNAME → Private Link名',val:'<vault>.privatelink.vaultcore.azure.net'},{y:381,title:'リンクしたPrivate DNSのAレコード',val:'Key Vault PEの実private IP'}];
 for(const n of nodes){out+=rect(229,n.y,564,68)+text(244,n.y+25,n.title,18,C.muted,600)+text(244,n.y+53,n.val,19,C.accent,600);}
 out+=down(510,255,280)+down(510,353,378);
 out+=rect(817,185,369,264,'#edf5f6')+text(834,216,'Private DNSは名前の対応表',18,C.muted)+text(834,241,'データを中継する装置ではない',18,C.muted);
 out+=rect(831,285,90,70)+text(843,327,'アプリ',17,C.ink,600)+rect(952,285,84,70)+text(971,314,'PE',18,C.accent,650)+text(963,338,'実IP',16,C.muted)+rect(1069,285,102,70)+text(1083,315,'Key Vault',16,C.ink,600)+text(1091,338,'本体',16,C.muted)+arrow(923,321,946,321)+arrow(1038,321,1063,321);
 out+=text(834,391,'通常名をアプリへ設定',18,C.ink)+text(834,421,'回答されたPEのIPへ接続',18,C.accent);
 if(key==='normal')out+=red(225,181,572,76);if(key==='cname')out+=red(225,279,572,76);if(key==='a')out+=red(225,377,572,76);if(key==='dns')out+=red(225,181,572,272);if(key==='data')out+=red(817,273,369,95);return out;
});
group([21,22,23,24,25,26],chainSvg,{22:['normal'],23:['cname'],24:['a'],25:['dns'],26:['data']},'問答中の構図は固定。上の通常名・中央のPrivate Link名を保持。026で右のデータ通信の図へ説明対象を切替');
const zonesView=base('private-link-zones','Private Link用の6ゾーンと接続先','Private DNS');
const zonesSvg=await svgSet(zonesView,['none','foundry','others'],key=>{
 let out=text(230,170,'Private DNSゾーン',18,C.muted,600)+text(880,170,'関連付ける接続口',18,C.muted,600);
 const names=['privatelink.cognitiveservices.azure.com','privatelink.openai.azure.com','privatelink.services.ai.azure.com','privatelink.vaultcore.azure.net','privatelink.azurecr.io','privatelink.database.windows.net'];
 names.forEach((name,i)=>{const y=184+i*46;out+=rect(225,y,625,43)+text(240,y+28,name,19,C.accent);if(i>2)out+=rect(861,y,320,43)+text(879,y+28,['Key Vault PE','ACR PE','SQL PE'][i-3],19,C.ink,600);});
 out+=rect(861,184,320,135,C.pale)+text(880,231,'同じFoundry PE',22,C.accent,650)+text(880,263,'3つの用途別ゾーン',18,C.muted)+text(880,291,'Cognitive / OpenAI / Services',16,C.muted);
 if(key==='foundry')out+=red(221,180,964,143);if(key==='others')out+=red(221,318,964,143);return out;
});
group([27,28,29],zonesSvg,{28:['foundry'],29:['others']},'Foundryの3つのゾーンを同じPEへまとめる図を置く');
const linksView=base('zone-associations','VNetリンクとDNS zone groupの役割','Private DNS');
const linksSvg=await svgSet(linksView,['none','roles','settings'],key=>{
 let out=rect(225,151,959,46,'#e8f4ff','#b6d9f3')+text(241,181,'教材注釈：下の線は設定の関連付け / データ通信の経路ではない',19,'#17496b',600);
 out+=rect(225,216,235,93,C.pale)+text(245,251,'このVNet',22,C.accent,600)+text(245,281,'ゾーンを参照する範囲',17,C.muted);
 out+=rect(596,216,259,93)+text(616,251,'Private DNSゾーン',21,C.accent,600)+text(616,281,'名前とIPの対応',18,C.muted);
 out+=rect(978,216,206,93,C.pale)+text(998,251,'PE',22,C.accent,600)+text(998,281,'実NICのIP群',17,C.muted);
 out+=arrow(464,264,589,264)+text(471,239,'VNetリンク',17,C.ink,600)+arrow(974,264,862,264)+text(863,239,'zone group',16,C.ink,600);
 out+=rect(225,330,959,55)+text(243,363,'VNetリンク：registrationEnabled:false / VM名を自動登録しない',19,C.ink);
 out+=rect(225,395,959,58)+text(243,420,'PEのDNS zone groupへゾーンIDを指定し、レコードを連携',19,C.ink)+text(243,444,'PE用Aレコードを別のBicepで手書きして二重管理しない',17,C.muted);
 if(key==='roles')out+=red(221,209,967,105);if(key==='settings')out+=red(221,326,967,131);return out;
});
group([30,31,32,33],linksSvg,{31:['roles'],32:['roles'],33:['settings']},'VNet/ゾーン/PEと、二種類の関連付けを同じ画面で保持');

group([34,35,36],fld('vnet-dns','VNetクライアントのDNSサーバー','DNSの問い合わせ先','教材注釈：VNetリンクだけで独自DNSへ転送設定は入らない',[
 row('input','IaC：network.dnsServers','公開params例：[]'),row('azure','空配列の意味','Azure提供DNS'),row('custom','独自DNSのIPv4を指定','そのDNSから必要な名前を解決する準備'),row('refs','独自DNS側の準備','ゾーン参照・転送・DNSへの到達性')]),{35:['input','azure','custom'],36:['refs']});
group([37,38,39,40],tbl('firewall-dns-input','Firewall自身の上流DNSを確認する','DNSの問い合わせ先','教材注釈：network.firewall.dnsServers はVNetの入力と別',[
 {label:'Firewall側の入力',width:320},{label:'適用される上流DNS',width:652}], [
 {id:'omitted',cells:['キーを未指定','VNetのnetwork.dnsServers入力を使用']},{id:'empty',cells:['空配列 [] を明示','Azure提供DNSを指定']},{id:'custom',cells:['独自DNSのIPv4を指定','指定したDNSを使用']},{id:'difference',cells:['VNetだけを独自DNSへ変更','Firewall側 [] は独自DNSへ置換されない']}]),{38:['omitted'],39:['empty','difference'],40:['omitted','empty']});
group([41,43],fld('dns-proxy-and-path','DNS proxyとクライアントの問い合わせ先','DNSの問い合わせ先','教材注釈：実際の問い合わせ経路に沿って許可を確認する',[
 row('proxy','FirewallのenableProxy','true / DNS proxyを有効化'),row('client','クライアントのDNS設定','proxy有効化だけでは変更されない'),row('firewall','Firewallへ問い合わせる場合','クライアント側のDNS設定も対応'),row('port','DNS通信','TCP / UDP 53'),row('rules','確認する制御','対応NSG / 通る場合は独自DNS用FW規則')]),{41:['proxy','client','firewall'],43:['port','rules']});
group([44],fld('dns-fallback','ゾーン内にない名前の扱いも確認する','DNSの問い合わせ先','教材注釈：必ず公開DNSへ逃がされるとは扱わない',[
 row('policy','Foundry・Key Vaultの4リンク','resolutionPolicy:Default'),row('others','その他のリンク','resolutionPolicy属性を省略'),row('fallback','Internet fallback','有効化する指定ではない'),row('check','確認する単位','問い合わせる名前ごとに回答を確認')]),{44:['fallback','check']});
group([45,46,47],tbl('eight-zones','Private Link用と内部入口用のゾーンを分ける','Private DNS','教材注釈：privateな名前だけでPEとは判断しない',[
 {label:'用途',width:300},{label:'ゾーン数',width:140},{label:'名前を対応させる先',width:532}], [
 {id:'pe',cells:['Private Link用','6','Foundry・Key Vault・ACR・SQLのPE']},{id:'apim',cells:['APIM内部入口用','1','APIMの実内部VIP / 自身のPEは作らない']},{id:'aca',cells:['ACA内部入口用','1','ACAの実staticIp / 自身のPEは作らない']},{id:'total',cells:['合計','8','同じVNetへリンク']}]),{46:['apim','aca','total'],47:['pe','apim','aca']});
group([48,49,50],fld('apim-internal-record','APIM：実FQDNと同名のゾーンを作る','Private DNS','教材注釈：azure-api.net 全体を自分たちのゾーンで覆わない',[
 row('zone','Private DNSゾーン名','対象APIM自身の実FQDN'),row('name','通常の接続先名の形式','<apim名>.azure-api.net'),row('record','Aレコードの名前','@ / ゾーンの頂点'),row('ip','対応させる値','作成後のAPIM実内部VIP'),row('ttl','TTL / 公開config','network.dnsTtl / 300秒')]),{48:['zone','record','ip'],49:['zone','name'],50:['zone','ip']});
group([51,52,53,54,55],tbl('aca-internal-record','ACA：実defaultDomainと子のアプリ名','Private DNS','教材注釈：PE zone groupの連携とは別に、BicepでAを明示作成',[
 {label:'確認する項目',width:320},{label:'この構築定義',width:652}], [
 {id:'zone',cells:['Private DNSゾーン名','ACA環境の実defaultDomain']},{id:'wildcard',cells:['* A / ワイルドカード','子のアプリ名 → 環境の実staticIp']},{id:'app',cells:['内部アプリ名の形式','ca-<stem>-portal.<defaultDomain>']},{id:'apex',cells:['@ A / ゾーン頂点','この定義には作らない']},{id:'ttl',cells:['TTL','ACAは300秒 / APIMも公開configは300秒']}]),{52:['zone','wildcard','app'],54:['wildcard','apex'],55:['ttl']});
group([56],fld('public-hostnames','利用者向け公開名はAppGWの公開入口へ','名前と接続口','教材注釈：内部IPへ直接つなぐための公開名ではない',[
 row('names','公開ホスト名','利用者向けGateway / Portal'),row('dns','公開DNS','Aレコード'),row('ip','公開名の解決先','Application Gatewayの公開IP'),row('internal','内部入口と区別','APIM実内部VIP / ACA実staticIp')]),{56:['dns','ip']});
group([57],fld('apim-network-mode','APIMの内部接続と公開設定を区別する','名前と接続口','教材注釈：内部名を持つ全サービスをDisabledと一括にしない',[
 row('vnet','APIM VNetモード','Internal'),row('pna','publicNetworkAccess','Enabled / 管理面もあるため'),row('aca','ACA環境','internal:true / 環境の内部入口'),row('pe','Foundry・KV・ACR・SQL本体','公開ネットワークアクセスを無効化')]),{57:['vnet','pna']});
group([58,59,60],fld('check-source','名前解決は問題の通信を始める場所で確認','送信元別の確認','教材注釈：手元PCの結果をアプリへそのまま当てはめない',[
 row('source','送信元','管理VM / ACA / APIMなど実際の実行元'),row('time','確認時刻','問い合わせた時刻を記録'),row('dns','問い合わせるDNS','その送信元が実際に使うDNS'),row('fqdn','問い合わせる名前','アプリが使う完全なFQDN')]),{59:['source'],60:['source','time','dns','fqdn']});
group([61,62,63,64,65],tbl('check-answer-and-pe','DNSの回答と対象PEの実体を照合する','送信元別の確認','教材注釈：private IPらしい値だけで正しいPEと判断しない',[
 {label:'確認する対象',width:310},{label:'そろえる対応',width:662}], [
 {id:'chain',cells:['CNAMEとAの連鎖','通常名 → Private Link名 → 対象PEのIP']},{id:'identity',cells:['PEの接続先・種類・状態','対象リソース / groupId / 接続状態']},{id:'ips',cells:['PEが返したNIC','実NICの全IP構成と回答IP群を照合']},{id:'difference',cells:['期待する宛先との違い','公開IP / 別の内部IP / 名前が見つからない']},{id:'link',cells:['レコード連携','ゾーン / VNetリンク / zone group / A']},{id:'custom',cells:['独自DNS','そのDNSから対象ゾーンを解決する構成']}]),{62:['chain'],63:['identity','ips'],64:['difference'],65:['link','custom']});
group([66,67,68],tbl('check-internal-reflection','内部名のAレコードと変更後の反映を確認','送信元別の確認','教材注釈：書き換えとサービスへの反映は別に確かめる',[
 {label:'確認する対象',width:310},{label:'照合・反映の観点',width:662}], [
 {id:'apim',cells:['APIMの@ A','実内部VIP / ネットワーク構成の適用']},{id:'aca',cells:['ACAの* A','環境の実staticIp']},{id:'ttl',cells:['変更直後のDNS','TTL / キャッシュ / サービスへの反映']},{id:'appgw',cells:['Application Gateway','資料の確認点：StopとStart']},{id:'vm',cells:['管理VM','DNS再取得 / 必要な再起動 / 保守時間']}]),{67:['ttl'],68:['apim','appgw','vm']});
group([69,70],fld('dns-to-connection','参照先と到達性を準備してから接続を確認','送信元別の確認','教材注釈：VNetピアリングだけでPrivate DNS設定は共有されない',[
 row('resolve','独自・中央DNSへの切替前','名前の参照先・転送を準備'),row('dnsnet','DNSへ到達するために','TCP / UDP 53の経路・許可'),row('expected','期待するIPが返った後','NSG / 具体的な経路 / サービス認証'),row('limit','名前解決の成功','後続の通信や操作の成功は別に確認')]),{69:['resolve','dnsnet'],70:['expected','limit']});
group([71,72,73],tbl('quiz-sql','確認1：SQL PEのIPが返った後の判断','送信元別の確認','問い：名前解決できれば、データベースを操作できる？',[
 {label:'判断する対象',width:330},{label:'確認する条件',width:642}], [
 {id:'dns',cells:['今回得られた回答','ACAから対象SQL PEのIPが返った']},{id:'network',cells:['到達性','回答IPへの経路・通信許可']},{id:'auth',cells:['サービス認証','SQLのEntra認証']},{id:'rights',cells:['操作権限','接続する主体が何を操作できるか']}]),{72:['network','auth','rights'],73:['auth','rights']},'質問071は赤枠なし。回答072から到達/認証/権限を強調');
group([74,75],tbl('quiz-source','確認2：PCの回答をAPIMへ当てはめられる？','送信元別の確認','問い：PCで同じ名前を解決できれば、APIM側の確認を省略できる？',[
 {label:'送信元',width:240},{label:'問い合わせ条件',width:380},{label:'回答と比較先',width:352}], [
 {id:'pc',cells:['手元PC','PCが使うDNS・参照ゾーン','手元で得られた回答']},{id:'apim',cells:['実際のAPIM','APIMが使うDNS・参照ゾーン','回答を対象PEの実IP群と照合']}]),{75:['apim']},'質問074の赤枠なし。回答075で実際の送信元APIMを強調');
group([77,78,79],tbl('quiz-aca-apex','確認3：ACAで問い合わせた名前を見分ける','Private DNS','問い：環境ドメイン名だけで期待するAが返らない。PEの故障？',[
 {label:'確認する対象',width:330},{label:'この構築定義で比較すること',width:642}], [
 {id:'name',cells:['問い合わせた名前','子のアプリ名か / ゾーン頂点か']},{id:'record',cells:['レコードの名前','ワイルドカード * / 頂点 @']},{id:'config',cells:['構築定義','* Aを作成 / @ Aは定義しない']},{id:'pe',cells:['ACAの接続口','自身のPEを作らず、環境の内部入口を使用']}]),{78:['name','config','pe'],79:['name','record']},'質問077は赤枠なし。回答078から定義との違いを強調');
group([80,81,82],tbl('summary-correspondence','今日確認した3つの対応','送信元別の確認','教材注釈：名前の対応表とデータ通信の経路を区別する',[
 {label:'説明できる関係',width:300},{label:'設定・確認する対応',width:672}], [
 {id:'name',cells:['通常名とPEのIP','通常FQDN → Private Link名 → PE実private IP']},{id:'types',cells:['接続口とゾーン','4種類のPE / Private Link用6ゾーン']},{id:'internal',cells:['内部入口と公開入口','APIM・ACAの内部用2ゾーン / 公開名はAppGWへ']},{id:'source',cells:['送信元と回答','どこから / どのDNSへ / 何を聞き / どのIPか']}]),{80:['name'],81:['types','internal'],82:['source']});
group([84,85],fld('next-nsg','次回：決まった宛先への通信を判断する','名前と接続口','教材注釈：今回そろえた送信元と宛先を、次の通信制御へつなぐ',[
 row('source','今回そろえるもの','送信元 / 使うDNS / 名前 / 回答IP'),row('direction','NSGで確かめるもの','送信と受信の規則'),row('priority','判断に使うもの','優先順位 / 状態追跡'),row('goal','次の問い','その宛先への通信は許可されるか')]),{84:['direction','priority'],85:['goal']});

const body=script.turns.filter(t=>t.visual.layout!=='hologram');if(body.length!==assigned.size)throw Error(`unassigned body ${body.length}/${assigned.size}`);
for(let i=0;i<script.turns.length;i++){const a=clone(source.turns[i]),b=clone(script.turns[i]);delete a.visual;delete b.visual;if(JSON.stringify(a)!==JSON.stringify(b))throw Error('nonvisual mutation');}
script.visualStyle='teaching-settings-v2';script.visualEdition='teaching-settings-v2';script.description='DNSとPrivate Endpointを、教材用の設定メニューと補助図で学ぶ第2回。質問から回答まで同じ構図を保ち、説明する設定を赤枠で示します。';script.seriesLabel='egakumi STUDY / 設定ビュー';
await fs.writeFile(path.join(DIR,'script.json'),JSON.stringify(script,null,2)+'\n');
await fs.writeFile(path.join(DIR,'settings-storyboard.json'),JSON.stringify({visualStyle:'teaching-settings-v2',camera:'fixed',source:'versions/portal-v2/script.json',nonVisualTurnFieldsPreserved:true,bodyTurnCount:body.length,groupCount:groups.length,sourceReferences:script.sourceReferences,baselineHash:{svg:'SHA256 of exact SVG after removing <g class="teaching-highlight">...</g>',settings:'SHA256 JSON.stringify(visual) with highlightRows/highlightFields omitted'},continuousPreview:{fromTurn:'lesson02-turn-030-mako',throughTurn:'lesson02-turn-033-ikumi',expectedSeconds:42.625,viewId:'zone-associations'},groups},null,2)+'\n');
console.log(JSON.stringify({turns:script.turns.length,bodyTurns:body.length,groups:groups.length,layouts:body.reduce((a,t)=>(a[t.visual.layout]=(a[t.visual.layout]??0)+1,a),{})},null,2));
