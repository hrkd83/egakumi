// Episode 09 QA thumbnails only. Never edits source media or shared files.
import fs from 'node:fs/promises';
import fsSync from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {spawnSync} from 'node:child_process';
const episode=path.dirname(fileURLToPath(import.meta.url));
const here=path.resolve(episode,'../..');
const out=path.join(here,'output/lesson-09-gemini');
const require=createRequire(path.join(here,'package.json'));
const sharp=require('sharp');
const mode=process.argv[2]??'preview';
if(!['preview','final'].includes(mode))throw new Error('Use preview or final');
const qa=path.join(out,`qa-${mode}`);
await fs.mkdir(qa,{recursive:true});
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const time=t=>`${Math.floor(t/60)}:${String(Math.floor(t%60)).padStart(2,'0')}.${String(Math.round(t%1*100)).padStart(2,'0')}`;
let frames=[];
if(mode==='preview'){
 const manifest=JSON.parse(await fs.readFile(path.join(out,'previews/manifest.json'),'utf8'));
 const files=['previews/cast-establish.png',...manifest.previews];
 for(const file of files){const p=path.resolve(out,file);if(!p.startsWith(out+path.sep))throw new Error('Preview escapes output');frames.push({id:path.basename(file,'.png'),file:p});}
}else{
 const timeline=JSON.parse(await fs.readFile(path.join(out,'lesson-timeline.json'),'utf8'));
 const exported=JSON.parse(await fs.readFile(path.join(out,'lesson-export.json'),'utf8'));
 const selected=timeline.scenes.filter(s=>s.speaker||['logo','cast-establish','closing'].includes(s.id));
 const nums=[];
 for(const s of selected){
  const sample=Math.min(s.end-1/timeline.fps,s.start+(s.speaker?Math.min(2,s.duration*.35):s.duration*.5));
  const frame=Math.floor(sample*timeline.fps);
  nums.push(frame);frames.push({id:s.id,time:frame/timeline.fps,frame,file:path.join(qa,`frame-${String(nums.length).padStart(3,'0')}.png`)});
 }
 const expression=nums.map(n=>`eq(n\\,${n})`).join('+');
 const args=['-v','error','-y','-i',path.join(out,exported.file),'-vf',`select=${expression}`,'-fps_mode','vfr',path.join(qa,'frame-%03d.png')];
 const result=spawnSync('ffmpeg',args,{encoding:'utf8',maxBuffer:1024*1024});
 if(result.status!==0)throw new Error(result.stderr);
 for(const item of frames)if(!fsSync.existsSync(item.file))throw new Error('Missing extracted frame '+item.file);
}
const sheets=[];
for(let offset=0;offset<frames.length;offset+=6){
 const items=frames.slice(offset,offset+6),layers=[];
 for(const [i,item] of items.entries()){
  const x=(i%2)*800,y=Math.floor(i/2)*480;
  const raster=await sharp(item.file).resize({width:800,height:450}).png().toBuffer();
  layers.push({input:raster,left:x,top:y+30});
  const label=`${String(offset+i+1).padStart(3,'0')} ${item.id}${item.time!==undefined?' / '+time(item.time):''}`;
  layers.push({input:Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="800" height="30"><rect width="800" height="30" fill="#071827"/><text x="12" y="21" font-family="sans-serif" font-size="17" fill="#e5f7fc">${esc(label)}</text></svg>`),left:x,top:y});
 }
 const file=path.join(qa,`sheet-${String(sheets.length+1).padStart(2,'0')}.jpg`);
 await sharp({create:{width:1600,height:Math.ceil(items.length/2)*480,channels:3,background:'#030710'}}).composite(layers).jpeg({quality:92}).toFile(file);
 sheets.push({file,frames:items.map(x=>x.id)});
}
const report={mode,frames,sheets,sourceMediaEdited:false};
await fs.writeFile(path.join(qa,'manifest.json'),JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify({mode,frames:frames.length,sheets:sheets.length,manifest:path.join(qa,'manifest.json')}));
