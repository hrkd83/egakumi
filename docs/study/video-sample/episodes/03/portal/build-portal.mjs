// Episode-local visual revision. Reuses the verified narration and music.
import fs from 'node:fs/promises';
import fsSync from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {spawn,spawnSync} from 'node:child_process';
import {once} from 'node:events';
import sharp from 'sharp';
import {renderPortalScene} from './portal-surface.mjs';

sharp.concurrency(2);
const HERE=path.dirname(fileURLToPath(import.meta.url));
const KIT=path.resolve(HERE,'../../..');
const BASE=path.join(KIT,'output/lesson-03-gemini');
const OUT=path.join(BASE,'portal');
const W=1600,H=900,FPS=24,UI={x:24,y:65,width:1552,height:659.6};
const read=async f=>JSON.parse(await fs.readFile(f,'utf8'));
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const n=v=>Number(v.toFixed(4));
const clamp=(v,a=0,b=1)=>Math.max(a,Math.min(b,v));
const smooth=v=>{v=clamp(v);return v*v*(3-2*v);};
const unit=v=>[...String(v)].reduce((a,c)=>a+(c.charCodeAt(0)<128?.56:1),0);
const run=(cmd,args)=>{const r=spawnSync(cmd,args,{encoding:'utf8',maxBuffer:8*1024*1024});if(r.error)throw r.error;if(r.status!==0)throw Error(r.stderr);return r.stdout.trim();};
const sceneDataBytes=await fs.readFile(path.join(HERE,'scene-data.json'));
const data=JSON.parse(sceneDataBytes.toString('utf8'));
const sceneDataSha256=hash(sceneDataBytes);
const surfaceRendererSha256=hash(await fs.readFile(path.join(HERE,'portal-surface.mjs')));
const compositorSha256=hash(await fs.readFile(fileURLToPath(import.meta.url)));
const timeline=await read(path.join(BASE,'lesson-timeline.json'));
const source=await read(path.join(BASE,'lesson-export.json'));
const sourceVideo=path.join(BASE,source.file);
if(hash(await fs.readFile(sourceVideo))!==source.sha256)throw Error('Verified source video changed');
if(timeline.fps!==FPS||timeline.width!==W||timeline.height!==H)throw Error('Unexpected source format');
await fs.mkdir(OUT,{recursive:true});
const lock=path.join(OUT,'.portal-build.lock');
for(const name of ['.lesson-build.lock','.gemini-generator.lock'])if(fsSync.existsSync(path.join(BASE,name)))throw Error('Original episode is active');
const fd=fsSync.openSync(lock,'wx',0o600);fsSync.writeSync(fd,String(process.pid));fsSync.closeSync(fd);
process.once('exit',()=>fsSync.rmSync(lock,{force:true}));
process.once('SIGINT',()=>process.exit(130));process.once('SIGTERM',()=>process.exit(143));

