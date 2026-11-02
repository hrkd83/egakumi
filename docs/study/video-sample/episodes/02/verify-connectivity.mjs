// Episode-local connectivity-revision preservation and fixed-view audit.
// No generation or network I/O. Run after the full connectivity build.
// --source-only true checks the source, archived assets and static rasters,
// without accepting a stale timeline or writing a final successful report.
import fs from 'node:fs/promises';
import {createReadStream} from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {renderLessonFrame} from '../../lesson-visual.mjs';

const episode=path.dirname(fileURLToPath(import.meta.url));
const here=path.resolve(episode,'../..');
const out=path.join(here,'output/lesson-02-gemini');
const options=Object.fromEntries(Array.from({length:Math.floor((process.argv.length-2)/2)},(_,i)=>[process.argv[2+i*2],process.argv[3+i*2]]));
const sourceOnly=options['--source-only']==='true';
const sharp=createRequire(import.meta.url)('sharp');sharp.concurrency(2);
const read=async file=>JSON.parse(await fs.readFile(file,'utf8'));
const hash=data=>crypto.createHash('sha256').update(data).digest('hex');
const digest=async file=>{const h=crypto.createHash('sha256');for await(const chunk of createReadStream(file))h.update(chunk);return h.digest('hex');};
const assert=(condition,message)=>{if(!condition)throw new Error(message);};
function canonical(value){
  if(Array.isArray(value))return value.map(canonical);
  if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])]));
  return value;
}
const same=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
const omit=(value,keys)=>Object.fromEntries(Object.entries(value).filter(([k])=>!keys.has(k)));
const close=(a,b)=>Number.isFinite(a)&&Number.isFinite(b)&&Math.abs(a-b)<1e-7;
const relative=file=>path.relative(here,file);
const archive=path.join(episode,'versions/settings-v2');
const oldOut=path.join(out,'versions/settings-v2');
const scriptFile=path.join(episode,'script.json'),oldFile=path.join(archive,'script.json');
const script=await read(scriptFile),original=await read(oldFile);
const initialScriptSha256=await digest(scriptFile);
const baseline=await read(path.join(episode,'settings-baseline.json'));
const revision=await read(path.join(episode,'connectivity-revision.json'));
assert(revision.totalTurnCount===86&&revision.newRecordingCount===7&&revision.unchangedOriginalTurnCount===79,'Unexpected connectivity revision counts');
const newIds=['lesson02-turn-011a3-mako','lesson02-turn-011b3-ikumi','lesson02-turn-011c3-mako','lesson02-turn-011d3-ikumi','lesson02-turn-011e3-manabu','lesson02-turn-011f3-ikumi'];
const replacedOldId='lesson02-turn-012-mako';
const replacement=script.turns.find(t=>/^lesson02-turn-012(?:r3|-r3)-mako$/.test(t.id));
assert(replacement,'The rerecorded 012r3 turn is missing');
const newRecordedIds=[...newIds,replacement.id],newRecordedSet=new Set(newRecordedIds);
const oldMap=new Map(original.turns.map(t=>[t.id,t])),newMap=new Map(script.turns.map(t=>[t.id,t]));
assert(script.visualStyle==='teaching-settings-v2','The connectivity revision must retain teaching-settings-v2');
assert(original.turns.length===80&&script.turns.length===86&&newMap.size===86,'Expected 80 original and 86 unique revised turns');
assert(!newMap.has(replacedOldId),'Original 012 must be replaced by a separately recorded ID');
for(const id of newIds)assert(newMap.has(id)&&newMap.get(id).chapterId==='connection-models',`${id}: missing introductory comparison turn`);
const sourceOrder=script.turns.filter(t=>!newIds.includes(t.id)).map(t=>t.id===replacement.id?replacedOldId:t.id);
assert(same(sourceOrder,original.turns.map(t=>t.id)),'Existing turn order changed');
const insertion=script.turns.findIndex(t=>t.id===newIds[0]);
assert(script.turns.slice(insertion,insertion+6).map(t=>t.id).join(',')===newIds.join(','),'The six comparison turns must form one contiguous conversation');
assert(script.turns[insertion+6].id===replacement.id,'The comparison must immediately precede the detailed PE question');
for(const old of original.turns){
  const current=newMap.get(old.id===replacedOldId?replacement.id:old.id);
  assert(current,`${old.id}: existing turn disappeared`);
  if(old.id!==replacedOldId)assert(same(current,old),`${old.id}: existing turn or visual changed`);
  else{
    assert(same(omit(current,new Set(['id'])),omit(old,new Set(['id']))),'012r3 must retain every source field except its new recording ID');
  }
}
const invariantTop=['schemaVersion','productionProfile','episodeKey','episodeNumber','episodeId','cast','speed','logoIntroSeconds','logoClosingSeconds','speechSettings','firstCut'];
for(const key of invariantTop)assert(same(script[key],original[key]),`Audio/identity top-level field ${key} changed`);
const oldChapters=new Map(original.chapters.map(c=>[c.id,c]));
assert(script.chapters.length===original.chapters.length+1,'Expected exactly one additional chapter');
for(const chapter of script.chapters){
  if(chapter.id==='connection-models')continue;
  assert(same(chapter,oldChapters.get(chapter.id)),`Existing chapter ${chapter.id} changed`);
}
const body=script.turns.filter(t=>t.chapterId!=='opening');
assert(body.length===82,'Expected 82 body turns');
const forbidden=new Set(['portal','motion','zoom','camera','azureMotion','pointer','mockPointer','pulse']);
function checkKeys(value,where){if(!value||typeof value!=='object')return;for(const [key,item] of Object.entries(value)){assert(!forbidden.has(key),`${where}: forbidden instruction ${key}`);checkKeys(item,where+'.'+key);}}
function withoutHighlight(svg){
  const token=/<\/?g\b[^>]*>/gi;let match,result='',cursor=0,depth=0;
  while((match=token.exec(svg))){
    const closing=/^<\//.test(match[0]);
    if(depth){if(closing)depth--;else if(!/\/\s*>$/.test(match[0]))depth++;if(!depth)cursor=token.lastIndex;}
    else if(!closing&&/\b(?:id|class)\s*=\s*["'][^"']*\bteaching-highlight\b[^"']*["']/.test(match[0])){result+=svg.slice(cursor,match.index);if(/\/\s*>$/.test(match[0]))cursor=token.lastIndex;else depth=1;}
  }
  assert(depth===0,'Unclosed teaching-highlight group');return result+svg.slice(cursor);
}
const groups=new Map(),prepared=new Map(),svgChecks=new Map();
for(const turn of body){
  const visual=turn.visual;
  assert(visual&&['settings','svg'].includes(visual.layout),`${turn.id}: use settings/svg`);
  assert(/^[-a-zA-Z0-9_]+$/.test(visual.viewId??''),`${turn.id}: missing fixed viewId`);checkKeys(visual,turn.id);
  let baseVisual=omit(visual,new Set(['highlightRows','highlightFields','heading']));
  let renderVisual={...visual},baselineRenderVisual={...baseVisual};
  if(visual.layout==='svg'){
    assert(typeof visual.svg==='string'&&!path.isAbsolute(visual.svg),`${turn.id}: use an episode-relative SVG`);
    const file=path.resolve(episode,visual.svg);assert(file.startsWith(episode+path.sep),`${turn.id}: unsafe SVG path`);
    const svg=await fs.readFile(file,'utf8');
    assert(!/<!DOCTYPE|<!ENTITY|<script\b|<foreignObject\b|\bon[a-z]+\s*=|(?:href|src)\s*=\s*["'](?!#|data:)|@import|<animate\b|<animateTransform\b|<set\b/i.test(svg),`${turn.id}: SVG must be self-contained and static`);
    assert(/viewBox\s*=\s*["']0 0 1207 511["']/.test(svg),`${turn.id}: wrong fixed SVG viewBox`);
    const stripped=withoutHighlight(svg);
    baseVisual=omit(baseVisual,new Set(['svg','svgSha256','svgDataUri']));baseVisual.svgBaselineSha256=hash(stripped);
    if(newIds.includes(turn.id))assert(visual.viewId===revision.comparison.viewId&&baseVisual.svgBaselineSha256===revision.comparison.baselineSha256,`${turn.id}: comparison baseline differs from the revision record`);
    renderVisual.svgDataUri='data:image/svg+xml;base64,'+Buffer.from(svg).toString('base64');
    baselineRenderVisual={...renderVisual,svgDataUri:'data:image/svg+xml;base64,'+Buffer.from(stripped).toString('base64')};
    svgChecks.set(file,hash(svg));
  }
  const existing=groups.get(visual.viewId);
  if(existing){assert(same(existing.baseVisual,baseVisual),`${turn.id}: base composition changed within viewId`);existing.turnIds.push(turn.id);}
  else groups.set(visual.viewId,{viewId:visual.viewId,layout:visual.layout,baseVisual,turnIds:[turn.id]});
  prepared.set(turn.id,{renderVisual,baselineRenderVisual});
}
assert(new Set(newIds.map(id=>newMap.get(id).visual.viewId)).size===1,'All six comparison turns must hold the same viewId');
assert(newIds.every(id=>newMap.get(id).visual.layout==='svg'),'The comparison must use the common fixed SVG composition');
// Original source hashes are retained even though the current narration, mix,
// captions and generation journal intentionally change for this longer edition.
const fileChecks=[];
assert(Object.keys(baseline.rawAudio).length===80,'Expected 80 original raw hashes');
for(const [name,expected] of Object.entries(baseline.rawAudio)){
  assert(path.basename(name)===name&&name.endsWith('-raw.wav'),`Unsafe raw baseline ${name}`);
  const actual=await digest(path.join(out,name));assert(actual===expected,`${name}: original raw changed`);fileChecks.push({file:name,sha256:actual});
}
for(const name of ['lesson-narration.wav','lesson-mix.wav','gemini-attempts.json','lesson-captions.vtt']){
  const actual=await digest(path.join(oldOut,name));assert(actual===baseline[name],`${name}: archived original changed`);fileChecks.push({file:'versions/settings-v2/'+name,sha256:actual});
}
const originalMovie='egakumi-study-02.mp4';assert(await digest(path.join(out,originalMovie))===baseline[originalMovie],'Original diagram movie was changed');
const oldExport=await read(path.join(oldOut,'lesson-export.json'));
assert(oldExport.file==='egakumi-study-02-settings.mp4','Unexpected archived settings movie');
assert(await digest(path.join(out,oldExport.file))===oldExport.sha256,'Completed settings movie was overwritten');
fileChecks.push({file:originalMovie,sha256:baseline[originalMovie]},{file:oldExport.file,sha256:oldExport.sha256});
const archivedSettingsAudit=await read(path.join(archive,'settings-verification.json'));
assert(archivedSettingsAudit.status==='passed','Archived settings audit must be successful');
const oldRaster=new Map(archivedSettingsAudit.rasterChecks.map(r=>[r.id,r]));
const rasterChecks=[],basePixels=new Map();
for(const [index,turn] of body.entries()){
  const {renderVisual,baselineRenderVisual}=prepared.get(turn.id);
  const args={phase:'body',episodeNumber:2,episodeLabel:script.episodeLabel??'第2回',seriesLabel:script.seriesLabel,title:script.topic??script.title,speaker:turn.speaker,chapter:script.chapters.find(c=>c.id===turn.chapterId),subtitleLines:turn.subtitleChunks?.[0]?.lines??turn.subtitleLines??[turn.caption]};
  const renderHash=async(visual,time)=>{const svg=await renderLessonFrame({...args,visual,time,turnTime:time,sceneTime:time});return hash(await sharp(Buffer.from(svg)).extract({left:65,top:204,width:1207,height:511}).ensureAlpha().raw().toBuffer());};
  const samples=[];for(const t of [0,2,10])samples.push(await renderHash(renderVisual,t));
  assert(new Set(samples).size===1,`${turn.id}: body changes over time`);
  const base=await renderHash(baselineRenderVisual,0);
  assert(!basePixels.has(turn.visual.viewId)||basePixels.get(turn.visual.viewId)===base,`${turn.id}: unhighlighted pixels changed within viewId`);basePixels.set(turn.visual.viewId,base);
  const old=oldRaster.get(turn.id===replacement.id?replacedOldId:turn.id);
  if(old)assert(old.bodySha256===samples[0]&&old.baselineBodySha256===base,`${turn.id}: existing body pixels changed`);
  rasterChecks.push({id:turn.id,viewId:turn.visual.viewId,sampleSeconds:[0,2,10],bodySha256:samples[0],baselineBodySha256:base,matchedArchivedSettings:!!old});
  if((index+1)%15===0)console.log(`Checked fixed body raster ${index+1}/${body.length}`);
}
let audioSelection=null,timelineCheck=null;
if(!sourceOnly){
  const generation=await read(path.join(out,'generation.json')),oldGeneration=await read(path.join(oldOut,'generation.json'));
  const oldConfig=await read(path.join(archive,'generation.json')),config=await read(path.join(episode,'generation.json'));
  const oldRows=new Map(oldGeneration.results.map(r=>[r.id,r])),rows=new Map(generation.results.map(r=>[r.id,r]));
  const oldCandidates=new Map(oldConfig.candidates.map(r=>[r.id,r])),candidates=new Map(config.candidates.map(r=>[r.id,r]));
  assert(generation.generationState==='complete'&&rows.size===86&&generation.results.length===86,'Expected 86 completed active recordings');
  assert(!rows.has(replacedOldId),'Old 012 recording remains selected');
  let reused=0;
  for(const turn of script.turns){
    const row=rows.get(turn.id),candidate=candidates.get(turn.id);assert(row&&candidate,`${turn.id}: missing selected recording/candidate`);
    if(newRecordedSet.has(turn.id)){assert(!oldRows.has(turn.id),`${turn.id}: new recording ID collides with the archive`);continue;}
    const old=oldRows.get(turn.id),oldCandidate=oldCandidates.get(turn.id);assert(old&&oldCandidate,`${turn.id}: original recording missing`);
    for(const key of ['speech','sourceSpeech','voice','speed','parameters','requestSha256','rawAudioSha256'])assert(same(row[key],old[key]),`${turn.id}: reused recording ${key} changed`);
    for(const key of ['speech','sourceSpeech','voice','speed','parameters'])assert(same(candidate[key],oldCandidate[key]),`${turn.id}: reused candidate ${key} changed`);
    assert(row.reusedRaw===true,`${turn.id}: old recording is not marked as reused`);reused++;
  }
  assert(reused===79,'Expected exactly 79 reused active recordings');
  const oldQuestion=oldCandidates.get(replacedOldId),newQuestion=candidates.get(replacement.id);
  for(const key of ['speech','sourceSpeech','voice','speed'])assert(same(oldQuestion[key],newQuestion[key]),`012r3 changed ${key} rather than preserving the words and voice`);
  assert(oldQuestion.parameters.style!==newQuestion.parameters.style,'012r3 must carry the changed immediate context in style');
  const oldJournal=await read(path.join(oldOut,'gemini-attempts.json')),journal=await read(path.join(out,'gemini-attempts.json'));
  const addedJournalIds=Object.keys(journal).filter(id=>!Object.hasOwn(oldJournal,id)).sort();
  assert(same(addedJournalIds,[...newRecordedIds].sort()),'Generation journal must add exactly the six comparison recordings and 012r3');
  for(const id of Object.keys(oldJournal))assert(same(journal[id],oldJournal[id]),`${id}: old request journal entry changed`);
  audioSelection={reusedRecordings:79,newRecordings:7,newRecordedIds,originalRawRecordingsPreserved:80,oldRequestEntriesUnchanged:80,activeRecordings:86,provenanceValidation:'Run the shared verify-lesson.py for full new recording/request provenance.'};
  const timeline=await read(path.join(out,'lesson-timeline.json')),oldTimeline=await read(path.join(oldOut,'lesson-timeline.json'));
  const exported=await read(path.join(out,'lesson-export.json'));
  assert(exported.file==='egakumi-study-02-connectivity.mp4','The connectivity build must use a separate movie filename');
  assert(exported.sha256===await digest(path.join(out,exported.file)),'Connectivity movie/export hash mismatch');
  assert(timeline.visualStyle==='teaching-settings-v2'&&timeline.bodyCamera==='fixed'&&!timeline.screenMotionTimeline?.length,'Expected fixed teaching settings timeline');
  const oldScenes=new Map(oldTimeline.scenes.map(s=>[s.id,s]));
  const extraIds=['chapter-connection-models',...newIds];
  const preservedOrder=timeline.scenes.filter(s=>!extraIds.includes(s.id)).map(s=>s.id===replacement.id?replacedOldId:s.id);
  assert(same(preservedOrder,oldTimeline.scenes.map(s=>s.id)),'Existing scene order changed');
  assert(timeline.scenes.length===oldTimeline.scenes.length+7,'Expected six speech scenes plus one chapter title');
  let frameCursor=0,addedFrames=0,changedQuestionFrames=0,preservedScenes=0;
  for(const scene of timeline.scenes){
    assert(scene.start===frameCursor/24,`${scene.id}: unexpected start time or temporal gap`);frameCursor+=scene.frameCount;
    assert(scene.end===frameCursor/24,`${scene.id}: unexpected end time`);
    const old=oldScenes.get(scene.id===replacement.id?replacedOldId:scene.id);
    if(old){
      if(scene.id===replacement.id)changedQuestionFrames=scene.frameCount-old.frameCount;
      else{for(const key of ['duration','frameCount'])assert(close(scene[key],old[key]),`${scene.id}: existing scene ${key} changed`);preservedScenes++;}
    }else{assert(extraIds.includes(scene.id),`${scene.id}: unexpected additional scene`);addedFrames+=scene.frameCount;}
    const turn=newMap.get(scene.id);
    if(turn)assert(same(omit(scene.visual,new Set(['svgSha256','svgDataUri'])),turn.visual),`${scene.id}: timeline visual differs from source`);
  }
  assert(frameCursor===timeline.frameCount&&frameCursor===oldTimeline.frameCount+addedFrames+changedQuestionFrames,'Timeline growth differs from the authorized added scenes and rerecording');
  for(const turn of original.turns.filter(t=>t.id!==replacedOldId)){
    const before=oldTimeline.subtitleCues.filter(c=>c.turnId===turn.id),after=timeline.subtitleCues.filter(c=>c.turnId===turn.id);
    const oldScene=oldScenes.get(turn.id),newScene=timeline.scenes.find(s=>s.id===turn.id);
    assert(before.length===after.length,`${turn.id}: existing subtitle cue count changed`);
    for(let i=0;i<before.length;i++){
      assert(same(omit(before[i],new Set(['start','end'])),omit(after[i],new Set(['start','end']))),`${turn.id}: existing subtitle text changed`);
      assert(close(before[i].start-oldScene.start,after[i].start-newScene.start)&&close(before[i].end-oldScene.start,after[i].end-newScene.start),`${turn.id}: existing subtitle relative timing changed`);
    }
  }
  timelineCheck={scenes:timeline.scenes.length,preservedSceneDurations:preservedScenes,addedFrames,changedQuestionFrames,durationSeconds:timeline.duration,frameCount:timeline.frameCount,subtitleCues:timeline.subtitleCues.length,oldQuestionId:replacedOldId,replacementQuestionId:replacement.id,finalMovie:exported.file,sha256:exported.sha256};
}
assert(await digest(scriptFile)===initialScriptSha256,'Source changed during audit; rerun when editing is complete');
for(const [file,expected] of svgChecks)assert(await digest(file)===expected,`SVG changed during audit: ${file}`);
const report={status:'passed',visualStyle:script.visualStyle,bodyCamera:'fixed',checkedAt:new Date().toISOString(),script:relative(scriptFile),scriptSha256:initialScriptSha256,originalScript:relative(oldFile),originalScriptSha256:await digest(oldFile),existingTurnsUnchanged:79,existingVisualsPreserved:76,newComparisonTurns:6,recontextualizedQuestion:{oldId:replacedOldId,newId:replacement.id},bodyTurns:body.length,bodyViews:groups.size,bodyTimeSamples:[0,2,10],scope:'Independent source, archive, selection and fixed-raster audit. Shared media/provenance validation and human conversation/audio review are separate.',fileChecks,groups:[...groups.values()].map(({baseVisual,...rest})=>rest),rasterChecks,audioSelection,timeline:timelineCheck};
if(sourceOnly){report.mode='source-only';console.log(JSON.stringify(report));}
else{
  const file=path.join(episode,'connectivity-verification.json'),temp=file+'.pending';await fs.writeFile(temp,JSON.stringify(report,null,2)+'\n');await fs.rename(temp,file);
  console.log(JSON.stringify({status:report.status,report:file,existingTurnsUnchanged:79,bodyTurns:body.length,bodyViews:groups.size,audioSelection,timeline:timelineCheck}));
}
