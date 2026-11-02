// Assemble a complete study episode from verified egakumi Speech recordings.
// This local renderer never handles credentials or makes synthesis requests.
import fs from 'node:fs/promises';
import fsSync from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {spawnSync} from 'node:child_process';
import {renderLessonRaster,getHologramCamera} from './lesson-visual.mjs';
import {audioSourceSignature,saveAudioCache,checkAudioCache} from './lesson-audio-cache.mjs';

const here=path.dirname(fileURLToPath(import.meta.url));
const require=createRequire(import.meta.url);
const sharp=require('sharp');sharp.concurrency(2);
const options=Object.fromEntries(Array.from({length:Math.floor((process.argv.length-2)/2)},(_,i)=>[process.argv[2+i*2],process.argv[3+i*2]]));
const scriptName=options['--script']??'lesson-01-script.json';
const outputName=options['--output']??'lesson-01';
const configName=options['--generation-config']??(outputName==='lesson-01'?'lesson-01-generation.json':'lesson-01-gemini-generation.json');
const castName=options['--cast']??(outputName==='lesson-01'?'podcast-cast.json':'podcast-cast-gemini.json');
const inputPath=name=>{const resolved=path.resolve(here,name);if(path.isAbsolute(name)||!resolved.startsWith(here+path.sep))throw new Error('Input paths must stay inside video-sample');return resolved;};
for(const name of [scriptName,configName,castName])inputPath(name);
if(!/^[a-z0-9][a-z0-9-]*$/.test(outputName))throw new Error('Use a simple unique output directory');
const out=path.join(here,'output',outputName),work=path.join(out,'render-work');
const fps=24,width=1600,height=900;
const read=async file=>JSON.parse(await fs.readFile(file,'utf8'));
const hash=buffer=>crypto.createHash('sha256').update(buffer).digest('hex');
const run=(cmd,args)=>{const r=spawnSync(cmd,args,{encoding:'utf8',maxBuffer:12*1024*1024});if(r.error)throw r.error;if(r.status!==0)throw new Error(`${cmd}: ${r.stderr}`);return r.stdout.trim();};
const probe=file=>Number(run('ffprobe',['-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',file]));
const script=await read(inputPath(scriptName));
const episodeNumber=script.episodeNumber??1,episodeKey=String(episodeNumber).toLowerCase().padStart(2,'0');
if(!/^(?:[0-9]{2}|a[1-4]|b[1-3])$/.test(episodeKey))throw new Error('Invalid episode number');
const episodeLabel=script.episodeLabel??`第${episodeNumber}回`;
await fs.mkdir(out,{recursive:true});
const buildLock=path.join(out,'.lesson-build.lock'),generationLock=path.join(out,'.gemini-generator.lock');
if(fsSync.existsSync(generationLock))throw new Error('Audio generation is active for this episode');
const buildDescriptor=fsSync.openSync(buildLock,'wx',0o600);fsSync.writeSync(buildDescriptor,String(process.pid));fsSync.closeSync(buildDescriptor);
process.once('exit',()=>fsSync.rmSync(buildLock,{force:true}));
process.once('SIGINT',()=>process.exit(130));process.once('SIGTERM',()=>process.exit(143));
if(fsSync.existsSync(generationLock))throw new Error('Audio generation started while acquiring the build lock');
const lessonTitle=script.topic??script.title.replace(/^第\d+回\s*/, '');
const omitted=new Set(script.firstCut?.omitTurnIds??[]);
const activeTurns=script.turns.filter(turn=>!omitted.has(turn.id));
const cast=await read(inputPath(castName));
const preparedVisuals=new Map();
for(const turn of activeTurns){
  if(!/^[a-z0-9][a-z0-9-]*$/.test(turn.id))throw new Error('Turn IDs must be simple unique local names');
  const visual={...(turn.visual??{})};
  if(episodeKey!=='01'&&!visual.layout)throw new Error(`Explicit topic visual.layout required: ${turn.id}`);
  if(visual.layout==='svg'){
    if(typeof visual.svg!=='string'||path.isAbsolute(visual.svg))throw new Error('Use an episode-local SVG path');
    const episodeDir=path.dirname(inputPath(scriptName)),svgFile=path.resolve(episodeDir,visual.svg);
    if(!svgFile.startsWith(episodeDir+path.sep))throw new Error('Custom SVG must stay inside the episode directory');
    if(!(await fs.realpath(svgFile)).startsWith((await fs.realpath(episodeDir))+path.sep))throw new Error('SVG symlink escapes the episode directory');
    const svgBytes=await fs.readFile(svgFile),svg=svgBytes.toString('utf8');
    if(!/<svg\b/i.test(svg)||/<!DOCTYPE|<!ENTITY|<script\b|<foreignObject\b|\bon[a-z]+\s*=|(?:href|src)\s*=\s*["'](?!#|data:)|@import/i.test(svg)||[...svg.matchAll(/url\(([^)]+)\)/gi)].some(m=>!m[1].trim().replace(/^["']|["']$/g,'').startsWith('#')))throw new Error('Use a self-contained, script-free SVG without external resources');
    visual.svgSha256=hash(svgBytes);visual.svgDataUri='data:image/svg+xml;base64,'+svgBytes.toString('base64');
  }
  preparedVisuals.set(turn.id,visual);
}
if(options['--preview']==='true'){
  const previewDir=path.join(out,'previews');await fs.mkdir(previewDir,{recursive:true});
  const previews=[];
  const wide=path.join(previewDir,'cast-establish.png');await fs.writeFile(wide,await renderLessonRaster({phase:'opening',episodeNumber,episodeLabel,title:lessonTitle,subtitleLines:[],turnTime:0,time:4}));
  for(const turn of activeTurns){const file=path.join(previewDir,turn.id+'.png');await fs.writeFile(file,await renderLessonRaster({phase:turn.chapterId==='opening'?'opening':'body',episodeNumber,episodeLabel,seriesLabel:script.seriesLabel,title:lessonTitle,chapter:script.chapters.find(c=>c.id===turn.chapterId),speaker:turn.speaker,visual:preparedVisuals.get(turn.id),subtitleLines:turn.subtitleChunks?.[0]?.lines??turn.subtitleLines??[turn.caption],turnTime:2,time:6}));previews.push(path.relative(out,file));}
  await fs.writeFile(path.join(previewDir,'manifest.json'),JSON.stringify({script:scriptName,episodeNumber,previews,networkRequestsMade:false},null,2)+'\n');
  console.log(JSON.stringify({previewDirectory:previewDir,frames:previews.length+1,networkRequestsMade:false}));process.exit(0);
}
if(!['ready-for-generation','first-cut-produced'].includes(script.status))throw new Error('Author and review the episode, then set status=ready-for-generation before a full build');
const config=await read(inputPath(configName));
if(config.output&&config.output!==outputName)throw new Error('Output directory differs from the episode generation config');
const generation=await read(path.join(out,'generation.json'));
const isGemini=generation.provider==='google-cloud'&&generation.modelId==='gemini-3.8-flash-tts';
const pronunciationEnhancement=isGemini?null:true;
const cleanSpeech=text=>text.replace(/\[(?:breathing|laughter|sighing)\]/g,' ').replace(/\s+/g,' ').trim();
const candidates=new Map((config.candidates??config.variants??[]).map(c=>[c.id,c]));
const music=await read(path.join(here,'assets/music/manifest.json'));
const audioSignature=audioSourceSignature(script,generation,music);
const reuseAudio=options['--reuse-audio']==='true'&&await checkAudioCache(out,audioSignature);
const endpoint=isGemini?`https://aiplatform.googleapis.com/v1/projects/${generation.projectId}/locations/global/publishers/google/models/gemini-3.8-flash-tts:generateContent`:'https://gateway.example.com/models/azure-speech/v1/synthesize';
if(generation.endpoint!==endpoint||generation.generationState!=='complete'||generation.credentialsPersisted!==false)throw new Error('Expected complete recordings with verified synthesis provenance');
if(isGemini&&(generation.projectId!=='egakumi-example'||generation.apiFamily!=='agent-platform-generate-content'))throw new Error('Unexpected Gemini project or API');
if(isGemini&&generation.configurationSha256&&generation.configurationSha256!==hash(await fs.readFile(inputPath(configName))))throw new Error('Gemini generation config changed');
if(isGemini&&generation.sourceScript&&generation.sourceScript!==scriptName)throw new Error('Generated audio belongs to a different source script');
if(isGemini&&config.sourceScriptSha256!==hash(await fs.readFile(path.join(here,scriptName))))throw new Error('Gemini source script changed');
if(script.speed!==1||Object.values(cast.members).some(p=>p.speed!==1)||(isGemini?cast.speech.modelId!=='gemini-3.8-flash-tts':cast.speech.enhancePronunciation!==true))throw new Error('Expected selected normal-speed cast');
const expectedRecordedTurns=isGemini?activeTurns:script.turns;
if(generation.results.length!==expectedRecordedTurns.length||new Set(generation.results.map(r=>r.id)).size!==expectedRecordedTurns.length||generation.results.some(r=>!expectedRecordedTurns.some(t=>t.id===r.id)))throw new Error('Recording count or selection mismatch');
for(const a of music.assets)if(hash(await fs.readFile(path.join(here,'assets/music',a.path)))!==a.derivative.sha256)throw new Error(`Changed music: ${a.path}`);
await fs.mkdir(work,{recursive:true});
const scenes=[];
let cursor=0;
function add(scene,seconds){const count=Math.ceil(seconds*fps);const value={...scene,start:cursor/fps,end:(cursor+count)/fps,duration:count/fps,frameCount:count};scenes.push(value);cursor+=count;return value;}
function silence(scene){if(!reuseAudio)run('ffmpeg',['-v','error','-y','-f','lavfi','-i','anullsrc=r=48000:cl=mono','-t',String(scene.duration),'-c:a','pcm_s16le',path.join(work,scene.id+'.wav')]);}
const logo=add({id:'logo',phase:'logo',speaker:null,subtitleLines:[]},script.logoIntroSeconds??4);silence(logo);
const establish=add({id:'cast-establish',phase:'opening',speaker:null,previousSpeaker:null,subtitleLines:[]},1.1);silence(establish);
let previousSpeaker=null,previousChapter=null;
const chapterById=Object.fromEntries(script.chapters.map((c,i)=>[c.id,{...c,index:i+1,count:script.chapters.length}]));
for(const [i,turn]of activeTurns.entries()){
  const chapter=chapterById[turn.chapterId];if(!chapter)throw new Error(`Unknown chapter ${turn.chapterId}`);
  const phase=turn.chapterId==='opening'?'opening':'body';
  if(phase==='body'&&previousChapter!==turn.chapterId){const titleScene=add({id:`chapter-${turn.chapterId}`,phase:'body',speaker:null,chapterId:turn.chapterId,chapter,visual:{...preparedVisuals.get(turn.id),heading:chapter.title},subtitleLines:[],chapterTitle:true},1.25);silence(titleScene);}
  const recording=generation.results.find(r=>r.id===turn.id),person=cast.members[turn.speaker];
  const expectedSpeech=isGemini?cleanSpeech(turn.speech):turn.speech,candidate=candidates.get(turn.id);
  if(!recording||recording.status!=='succeeded'||recording.httpStatus!==200||recording.speed!==1||recording.voice!==person.voiceId||recording.speech!==expectedSpeech||(isGemini?(!candidate||candidate.speech!==expectedSpeech||candidate.voice!==person.voiceId||candidate.parameters?.style!==recording.parameters?.style||!recording.parameters?.style):recording.parameters?.enhancePronunciation!==true))throw new Error(`Invalid recording ${turn.id}`);
  if(isGemini){
    if(recording.sourceSpeech!==turn.speech||candidate.sourceSpeech!==turn.speech||recording.sourceSpeechSha256!==hash(Buffer.from(turn.speech)))throw new Error(`Gemini source text mismatch ${turn.id}`);
    if(!recording.requestFile||path.isAbsolute(recording.requestFile)||recording.requestFile.split(/[\\/]/).includes('..'))throw new Error(`Invalid Gemini request path ${turn.id}`);
    const requestFile=path.join(out,recording.requestFile);
    const request=JSON.parse(await fs.readFile(requestFile,'utf8'));
    if(request.contents?.[0]?.parts?.[0]?.text!==expectedSpeech||request.contents[0].parts[0].speechMetadata?.style!==recording.parameters.style||request.generationConfig?.speechConfig?.voiceConfig?.voice!==person.voiceId||request.generationConfig.responseModalities?.join(',')!=='AUDIO')throw new Error(`Gemini request mismatch ${turn.id}`);
    if(recording.requestSha256&&hash(await fs.readFile(requestFile))!==recording.requestSha256)throw new Error(`Changed Gemini request ${turn.id}`);
  }
  const raw=path.join(out,turn.id+'-raw.wav');
  if(hash(await fs.readFile(raw))!==recording.rawAudioSha256)throw new Error(`Changed raw audio ${turn.id}`);
  const rawMeta=JSON.parse(run('ffprobe',['-v','error','-show_streams','-of','json',raw])).streams.find(s=>s.codec_type==='audio');
  const sourceRate=Number(rawMeta.sample_rate),sourceSamples=Math.round(Number(rawMeta.duration_ts)*Number(rawMeta.time_base.split('/')[0])/Number(rawMeta.time_base.split('/')[1])*sourceRate);
  if(rawMeta.codec_name!=='pcm_s16le'||Number(rawMeta.channels)!==1||sourceRate!==(isGemini?24000:16000)||!Number.isSafeInteger(sourceSamples)||sourceSamples<=0)throw new Error(`Invalid raw PCM ${turn.id}`);
  const audioDuration=sourceSamples/sourceRate,voiceDelay=phase==='opening'?.12:.06,gapAfter=(turn.gapAfterSeconds??.10)+(turn.pauseAfterSeconds??0);
  const normalizedSpeechSamples=sourceSamples*48000/sourceRate;
  if(!Number.isFinite(audioDuration)||audioDuration<=0)throw new Error('Invalid speech duration');
  const scene=add({...turn,visual:preparedVisuals.get(turn.id),sourceSpeech:turn.speech,speech:expectedSpeech,phase,chapter,voice:person.voiceId,speed:1,enhancePronunciation:pronunciationEnhancement,audioDuration,sourceSampleRate:sourceRate,sourceSamples,normalizedSpeechSamples,voiceDelay,gapAfter,rawAudioSha256:recording.rawAudioSha256,previousSpeaker:phase==='opening'?previousSpeaker:null},audioDuration+voiceDelay+gapAfter);
  const sceneSamples=Math.round(scene.duration*48000);
  if(!reuseAudio)run('ffmpeg',['-v','error','-y','-i',raw,'-af',`loudnorm=I=-16:TP=-2:LRA=7,aresample=48000,asetpts=N/SR/TB,apad=whole_len=${normalizedSpeechSamples},atrim=end_sample=${normalizedSpeechSamples},adelay=${Math.round(voiceDelay*1000)}:all=1,apad=whole_len=${sceneSamples},atrim=end_sample=${sceneSamples}`,'-ar','48000','-c:a','pcm_s16le',path.join(work,turn.id+'.wav')]);
  previousSpeaker=turn.speaker;previousChapter=turn.chapterId;
  if((i+1)%15===0)console.log(`Prepared narration ${i+1}/${activeTurns.length}`);
}
const closing=add({id:'closing',phase:'closing',speaker:null,subtitleLines:[]},script.logoClosingSeconds??script.closingSeconds??8);silence(closing);
const duration=cursor/fps,sampleCount=Math.round(duration*48000);
const endingSource=music.mixPolicy.closing.brandSourceSeconds[0];
if(!reuseAudio){
await fs.writeFile(path.join(work,'audio-concat.txt'),scenes.map(s=>`file '${s.id}.wav'`).join('\n')+'\n');
run('ffmpeg',['-v','error','-y','-f','concat','-safe','0','-i',path.join(work,'audio-concat.txt'),'-af',`loudnorm=I=-16:TP=-2:LRA=7,aresample=48000,asetpts=N/SR/TB,apad=whole_len=${sampleCount},atrim=end_sample=${sampleCount}`,'-ar','48000','-c:a','pcm_s16le',path.join(out,'lesson-narration.wav')]);
console.log(`Narration complete: ${(duration/60).toFixed(2)} minutes`);

const brand=path.join(here,'assets/music/brand-theme.mp3'),bed=path.join(here,'assets/music/main-bgm.mp3');
const bedStart=logo.duration-.6,bedDuration=closing.start-bedStart,bedLength=194.8;
const loopCount=Math.max(2,Math.ceil((bedDuration-2)/(bedLength-2)));
const filters=[
  '[0:a]aresample=48000,aformat=channel_layouts=stereo,asplit=2[voice][key]',
  `[1:a]asplit=2[intro0][outro0]`,
  `[intro0]atrim=duration=${logo.duration+1.25},asetpts=PTS-STARTPTS,afade=t=in:d=0.22,afade=t=out:st=${logo.duration-.75}:d=2,volume=0.62,apad=whole_len=${sampleCount},atrim=end_sample=${sampleCount}[intro]`,
  `[outro0]atrim=start=${endingSource}:duration=${closing.duration},asetpts=PTS-STARTPTS,afade=t=in:d=1,afade=t=out:st=6:d=2,volume=0.62,adelay=${Math.round(closing.start*1000)}:all=1,apad=whole_len=${sampleCount},atrim=end_sample=${sampleCount}[outro]`,
  `[2:a]atrim=duration=${bedLength},asetpts=PTS-STARTPTS,asplit=${loopCount}`+Array.from({length:loopCount},(_,i)=>`[loop${i}]`).join('')
];
let last='loop0';
for(let i=1;i<loopCount;i++){const next='xf'+i;filters.push(`[${last}][loop${i}]acrossfade=d=2:c1=tri:c2=tri[${next}]`);last=next;}
filters.push(`[${last}]atrim=duration=${bedDuration},asetpts=PTS-STARTPTS,afade=t=in:d=1.2,afade=t=out:st=${bedDuration-1}:d=1,volume=0.15,adelay=${Math.round(bedStart*1000)}:all=1,apad=whole_len=${sampleCount},atrim=end_sample=${sampleCount}[bed]`);
filters.push('[intro][bed][outro]amix=inputs=3:normalize=0[music]');
filters.push('[music][key]sidechaincompress=threshold=0.055:ratio=4:attack=20:release=400:makeup=1[ducked]');
filters.push(`[voice][ducked]amix=inputs=2:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=8,aresample=48000,asetpts=N/SR/TB,apad=whole_len=${sampleCount},atrim=end_sample=${sampleCount}[mixed]`);
run('ffmpeg',['-v','error','-y','-i',path.join(out,'lesson-narration.wav'),'-i',brand,'-i',bed,'-filter_complex_threads','1','-filter_complex',filters.join(';'),'-map','[mixed]','-ar','48000','-ac','2','-c:a','pcm_s16le',path.join(out,'lesson-mix.wav')]);
console.log('Music loop and closing mix complete');
await saveAudioCache(out,audioSignature);
}else{
  for(const file of ['lesson-narration.wav','lesson-mix.wav'])if(Math.abs(probe(path.join(out,file))-duration)>1/48000)throw new Error('Cached audio duration differs from timeline');
  console.log(`Using verified saved audio: ${(duration/60).toFixed(2)} minutes`);
}

