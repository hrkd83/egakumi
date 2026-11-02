// Episode 08 teaching-settings-v2 views. No network, audio, cast or shared-file writes.
import fs from 'node:fs/promises';
import crypto from 'node:crypto';
import {renderSettingsShell,renderSettingsBody} from '../../settings-visual.mjs';
const HERE=new URL('./',import.meta.url);
const scriptFile=new URL('script.json',HERE);
const script=JSON.parse(await fs.readFile(scriptFile,'utf8'));
const before=structuredClone(script);
const MENU=['概要','接続経路','受信NSG','SSH認証','管理実行','作業結果','下流接続'];
const footer='教材41〜43節・リポジトリ定義の説明。稼働環境の取得値ではない。';
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const sha=s=>crypto.createHash('sha256').update(s).digest('hex');
const txt=(x,y,s,size=17,color='#20323e',weight=400,anchor='start')=>`<text x="${x}" y="${y}" font-size="${size}" fill="${color}" font-weight="${weight}" text-anchor="${anchor}">${esc(s)}</text>`;
const rect=(x,y,w,h,fill='#fff',stroke='#d5e0e5',sw=1,r=4)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
const field=(id,label,value)=>({id,label,value});
const definitions=new Map();
function meta(viewId,title,section,subtitle,heading=title){return {viewId,areaLabel:'基盤管理者 / 教材用設定メニュー',heading,title,kind:'管理VM',section,menu:[...MENU],subtitle,footer};}
function fields(id,title,section,subtitle,rows,notice){const v={layout:'settings',...meta(id,title,section,subtitle),fields:rows.map(r=>field(...r)),highlightFields:[]};if(notice)v.notice=notice;definitions.set(id,{visual:v});return id;}
function table(id,title,section,subtitle,columns,rows){const v={layout:'settings',...meta(id,title,section,subtitle),table:{columns:columns.map(([label,width])=>({label,width})),rows:rows.map(([id,...cells])=>({id,cells}))},highlightRows:[]};definitions.set(id,{visual:v});return id;}
function graph(id,title,section,subtitle,kind){definitions.set(id,{visual:{layout:'svg',...meta(id,title,section,subtitle)},graph:kind});return id;}
fields('mgmt-vm-overview','管理VMの役割と配置','概要','作業する場所と、業務の常駐実行層を区別する',[
 ['purpose','用途（教材注釈）','証明書の取得・更新 / 内部保守 / イメージビルド'],
 ['os','OS','Linux'],['private','NICのアドレス','Dynamic private IPv4'],['public','Public IP','付与しない'],['boundary','役割の境界（教材注釈）','業務の常駐実行層は同居させない']]);
fields('mgmt-ssh-stages','SSH接続でそろえる3段階','接続経路','到達・許可・ログインを、同じ接続として確認する',[
 ['route','① private IPへの到達','企業側の接続経路 / 往路と復路'],['nsg','② 通信の許可','受信NSGの送信元・宛先・ポート'],['login','③ OSへのログイン','SSHの待受 / 公開鍵 または Entra SSH'],['source','説明の根拠','教材41〜43節 / 定義・公開設定例'],['scope','実環境との区別','この動画は稼働中Azureの実測結果ではない']]);
graph('mgmt-private-path','private IPまでの経路と、基盤内の許可','接続経路','運用端末を左、対象の管理VMを右へ固定する','private-path');
fields('mgmt-nic-output','対象VM / NICから宛先を確認する','接続経路','サブネットの範囲からVMの実IPを推測しない',[
 ['allocation','privateIPAllocationMethod','Dynamic'],['version','privateIPAddressVersion','IPv4'],['public','Public IP','付与しない'],['output','宛先IPの確認元','対象VM / NICの実出力'],['tuple','照合する接続','接続元 → 宛先実IP / プロトコル・ポート']]);
table('mgmt-operator-rules','運用元配列と管理VMの受信NSG','受信NSG','空配列と指定時を、同じ表で比較する',[
 ['設定・条件',300],['値',300],['読み方（教材注釈）',372]],[
 ['source','許可元CIDR','NSGが見る送信元IP','端末の公開IPと即断しない'],
 ['empty','operatorAddressPrefixes=[]','Deny / 優先度4080','source * / 一般のSSH受信は拒否'],
 ['allow','運用元CIDRを指定','Allow / 優先度100','宛先は管理VMの実IP'],
 ['port','プロトコル / ポート','公開config：Tcp / 22','設定値とOSの待受を合わせる'],
 ['final','先に一致しない受信','Deny / 優先度4096','環境固有の追加規則も照合'],
 ['rule','allow-operator-management','規則名とaccessを別に読む','名前だけでAllowと判断しない']]);
