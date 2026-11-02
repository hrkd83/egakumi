import fs from 'node:fs/promises';
import path from 'node:path';
import sharp from 'sharp';
const here=path.dirname(new URL(import.meta.url).pathname);
const base=path.join(here,'final-frames');
const manifest=JSON.parse(await fs.readFile(path.join(base,'manifest.json'),'utf8'));
const selected=manifest.frames.filter(f=>f.phase==='body');
const sheets=[];
for(let start=0;start<selected.length;start+=4){
 const group=selected.slice(start,start+4),composites=[];
 for(let i=0;i<group.length;i++){
  const frame=group[i],x=i%2*1227,y=Math.floor(i/2)*705;
  const png=path.join(base,frame.file);
  const body=await sharp(png).extract({left:65,top:204,width:1207,height:511}).png().toBuffer();
  const captions=await sharp(png).extract({left:40,top:758,width:1520,height:125}).resize({width:1207,height:99}).png().toBuffer();
  const label=Buffer.from(`<svg width="1207" height="48"><rect width="1207" height="48" fill="#d9e6eb"/><text x="12" y="22" font-family="Arial" font-size="18" fill="#20323e">${frame.file} / ${frame.seconds.toFixed(3)} sec</text><text x="12" y="43" font-family="Arial" font-size="16" fill="#20323e">${frame.sceneId} / source ${manifest.sourceSha256.slice(0,16)}</text></svg>`);
  composites.push({input:label,left:x,top:y},{input:body,left:x,top:y+48},{input:captions,left:x,top:y+566});
 }
 const file=`final-contact-${String(start/4+1).padStart(2,'0')}.png`;
 await sharp({create:{width:2434,height:1410,channels:3,background:'#ffffff'}}).composite(composites).png().toFile(path.join(base,file));
 sheets.push({file,frames:group.map(f=>f.file)});
}
await fs.writeFile(path.join(base,'contacts.json'),JSON.stringify(sheets,null,2)+'\n');
console.log(JSON.stringify(sheets));