const png=async(f,w)=>'data:image/png;base64,'+(await sharp(path.join(KIT,f)).resize({width:w}).png().toBuffer()).toString('base64');
const [symbol,wordmark]=await Promise.all([png('assets/brand/egakumi-symbol.webp',64),png('assets/brand/egakumi-wordmark.webp',240)]);
const portraits=Object.fromEntries(await Promise.all(['mako','manabu','ikumi'].map(async id=>[id,await png(`assets/portraits/${id}.webp`,160)])));
const people={mako:{name:'MAKO',role:'Moderator',color:'#79e2e1'},manabu:{name:'MANABU',role:'Product Manager',color:'#88deaf'},ikumi:{name:'IKUMI',role:'Solution Architect',color:'#8ccaff'}};
const bodyScenes=timeline.scenes.filter(s=>s.phase==='body');
const bodyStart=Math.round(bodyScenes[0].start*FPS);
const bodyEnd=Math.round(timeline.scenes.at(-1).start*FPS);
const chapterFirst=new Map();
for(const s of bodyScenes)if(s.speaker&&!chapterFirst.has(s.chapterId))chapterFirst.set(s.chapterId,s.id);
const cuesByTurn=new Map();
for(const c of timeline.subtitleCues){if(!cuesByTurn.has(c.turnId))cuesByTurn.set(c.turnId,[]);cuesByTurn.get(c.turnId).push(c);}
const resolveSpec=s=>{
  const id=s.speaker?s.id:chapterFirst.get(s.chapterId);
  const spec=data.scenes[id];if(!spec)throw Error(`Missing Portal scene ${id}`);
  return s.chapterTitle?{...spec,focus:[],chapterTitle:true}:spec;
};
const rectUnion=rs=>{
  if(!rs?.length)return null;
  const x=Math.min(...rs.map(r=>r.x)),y=Math.min(...rs.map(r=>r.y));
  return {x,y,width:Math.max(...rs.map(r=>r.x+r.width))-x,height:Math.max(...rs.map(r=>r.y+r.height))-y};
};
const cameraFor=rs=>{
  const b=rectUnion(rs);if(!b)return {x:0,y:0,width:1600,height:680};
  const z=clamp(Math.min(1.6,1600/(b.width+90),680/(b.height+105)),1,1.6),width=1600/z,height=680/z;
  return {x:clamp(b.x+b.width/2-width/2,0,1600-width),y:clamp(b.y+b.height/2-height/2,0,680-height),width,height};
};
const interpolate=(a,b,p)=>Object.fromEntries(Object.keys(a).map(k=>[k,a[k]+(b[k]-a[k])*p]));
const sameCamera=(a,b)=>Object.keys(a).every(k=>Math.abs(a[k]-b[k])<.01);

