// Inspection-only contact sheets in the temporary directory, no media mutation.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const here=path.dirname(fileURLToPath(import.meta.url));
const require=createRequire(import.meta.url);
const sharp=require('sharp');
const script=JSON.parse(await fs.readFile(path.join(here,'script.json'),'utf8'));
const seen=new Set();
const ids=[];
for(const turn of script.turns){
  const key=JSON.stringify(turn.visual);
  if(!seen.has(key)){seen.add(key);ids.push(turn.id);}
}
const out=path.join('/private/tmp','egakumi-a1-v2-qa');
await fs.mkdir(out,{recursive:true});
const previews=path.resolve(here,'../../output/lesson-a1-gemini/previews');
const sheets=[];
for(let begin=0;begin<ids.length;begin+=6){
  const items=await Promise.all(ids.slice(begin,begin+6).map(async(id,i)=>({
    input:await sharp(path.join(previews,id+'.png')).resize(800,450).png().toBuffer(),
    left:(i%2)*800,top:Math.floor(i/2)*450,
  })));
  const file=path.join(out,`sheet-${String(begin/6+1).padStart(2,'0')}.png`);
  await sharp({create:{width:1600,height:1350,channels:3,background:'#020710'}}).composite(items).png().toFile(file);
  sheets.push({file,ids:ids.slice(begin,begin+6)});
}
console.log(JSON.stringify({uniqueVisuals:ids.length,sheets},null,2));
