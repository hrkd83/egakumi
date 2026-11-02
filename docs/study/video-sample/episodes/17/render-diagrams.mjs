import fs from 'node:fs/promises';
import sharp from 'sharp';
import {renderSettingsShell} from '../../settings-visual.mjs';

const E=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const text=(x,y,s,size=19,color='#20323e',weight=400)=>`<text x="${x}" y="${y}" font-family="Hiragino Sans,Yu Gothic,Meiryo,sans-serif" font-size="${size}" font-weight="${weight}" fill="${color}">${E(s)}</text>`;
const rect=(x,y,w,h,fill='#fff',stroke='#c9dce1',r=7)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}"/>`;
const arrow=(d,dashed=false)=>`<path d="${d}" fill="none" stroke="${dashed?'#547d99':'#087f8c'}" stroke-width="2.8" ${dashed?'stroke-dasharray="6 5"':''} marker-end="url(#${dashed?'back':'arrow'})"/>`;
const card=(x,y,w,h,title,lines)=>rect(x,y,w,h)+text(x+15,y+29,title,21,'#087f8c',650)+lines.map((s,i)=>text(x+15,y+57+i*24,s,18)).join('');
const defs=`<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#087f8c"/></marker><marker id="back" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#547d99"/></marker></defs>`;
const shell=(id,title,section,subtitle,content)=>`<svg xmlns="http://www.w3.org/2000/svg" width="1207" height="511" viewBox="0 0 1207 511">${defs}${renderSettingsShell({viewId:`lesson17-${id}`,areaLabel:'Portalのサービスと管理',title,kind:'発行後のキー管理',section,menu:['操作と保存先','所有グループ','利用制限','確認問題'],subtitle,footer:'現行リポジトリ定義に基づく教材用の設定ビュー。稼働環境の取得画面ではない。'},content)}</svg>`;

const management=rect(214,147,972,328,'#f6fafb')
 +text(234,177,'管理操作はPortalがAPIMとSQLへ依頼する',21,'#20323e',650)
 +card(235,252,190,91,'Portal',['本人・所属を確認'])
 +card(520,195,292,106,'APIM',['状態変更・キー取得','APIMサブスクリプション'])
 +card(520,337,292,103,'SQL',['台帳・操作監査','変更を同じSQL処理で保存'])
 +arrow('M425 284H469V245H516')
 +arrow('M425 311H469V388H516')
 +text(434,231,'管理API',17,'#087f8c')
 +text(433,373,'保存',17,'#087f8c')
 +rect(845,195,319,245,'#fff')
 +rect(845,195,319,35,'#e8f0f3','#c9dce1',7)
 +text(861,219,'確認する保存先',18,'#526b78',650)
 +text(861,258,'キー実値',18,'#526b78')+text(1012,258,'APIM',20,'#087f8c',650)
 +'<path d="M859 275H1150" stroke="#d9e5e8"/>'
 +text(861,302,'状態・履歴',18,'#526b78')+text(1012,302,'SQL',20,'#087f8c',650)
 +'<path d="M859 319H1150" stroke="#d9e5e8"/>'
 +text(861,346,'キー実値をSQLへ保存しない',18,'#20323e')
 +text(861,382,'SQL処理とAPIM操作は別',18,'#20323e')
 +text(861,412,'結果不明なら再照会',18,'#087f8c',650)
 +text(234,463,'SQLの保存成功だけで、APIMの操作完了を推測しない',18,'#526b78');

const limits=rect(214,147,972,328,'#f6fafb')
 +text(234,176,'設定の保存',20,'#20323e',650)
 +card(234,188,184,85,'管理者画面',['読込時の版＋新設定'])
 +card(473,188,224,85,'Portal',['現在所属・承認を確認'])
 +card(752,188,411,85,'SQL',['版一致 → 制限＋変更監査を一緒に保存'])
 +arrow('M418 228H468')+arrow('M697 228H747')
 +text(234,303,'API利用時：実APIMサブスクリプションIDで照会',20,'#20323e',650)
 +card(234,320,180,85,'APIM',['取得した制限を適用'])
 +card(535,320,299,85,'Portal内部profile API',['専用鍵でAPIMを確認'])
 +card(955,320,208,85,'SQL',['該当する制限を返す'])
 +arrow('M414 353H530')+text(426,339,'専用鍵＋ID',17,'#087f8c')
 +arrow('M834 353H950')+text(849,339,'設定を読む',17,'#087f8c')
 +arrow('M955 390H839',true)+arrow('M535 390H419',true)
 +rect(234,424,929,38,'#e8f4ff','#b6d9f3',4)
 +text(248,450,'APIMの取得結果：30秒再利用',19,'#17496b',650)
 +text(696,450,'照会障害：503で止める',19,'#17496b',650);

await fs.mkdir(new URL('./diagrams/',import.meta.url),{recursive:true});
for(const [name,svg] of [
 ['management-flow',shell('management-flow','キー管理の操作と保存先','操作と保存先','Portal・SQL・APIMの担当と、途中失敗時の確認を対応付ける',management)],
 ['limits-flow',shell('limits-flow','利用制限の保存からAPI利用への反映','利用制限','設定保存と利用時の取得を分け、版数・キャッシュ・障害時の扱いを確認する',limits)],
]){
 await fs.writeFile(new URL(`./diagrams/${name}.svg`,import.meta.url),svg);
 await sharp(Buffer.from(svg)).png().toFile(new URL(`./diagrams/${name}.png`,import.meta.url).pathname);
 console.log(name);
}