async function surfaceFor(spec){
  const r=renderPortalScene(spec);
  if(!r.svg||r.width!==1600||r.height!==680)throw Error('Portal surface must be 1600x680');
  if(/<script\b|<foreignObject\b|<!ENTITY|<!DOCTYPE/i.test(r.svg))throw Error('Unsafe SVG');
  for(const b of r.focusRects??[])if(![b.x,b.y,b.width,b.height].every(Number.isFinite)||b.x<0||b.y<0||b.width<=0||b.height<=0||b.x+b.width>1600.1||b.y+b.height>680.1)throw Error(`Invalid focus rectangle ${b.id}`);
  return {...r,uri:'data:image/png;base64,'+(await sharp(Buffer.from(r.svg),{density:144}).png().toBuffer()).toString('base64')};
}
const image=(uri,x,y,w,h,extra='')=>`<image href="${uri}" x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="xMidYMid meet" ${extra}/>`;
function uiLayer(surface,cam,opacity=1){return `<svg x="${UI.x}" y="${UI.y}" width="${UI.width}" height="${UI.height}" viewBox="${n(cam.x)} ${n(cam.y)} ${n(cam.width)} ${n(cam.height)}" opacity="${n(opacity)}">${image(surface.uri,0,0,1600,680)}</svg>`;}
function frameSvg({scene,spec,surface,cam,previous,fade=1,elapsed=5,settled=true,cue,cursorFrom}){
  const person=people[scene.speaker];
  let svg=`<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900"><defs><style>text{font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,sans-serif}</style><clipPath id="ui"><rect x="24" y="65" width="1552" height="659.6" rx="10"/></clipPath><clipPath id="portrait"><rect x="36" y="764" width="92" height="107" rx="9"/></clipPath></defs><rect width="1600" height="900" fill="#0b1425"/>`;
  svg+=image(symbol,24,9,36,36)+image(wordmark,72,7,135,41);
  svg+=`<text x="235" y="34" font-size="20" font-weight="650" fill="#e8f0f8">第3回  NSGによる通信制御</text><text x="1570" y="33" text-anchor="end" font-size="15" fill="#bacbdc">学習用 再現画面  ·  リポジトリの設定定義に基づく</text><g clip-path="url(#ui)">`;
  if(previous&&fade<1)svg+=uiLayer(previous.surface,previous.cam,1);
  svg+=uiLayer(surface,cam,fade);
  if((surface.focusRects??[]).length&&!scene.chapterTitle){
    const z=1600/cam.width;
    svg+=`<svg x="${UI.x}" y="${UI.y}" width="${UI.width}" height="${UI.height}" viewBox="${n(cam.x)} ${n(cam.y)} ${n(cam.width)} ${n(cam.height)}">`;
    surface.focusRects.forEach((r,i)=>{
      const p=settled?1:smooth((elapsed-.2-i*.08)/.4),pad=.8/z,perimeter=2*(r.width+r.height+pad*4);
      svg+=`<rect x="${n(r.x-pad)}" y="${n(r.y-pad)}" width="${n(r.width+pad*2)}" height="${n(r.height+pad*2)}" rx="${n(3/z)}" fill="#ff3636" fill-opacity="${n(.025*p)}" stroke="#fff" stroke-width="${n(5.5/z)}" opacity="${n(.75*p*fade)}"/><rect x="${n(r.x-pad)}" y="${n(r.y-pad)}" width="${n(r.width+pad*2)}" height="${n(r.height+pad*2)}" rx="${n(3/z)}" fill="none" stroke="#ed2839" stroke-width="${n(3.5/z)}" stroke-dasharray="${n(perimeter*p)} ${n(perimeter)}" opacity="${n(fade)}"/>`;
    });
    const r=surface.focusRects[0],cp=settled?1:smooth(elapsed/.85),tx=r.x+Math.min(r.width-12,Math.max(25,r.width*.8)),ty=r.y+r.height*.6;
    const px=tx+((cursorFrom?.x??1380)-tx)*(1-cp),py=ty+((cursorFrom?.y??72)-ty)*(1-cp);
    svg+=`<g transform="translate(${n(px)} ${n(py)}) scale(${n(1/z)})" opacity="${n(fade)}"><circle cx="0" cy="0" r="18" fill="#ff5757" opacity=".13"/><path d="M0 0V24L6 18L11 29L16 26L11 16H21Z" fill="#17283b" stroke="#fff" stroke-width="2"/></g></svg>`;
  }
  if(scene.chapterTitle)svg+=`<rect x="254" y="126" width="1070" height="84" rx="10" fill="#0078d4" fill-opacity=".96"/><text x="285" y="179" fill="#fff" font-size="31" font-weight="650">${esc(scene.chapter.title)}</text>`;
  svg+='</g><rect x="24" y="65" width="1552" height="659.6" rx="10" fill="none" stroke="#3a4d64"/>';
  svg+=`<rect x="24" y="744" width="1552" height="139" rx="10" fill="#132238" stroke="#3b516a"/>`;
  if(person){
    svg+=`<g clip-path="url(#portrait)">${image(portraits[scene.speaker],24,755,115,127)}</g><path d="M148 744H455" stroke="${person.color}" stroke-width="3"/><text x="150" y="772" font-size="16" font-weight="650" fill="${person.color}">${person.name} / ${person.role}</text>`;
  }else svg+='<text x="150" y="774" font-size="18" fill="#8fb9d6">egakumi 基盤管理者向け勉強会</text>';
  const lines=cue?.lines??[],size=Math.min(31,1305/Math.max(1,...lines.map(unit)));
  lines.forEach((line,i)=>{const y=lines.length===1?834:815+i*40;svg+=`<text x="847" y="${y}" text-anchor="middle" font-size="${n(size)}" font-weight="600" fill="#f1f7fd">${esc(line)}</text>`;});
  if(!lines.length)svg+=`<text x="150" y="826" font-size="27" font-weight="600" fill="#edf4fb">${esc(scene.chapter?.title??'NSGの設定と通信の判定')}</text>`;
  return svg+'</svg>';
}
const frameBuffer=svg=>sharp(Buffer.from(svg)).removeAlpha().raw().toBuffer();

