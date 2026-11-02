// Episode-local visual QA. The shared renderer remains unchanged.
import fs from 'node:fs/promises';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),sharp=require('sharp');
const script=JSON.parse(await fs.readFile(new URL('./script.json',import.meta.url),'utf8'));
const out=new URL('../../output/lesson-13-gemini/previews/',import.meta.url);
const selected=[],seen=new Set();
for(const turn of script.turns){
  const v=turn.visual;
  const key=v.layout==='hologram'?`opening-${turn.speaker}`:v.svg??v.viewId;
  if(seen.has(key))continue;
  seen.add(key);selected.push(turn);
}
for(let offset=0;offset<selected.length;offset+=6){
  const group=selected.slice(offset,offset+6),composite=[];
  for(const [i,turn]of group.entries()){
    const buffer=await sharp(new URL(turn.id+'.png',out).pathname).resize(800,450).toBuffer();
    composite.push({input:buffer,left:(i%2)*800,top:Math.floor(i/2)*450});
  }
  const sheet=new URL(`review-sheet-${String(offset/6+1).padStart(2,'0')}.jpg`,out);
  await sharp({create:{width:1600,height:Math.ceil(group.length/2)*450,channels:3,background:'#030710'}}).composite(composite).jpeg({quality:92}).toFile(sheet.pathname);
  console.log(JSON.stringify({sheet:sheet.pathname,turns:group.map(t=>t.id)}));
}