fields('mgmt-operator-port','NSGのポートとLinuxの待受','受信NSG','ネットワークの入力とOSの設定を分ける',[
 ['protocol','operatorProtocol','公開config：Tcp'],['port','operatorPorts','公開config：["22"]'],['effect','NSG設定の担当','指定プロトコル・宛先ポートの許可条件'],['listen','Linuxの待受','SSHの実効待受ポートを別に確認'],['automatic','設定変更の効果','operatorPortsだけでOSの待受は変わらない']]);
fields('mgmt-auth-overview','到達後に使う、二つのSSH認証','SSH認証','どちらも、private IPへの到達とNSG許可が前提',[
 ['route','共通の前提','対象VMへTCP到達 / NSG許可 / SSH待受'],['key','公開鍵ログイン','初期管理ユーザーのauthorized_keys'],['entra','Entra SSH','ログイン拡張 + EntraのVMログイン権限'],['password','パスワードの扱い','どちらもパスワード開放ではない']]);
fields('mgmt-ssh-key','初期管理ユーザーの公開鍵ログイン','SSH認証','VMへ渡す公開鍵と、手元で保持する秘密鍵を区別する',[
 ['user','management.adminUsername','公開例：egakumiadmin'],['public','management.sshPublicKeyFile','公開例：../secrets/management-ssh.pub'],['authorized','OS配置先（教材注釈）','/home/${adminUsername}/.ssh/authorized_keys'],['private','対応する秘密鍵','手元で保持 / Azureへ渡さない'],['output','教材・通常出力','秘密鍵を転載しない'],['role','Entra VMログインロール','この鍵方式に重ねて必須としない']]);
fields('mgmt-entra-ssh','Entra SSHとログイン割り当て','SSH認証','空の割り当てと、既存・継承の権限を区別する',[
 ['extension','ログイン拡張','AADSSHLoginForLinux'],['assignment','割当要素（教材注釈）','principalId / principalType\nrole：User / Administrator'],['role','ロール名（教材注釈）','Virtual Machine User Login\nVirtual Machine Administrator Login'],['scope','割り当ての範囲','VM単体スコープ'],['empty','公開例：loginAssignments=[]','ロールを追加しない / 鍵の無効化ではない'],['permissions','別に確認する権限','継承権限 / VMによるKey Vault等の利用権限']]);
fields('mgmt-ssh-os','LinuxのSSH保護設定','SSH認証','公開鍵・Entra SSHの経路を保ち、パスワードを開放しない',[
 ['vm','disablePassword\nAuthentication','true（VM定義）'],['password','PasswordAuthentication','no'],['interactive','KbdInteractiveAuthentication','no'],['root','PermitRootLogin','no'],['key','PubkeyAuthentication','yes'],['validate','設定適用の確認','sshd -t → 実効設定 → reload']]);
graph('mgmt-management-execution','SSHとManaged Run Commandの入口・権限','管理実行','二つの入口と、配備で実行する作業を同じ画面へ保持する','management');
fields('mgmt-execution-result','管理APIの受付から、作業結果まで確認','作業結果','受付・実行・成果物を順に照合する',[
 ['api','管理APIの受付','受付だけでVM内の作業成功としない'],['provisioning','provisioningState','教材注釈：管理状態を作業成功と同一視しない'],['execution','instanceView.executionState','Succeededを確認（ビルドの成功条件）'],['exit','exitCode','0を確認（ビルドの成功条件）'],['artifact','ビルド成果物','ソースhash / image digestを照合']]);
graph('mgmt-downstream','VMからの下流接続と、必要な設定','下流接続','上：公開依存先へFW経由 / 下：PE宛ての直接経路の例','downstream');
fields('mgmt-ssh-triage','SSHで入れないときの確認先','接続経路','同じ接続について、止まった段階を絞る',[
 ['target','① 対象','VM / NICの出力でprivate IPを確認'],['route','② 到達経路','接続元からprivate IPへ / 復路も確認'],['nsg','③ 受信NSG','実際の送信元 / 規則 / 宛先ポート'],['listen','④ OSの待受','sshdの待受・実効設定'],['auth','⑤ 選んだ認証方式','公開鍵の対応 または Entra VMログイン権限']]);
fields('mgmt-command-triage','自動処理が失敗したときの確認先','管理実行','処理を開始できない場合と、作業中の失敗を区別する',[
 ['api','① 配備実行主体','Run Command等の管理操作権限'],['agent','② 管理実行','VM agent / Run Commandの実行状態'],['network','③ VMからの接続','DNS / 送信NSG / UDR / 経路上のFW'],['auth','④ 処理先の認証','サービスを利用する権限'],['result','⑤ 作業の結果','終了コード / 成果物']]);
