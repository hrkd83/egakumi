// Apply the reviewed screen plan without changing any spoken or caption text.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url));
const original=JSON.parse(await fs.readFile(path.join(here,'versions/diagram-v1/script.json'),'utf8'));
const {screens}=JSON.parse(await fs.readFile(path.join(here,'portal-screens.json'),'utf8'));
const diagrams=JSON.parse(await fs.readFile(path.join(here,'diagram-motion.json'),'utf8')).screens;
const map={};
const assign=(numbers,screen,focus,noteLines=[])=>numbers.forEach(number=>{map[number]={screen,focus,noteLines};});
assign([5,9,10,15],'resources','list');
assign([6,80],'dns-records-keyvault','record');
assign([7,21],'pe-dns-config','name');
assign([8],'pe-dns-config','ip');
assign([12],'pe-overview','resource');
assign([16,17],'pe-dns-acr','all');
assign([18,63],'nic-ipconfigs','all');
assign([19,20],'kv-networking','public',['PE・DNS・通信許可・サービス認証は別の設定。ここはKey Vault本体の公開接続。']);
assign([27,81],'dns-zones','all');
assign([28],'dns-zones','foundry');
assign([29],'dns-zones','keyvault',['下の3ゾーンはKey Vault・ACR・SQL用。接続先サービスとの対応を確認する。']);
assign([30,36],'dns-vnet-links','vnet');
assign([31],'dns-vnet-links','vnet',['PEのDNS configurationにあるDNS zone groupは別の関連付け。次の画面で確認する。']);
assign([32,65],'pe-dns-config','group');
assign([33],'dns-vnet-links','autoreg',['Auto registrationはVM名の自動登録。PEのzone groupによる連携とは別。']);
assign([34,75],'vnet-dns','mode');
assign([35],'vnet-dns','azure',['教材の公開params：network.dnsServers=[] はAzure提供DNSの指定。']);
assign([37,38],'firewall-dns','upstream');
assign([39,40],'firewall-dns','azure',['教材のIaC入力：Firewall側が未指定ならVNet入力を使用。明示した [] はAzure提供DNS。']);
assign([41],'firewall-dns','proxy',['DNS proxyを有効にするだけでは、VNetクライアントの問い合わせ先は変わらない。']);
assign([43],'nsg-dns-rules','dns',['実際の問い合わせ経路でTCP/UDP 53を確認。Firewallを通る問い合わせだけがFWの判定対象。']);
assign([44],'dns-vnet-links','link',['教材のリンク定義はInternet fallbackを有効化していない。ゾーン内にない名前も個別に確認。']);
assign([45,46,47],'dns-zones','internal',['Private Link用6ゾーン ＋ APIM・ACA内部入口用2ゾーン。内部IPの接続先すべてがPEではない。']);
assign([48],'dns-records-apim','apex');
assign([49],'dns-records-apim','zone');
assign([50,66],'dns-records-apim','ip');
assign([51],'aca-json','domain');
assign([52],'dns-records-aca','wildcard');
assign([53,54,77,78,79],'dns-records-aca','noApex',['この構築定義に @ のAレコードはない。@ のSOAと、* のAレコードを区別する。']);
assign([55],'dns-records-aca','ttl',['APIMの @ A とACAの * A はBicepが明示的に作成。PEのzone group連携とは別。']);
assign([56],'public-dns','ip',['Gateway・Portalの公開DNS Aは、このAppGW公開IPへ向ける。内部VIPへは直接向けない。']);
assign([57],'apim-network','mode',['教材のBicep：APIMはInternal VNetモードでも publicNetworkAccess=Enabled。']);
assign([58,59,74],'dns-check','source',['ここは管理VMを送信元とする確認例。手元PCやAPIM・ACAの結果と同一視しない。']);
assign([60,82],'dns-check','all',['送信元・確認時刻・問い合わせDNS・FQDN・回答IPを、同じ通信についてそろえる。']);
assign([61],'dns-check','cname');
assign([62],'dns-check','cname',['通常名 → Private Link名 → 対象PEのIP。送信元で得た回答を実体と照合する。']);
assign([64],'dns-check','ip',['公開IP・別の内部IP・名前なしを区別し、期待するPEや内部入口の実IPと照合する。']);
assign([67],'dns-records-apim','ttl',['TTL・キャッシュ・各サービスへの設定反映を確認。保存と反映完了は分けて確かめる。']);
assign([68],'apim-network','vnet',['APIMのネットワーク構成適用、AppGWのStop/Start、VMのDNS再取得は保守時間に確認。']);
assign([69],'vnet-dns-custom','address',['切替例：独自DNSからPrivate DNSを解決できる参照・転送と、DNSへの到達を先に用意する。']);
assign([70],'pe-overview','connection',['期待するIPが返った後は、NSG・経路・サービス認証と操作権限を確認する。']);
assign([71,72,73],'pe-dns-sql','ip',['確認1：SQL PEのIPが返っても、到達・Entra認証・操作権限の確認は必要。']);
assign([84,85],'nsg-dns-rules','priority',['次回：同じ送信元・宛先に対して、NSGの送信・受信・優先順位・状態追跡を確認する。']);
const diagramFocus={13:['data-path','pe'],22:['dns-chain','normal'],23:['dns-chain','link'],24:['dns-chain','ip'],25:['dns-chain','link'],26:['data-path','pe']};

