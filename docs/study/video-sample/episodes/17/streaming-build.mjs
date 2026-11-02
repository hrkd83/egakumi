// Episode-17-only streaming adaptation of the verified shared builder.
// The shared sources, input script, generation records and audio are read-only.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {spawnSync} from 'node:child_process';

const episode=path.dirname(fileURLToPath(import.meta.url));
const base=path.resolve(episode,'../..');
const builderSource=path.join(base,'build-lesson.mjs');
const visualSource=path.join(base,'lesson-visual.mjs');
const builderLocal=path.join(episode,'.streaming-build.generated.mjs');
const visualLocal=path.join(episode,'.streaming-visual.generated.mjs');
const profile=JSON.parse(await fs.readFile(path.join(base,'production-profile.json'),'utf8'));
const sha=value=>crypto.createHash('sha256').update(value).digest('hex');
const operations=[];
function replaceOnce(source,oldText,newText,label){
  const count=source.split(oldText).length-1;
  if(count!==1)throw new Error('Refusing ambiguous transform '+label+': expected 1, found '+count);
  operations.push(label);
  return source.replace(oldText,newText);
}
async function verifiedSource(file){
  const data=await fs.readFile(file);
  if(sha(data)!==profile.commonFileSha256[path.basename(file)])throw new Error('Shared source hash differs from production profile: '+file);
  return data.toString('utf8');
}
let visual=await verifiedSource(visualSource);
visual=replaceOnce(visual,"from './hologram-visual.mjs'","from "+JSON.stringify(pathToFileURL(path.join(base,'hologram-visual.mjs')).href),'visual hologram import');
visual=replaceOnce(visual,"from './settings-visual.mjs'","from "+JSON.stringify(pathToFileURL(path.join(base,'settings-visual.mjs')).href),'visual settings import');
visual=replaceOnce(visual,'const HERE=path.dirname(fileURLToPath(import.meta.url));','const HERE='+JSON.stringify(base)+';','visual asset base');
visual=replaceOnce(visual,'const require=createRequire(import.meta.url);','const require=createRequire('+JSON.stringify(pathToFileURL(visualSource).href)+');','visual dependency base');
visual=replaceOnce(visual,'if(pngCache.size>220)','if(pngCache.size>4)','four-entry renderer cache');

let builder=await verifiedSource(builderSource);
builder=replaceOnce(builder,"import {spawnSync} from 'node:child_process';","import {spawn,spawnSync} from 'node:child_process';",'async encoder import');
builder=replaceOnce(builder,"from './lesson-visual.mjs'","from "+JSON.stringify(pathToFileURL(visualLocal).href),'local bounded visual import');
builder=replaceOnce(builder,"from './lesson-audio-cache.mjs'","from "+JSON.stringify(pathToFileURL(path.join(base,'lesson-audio-cache.mjs')).href),'audio validation import');
builder=replaceOnce(builder,'const here=path.dirname(fileURLToPath(import.meta.url));','const here='+JSON.stringify(base)+';','builder asset/input base');
builder=replaceOnce(builder,'const require=createRequire(import.meta.url);','const require=createRequire('+JSON.stringify(pathToFileURL(builderSource).href)+');','builder dependency base');
builder=replaceOnce(builder,"const reuseAudio=options['--reuse-audio']==='true'&&await checkAudioCache(out,audioSignature);","const reuseAudio=options['--reuse-audio']==='true'&&await checkAudioCache(out,audioSignature);\nif(!reuseAudio)throw new Error('Episode 17 streaming build requires a verified existing audio cache; audio regeneration is prohibited.');",'require verified reused audio');

