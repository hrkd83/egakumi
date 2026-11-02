// Assemble review-only contact sheets from the final preview manifest.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import sharp from 'sharp';

const dir=path.dirname(fileURLToPath(import.meta.url));
const out=path.resolve(dir,'../../output/lesson-a2-gemini');
const script=JSON.parse(await fs.readFile(path.join(dir,'script.json'),'utf8'));
const preview=JSON.parse(await fs.readFile(path.join(out,'previews/manifest.json'),'utf8'));
const available=new Set(preview.previews);
const selected=[{file:'previews/cast-establish.png',id:'cast-establish',heading:'cast establish'}];
const seen=new Set();
for(const turn of script.turns){
  const key=JSON.stringify(turn.visual);
  const file=`previews/${turn.id}.png`;
  if(!seen.has(key)&&available.has(file)){
    seen.add(key);
    selected.push({file,id:turn.id,heading:turn.visual.heading??''});
  }
}
const target=path.join(out,'previews/qa-contacts');
await fs.mkdir(target,{recursive:true});
for(let start=0;start<selected.length;start+=6){
  const batch=selected.slice(start,start+6),images=[];
  for(let i=0;i<batch.length;i++)images.push({
    input:await sharp(path.join(out,batch[i].file)).resize(800,450).png().toBuffer(),
    left:(i%2)*800,top:Math.floor(i/2)*450,
  });
  await sharp({create:{width:1600,height:Math.ceil(batch.length/2)*450,channels:3,background:'#071019'}})
    .composite(images).png().toFile(path.join(target,`contact-${String(start/6+1).padStart(2,'0')}.png`));
}
await fs.writeFile(path.join(target,'manifest.json'),JSON.stringify({frames:selected},null,2)+'\n');
console.log(JSON.stringify({directory:target,frames:selected.length,sheets:Math.ceil(selected.length/6)}));