const cueList=[],visualEntries=[],cameraTimeline=[];
let fileIndex=0,posterWritten=false;
const argsFor=(scene,more={})=>({time:scene.start,duration,phase:scene.phase,speaker:scene.speaker,previousSpeaker:scene.previousSpeaker,turnTime:0,sceneTime:0,title:lessonTitle,episodeNumber,episodeLabel,seriesLabel:script.seriesLabel,chapter:scene.chapter,visual:scene.visual??{},subtitleLines:scene.subtitleLines??[],logoDuration:logo.duration,closingDuration:closing.duration,...more});
const imageEntry=(file,count)=>{visualEntries.push({file,frames:count});};
async function still(scene,subtitleLines,count,start){
  const file=path.join(work,`still-${String(fileIndex++).padStart(4,'0')}.png`);
  await fs.writeFile(file,await renderLessonRaster(argsFor(scene,{subtitleLines})));
  imageEntry(file,count);
  if(scene.speaker)cueList.push({start,end:start+count/fps,speaker:scene.speaker,lines:subtitleLines,turnId:scene.id});
  if(!posterWritten&&scene.phase==='body'&&!scene.chapterTitle){await fs.copyFile(file,path.join(out,'lesson-poster.png'));posterWritten=true;}
}
function chunksFor(scene){
  const chunks=scene.subtitleChunks?.length?scene.subtitleChunks:[{caption:scene.caption,lines:scene.subtitleLines,weight:1}];
  for(const c of chunks)if(!Array.isArray(c.lines)||c.lines.length>2||!c.lines.length)throw new Error(`Invalid captions ${scene.id}`);
  const weight=chunks.reduce((n,c)=>n+(c.weight??[...c.caption].length),0);
  let used=0;
  return chunks.map((c,i)=>{const count=i===chunks.length-1?scene.frameCount-used:Math.max(1,Math.floor(scene.frameCount*(c.weight??[...c.caption].length)/weight));const result={...c,frames:count,start:scene.start+used/fps};used+=count;return result;});
}
for(const scene of scenes){
  if(scene.phase==='logo'){
    const approved=path.join(here,'assets/brand/egakumi-logo-intro.mp4');
    const dir=path.join(work,'logo');await fs.mkdir(dir,{recursive:true});
    // The approved common sting is reused without re-rendering its artwork.
    if(await fs.access(approved).then(()=>true,()=>false)){
      run('ffmpeg',['-v','error','-y','-i',approved,'-vf','fps=24','-frames:v',String(scene.frameCount),path.join(dir,'%04d.png')]);
      for(let f=1;f<=scene.frameCount;f++)imageEntry(path.join(dir,`${String(f).padStart(4,'0')}.png`),1);
    }else{
      for(let f=0;f<scene.frameCount;f++){const file=path.join(dir,`${String(f+1).padStart(4,'0')}.png`);await fs.writeFile(file,await renderLessonRaster(argsFor(scene,{sceneTime:f/fps,time:f/fps})));imageEntry(file,1);}
    }
  }else if(scene.phase==='opening'||scene.phase==='closing'){
    const chunks=scene.speaker?chunksFor(scene):[{lines:[],frames:scene.frameCount,start:scene.start}];
    let f=0;
    for(const chunk of chunks){
      if(scene.speaker)cueList.push({start:chunk.start,end:chunk.start+chunk.frames/fps,speaker:scene.speaker,lines:chunk.lines,turnId:scene.id});
      const pending=[];
      // Camera pans use all 24 frames per second. The slow closing fade uses
      // twelve animation samples, each held for two output frames.
      const step=scene.phase==='opening'?1:2;
      for(let local=0;local<chunk.frames;local+=step){
        const frame=f+local,count=Math.min(step,chunk.frames-local),file=path.join(work,`motion-${scene.id}-${String(frame).padStart(4,'0')}.png`);
        const args=argsFor(scene,{time:scene.start+frame/fps,sceneTime:frame/fps,turnTime:frame/fps,subtitleLines:chunk.lines});
        pending.push(renderLessonRaster(args).then(buffer=>fs.writeFile(file,buffer)));
        imageEntry(file,count);
        if(pending.length===3){await Promise.all(pending);pending.length=0;}
      }
      await Promise.all(pending);f+=chunk.frames;
    }
    if(scene.phase==='opening')cameraTimeline.push({id:scene.id,start:scene.start,end:scene.end,speaker:scene.speaker,previousSpeaker:scene.previousSpeaker,from:getHologramCamera(argsFor(scene,{turnTime:0})),to:getHologramCamera(argsFor(scene,{turnTime:2}))});
  }else if(scene.speaker){for(const chunk of chunksFor(scene))await still(scene,chunk.lines,chunk.frames,chunk.start);}
  else await still(scene,[],scene.frameCount,scene.start);
  if(scene.phase!=='body'||scene.chapterTitle)console.log(`Rendered ${scene.id}`);
}
const renderedFrames=visualEntries.reduce((n,e)=>n+e.frames,0);
if(renderedFrames!==cursor)throw new Error('Image timeline differs from audio timeline');
const visualConcat=visualEntries.map(e=>`file '${e.file}'\noption framerate 24\nduration ${(e.frames/fps).toFixed(9)}`).join('\n')+`\nfile '${visualEntries.at(-1).file}'\noption framerate 24\n`;
await fs.writeFile(path.join(work,'visual-concat.txt'),visualConcat);
const stamp=seconds=>{const ms=Math.round(seconds*1000);return `${String(Math.floor(ms/3600000)).padStart(2,'0')}:${String(Math.floor(ms/60000)%60).padStart(2,'0')}:${String(Math.floor(ms/1000)%60).padStart(2,'0')}.${String(ms%1000).padStart(3,'0')}`;};
await fs.writeFile(path.join(out,'lesson-captions.vtt'),'WEBVTT\n\n'+cueList.map((c,i)=>`${i+1}\n${stamp(c.start)} --> ${stamp(c.end)}\n${cast.members[c.speaker].name}: ${c.lines.join('\n')}\n`).join('\n'));
const chapters=script.chapters.map(c=>({...c,start:scenes.find(s=>s.chapterId===c.id)?.start??logo.end}));
const timeline={title:lessonTitle,description:script.description??script.learningObjectives?.[0]??lessonTitle,episodeNumber,episodeKey,episodeLabel,episodeId:script.episodeId??episodeKey,script:scriptName,duration,fps,width,height,frameCount:cursor,scenes,subtitleCues:cueList,chapters,cameraTimeline,cast:cast.members,castFile:castName,generationConfig:configName,speed:1,enhancePronunciation:pronunciationEnhancement,synthesisProvider:isGemini?'google-cloud':'egakumi-gateway',synthesisModel:isGemini?'gemini-3.8-flash-tts':'DragonHDLatestNeural',pronunciationPolicy:isGemini?'Japanese natural-language style directions and kana speaker names':'Azure enhancePronunciation=true',synthesisEndpoint:endpoint,sourceTurnCount:script.turns.length,editedTurnCount:activeTurns.length,omittedTurnIds:[...omitted],music:{brand:'brand-theme.mp3',body:'main-bgm.mp3',bodyCrossfadeSeconds:2,closingBrandSourceSeconds:[endingSource,endingSource+closing.duration],closingDuration:closing.duration,ducking:true,sourceManifest:music},sourceBasis:'Current repository study notes and public configuration examples; not a live Azure inspection.',createdAt:new Date().toISOString()};
await fs.writeFile(path.join(out,'lesson-timeline.json'),JSON.stringify(timeline,null,2)+'\n');
await fs.writeFile(path.join(out,'lesson-transcript.md'),`# ${episodeLabel} ${lessonTitle}\n\n`+script.chapters.map(c=>`## ${c.title}\n\n`+activeTurns.filter(t=>t.chapterId===c.id).map(t=>`**${cast.members[t.speaker].name}**：${t.caption}`).join('\n\n')).join('\n\n')+'\n');
const file=episodeKey==='01'?`egakumi-study-01-network-overview.mp4`:`egakumi-study-${episodeKey}.mp4`;
console.log(`Encoding ${(duration/60).toFixed(2)} minutes / ${cursor} frames`);
run('ffmpeg',['-v','error','-y','-f','concat','-safe','0','-i',path.join(work,'visual-concat.txt'),'-i',path.join(out,'lesson-mix.wav'),'-map','0:v','-map','1:a','-t',String(duration),'-vf','fps=24,scale=out_color_matrix=bt709','-fps_mode','cfr','-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p','-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-c:a','aac','-b:a','192k','-movflags','+faststart',path.join(out,file)]);
// An independent common closing contains only the official logo and brand tail.
run('ffmpeg',['-v','error','-y','-ss',String(closing.start),'-i',path.join(out,file),'-t',String(closing.duration),'-c:v','libx264','-preset','fast','-crf','18','-c:a','aac','-b:a','192k','-movflags','+faststart',path.join(out,'egakumi-common-closing.mp4')]);
const sha256=hash(await fs.readFile(path.join(out,file)));
const exported={file,sha256,duration,fps,width,height,frameCount:cursor,speechSpeedChanged:false,enhancePronunciation:pronunciationEnhancement,synthesisProvider:timeline.synthesisProvider,synthesisModel:timeline.synthesisModel,commonClosing:'egakumi-common-closing.mp4',commonClosingDuration:closing.duration};
await fs.writeFile(path.join(out,'lesson-export.json'),JSON.stringify(exported,null,2)+'\n');
await fs.writeFile(path.join(out,'index.html'),player(timeline,exported));
if(options['--write-script']==='true'){script.status='first-cut-produced';script.output=`output/${outputName}/${file}`;script.actualDurationSeconds=duration;await fs.writeFile(path.join(here,scriptName),JSON.stringify(script,null,2)+'\n');}
await fs.rm(work,{recursive:true,force:true});
console.log(JSON.stringify({file:path.join(out,file),duration,minutes:duration/60,frames:cursor,sha256}));

