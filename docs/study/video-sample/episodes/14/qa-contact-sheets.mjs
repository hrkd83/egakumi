import fs from 'node:fs/promises';
import path from 'node:path';
import sharp from 'sharp';
const here=path.dirname(new URL(import.meta.url).pathname);
const script=JSON.parse(await fs.readFile(path.join(here,'script.json'),'utf8'));
const out=path.resolve(here,'../../output/lesson-14-gemini');
const selected=[];const seen=new Set();
for(const turn of script.turns){const id=turn.visual.viewId;if(!id||seen.has(id))continue;seen.add(id);selected.push(turn.id);}
// Include each body view once, representative answer highlights, and all quizzes.
for(const n of [5,7,9,11,19,24,26,28,32,41,44,49,51,54,56,58,61,63,70,71,73,74,76,78,79]){
 const turn=script.turns[n-1];if(!selected.includes(turn.id))selected.push(turn.id);
}
await fs.mkdir(path.join(here,'qa'),{recursive:true});
const manifest=[];
for(let start=0;start<selected.length;start+=4){
 const group=selected.slice(start,start+4),composite=[];
 for(let i=0;i<group.length;i++){
  const turn=script.turns.find(t=>t.id===group[i]);
  const body=await sharp(path.join(out,'previews',group[i]+'.png')).extract({left:65,top:204,width:1207,height:511}).png().toBuffer();
  const y=(i%2)*555,x=Math.floor(i/2)*1227;
  const label=Buffer.from(`<svg width="1207" height="32"><rect width="1207" height="32" fill="#d9e6eb"/><text x="12" y="23" font-family="Arial" font-size="20" fill="#20323e">${group[i]} / ${turn.visual.viewId}</text></svg>`);
  composite.push({input:label,left:x,top:y},{input:body,left:x,top:y+32});
 }
 const file=`qa/body-sheet-${String(start/4+1).padStart(2,'0')}.png`;
 await sharp({create:{width:2434,height:1110,channels:3,background:'#ffffff'}}).composite(composite).png().toFile(path.join(here,file));
 manifest.push({file,turnIds:group});
}
await fs.writeFile(path.join(here,'qa/manifest.json'),JSON.stringify(manifest,null,2)+'\n');
console.log(JSON.stringify(manifest));
