// Read only completed episode 15 QA frames; write only final-frames/ sheets.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import sharp from 'sharp';

const here=path.dirname(fileURLToPath(import.meta.url));
const base=path.join(here,'final-frames');
const replace=process.argv.includes('--replace');
if(process.argv.slice(2).some(arg=>arg!=='--replace'))throw new Error('Only --replace is supported.');
const sha=buffer=>crypto.createHash('sha256').update(buffer).digest('hex');
const manifestBytes=await fs.readFile(path.join(base,'manifest.json'));
const manifest=JSON.parse(manifestBytes);
if(manifest.episodeKey!=='15'||manifest.sourceType!=='completed-mp4'||manifest.fps!==24
   ||manifest.width!==1600||manifest.height!==900||manifest.frames.length<30||manifest.frames.length>35)
  throw new Error('Expected the completed episode 15 extraction manifest.');
if((await fs.lstat(base)).isSymbolicLink())throw new Error('Destination must not be a symlink.');
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
for(const frame of manifest.frames){
 if(!/^\d{2}-[a-z0-9-]+\.png$/.test(frame.file))throw new Error('Unsafe source PNG filename.');
 const file=path.join(base,frame.file);
 if((await fs.lstat(file)).isSymbolicLink())throw new Error('QA frame must not be a symlink.');
 const bytes=await fs.readFile(file);
 if(sha(bytes)!==frame.sha256)throw new Error(`Changed source QA PNG: ${frame.file}`);
 const meta=await sharp(bytes).metadata();
 if(meta.width!==1600||meta.height!==900)throw new Error(`Unexpected dimensions: ${frame.file}`);
}

const body=manifest.frames.filter(f=>f.phase==='body');
const cast=manifest.frames.filter(f=>f.phase!=='body');
const plans=[];
for(const [prefix,frames] of [['body',body],['cast',cast]]){
 for(let start=0;start<frames.length;start+=4){
  plans.push({file:`final-contact-${prefix}-${String(start/4+1).padStart(2,'0')}.png`,
              kind:prefix,frames:frames.slice(start,start+4)});
 }
}
for(const file of [...plans.map(p=>p.file),'contacts.json']){
 const target=path.join(base,file);
 const info=await fs.lstat(target).catch(error=>{if(error.code==='ENOENT')return null;throw error;});
 if(info?.isSymbolicLink())throw new Error('Contact output must not be a symlink.');
 if(info&&!replace)throw new Error(`${file} exists; use --replace intentionally.`);
}
const sheets=[];
for(const plan of plans){
 const composites=[];
 const cellWidth=1207,cellHeight=plan.kind==='body'?690:735,gap=20;
 for(let i=0;i<plan.frames.length;i++){
  const frame=plan.frames[i],x=i%2*(cellWidth+gap),y=Math.floor(i/2)*(cellHeight+gap);
  const png=path.join(base,frame.file);
  const label=Buffer.from(`<svg width="1207" height="48"><rect width="1207" height="48" fill="#d9e6eb"/><text x="12" y="22" font-family="Arial" font-size="18" fill="#20323e">${esc(frame.file)} / ${frame.seconds.toFixed(3)} sec</text><text x="12" y="43" font-family="Arial" font-size="16" fill="#20323e">${esc(frame.sceneId)} / source ${esc(manifest.sourceSha256.slice(0,16))}</text></svg>`);
  composites.push({input:label,left:x,top:y});
  if(plan.kind==='body'){
   const settings=await sharp(png).extract({left:65,top:204,width:1207,height:511}).png().toBuffer();
   const captions=await sharp(png).extract({left:40,top:758,width:1520,height:125}).resize({width:1207,height:99}).png().toBuffer();
   composites.push({input:settings,left:x,top:y+48},{input:captions,left:x,top:y+566});
  }else{
   const full=await sharp(png).resize({width:1207}).png().toBuffer();
   composites.push({input:full,left:x,top:y+48});
  }
 }
 const target=path.join(base,plan.file);
 await sharp({create:{width:cellWidth*2+gap,height:cellHeight*2+gap,channels:3,background:'#ffffff'}})
  .composite(composites).png().toFile(target);
 sheets.push({file:plan.file,kind:plan.kind,sha256:sha(await fs.readFile(target)),frames:plan.frames.map(f=>f.file)});
}
const result={episodeKey:'15',sourceSha256:manifest.sourceSha256,extractionManifestSha256:sha(manifestBytes),
 sheets,visualReviewPerformedByThisScript:false,networkRequestsMade:false};
await fs.writeFile(path.join(base,'contacts.json'),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify({file:path.join(base,'contacts.json'),sheets:sheets.length,frames:manifest.frames.length}));