fields('mgmt-check-private','確認① 許可元へ公開IPを追加したら？','確認','この設定だけで、管理VMへSSHできると言えるか',[
 ['change','設問の条件（教材例）','手元の公開IPを許可元へ追加'],['destination','管理VMのNIC','Public IPなし / 実private IPは出力で確認'],['route','到達経路の確認','private IPまでの企業側経路 / 復路'],['nsg','通信の確認','NSGが見る送信元 / 宛先 / ポート'],['login','OSの確認','SSH待受 / 選んだ認証方式']]);
table('mgmt-check-empty','確認② 二つの空配列は、何を指定する？','確認','鍵ログインと管理実行を、まとめて無効にする指定か',[
 ['設定・方式',340],['確認する対象',632]],[
 ['operator','operatorAddressPrefixes=[]','管理VMの受信NSG：Deny / 優先度4080'],
 ['assignments','loginAssignments=[]','このIaCからEntra VMログインロールを追加しない'],
 ['key','初期ユーザーの公開鍵','鍵そのものを無効にする指定ではない'],
 ['command','Managed Run Command','上記の空配列は、管理実行を停止するフラグではない']]);
fields('mgmt-check-build','確認③ API受付後のビルド失敗を調べる','確認','作業の成否を、どの状態と成果物で説明するか',[
 ['api','設問の条件（教材例）','Run CommandのAPIは受け付けられた'],['execution','VM内の実行','executionState / exitCode'],['artifact','ビルド成果物','ソースhash / image digest'],['network','VMから依存先への接続','DNS / 実効経路 / 許可 / 処理先の認証'],['scope','調査する範囲','受信NSGだけが原因とは判断しない']]);
fields('mgmt-summary','接続と作業実行を、役割で説明する','概要','入口から作業結果までを、一つの接続・処理として追う',[
 ['route','SSH：到達経路','private IPまでの往路・復路'],['nsg','SSH：通信許可','NSG / 実際の接続元 / 宛先ポート'],['login','SSH：OSへのログイン','待受 / 初期ユーザーの公開鍵 または Entra SSH'],['permission','権限の区別','人のVMログイン / 管理操作 / VMのサービス利用'],['command','管理実行の入口','Azure管理API → VM agent → VM内処理'],['result','両方式の後続処理','VMからの接続 / サービス認証 / 作業結果']]);
fields('mgmt-next-a1','次回：必修A1 ハブ＆スポーク接続と往復経路','次回A1','独立VNetでの基準を、企業ハブへ接続する設計へつなぐ',[
 ['base','今日の基準','管理接続の経路 / 許可 / 権限'],['connection','接続後の設計','企業ハブへ、どの範囲を接続するか'],['central','集約するもの','出口・接続先などを個別に選ぶ'],['local','維持するもの','必要なローカル経路と通信制御'],['return','往復の整合','往路と復路をそろえる']]);
