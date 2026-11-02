import fs from 'node:fs/promises';
import sharp from 'sharp';
const here=new URL('./',import.meta.url);
const script=JSON.parse(await fs.readFile(new URL('script.json',here),'utf8'));
const output=new URL('../../output/lesson-15-gemini/previews/',here);
const selected=[],seen=new Set();
for(const t of script.turns){
 const id=t.visual.viewId;
 if(id&&!seen.has(id)){selected.push(t);seen.add(id);}
}
for(const n of [8,20,28,31,33,37,40,44,46,49,54,64,67,69,70,72,74,78]){
 const t=script.turns[n-1];if(!selected.includes(t))selected.push(t);
}
await fs.mkdir(new URL('qa/',here),{recursive:true});
const manifest=[];
for(let i=0;i<selected.length;i+=4){
 const group=selected.slice(i,i+4),pieces=[];
 for(let j=0;j<group.length;j++){
  const turn=group[j],x=j%2*1227,y=Math.floor(j/2)*555;
  const body=await sharp(new URL(turn.id+'.png',output).pathname).extract({left:65,top:204,width:1207,height:511}).png().toBuffer();
  const label=Buffer.from(`<svg width="1207" height="32"><rect width="1207" height="32" fill="#d9e6eb"/><text x="10" y="23" font-size="20">${turn.id} / ${turn.visual.viewId}</text></svg>`);
  pieces.push({input:body,left:x,top:y+32},{input:label,left:x,top:y});
 }
 const file=`qa/sheet-${String(i/4+1).padStart(2,'0')}.png`;
 await sharp({create:{width:2434,height:1110,channels:3,background:'#ffffff'}}).composite(pieces).png().toFile(new URL(file,here).pathname);
 manifest.push({file,turns:group.map(t=>t.id)});
}
await fs.writeFile(new URL('qa/manifest.json',here),JSON.stringify(manifest,null,2)+'\n');
console.log(JSON.stringify(manifest));
