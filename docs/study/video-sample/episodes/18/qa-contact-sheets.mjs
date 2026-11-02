import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import sharp from 'sharp';
const here=path.dirname(fileURLToPath(import.meta.url));
const script=JSON.parse(await fs.readFile(path.join(here,'script.json'),'utf8'));
const out=path.resolve(here,'../../output/lesson-18-gemini');
const selected=[],seen=new Set();
for(const turn of script.turns){
  if(turn.chapterId==='opening')continue;
  const signature=JSON.stringify(turn.visual);
  if(!seen.has(signature)){seen.add(signature);selected.push(turn);}
}
await fs.mkdir(path.join(here,'qa'),{recursive:true});
const manifest=[];
for(let start=0;start<selected.length;start+=4){
  const group=selected.slice(start,start+4),composite=[];
  for(let i=0;i<group.length;i++){
    const turn=group[i],x=(i%2)*1227,y=Math.floor(i/2)*555;
    const body=await sharp(path.join(out,'previews',turn.id+'.png')).extract({left:65,top:204,width:1207,height:511}).png().toBuffer();
    const label=Buffer.from(`<svg width="1207" height="32"><rect width="1207" height="32" fill="#d9e6eb"/><text x="12" y="23" font-family="Arial" font-size="20" fill="#20323e">${turn.id} / ${turn.visual.viewId}</text></svg>`);
    composite.push({input:label,left:x,top:y},{input:body,left:x,top:y+32});
  }
  const file=`qa/body-sheet-${String(start/4+1).padStart(2,'0')}.jpg`;
  await sharp({create:{width:2434,height:1110,channels:3,background:'#ffffff'}}).composite(composite).jpeg({quality:94}).toFile(path.join(here,file));
  manifest.push({file,turnIds:group.map(t=>t.id)});
}
await fs.writeFile(path.join(here,'qa/manifest.json'),JSON.stringify(manifest,null,2)+'\n');
console.log(JSON.stringify({panels:selected.length,sheets:manifest.length,manifest}));
