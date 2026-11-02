import fs from 'node:fs/promises';
import {renderSettingsShell} from '../../settings-visual.mjs';
const dest=new URL('./diagrams/',import.meta.url);
await fs.mkdir(dest,{recursive:true});
const font='Hiragino Sans,Yu Gothic,Meiryo,sans-serif';
const tx=(x,y,s,size=19,color='#183844',weight=500)=>`<text x="${x}" y="${y}" font-family="${font}" font-size="${size}" font-weight="${weight}" fill="${color}">${s.replaceAll('&','&amp;').replaceAll('<','&lt;')}</text>`;
const box=(x,y,w,h,fill='#fff',stroke='#c5d8df')=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="8" fill="${fill}" stroke="${stroke}" stroke-width="1.4"/>`;
const outline=(x,y,w,h)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="8" fill="none" stroke="#d84343" stroke-width="3"/>`;
const arrow=(x1,x2,y)=>`<path d="M${x1} ${y}H${x2}" fill="none" stroke="#288a94" stroke-width="2.5"/><path d="m${x2-8} ${y-5} 8 5-8 5" fill="none" stroke="#288a94" stroke-width="2.5"/>`;
async function save(name,title,section,content){
 const shell=renderSettingsShell({areaLabel:'APIM / 利用制限と下流認証',title,kind:'教材用ポリシー設定',section,menu:['回数制限','下流認証'],subtitle:'設定と処理の位置関係を確認する',footer:'公開設定例・リポジトリ定義に基づく教材用の補助図'},content);
 await fs.writeFile(new URL(name+'.svg',dest),`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1207 511">${shell}</svg>\n`);
}
for(const focus of ['none','count','operation']){
 let s=box(214,147,972,328,'#f6fafb');
 s+=tx(235,178,'通常REST：共通検査 → 利用枠 → 下流認証準備 → 固有検査',20,'#17496b',650);
 const nodes=[['共通入力検査','model / profile'],['利用枠の取得','取得／キャッシュ'],['回数を計数','inboundで1回'],['下流認証準備','入口値を除去'],['固有検査','PII・文書 等']];
 nodes.forEach(([name,detail],i)=>{let x=234+i*188;s+=box(x,204,165,88,i===2?'#e7f4f4':'#fff');s+=tx(x+12,237,name,18,'#086975',650)+tx(x+12,268,detail,16,'#52727d');if(i<4)s+=arrow(x+165,x+185,250);});
 if(focus==='count')s+=outline(610,204,165,88);
 if(focus==='operation')s+=outline(986,204,165,88);
 s+=box(234,317,917,132,'#fff');
 s+=tx(252,347,'計数前の拒否：未知model / profile、共通サイズ検査',19);
 s+=tx(252,383,'計数後の拒否：PII・文書等の固有検査で起きる場合がある',19);
 s+=tx(252,425,'回数は「成功回数」ではない。固有検査の後に業務呼出しへ進む。',18,'#52727d');
 await save('count-'+focus,'要求を数えるタイミング','回数制限',s);
}
for(const focus of ['none','strip','backend']){
 let s=box(214,147,972,328,'#f6fafb');
 s+=tx(235,178,'Coreを呼ぶ契約と、接続先を呼ぶ権限を分ける',20,'#17496b',650);
 s+=box(234,204,220,149)+tx(250,234,'呼出元 → Core',21,'#086975',650)+tx(250,271,'APIMサブスクリプション',16)+tx(250,297,'キーを検証',19)+tx(250,330,'利用枠は契約IDで管理',17,'#52727d');
 s+=arrow(454,480,275);
 s+=box(482,204,332,149,'#e7f4f4')+tx(499,234,'APIM：入口資格情報を除去',21,'#086975',650)+tx(499,271,'キー／受信Authorization',19)+tx(499,300,'入口識別値／入力profile',19)+tx(499,330,'本人接続証明も下流へ渡さない',17,'#52727d');
 s+=arrow(814,840,275);
 s+=box(842,204,324,149)+tx(859,234,'APIM → 接続先',21,'#086975',650)+tx(859,271,'Managed Identity',20)+tx(859,300,'専用キー・ヘッダー等',19)+tx(859,330,'本人OAuthの下流token',19);
 if(focus==='strip')s+=outline(482,204,332,149);
 if(focus==='backend')s+=outline(842,204,324,149);
 s+=box(234,375,932,74,'#fff')+tx(252,405,'下流の接続方式は登録済み設定から選ぶ。入口キーを代用品にしない。',19)+tx(252,433,'本人OAuth接続では、Coreの接続証明照合とAPIMの接続利用権を確認する。',17,'#52727d');
 await save('auth-'+focus,'入口から下流で資格情報を切り替える','下流認証',s);
}
console.log('Wrote 6 episode-12 setting diagrams');