const turns=new Map();
function assign(range,viewId,focus={}){for(const i of range){const h=focus[i]??[];turns.set(i,{viewId,highlights:h});}}
const seq=(a,b)=>Array.from({length:b-a+1},(_,i)=>a+i);
assign(seq(5,8),'mgmt-vm-overview',{6:['purpose','boundary'],7:['os','private','public'],8:['private','public']});
assign(seq(9,11),'mgmt-ssh-stages',{9:['route','nsg','login'],10:['source','scope'],11:['route','nsg','login']});
assign(seq(12,17),'mgmt-private-path',{12:['pc','enterprise','vm','roundtrip'],14:['scope'],15:['scope'],17:['nsg','scope']});
assign(seq(18,21),'mgmt-nic-output',{19:['allocation','output'],20:['output','tuple'],21:['output','tuple']});
assign(seq(22,29),'mgmt-operator-rules',{23:['source'],24:['source'],25:['allow','port'],27:['empty','final'],28:['rule'],29:['source','allow','port','final','rule']});
assign(seq(30,32),'mgmt-operator-port',{31:['port','listen','automatic'],32:['effect','listen']});
assign(seq(33,34),'mgmt-auth-overview',{34:['route','key','entra']});
assign(seq(35,39),'mgmt-ssh-key',{36:['public','authorized','private','output'],37:['public','private'],39:['role']});
assign(seq(40,45),'mgmt-entra-ssh',{41:['extension','assignment','scope'],42:['role'],43:['permissions'],45:['empty','permissions']});
assign(seq(46,48),'mgmt-ssh-os',{47:['vm','password','interactive','root','key'],48:['validate']});
assign(seq(49,57),'mgmt-management-execution',{50:['api','agent','work'],51:['ssh-operator','deployer'],52:['tasks'],54:['deployer','api','permissions'],56:['empty','api'],57:['ssh-operator','deployer','permissions']});
assign(seq(58,60),'mgmt-execution-result',{59:['api','provisioning','execution','exit','artifact'],60:['execution','exit','artifact']});
assign(seq(61,67),'mgmt-downstream',{62:['vm-public','vm-private'],63:['dns','network','firewall','auth'],64:['dns','network','firewall','auth'],66:['dns','network','firewall','auth'],67:['fw','pe','public-path','fw-public','private-path','pe-service']});
assign(seq(68,70),'mgmt-ssh-triage',{69:['target','route','nsg','listen','auth'],70:['target','route']});
assign(seq(71,72),'mgmt-command-triage',{72:['api','agent','network','auth','result']});
assign(seq(73,75),'mgmt-check-private',{74:['destination','route'],75:['nsg','login']});
assign(seq(76,78),'mgmt-check-empty',{77:['operator','assignments','key','command'],78:['operator','assignments']});
assign(seq(79,81),'mgmt-check-build',{80:['execution','artifact','network','scope'],81:['execution','network','artifact']});
assign(seq(82,84),'mgmt-summary',{83:['route','nsg','login'],84:['permission','command','result']});
assign(seq(85,86),'mgmt-next-a1',{85:['connection'],86:['central','local','return']});
function makeGraph(def,selected){
 const boxes=new Map();let body='';
 const addBox=(id,x,y,w,h)=>boxes.set(id,{x,y,w,h});
 const node=(id,x,y,w,label,detail)=>{addBox(id,x,y,w,56);return rect(x,y,w,56,'#f2f8f8','#87aab1')+txt(x+w/2,y+23,label,20,'#174b55',600,'middle')+txt(x+w/2,y+45,detail,15,'#52727d',400,'middle');};
 const path=(id,d,dashed=false)=>{const matching=/^M([\d.]+) ([\d.]+)H([\d.]+)$/.exec(d);if(matching){const a=+matching[1],y=+matching[2],b=+matching[3];addBox(id,Math.min(a,b)-4,y-12,Math.abs(a-b)+8,24);}return `<path d="${d}" fill="none" stroke="#087f8c" stroke-width="2.2" ${dashed?'stroke-dasharray="6 4"':''} marker-end="url(#arrow08)"/>`;};
 const row=(id,y,label,value,h=42)=>{addBox(id,216,y+2,968,h-4);return rect(214,y,972,h)+txt(230,y+h/2+6,label,17,'#586875',500)+txt(475,y+h/2+6,value,17,'#087f8c');};
 body+='<defs><marker id="arrow08" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#087f8c"/></marker></defs><g font-family="Hiragino Sans,Yu Gothic,Meiryo,sans-serif">';
 if(def.graph==='private-path'){
  body+=txt(231,169,'運用端末 → 企業側の経路 → 受信NSG → 対象VMのprivate IP',18,'#20323e',600);
  body+=node('pc',230,190,195,'運用端末','接続元を確認')+node('enterprise',482,190,195,'企業側の経路','接続方式を別に準備')+node('nsg',734,190,195,'受信NSG','通信条件を判定')+node('vm',986,190,184,'管理VM','実IPは出力を確認');
  body+=path('path1','M425 218H482')+path('path2','M677 218H734')+path('path3','M929 218H986');
  body+='<path d="M1078 246V277H328V246" fill="none" stroke="#087f8c" stroke-width="2.2" stroke-dasharray="6 4" marker-end="url(#arrow08)"/>'+txt(702,269,'戻りの経路も照合',16,'#52727d',400,'middle');addBox('roundtrip',300,251,800,34);
  body+=row('scope',307,'このinfraの構築範囲','VPN Gateway・Bastion・VNetピアリングは作らない',52);
  body+=row('allow',359,'NSG許可元の記入','private IPへの接続経路を新しく作る操作ではない',52);
  body+=row('ip',411,'宛先の確認','管理VM / NICの実出力で確認し、値を推測しない',52);
 }else if(def.graph==='management'){
  body+=txt(230,166,'運用者のSSH：private IPまでの通信とOS認証',17,'#20323e',600);
  body+=node('ssh-operator',230,178,195,'運用者','SSHの接続ユーザー')+node('private-route',482,178,195,'private接続','到達経路 + 受信NSG')+node('ssh-auth',734,178,195,'SSH待受・認証','公開鍵 または Entra')+node('ssh-work',986,178,184,'VM内の保守','接続ユーザーで作業');
  body+=path('ssh1','M425 206H482')+path('ssh2','M677 206H734')+path('ssh3','M929 206H986');
  body+=txt(230,258,'Managed Run Command：Azure管理面の指示で実行',17,'#20323e',600);
  body+=node('deployer',230,269,195,'配備実行主体','管理操作の権限')+node('api',482,269,195,'Azure管理API','Run Command')+node('agent',734,269,195,'VM agent','管理指示を処理')+node('work',986,269,184,'VM内の処理','スクリプトを実行');
  body+=path('run1','M425 297H482')+path('run2','M677 297H734')+path('run3','M929 297H986');
  body+=row('tasks',340,'自動処理の例','Linux監査 / 証明書初期化 / Portal・Brokerビルド',44);
  body+=row('permissions',384,'権限の分担','管理操作の権限 ≠ SSH秘密鍵・Entra VMログイン権限',44);
  body+=row('empty',428,'運用元配列が空','受信NSGの指定。Run Commandを止めるフラグではない',44);
 }else if(def.graph==='downstream'){
  body+=txt(231,165,'上：公開依存先へ、経路上のFirewallを通る例',17,'#20323e',600);
  body+=node('vm-public',230,178,225,'管理VM','公開宛ての処理')+node('fw',589,178,225,'Firewall','経路上の通信を判定')+node('public',948,178,225,'公開依存先','許可された接続先');
  body+=path('public-path','M455 206H589')+path('fw-public','M814 206H948');
  body+=txt(231,267,'下：PE宛ての具体的な直接経路の例',17,'#20323e',600);
  body+=node('vm-private',230,280,225,'管理VM','PE宛ての処理')+node('pe',589,280,225,'Private Endpoint','対象サービスの接続口')+node('service',948,280,225,'対象サービス','利用の認証・権限');
  body+=path('private-path','M455 308H589',true)+path('pe-service','M814 308H948',true);
  body+=row('dns',351,'VM側のDNS','接続先の名前 → VMから見たIPを確認',31);
  body+=row('network',382,'送信NSG / UDR','同じ宛先・ポートと、選ばれた実効経路を確認',31);
  body+=row('firewall',413,'経路上のFirewall','FW経由の通信は、該当する許可ルールを照合',31);
  body+=row('auth',444,'サービス利用権限','SSH / Run Commandの開始とは別に確認',31);
 }else throw new Error('Unknown graph');
 body+='</g><g id="explanation-highlights">';
 for(const id of selected){if(!boxes.has(id))throw new Error('Unknown graph focus '+id);const {x,y,w,h}=boxes.get(id);body+=rect(x-2,y-2,w+4,h+4,'none','#de343b',2.8,5);}
 body+='</g>';
 return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1207 511">${renderSettingsShell(def.visual,body)}</svg>\n`;
}
await fs.mkdir(new URL('diagrams/settings-v2/',HERE),{recursive:true});
let svgCount=0;
for(const [index,entry] of turns){
 const t=script.turns[index-1],def=definitions.get(entry.viewId);if(!t||!t.id.startsWith(`lesson08-turn-${String(index).padStart(3,'0')}-`))throw new Error('Turn order changed');
 const v=structuredClone(def.visual);
 if(v.layout==='settings'){
  if(v.fields)v.highlightFields=entry.highlights;else v.highlightRows=entry.highlights;
  renderSettingsBody(v); // validates dimensions, row limits, menu and every highlight ID
 }else{
  const focus=entry.highlights.join('-')||'plain';
  v.svg=`diagrams/settings-v2/${entry.viewId}-${focus}.svg`;
  await fs.writeFile(new URL(v.svg,HERE),makeGraph(def,entry.highlights));svgCount++;
 }
 t.visual=v;
}
script.visualStyle='teaching-settings-v2';
const stripVisuals=s=>{const c=structuredClone(s);delete c.visualStyle;c.turns=c.turns.map(t=>{const o={...t};delete o.visual;return o;});return c;};
if(JSON.stringify(stripVisuals(before))!==JSON.stringify(stripVisuals(script)))throw new Error('A non-visual source field changed');
await fs.writeFile(scriptFile,JSON.stringify(script,null,2)+'\n');
console.log(JSON.stringify({style:script.visualStyle,bodyTurns:turns.size,viewCount:definitions.size,svgTurns:svgCount,nonVisualFieldsUnchanged:true,nonVisualSHA256:sha(JSON.stringify(stripVisuals(script))),views:[...definitions.keys()]},null,2));