const script=structuredClone(original),applied=[];
for(const turn of script.turns){
  if(turn.chapterId==='opening')continue;
  const number=Number(turn.id.match(/turn-(\d+)/)[1]);
  let screen,focus,file,kind='portal',noteLines=[];
  if(diagramFocus[number]){
    const [key,id]=diagramFocus[number];screen='diagram-'+key;focus=diagrams[key].focuses[id];file=diagrams[key].file;kind='diagram';
  }else{
    const entry=map[number];if(!entry)throw new Error(`Missing visual mapping: ${turn.id}`);
    const source=screens[entry.screen];if(!source)throw new Error(`Missing screen: ${entry.screen}`);
    focus=source.focuses[entry.focus];if(!focus)throw new Error(`Missing focus ${entry.screen}/${entry.focus}; available: ${Object.keys(source.focuses)}`);
    screen=entry.screen;file=source.file;noteLines=entry.noteLines;
  }
  if(focus.x<0||focus.y<0||focus.x+focus.width>1207||focus.y+focus.height>511)throw new Error(`Focus exceeds the canvas: ${turn.id}`);
  turn.visual={layout:'svg',heading:turn.visual.heading,svg:file,portal:{kind,screen,focus:structuredClone(focus),noteLines}};
  applied.push({turnId:turn.id,kind,screen,focus:focus.label});
}
script.visualEdition={id:'azure-portal-replica-v2',description:'Azure設定画面の教材用再現、説明中の欄の赤枠、カーソル移動と滑らかなズーム。必要な箇所に補助図を挿入。',liveAzureScreenshots:false,illustrativeValues:true};
script.description='Azureの設定画面を再現し、赤枠とズームでDNS・Private Endpointの確認箇所を追います。名前解決と通信の関係は補助図で整理します。';
script.seriesLabel='AZURE PORTAL / DNS & PRIVATE ENDPOINT';
for(let i=0;i<script.turns.length;i++){
  for(const field of ['id','speaker','chapterId','speech','caption','subtitleLines','subtitleChunks','delivery','gapAfterSeconds','pauseAfterSeconds']){
    if(JSON.stringify(script.turns[i][field])!==JSON.stringify(original.turns[i][field]))throw new Error(`Audio or subtitle plan changed: ${field}`);
  }
}
await fs.writeFile(path.join(here,'script.json'),JSON.stringify(script,null,2)+'\n');
await fs.writeFile(path.join(here,'portal-applied-plan.json'),JSON.stringify({turns:applied,counts:{portal:applied.filter(x=>x.kind==='portal').length,diagram:applied.filter(x=>x.kind==='diagram').length,hologram:4},speechAndCaptionsChanged:false},null,2)+'\n');
console.log(JSON.stringify({turns:script.turns.length,screenTurns:applied.filter(x=>x.kind==='portal').length,diagramTurns:applied.filter(x=>x.kind==='diagram').length,speechAndCaptionsChanged:false}));