if(process.argv.includes('--preview')){
  const dir=path.join(OUT,'previews');await fs.mkdir(dir,{recursive:true});
  const requested=['009','020','036','048','051','059','077','096','100','103','105','109'];
  const files=[];
  for(const id of requested){
    const scene=bodyScenes.find(s=>s.speaker&&s.id.includes('-'+id+'-'));if(!scene)continue;
    const spec=resolveSpec(scene),surface=await surfaceFor(spec),target=cameraFor(surface.focusRects),cue=cuesByTurn.get(scene.id)?.[0];
    for(const [phase,cam]of [['wide',{x:0,y:0,width:1600,height:680}],['zoom',target]]){
      const f=path.join(dir,scene.id+'-'+phase+'.png');await sharp(Buffer.from(frameSvg({scene,spec,surface,cam,cue}))).png().toFile(f);files.push(path.relative(OUT,f));
    }
  }
  await fs.writeFile(path.join(dir,'manifest.json'),JSON.stringify({files,networkRequestsMade:false},null,2)+'\n');
  console.log(JSON.stringify({previews:files.length,directory:dir}));process.exit(0);
}

const work=path.join(OUT,'render-work');await fs.mkdir(work,{recursive:true});
const sampleTurn=process.argv.includes('--sample-turn')?process.argv[process.argv.indexOf('--sample-turn')+1]:null;
const renderScenes=sampleTurn?bodyScenes.filter(s=>s.speaker&&s.id.includes('-'+sampleTurn+'-')):bodyScenes;
if(!renderScenes.length)throw Error('Requested sample scene not found');
const expectedBodyFrames=renderScenes.reduce((sum,s)=>sum+s.frameCount,0);
const bodyFile=path.join(work,'portal-body.mp4');
const encoder=spawn('ffmpeg',['-hide_banner','-loglevel','warning','-y','-f','rawvideo','-pixel_format','rgb24','-video_size','1600x900','-framerate','24','-i','pipe:0','-an','-vf','scale=out_color_matrix=bt709','-c:v','libx264','-preset','fast','-crf','18','-threads','4','-pix_fmt','yuv420p','-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-video_track_timescale','12288',bodyFile],{stdio:['pipe','ignore','pipe']});
let encoderLog='';encoder.stderr.on('data',b=>{encoderLog=(encoderLog+b.toString()).slice(-24000);});
const encoderExit=new Promise((resolve,reject)=>{encoder.once('error',reject);encoder.once('exit',code=>code===0?resolve():reject(Error('Encoder failed: '+encoderLog)));});
// Attach rejection handling now, while frames are being produced.
encoderExit.catch(()=>{});
const push=async b=>{if(!encoder.stdin.write(b))await once(encoder.stdin,'drain');};
let previous=null,frames=0;
const motionLog=[];
for(const [i,scene]of renderScenes.entries()){
  const spec=resolveSpec(scene),surface=await surfaceFor(spec);
  const newView=previous?.surface.viewKey!==surface.viewKey;
  const start=newView?{x:0,y:0,width:1600,height:680}:previous?.cam??{x:0,y:0,width:1600,height:680};
  const sequence=scene.chapterTitle?[]:spec.focusSequence??[];
  const rawBeats=sequence.length?sequence:[{fraction:0,focus:spec.focus??[]}];
  if(rawBeats[0].fraction!==0)throw Error('Focus sequence must start at zero');
  const beats=rawBeats.map((b,j)=>{
    if(b.fraction<0||b.fraction>=1||(j&&b.fraction<=rawBeats[j-1].fraction))throw Error('Invalid focus sequence');
    const rects=sequence.length?surface.focusRects.filter(r=>b.focus.includes(r.id)):surface.focusRects;
    if(sequence.length&&!rects.length)throw Error('Unresolved focus sequence '+scene.id);
    return {startFrame:Math.round(scene.frameCount*b.fraction),focus:b.focus,rects,cameraTo:scene.chapterTitle?{x:0,y:0,width:1600,height:680}:cameraFor(rects)};
  });
  beats.forEach((b,j)=>{
    b.cameraFrom=j?beats[j-1].cameraTo:start;
    b.animate=!scene.chapterTitle&&((j===0&&newView)||!sameCamera(b.cameraFrom,b.cameraTo)||JSON.stringify(j?beats[j-1].focus:previous?.focus)!==JSON.stringify(b.focus));
    b.motionSeconds=b.animate?Math.min(1.35,((beats[j+1]?.startFrame??scene.frameCount)-b.startFrame)/FPS*.4):0;
    b.motionFrames=Math.ceil(b.motionSeconds*FPS);
    const oldRect=j?beats[j-1].rects[0]:!newView?previous?.surface.focusRects?.[0]:null;
    b.cursorFrom=oldRect?{x:oldRect.x+oldRect.width*.8,y:oldRect.y+oldRect.height*.6}:null;
  });
  const end=beats.at(-1).cameraTo;
  const cues=cuesByTurn.get(scene.id)??[];
  motionLog.push({id:scene.id,chapterId:scene.chapterId,start:scene.start,end:scene.end,kind:spec.kind,resource:spec.resource,direction:spec.direction,viewKey:surface.viewKey,focus:spec.focus??[],focusRects:surface.focusRects,cameraFrom:start,cameraTo:end,motionSeconds:beats.reduce((n,b)=>n+b.motionSeconds,0),focusBeats:beats.map(b=>({startSeconds:b.startFrame/FPS,focus:b.focus,cameraFrom:b.cameraFrom,cameraTo:b.cameraTo,motionSeconds:b.motionSeconds})),captionCues:cues.length,chapterTitle:!!scene.chapterTitle});
  for(let frame=0;frame<scene.frameCount;){
    const seconds=frame/FPS,globalTime=scene.start+seconds+1e-5,cue=cues.find(c=>globalTime>=c.start-1e-5&&globalTime<c.end-1e-5);
    const bi=beats.findLastIndex(b=>frame>=b.startFrame),beat=beats[bi],localFrame=frame-beat.startFrame,elapsed=localFrame/FPS;
    const settled=localFrame>=beat.motionFrames;
    let count=settled?(beats[bi+1]?.startFrame??scene.frameCount)-frame:1;
    if(cue)count=Math.min(count,Math.max(1,Math.round((cue.end-scene.start)*FPS)-frame));
    const p=settled?1:smooth((elapsed-(newView&&bi===0?.18:0))/Math.max(.2,beat.motionSeconds-(newView&&bi===0?.18:0)));
    const cam=interpolate(beat.cameraFrom,beat.cameraTo,p),fade=previous&&newView&&bi===0&&!settled?smooth(seconds/.22):1;
    const activeSurface={...surface,focusRects:beat.rects};
    const svg=frameSvg({scene,spec,surface:activeSurface,cam,previous:previous&&newView?previous:null,fade,elapsed,settled,cue,cursorFrom:beat.cursorFrom});
    const buffer=await frameBuffer(svg);
    if(buffer.length!==W*H*3)throw Error('Unexpected raster byte count');
    for(let hold=0;hold<count;hold++)await push(buffer);
    frames+=count;frame+=count;
  }
  previous={surface:{...surface,focusRects:beats.at(-1).rects},cam:end,focus:beats.at(-1).focus};
  if((i+1)%10===0||i===renderScenes.length-1){console.log(`Portal scenes ${i+1}/${renderScenes.length} — ${frames}/${expectedBodyFrames} body frames`);await fs.writeFile(path.join(OUT,'build-progress.json'),JSON.stringify({phase:'rendering',scenes:i+1,totalScenes:renderScenes.length,bodyFrames:frames,totalBodyFrames:expectedBodyFrames})+'\n');}
}
encoder.stdin.end();await encoderExit;
if(frames!==expectedBodyFrames)throw Error('Portal body frame count differs from source timeline');
await fs.writeFile(path.join(work,'encoder.log'),encoderLog);
if(sampleTurn){
  const scene=renderScenes[0],file='portal-motion-sample.mp4';
  run('ffmpeg',['-v','error','-y','-i',bodyFile,'-ss',String(scene.start),'-i',sourceVideo,'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k','-t',String(scene.duration),'-movflags','+faststart',path.join(OUT,file)]);
  await fs.writeFile(path.join(OUT,'sample-export.json'),JSON.stringify({file,scene:scene.id,duration:scene.duration,frames,focusBeats:motionLog[0].focusBeats},null,2)+'\n');
  await fs.rm(work,{recursive:true,force:true});
  console.log(JSON.stringify({sample:path.join(OUT,file),seconds:scene.duration}));process.exit(0);
}
console.log('Portal body encoded; preserving opening, closing and original AAC audio');
const common=['-an','-c:v','libx264','-preset','fast','-crf','18','-threads','4','-pix_fmt','yuv420p','-r','24','-video_track_timescale','12288'];
const opening=path.join(work,'opening.mp4'),closing=path.join(work,'closing.mp4');
run('ffmpeg',['-v','error','-y','-i',sourceVideo,'-frames:v',String(bodyStart),...common,opening]);
run('ffmpeg',['-v','error','-y','-ss',String(bodyEnd/FPS),'-i',sourceVideo,'-frames:v','192',...common,closing]);
const concat=path.join(work,'segments.txt');await fs.writeFile(concat,[opening,bodyFile,closing].map(f=>`file '${f}'`).join('\n')+'\n');
const file='egakumi-study-03-azure-portal.mp4',full=path.join(OUT,file);
run('ffmpeg',['-v','error','-y','-f','concat','-safe','0','-i',concat,'-i',sourceVideo,'-map','0:v:0','-map','1:a:0','-c','copy','-t',String(timeline.duration),'-movflags','+faststart',full]);
const exported={file,sha256:hash(await fs.readFile(full)),duration:timeline.duration,fps:FPS,width:W,height:H,frameCount:timeline.frameCount,sourceVideo:source.file,sourceSha256:source.sha256,originalAudioCopied:true,speechRegenerated:false,simulatedAzurePortal:true,bodyScenes:bodyScenes.length,animatedScenes:motionLog.filter(s=>s.motionSeconds>0).length};
await fs.writeFile(path.join(OUT,'portal-export.json'),JSON.stringify(exported,null,2)+'\n');
await fs.writeFile(path.join(OUT,'portal-timeline.json'),JSON.stringify({schemaVersion:1,duration:timeline.duration,fps:FPS,bodyStartFrame:bodyStart,bodyEndFrame:bodyEnd,sourceTimelineSha256:hash(await fs.readFile(path.join(BASE,'lesson-timeline.json'))),sceneDataSha256,surfaceRendererSha256,compositorSha256,simulatedAzurePortal:true,motionSamplingFps:24,scenes:motionLog},null,2)+'\n');
const poster=bodyScenes.find(s=>s.id.includes('-036-')),posterSpec=resolveSpec(poster),posterSurface=await surfaceFor(posterSpec);
await sharp(Buffer.from(frameSvg({scene:poster,spec:posterSpec,surface:posterSurface,cam:cameraFor(posterSurface.focusRects),cue:cuesByTurn.get(poster.id)?.[0]}))).png().toFile(path.join(OUT,'portal-poster.png'));
let html=await fs.readFile(path.join(BASE,'index.html'),'utf8');
html=html.replaceAll(source.file,file).replaceAll('lesson-poster.png','portal-poster.png');
for(const name of ['lesson-captions.vtt','lesson-transcript.md','egakumi-common-closing.mp4','lesson-mix.wav','lesson-narration.wav'])html=html.replaceAll(name,'../'+name);
html=html.replace('<h1>NSGによる通信制御</h1>','<h1>NSGによる通信制御｜Azureの設定画面で学ぶ</h1>');
html=html.replace('関連付けた区画を基準に送信・受信を区別し、適用するNSGを選ぶ。','Azure Portalを模した学習画面で、規則の一覧と詳細を赤枠・ズームで追います。通信の仕組みは補助図で確かめます。');
html=html.replace('</nav>','<a href="../index.html">前の図解版</a></nav>');
await fs.writeFile(path.join(OUT,'index.html'),html);
await fs.writeFile(path.join(OUT,'build-progress.json'),JSON.stringify({phase:'complete',file,frameCount:timeline.frameCount})+'\n');
await fs.rm(work,{recursive:true,force:true});
console.log(JSON.stringify(exported));
