// Episode-local introductory comparison. The same canvas is held throughout
// the question and explanation; only the marked teaching outline changes.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {renderSettingsShell} from '../../settings-visual.mjs';

const DIR=path.dirname(fileURLToPath(import.meta.url));
const C={ink:'#20323e',muted:'#617985',accent:'#087f8c',red:'#de343b',pale:'#edf5f6',line:'#bfd2d9'};
const esc=x=>String(x).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const rect=(x,y,w,h,fill='#fff',stroke=C.line,r=5,sw=1)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
const text=(x,y,s,size=18,color=C.ink,weight=400)=>`<text x="${x}" y="${y}" font-family="Hiragino Sans,Yu Gothic,Meiryo,sans-serif" font-size="${size}" fill="${color}" font-weight="${weight}">${esc(s)}</text>`;
const red=(x,y,w,h)=>`<g class="teaching-highlight">${rect(x,y,w,h,'none',C.red,5,2.8)}</g>`;
const arrow=(x1,y,x2)=>`<path d="M${x1} ${y}H${x2}m-7 -5l7 5l-7 5" fill="none" stroke="${C.accent}" stroke-width="2.4"/>`;
const sha=s=>crypto.createHash('sha256').update(s).digest('hex');

export const comparisonView={
  layout:'svg',viewId:'connectivity-comparison',areaLabel:'DNSとPrivate Endpoint',
  title:'まず、PEとサブネットを使う方式を分ける',kind:'ネットワーク設定',
  section:'接続方式の比較',menu:['接続方式の比較','Private DNS','送信元別の確認'],
  subtitle:'この構成の比較 / 用語・機能の範囲はサービスやSKUで異なる',
  footer:'教材図 / APIMの管理面まですべてprivateになるという意味ではありません'
};

function content(key){
  let out=text(229,170,'A  Private Endpoint：VNet内に接続口だけを追加',20,C.accent,650);
  out+=rect(225,185,640,101,C.pale)+text(240,205,'自分たちのVNet',14,C.accent,650);
  out+=rect(242,218,195,57)+text(260,241,'アプリ',20,C.ink,650)+text(260,265,'名前を使って接続',15,C.muted);
  out+=rect(540,218,240,57)+text(554,241,'Private Endpoint',20,C.accent,650)+text(554,265,'VNet内の接続口 / private IP',15,C.muted);
  out+=rect(922,218,262,57)+text(937,241,'Azureサービス本体',20,C.ink,650)+text(937,265,'VNet外 / 例：Key Vault',15,C.muted);
  out+=arrow(441,247,532)+arrow(784,247,914);
  out+=text(229,311,'B  サブネットを使う方式：この構成のAPIM・ACA',20,C.accent,650);
  out+=rect(225,322,959,106,C.pale)+text(240,343,'自分たちのVNet',14,C.accent,650);
  out+=text(240,374,'この構成では',15,C.muted)+text(240,400,'自身のPEを作らない',15,C.ink,600);
  out+=rect(420,332,340,85,'#e2f0f1','#9cbfc4')+text(434,350,'APIM用サブネット',13,C.muted);
  out+=rect(429,357,322,58)+text(440,381,'APIMゲートウェイ',19,C.ink,650)+text(440,403,'VNetインジェクション / Internal',15,C.accent,600);
  out+=rect(784,332,384,85,'#e2f0f1','#9cbfc4')+text(798,350,'ACA環境用サブネット',13,C.muted);
  out+=rect(793,357,366,58)+text(806,381,'ACAの内部環境',19,C.ink,650)+text(806,403,'独自VNetとの統合 / 内部入口',15,C.accent,600);
  out+=rect(225,440,959,35,'#e8f4ff','#b6d9f3',3);
  out+=text(237,463,'DNSの解決先：',16,'#17496b',650)+text(379,463,'PEの実private IP',16,C.accent,600)+text(628,463,'APIMの実内部VIP',16,C.accent,600)+text(908,463,'ACAの実staticIp',16,C.accent,600);
  if(key==='pe')out+=red(221,181,967,109);
  if(key==='subnets')out+=red(221,318,967,114);
  if(key==='apim')out+=red(416,328,348,93);
  if(key==='aca')out+=red(780,328,392,93);
  if(key==='dns')out+=red(221,436,967,43);
  return out;
}

export async function makeConnectivityComparison(){
  await fs.mkdir(path.join(DIR,'diagrams'),{recursive:true});
  const files={};
  for(const key of ['none','pe','subnets','apim','aca','dns']){
    const file=`diagrams/connectivity-comparison-${key}.svg`;
    files[key]=file;
    const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="1207" height="511" viewBox="0 0 1207 511">${renderSettingsShell(comparisonView,content(key))}</svg>`;
    await fs.writeFile(path.join(DIR,file),svg);
  }
  return {view:comparisonView,files,baselineSha256:sha(await fs.readFile(path.join(DIR,files.none),'utf8'))};
}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  console.log(JSON.stringify(await makeConnectivityComparison(),null,2));
}