const startMarker='const cueList=[],visualEntries=[],cameraTimeline=[];';
const endMarker='const stamp=seconds=>';
if(builder.split(startMarker).length!==2||builder.split(endMarker).length!==2)throw new Error('Render region markers changed.');
const renderStart=builder.indexOf(startMarker),renderEnd=builder.indexOf(endMarker);
if(renderStart>=renderEnd)throw new Error('Render region order changed.');
operations.push('replace PNG spool with one-frame bounded image2pipe rendering');
const streaming=String.raw`const cueList=[],cameraTimeline=[];
let posterWritten=false,streamingFrames=0;
const argsFor=(scene,more={})=>({time:scene.start,duration,phase:scene.phase,speaker:scene.speaker,previousSpeaker:scene.previousSpeaker,turnTime:0,sceneTime:0,title:lessonTitle,episodeNumber,episodeLabel,seriesLabel:script.seriesLabel,chapter:scene.chapter,visual:scene.visual??{},subtitleLines:scene.subtitleLines??[],logoDuration:logo.duration,closingDuration:closing.duration,...more});
function chunksFor(scene){
  const chunks=scene.subtitleChunks?.length?scene.subtitleChunks:[{caption:scene.caption,lines:scene.subtitleLines,weight:1}];
  for(const c of chunks)if(!Array.isArray(c.lines)||c.lines.length>2||!c.lines.length)throw new Error('Invalid captions '+scene.id);
  const weight=chunks.reduce((n,c)=>n+(c.weight??[...c.caption].length),0);
  let used=0;
  return chunks.map((c,i)=>{const count=i===chunks.length-1?scene.frameCount-used:Math.max(1,Math.floor(scene.frameCount*(c.weight??[...c.caption].length)/weight));const result={...c,frames:count,start:scene.start+used/fps};used+=count;return result;});
}
const streamingFile=episodeKey==='01'?'egakumi-study-01-network-overview.mp4':'egakumi-study-'+episodeKey+'.mp4';
const encoder=spawn('ffmpeg',['-v','error','-y','-f','image2pipe','-framerate','24','-vcodec','png','-i','pipe:0','-i',path.join(out,'lesson-mix.wav'),'-map','0:v','-map','1:a','-t',String(duration),'-frames:v',String(cursor),'-vf','fps=24,scale=out_color_matrix=bt709','-fps_mode','cfr','-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p','-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-c:a','aac','-b:a','192k','-movflags','+faststart',path.join(out,streamingFile)],{stdio:['pipe','ignore','pipe']});
let encoderError=null,encoderClosed=false,encoderStderr='';
encoder.stderr.on('data',data=>{encoderStderr=(encoderStderr+data.toString()).slice(-65536);});
encoder.stdin.on('error',error=>{encoderError=error;});
const encoderFinished=new Promise((resolve,reject)=>{
  encoder.once('error',error=>{encoderError=error;reject(error);});
  encoder.once('close',code=>{encoderClosed=true;if(code===0)resolve();else{encoderError=new Error('Streaming ffmpeg exited '+code+': '+encoderStderr);reject(encoderError);}});
});
// Rendering awaits each write callback, keeping only one submitted PNG Buffer
// in flight even when the encoder applies pipe backpressure.
encoderFinished.catch(()=>{});
process.once('exit',()=>{if(!encoderClosed)encoder.kill('SIGTERM');});
async function feed(buffer,count){
  if(!Number.isSafeInteger(count)||count<1)throw new Error('Invalid streaming frame count.');
  for(let i=0;i<count;i++){
    if(encoderError)throw encoderError;
    if(encoderClosed)throw new Error('Encoder closed before all frames were sent.');
    await new Promise((resolve,reject)=>encoder.stdin.write(buffer,error=>error?reject(error):resolve()));
    streamingFrames++;
  }
}
async function still(scene,subtitleLines,count,start){
  const buffer=await renderLessonRaster(argsFor(scene,{subtitleLines}));
  if(!posterWritten&&scene.phase==='body'&&!scene.chapterTitle){await fs.writeFile(path.join(out,'lesson-poster.png'),buffer);posterWritten=true;}
  await feed(buffer,count);
  if(scene.speaker)cueList.push({start,end:start+count/fps,speaker:scene.speaker,lines:subtitleLines,turnId:scene.id});
}
async function approvedLogo(scene,approved){
  const decoder=spawn('ffmpeg',['-v','error','-i',approved,'-vf','fps=24','-frames:v',String(scene.frameCount),'-f','image2pipe','-vcodec','png','-compression_level','1','pipe:1'],{stdio:['ignore','pipe','pipe']});
  let stderr='',closed=false;
  decoder.stderr.on('data',data=>{stderr=(stderr+data.toString()).slice(-16384);});
  const done=new Promise((resolve,reject)=>{decoder.once('error',reject);decoder.once('close',code=>{closed=true;code===0?resolve():reject(new Error('Logo decoder exited '+code+': '+stderr));});});
  done.catch(()=>{});
  const onExit=()=>{if(!closed)decoder.kill('SIGTERM');};process.once('exit',onExit);
  let pending=Buffer.alloc(0),frames=0;
  try{
    for await(const data of decoder.stdout){
      pending=Buffer.concat([pending,data]);
      while(pending.length>=8){
        if(!pending.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])))throw new Error('Logo decoder returned an invalid PNG signature.');
        let offset=8,end=0;
        while(offset+12<=pending.length){
          const size=pending.readUInt32BE(offset),next=offset+12+size;
          if(next>pending.length)break;
          if(pending.toString('ascii',offset+4,offset+8)==='IEND'){end=next;break;}
          offset=next;
        }
        if(!end)break;
        await feed(pending.subarray(0,end),1);frames++;
        pending=pending.subarray(end);
      }
      if(pending.length>32*1024*1024)throw new Error('Logo PNG stream exceeded one-frame memory bound.');
    }
    await done;
    if(pending.length||frames!==scene.frameCount)throw new Error('Approved logo frame count differs from timeline.');
  }catch(error){decoder.kill('SIGTERM');throw error;}
  finally{process.removeListener('exit',onExit);}
}
const streamingStarted=Date.now();
console.log('Streaming render and H.264 encoding started: '+cursor+' frames / '+duration+' seconds');
try{
  for(const scene of scenes){
    if(scene.phase==='logo'){
      const approved=path.join(here,'assets/brand/egakumi-logo-intro.mp4');
      if(await fs.access(approved).then(()=>true,()=>false))await approvedLogo(scene,approved);
      else for(let f=0;f<scene.frameCount;f++)await feed(await renderLessonRaster(argsFor(scene,{sceneTime:f/fps,time:f/fps})),1);
    }else if(scene.phase==='opening'||scene.phase==='closing'){
      const chunks=scene.speaker?chunksFor(scene):[{lines:[],frames:scene.frameCount,start:scene.start}];
      let f=0;
      for(const chunk of chunks){
        if(scene.speaker)cueList.push({start:chunk.start,end:chunk.start+chunk.frames/fps,speaker:scene.speaker,lines:chunk.lines,turnId:scene.id});
        const step=scene.phase==='opening'?1:2;
        for(let local=0;local<chunk.frames;local+=step){
          const frame=f+local,count=Math.min(step,chunk.frames-local);
          const args=argsFor(scene,{time:scene.start+frame/fps,sceneTime:frame/fps,turnTime:frame/fps,subtitleLines:chunk.lines});
          await feed(await renderLessonRaster(args),count);
        }
        f+=chunk.frames;
      }
      if(scene.phase==='opening')cameraTimeline.push({id:scene.id,start:scene.start,end:scene.end,speaker:scene.speaker,previousSpeaker:scene.previousSpeaker,from:getHologramCamera(argsFor(scene,{turnTime:0})),to:getHologramCamera(argsFor(scene,{turnTime:2}))});
    }else if(scene.speaker){for(const chunk of chunksFor(scene))await still(scene,chunk.lines,chunk.frames,chunk.start);}
    else await still(scene,[],scene.frameCount,scene.start);
    const elapsed=(Date.now()-streamingStarted)/1000;
    console.log('Streamed '+scene.id+' / '+streamingFrames+'/'+cursor+' frames / '+elapsed.toFixed(1)+'s / '+(streamingFrames/Math.max(.001,elapsed)).toFixed(1)+' output fps');
  }
  const renderedFrames=streamingFrames;
  if(renderedFrames!==cursor)throw new Error('Image timeline differs from audio timeline');
  encoder.stdin.end();
}catch(error){encoder.stdin.destroy();encoder.kill('SIGTERM');throw error;}
`;
builder=builder.slice(0,renderStart)+streaming+builder.slice(renderEnd);
const encodeLine="run('ffmpeg',['-v','error','-y','-f','concat','-safe','0','-i',path.join(work,'visual-concat.txt'),'-i',path.join(out,'lesson-mix.wav'),'-map','0:v','-map','1:a','-t',String(duration),'-vf','fps=24,scale=out_color_matrix=bt709','-fps_mode','cfr','-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p','-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-c:a','aac','-b:a','192k','-movflags','+faststart',path.join(out,file)]);";
builder=replaceOnce(builder,encodeLine,"await encoderFinished;\nif(encoderError)throw encoderError;\nif(file!==streamingFile)throw new Error('Streaming export filename differs.');",'await concurrent encoder completion');
builder=replaceOnce(builder,"if(options['--write-script']==='true'){script.status='first-cut-produced';script.output=\u0060output/${outputName}/${file}\u0060;script.actualDurationSeconds=duration;await fs.writeFile(path.join(here,scriptName),JSON.stringify(script,null,2)+'\\n');}","if(options['--write-script']==='true')throw new Error('Streaming runner does not modify the source script.');",'prohibit source script mutation');
if(builder.includes('visual-concat.txt')||builder.includes('visualEntries')||builder.includes('motion-${scene.id}'))throw new Error('PNG spooling remnants remain.');
await fs.writeFile(visualLocal,visual);
await fs.writeFile(builderLocal,builder);
for(const file of [visualLocal,builderLocal]){
  const check=spawnSync(process.execPath,['--check',file],{encoding:'utf8'});
  if(check.status!==0)throw new Error(check.stderr||'Generated module syntax check failed.');
}
const flags=process.argv.slice(2);
const checkOnly=flags.length===1&&['--check','--dry-run'].includes(flags[0]);
const explicitReuse=flags.length===2&&flags[0]==='--reuse-audio'&&flags[1]==='true';
if(flags.length&&!checkOnly&&!explicitReuse)throw new Error('Use --reuse-audio true to render, or --check for a local syntax/transform check.');
console.log(JSON.stringify({episode:'17',sourceBuilderSha256:sha(await fs.readFile(builderSource)),sourceVisualSha256:sha(await fs.readFile(visualSource)),transforms:operations,rendererCacheEntries:4,pngSpooling:false,reuseAudioRequired:true,networkRequestsMade:false}));
if(checkOnly)process.exit(0);
process.argv=[process.execPath,builderLocal,'--script','episodes/17/script.json','--generation-config','episodes/17/generation.json','--cast','podcast-cast-gemini.json','--output','lesson-17-gemini','--reuse-audio','true'];
await import(pathToFileURL(builderLocal).href);
