import fs from 'node:fs/promises';
import path from 'node:path';
import sharp from 'sharp';
import {fileURLToPath} from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url));
const out=path.resolve(here,'../../output/lesson-12-gemini');
const script=JSON.parse(await fs.readFile(path.join(here,'script.json'),'utf8'));
const reps=new Map();
const omitted=new Set(script.firstCut?.omitTurnIds??[]);
for(const t of script.turns.filter(t=>!omitted.has(t.id))){
  const key=t.visual.layout==='svg'?t.visual.svg:(t.visual.viewId??t.id);
  if(!reps.has(key)||((t.visual.highlightRows?.length||t.visual.highlightFields?.length)&&!reps.get(key).highlighted))reps.set(key,{t,highlighted:Boolean(t.visual.highlightRows?.length||t.visual.highlightFields?.length)});
}
const items=[{file:'cast-establish.png',label:'cast-establish'},...Array.from(reps.values(),({t})=>({file:t.id+'.png',label:t.visual.viewId??t.id}))];
const qa=path.join(out,'visual-qa');await fs.mkdir(qa,{recursive:true});
for(let start=0;start<items.length;start+=9){
  const set=items.slice(start,start+9),layers=[];
  for(let i=0;i<set.length;i++){
    const x=i%3*480,y=Math.floor(i/3)*298;
    layers.push({input:await sharp(path.join(out,'previews',set[i].file)).resize(480,270).toBuffer(),left:x,top:y});
    const label=set[i].label.replace(/[<>&]/g,'');
    layers.push({input:Buffer.from(`<svg width="480" height="28"><rect width="480" height="28" fill="#071321"/><text x="10" y="20" fill="#d1eeed" font-size="14" font-family="sans-serif">${label}</text></svg>`),left:x,top:y+270});
  }
  await sharp({create:{width:1440,height:Math.ceil(set.length/3)*298,channels:3,background:'#071321'}}).composite(layers).png().toFile(path.join(qa,`sheet-${start/9+1}.png`));
}
await fs.writeFile(path.join(qa,'manifest.json'),JSON.stringify({items},null,2)+'\n');
console.log(JSON.stringify({representativeViews:reps.size,sheets:Math.ceil(items.length/9),directory:qa}));