function player(t,e){
  const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
  const time=s=>`${String(Math.floor(s/60)).padStart(2,'0')}:${String(Math.floor(s%60)).padStart(2,'0')}`;
  return `<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(t.episodeLabel)}｜${esc(t.title)}</title><style>*{box-sizing:border-box}body{margin:0;background:#030710;color:#e5f7fc;font:16px/1.8 -apple-system,BlinkMacSystemFont,"Hiragino Kaku Gothic ProN",sans-serif}main{max-width:1280px;margin:32px auto;padding:0 24px 48px}.eyebrow{font-size:13px;color:#84dcd8;letter-spacing:.15em}h1{font-size:31px;line-height:1.4}p{color:#98b7c7}video{display:block;width:100%;aspect-ratio:16/9;background:#030710;border:1px solid #285168;border-radius:10px;margin:22px 0}nav{display:flex;gap:18px;flex-wrap:wrap}a{color:#91e6e0;text-underline-offset:4px}.primary{display:inline-block;background:#91e6e0;color:#061620;padding:9px 18px;border-radius:6px;text-decoration:none}.chapters{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:25px 0}button{padding:13px 15px;background:#0b2030;border:1px solid #2b5368;border-radius:7px;color:#d7f4f8;font:inherit;text-align:left;cursor:pointer}button span{display:block;font-size:13px;color:#84c5ce}details{padding:18px;background:#081725;border:1px solid #26495f;border-radius:8px;margin-top:24px}summary{cursor:pointer}audio{display:block;width:100%;margin:14px 0}.note{font-size:13px}small{color:#88acbd}@media(max-width:700px){main{padding:0 14px}h1{font-size:25px}.chapters{grid-template-columns:1fr 1fr}}</style><main><div class="eyebrow">egakumi / 基盤管理者向け勉強会 / ${esc(t.episodeLabel)}</div><h1>${esc(t.title)}</h1><p>${esc(t.description)}</p><video id="lesson-video" controls playsinline preload="metadata" poster="lesson-poster.png" src="${esc(e.file)}" aria-label="${esc(t.episodeLabel)} ${esc(t.title)}"><track kind="captions" src="lesson-captions.vtt" srclang="ja" label="日本語"></video><p>${time(t.duration)} / MAKO・MANABU・IKUMI / 通常速度・${t.synthesisProvider==='google-cloud'?'日本語の話し方指定':'発音強化ON'} / BGMあり</p><nav><a class="primary" download href="${esc(e.file)}">動画を保存</a><a download href="lesson-captions.vtt">字幕を保存</a><a href="lesson-transcript.md">台本を読む</a><a download href="egakumi-common-closing.mp4">共通クロージングを保存</a></nav><div class="chapters">${t.chapters.map(c=>`<button data-seek="${c.start}"><span>${time(c.start)}</span>${esc(c.title)}</button>`).join('')}<button data-seek="${t.scenes.at(-1).start}"><span>${time(t.scenes.at(-1).start)}</span>クロージング</button></div><details><summary>音声だけを聴く・資料と制作条件</summary><p>BGMを含む音声</p><audio controls preload="none" src="lesson-mix.wav"></audio><p>声のみ</p><audio controls preload="none" src="lesson-narration.wav"></audio><p>声は${Object.values(t.cast).map(p=>`${esc(p.name)}=${esc(p.voiceId)}`).join('、')}。${t.synthesisProvider==='google-cloud'?'Gemini 3.8 Flash TTSをGoogle CloudのAPIから直接生成しています。':'egakumiゲートウェイ経由で生成しています。'}</p><p>資料のリポジトリ定義と公開設定例に基づく説明です。稼働環境から取得・検証した値とは区別します。</p><p>本編は既存Podcastのmain-bgm、終了はbrand-themeの63.8〜71.8秒を使っています。</p></details></main><script>const video=document.querySelector('video');document.querySelectorAll('[data-seek]').forEach(b=>b.addEventListener('click',()=>{video.currentTime=Number(b.dataset.seek);video.play()}));document.addEventListener('play',e=>{if(['AUDIO','VIDEO'].includes(e.target.tagName))document.querySelectorAll('audio,video').forEach(m=>{if(m!==e.target)m.pause()})},true)</script></html>`;
}
