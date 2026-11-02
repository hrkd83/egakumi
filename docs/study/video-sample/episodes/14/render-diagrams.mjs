import fs from 'node:fs/promises';
import {renderSettingsShell} from '../../settings-visual.mjs';

// Episode 14 only. These diagrams describe responsibilities and associations;
// they are not Azure screenshots or a packet-level execution sequence.
const ink='#20323e', muted='#5d7580', teal='#087f8c', blue='#3379a7';
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const rect=(x,y,w,h,fill='#fff',stroke='#c6d8de')=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="7" fill="${fill}" stroke="${stroke}" stroke-width="1.5"/>`;
const text=(x,y,s,size=19,color=ink,weight=400)=>`<text x="${x}" y="${y}" font-size="${size}" fill="${color}" font-weight="${weight}">${esc(s)}</text>`;
const arrow=(d,color=teal,dashed=false)=>`<path d="${d}" fill="none" stroke="${color}" stroke-width="2.4" ${dashed?'stroke-dasharray="6 5" ':''}marker-end="url(#arrow-${color===blue?'blue':'teal'})"/>`;
const defs=`<defs><marker id="arrow-teal" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0L8 4L0 8Z" fill="${teal}"/></marker><marker id="arrow-blue" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0L8 4L0 8Z" fill="${blue}"/></marker></defs>`;
const common={areaLabel:'Portal のサービスと管理',kind:'教材用の関連図',menu:['役割の分担','本人と所有','会社と費用'],footer:'リポジトリの実装説明に基づく概念図・稼働環境の取得画面ではない'};

const roles=`${defs}<g font-family="Hiragino Sans,Yu Gothic,Meiryo,sans-serif">
${rect(214,147,972,328,'#f8fbfc')}
${text(234,174,'Portal が SQL と APIM を個別に呼び出す',19,muted,500)}
${rect(234,267,180,104,'#edf5fb','#b9d3e5')}
${text(254,300,'ブラウザー',22,ink,600)}
${text(254,331,'画面から操作',18,muted)}
${text(254,354,'本人の要求',18,muted)}
${arrow('M414 319H483')}
${text(429,304,'HTTP',16,muted)}
${rect(494,243,236,152,'#e9f5f3','#98c9c5')}
${text(516,276,'Portal',24,teal,650)}
${text(516,307,'本人確認・入力検査',18)}
${text(516,334,'所属と権限の判断',18)}
${text(516,361,'SQL・APIMへの要求',18)}
${rect(828,199,338,112)}
${text(850,230,'SQL',23,teal,650)}
${text(850,257,'会社・所有グループ・キー台帳',18)}
${text(850,284,'ログイン状態・操作の監査',18)}
${rect(828,334,338,112)}
${text(850,365,'APIM',23,teal,650)}
${text(850,392,'APIMサブスクリプション',19)}
${text(850,420,'APIキーの実値と利用時の制御',18)}
${arrow('M730 292H775V255H818')}
${arrow('M730 346H775V390H818')}
${text(234,456,'ブラウザーから SQL へ直接接続しない。矢印は管理処理の要求先。',18,muted)}
</g>`;

const identity=`${defs}<g font-family="Hiragino Sans,Yu Gothic,Meiryo,sans-serif">
${rect(214,147,972,328,'#f8fbfc')}
${text(234,174,'操作時の現在所属を確認し、グループ所有のキーへ対応付ける',19,muted,500)}
${rect(234,212,258,142,'#edf5fb','#b9d3e5')}
${text(254,244,'本人',23,ink,650)}
${text(254,274,'Entra の tenant ID',19)}
${text(254,302,'＋ Object ID',19)}
${text(254,332,'portalUser / Session',17,muted)}
${arrow('M492 281H556')}
${text(505,263,'照会',16,muted)}
${rect(567,212,256,142,'#e9f5f3','#98c9c5')}
${text(587,244,'Microsoft Graph',22,teal,650)}
${text(587,274,'現在の所属グループ',19)}
${text(587,302,'本人 ID を使って確認',18)}
${text(587,332,'所属の名簿を SQL に保存しない',15,muted)}
${arrow('M823 281H887')}
${text(836,263,'照合',16,muted)}
${rect(898,212,268,142)}
${text(918,244,'SQL の管理情報',22,teal,650)}
${text(918,274,'所有グループ → APIキー',18)}
${text(918,302,'製品承認・キー状態',19)}
${text(918,332,'本人とキーの直接 FK はない',16,muted)}
${arrow('M1032 354V380',blue,true)}
${rect(234,391,932,65,'#f1f4fa','#c9d5e4')}
${text(254,418,'companyId → portalCompany：利用会社と費用帰属の対応',20,blue,600)}
${text(254,443,'会社の一致だけでは管理権限を与えない。所有と操作権限は Entra グループで判断。',17,muted)}
</g>`;

await fs.mkdir(new URL('./diagrams/',import.meta.url),{recursive:true});
for(const [name,viewId,title,section,content] of [
  ['roles-flow','lesson14-roles-flow','Portal・SQL・APIM の役割を分ける','役割の分担',roles],
  ['identity-flow','lesson14-identity-flow','本人から管理できるキーへ','本人と所有',identity],
]){
  const shell=renderSettingsShell({...common,viewId,title,section,subtitle:'設定と保存先の関係を、固定した画面で確認する'},content);
  const path=new URL(`./diagrams/${name}.svg`,import.meta.url);
  await fs.writeFile(path,`<svg xmlns="http://www.w3.org/2000/svg" width="1207" height="511" viewBox="0 0 1207 511">${shell}</svg>\n`);
  console.log(JSON.stringify({file:path.pathname,viewId}));
}
