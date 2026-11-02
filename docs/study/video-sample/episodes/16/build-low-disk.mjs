// Episode 16 fallback: run the approved builder with lossless PNG pipe transport.
// Shared source/assets are read only. Audio, timing, captions and x264 settings
// stay in the approved builder; only temporary PNG storage is replaced.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {spawnSync} from 'node:child_process';

const episodeDir=path.dirname(fileURLToPath(import.meta.url));
const toolkit=path.resolve(episodeDir,'../..');
const shared=path.join(toolkit,'build-lesson.mjs');
const profile=JSON.parse(await fs.readFile(path.join(toolkit,'production-profile.json'),'utf8'));
let code=await fs.readFile(shared,'utf8');
const sourceSha256=crypto.createHash('sha256').update(code).digest('hex');
if(sourceSha256!==profile.commonFileSha256['build-lesson.mjs'])throw new Error('Approved builder hash mismatch');
const replace=(before,after)=>{
  if(code.split(before).length!==2)throw new Error('Expected exactly one shared source match: '+before.slice(0,90));
  code=code.replace(before,after);
};
replace("import {spawnSync} from 'node:child_process';","import {spawnSync,spawn} from 'node:child_process';");
for(const module of ['lesson-visual.mjs','lesson-audio-cache.mjs'])replace("from './"+module+"'","from "+JSON.stringify(pathToFileURL(path.join(toolkit,module)).href));
replace('const here=path.dirname(fileURLToPath(import.meta.url));','const here='+JSON.stringify(toolkit)+';');
replace('const require=createRequire(import.meta.url);',"const require=createRequire(path.join(here,'build-lesson.mjs'));");
replace("const castName=options['--cast']??(outputName==='lesson-01'?'podcast-cast.json':'podcast-cast-gemini.json');",`const castName=options['--cast']??(outputName==='lesson-01'?'podcast-cast.json':'podcast-cast-gemini.json');
if(scriptName!=='episodes/16/script.json'||outputName!=='lesson-16-gemini'||configName!=='episodes/16/generation.json'||castName!=='podcast-cast-gemini.json'||options['--write-script']==='true')throw new Error('This transport wrapper is restricted to episode 16');`);
replace("console.log(`Narration complete: ${(duration/60).toFixed(2)} minutes`);",`// Narration is now complete; only episode-local, reproducible scene WAVs are released.
for(const scene of scenes)await fs.rm(path.join(work,scene.id+'.wav'),{force:true});
console.log(\`Narration complete: \${(duration/60).toFixed(2)} minutes\`);`);
const visualStart=code.indexOf('const cueList=[],visualEntries=[],cameraTimeline=[];');
const visualEnd=code.indexOf('const renderedFrames=visualEntries.reduce',visualStart);
if(visualStart<0||visualEnd<0)throw new Error('Cannot locate approved visual block');
const oldVisual=code.slice(visualStart,visualEnd);
const chunksStart=oldVisual.indexOf('function chunksFor(scene){');
const chunksEnd=oldVisual.indexOf('\nfor(const scene of scenes){',chunksStart);
if(chunksStart<0||chunksEnd<0)throw new Error('Cannot locate approved subtitle chunk function');
const chunksFor=oldVisual.slice(chunksStart,chunksEnd);
const streamVisual=`const cueList=[],visualEntries=[],cameraTimeline=[];
let posterWritten=false;
const argsFor=(scene,more={})=>({time:scene.start,duration,phase:scene.phase,speaker:scene.speaker,previousSpeaker:scene.previousSpeaker,turnTime:0,sceneTime:0,title:lessonTitle,episodeNumber,episodeLabel,seriesLabel:script.seriesLabel,chapter:scene.chapter,visual:scene.visual??{},subtitleLines:scene.subtitleLines??[],logoDuration:logo.duration,closingDuration:closing.duration,...more});
const streamedVideoFile=episodeKey==='01'?'egakumi-study-01-network-overview.mp4':'egakumi-study-'+episodeKey+'.mp4';
const pngEncoder=spawn('ffmpeg',['-v','error','-y','-f','image2pipe','-framerate','24','-c:v','png','-i','pipe:0','-i',path.join(out,'lesson-mix.wav'),'-map','0:v','-map','1:a','-t',String(duration),'-vf','fps=24,scale=out_color_matrix=bt709','-fps_mode','cfr','-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p','-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-c:a','aac','-b:a','192k','-movflags','+faststart',path.join(out,streamedVideoFile)],{stdio:['pipe','ignore','pipe']});
let encoderError=null,encoderStderr='';
pngEncoder.stderr.on('data',data=>{encoderStderr=(encoderStderr+data.toString()).slice(-12000)});
pngEncoder.stdin.on('error',error=>{encoderError=error});
const encoderDone=new Promise((resolve,reject)=>{pngEncoder.once('error',reject);pngEncoder.once('close',(code,signal)=>code===0?resolve():reject(new Error('PNG stream ffmpeg failed (exit='+code+', signal='+(signal??'none')+'): '+(encoderStderr||'[stderr empty]'))))});
// An early encoder failure must be observed even before the final await.
encoderDone.catch(error=>{encoderError=error});
process.once('exit',()=>{if(pngEncoder.exitCode===null)pngEncoder.kill('SIGTERM')});
async function throwEncoderFailure(writeError){
  // EPIPE can arrive before ffmpeg's stderr and close event. Wait for close so
  // the originating encoder error, exit code and signal survive in build.log.
  let terminalError=null;
  try{await encoderDone;}catch(error){terminalError=error;}
  const details=[writeError?.message,terminalError?.message].filter(Boolean);
  if(encoderStderr&&!details.some(message=>message.includes(encoderStderr)))details.push(encoderStderr);
  throw new Error([...new Set(details)].join('\\n'));
}
async function imageEntry(buffer,count){
  if(!Buffer.isBuffer(buffer)||!Number.isSafeInteger(count)||count<1)throw new Error('Invalid lossless PNG entry');
  for(let frame=0;frame<count;frame++){
    if(encoderError)await throwEncoderFailure(encoderError);
    try{
      await new Promise((resolve,reject)=>pngEncoder.stdin.write(buffer,error=>error?reject(error):resolve()));
    }catch(error){await throwEncoderFailure(error);}
  }
  visualEntries.push({frames:count});
}
async function finishPngEncoder(){
  pngEncoder.stdin.end();
  try{await encoderDone;}catch(error){await throwEncoderFailure(error);}
  if(encoderError)await throwEncoderFailure(encoderError);
}
async function still(scene,subtitleLines,count,start){
  const buffer=await renderLessonRaster(argsFor(scene,{subtitleLines}));
  if(!posterWritten&&scene.phase==='body'&&!scene.chapterTitle){await fs.writeFile(path.join(out,'lesson-poster.png'),buffer);posterWritten=true;}
  await imageEntry(buffer,count);
  if(scene.speaker)cueList.push({start,end:start+count/fps,speaker:scene.speaker,lines:subtitleLines,turnId:scene.id});
}
${chunksFor}
for(const scene of scenes){
  if(scene.phase==='logo'){
    const approved=path.join(here,'assets/brand/egakumi-logo-intro.mp4');
    const dir=path.join(work,'logo');await fs.mkdir(dir,{recursive:true});
    if(await fs.access(approved).then(()=>true,()=>false)){
      run('ffmpeg',['-v','error','-y','-i',approved,'-vf','fps=24','-frames:v',String(scene.frameCount),path.join(dir,'%04d.png')]);
      for(let frame=1;frame<=scene.frameCount;frame++){
        const file=path.join(dir,String(frame).padStart(4,'0')+'.png');
        await imageEntry(await fs.readFile(file),1);await fs.rm(file);
      }
    }else{
      for(let frame=0;frame<scene.frameCount;frame++)await imageEntry(await renderLessonRaster(argsFor(scene,{sceneTime:frame/fps,time:frame/fps})),1);
    }
  }else if(scene.phase==='opening'||scene.phase==='closing'){
    const chunks=scene.speaker?chunksFor(scene):[{lines:[],frames:scene.frameCount,start:scene.start}];
    let frameBase=0;
    for(const chunk of chunks){
      if(scene.speaker)cueList.push({start:chunk.start,end:chunk.start+chunk.frames/fps,speaker:scene.speaker,lines:chunk.lines,turnId:scene.id});
      const step=scene.phase==='opening'?1:2;
      for(let local=0;local<chunk.frames;local+=step){
        const frame=frameBase+local,count=Math.min(step,chunk.frames-local);
        const buffer=await renderLessonRaster(argsFor(scene,{time:scene.start+frame/fps,sceneTime:frame/fps,turnTime:frame/fps,subtitleLines:chunk.lines}));
        await imageEntry(buffer,count);
      }
      frameBase+=chunk.frames;
    }
    if(scene.phase==='opening')cameraTimeline.push({id:scene.id,start:scene.start,end:scene.end,speaker:scene.speaker,previousSpeaker:scene.previousSpeaker,from:getHologramCamera(argsFor(scene,{turnTime:0})),to:getHologramCamera(argsFor(scene,{turnTime:2}))});
  }else if(scene.speaker){for(const chunk of chunksFor(scene))await still(scene,chunk.lines,chunk.frames,chunk.start);}
  else await still(scene,[],scene.frameCount,scene.start);
  if(scene.phase!=='body'||scene.chapterTitle)console.log('Streamed '+scene.id);
}
`;
code=code.slice(0,visualStart)+streamVisual+code.slice(visualEnd);
const concatStart=code.indexOf('const visualConcat=visualEntries.map');
const concatEnd=code.indexOf('const stamp=seconds=>',concatStart);
if(concatStart<0||concatEnd<0)throw new Error('Cannot locate approved visual concat block');
code=code.slice(0,concatStart)+code.slice(concatEnd);
const encodeStart=code.indexOf("run('ffmpeg',['-v','error','-y','-f','concat','-safe','0','-i',path.join(work,'visual-concat.txt')");
const encodeEnd=code.indexOf('\n// An independent common closing',encodeStart);
if(encodeStart<0||encodeEnd<0)throw new Error('Cannot locate approved final encoder');
code=code.slice(0,encodeStart)+'await finishPngEncoder();'+code.slice(encodeEnd);
if(process.argv.includes('--check-only')){
  const result=spawnSync(process.execPath,['--input-type=module','--check'],{input:code,encoding:'utf8'});
  if(result.status!==0)throw new Error(result.stderr);
  console.log(JSON.stringify({status:'syntax-checked',sourceSha256,source:shared,transport:'lossless PNG image2pipe',episode:'16',networkRequestsMade:false}));
}else{
  console.log('Episode 16 lossless PNG transport; approved builder SHA-256 '+sourceSha256);
  try {
    await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
  } catch(error) {
    // Avoid dumping the entire data URL module in a stack trace.
    console.error(error.message);
    process.exitCode=1;
  }
}
