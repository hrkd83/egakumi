// Episode-local visual inspection of the encoded deliverable.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {spawnSync} from 'node:child_process';
const dir=path.dirname(fileURLToPath(import.meta.url));
const base=path.resolve(dir,'../..');
const require=createRequire(path.join(base,'package.json'));
const sharp=require('sharp');
const out=path.join(base,'output/lesson-a2-gemini');
const frames=path.join(out,'final-review-v2');
await fs.mkdir(frames,{recursive:true});
const timeline=JSON.parse(await fs.readFile(path.join(out,'lesson-timeline.json'),'utf8'));
const exported=JSON.parse(await fs.readFile(path.join(out,'lesson-export.json'),'utf8'));
const selected=[];
const seen=new Set();
for(const scene of timeline.scenes){
  const key=scene.phase==='opening'?`opening-${scene.speaker??'wide'}`:JSON.stringify(scene.visual??{phase:scene.phase});
  if(seen.has(key)||scene.chapterTitle)continue;
  seen.add(key);
  const time=scene.start+Math.min(scene.duration/2,scene.phase==='opening'?2:3);
  selected.push({id:scene.id,phase:scene.phase,time});
}
for(const row of selected){
  row.file=`${String(selected.indexOf(row)+1).padStart(2,'0')}-${row.id}.png`;
  const run=spawnSync('ffmpeg',['-v','error','-y','-ss',String(row.time),'-i',path.join(out,exported.file),'-frames:v','1',path.join(frames,row.file)],{encoding:'utf8'});
  if(run.status!==0)throw new Error(run.stderr);
}
for(let start=0;start<selected.length;start+=12){
  const batch=selected.slice(start,start+12),images=[];
  for(let i=0;i<batch.length;i++)images.push({input:await sharp(path.join(frames,batch[i].file)).resize(533,300).png().toBuffer(),left:(i%3)*533,top:Math.floor(i/3)*300});
  await sharp({create:{width:1599,height:Math.ceil(batch.length/3)*300,channels:3,background:'#071019'}}).composite(images).png().toFile(path.join(frames,`contact-${String(start/12+1).padStart(2,'0')}.png`));
}
await fs.writeFile(path.join(frames,'manifest.json'),JSON.stringify({encodedFile:exported.file,sha256:exported.sha256,frames:selected},null,2)+'\n');
console.log(JSON.stringify({directory:frames,frames:selected.length}));
