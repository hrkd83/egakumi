// Episode-local preservation and fixed-view audit. No generation or network I/O.
// Run after the full build; --source-only true performs the source/raster checks
// without accepting the old output timeline or writing a successful final report.
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
const scriptFile=path.join(episode,'script.json');
const oldFile=path.join(episode,'versions/portal-v2/script.json');
const script=await read(scriptFile),original=await read(oldFile);
const initialScriptSha256=await digest(scriptFile);
const baseline=await read(path.join(episode,'settings-baseline.json'));
assert(script.visualStyle==='teaching-settings-v2','Canonical script must select teaching-settings-v2');
assert(script.turns.length===80&&original.turns.length===80,'Expected the original 80 recorded turns');
const presentationTop=new Set(['turns','description','seriesLabel','editorialNotes','visualEdition','visualStyle','status','output','actualDurationSeconds']);
assert(same(omit(script,presentationTop),omit(original,presentationTop)),'A non-presentation top-level script field changed');
for(let i=0;i<80;i++)assert(same(omit(script.turns[i],new Set(['visual'])),omit(original.turns[i],new Set(['visual']))),`${original.turns[i].id}: a non-visual turn field changed`);
const body=script.turns.filter(t=>t.chapterId!=='opening');
assert(body.length===76,'Expected exactly 76 body turns');
const forbidden=new Set(['portal','motion','zoom','camera','azureMotion','pointer','mockPointer','pulse']);
function checkKeys(value,where){
  if(!value||typeof value!=='object')return;
  for(const [key,item] of Object.entries(value)){
    assert(!forbidden.has(key),`${where}: forbidden body instruction ${key}`);
    checkKeys(item,where+'.'+key);
  }
}
// Highlight groups are the sole permitted variable part of a custom SVG.
// Remove a marked <g> with balanced nesting rather than an unbounded regex.
function withoutHighlight(svg){
  const token=/<\/?g\b[^>]*>/gi;let match,result='',cursor=0,depth=0,removed=0;
  while((match=token.exec(svg))){
    const closing=/^<\//.test(match[0]);
    if(depth){
      if(closing)depth--;else if(!/\/\s*>$/.test(match[0]))depth++;
      if(!depth)cursor=token.lastIndex;
    }else if(!closing&&/\b(?:id|class)\s*=\s*["'][^"']*\bteaching-highlight\b[^"']*["']/.test(match[0])){
      result+=svg.slice(cursor,match.index);removed++;
      if(/\/\s*>$/.test(match[0]))cursor=token.lastIndex;else depth=1;
    }
  }
  assert(depth===0,'Unclosed SVG teaching-highlight group');
  return {svg:result+svg.slice(cursor),removed};
}
const storyboardFile=path.join(episode,'settings-storyboard.json');
const storyboard=await fs.access(storyboardFile).then(()=>read(storyboardFile),()=>null);
const declaredGroups=storyboard?.groups??storyboard?.views??[];
assert(storyboard?.visualStyle==='teaching-settings-v2'&&Array.isArray(declaredGroups)&&declaredGroups.length>0,'A settings storyboard with baseline groups is required');
const declaredIds=declaredGroups.flatMap(g=>g.turnIds??[]);
assert(declaredIds.length===76&&new Set(declaredIds).size===76&&body.every(t=>declaredIds.includes(t.id)),'Storyboard groups must cover each body turn exactly once');
const groups=new Map(),prepared=new Map();
for(const turn of body){
  const visual=turn.visual;
  assert(visual&&['settings','svg'].includes(visual.layout),`${turn.id}: body layout must be settings or svg`);
  assert(/^[-a-zA-Z0-9_]+$/.test(visual.viewId??''),`${turn.id}: missing stable viewId`);
  checkKeys(visual,turn.id);
  let baseVisual=omit(visual,new Set(['highlightRows','highlightFields','heading']));
  let baseHash,renderVisual={...visual},baselineRenderVisual={...baseVisual};
  if(visual.layout==='svg'){
    assert(typeof visual.svg==='string'&&!path.isAbsolute(visual.svg),`${turn.id}: use a relative episode SVG path`);
    const file=path.resolve(episode,visual.svg);
    assert(file.startsWith(episode+path.sep),`${turn.id}: SVG escapes episode`);
    const svg=await fs.readFile(file,'utf8');
    assert(!/<!DOCTYPE|<!ENTITY|<script\b|<foreignObject\b|\bon[a-z]+\s*=|(?:href|src)\s*=\s*["'](?!#|data:)|@import|<animate\b|<animateTransform\b|<set\b/i.test(svg),`${turn.id}: SVG must be self-contained and static`);
    assert(/viewBox\s*=\s*["']0 0 1207 511["']/.test(svg),`${turn.id}: SVG must use the fixed body viewBox`);
    const stripped=withoutHighlight(svg);
    baseHash=hash(stripped.svg);
    baseVisual=omit(baseVisual,new Set(['svg','svgSha256','svgDataUri']));
    baseVisual.svgBaselineSha256=baseHash;
    renderVisual.svgDataUri='data:image/svg+xml;base64,'+Buffer.from(svg).toString('base64');
    baselineRenderVisual={...renderVisual,svgDataUri:'data:image/svg+xml;base64,'+Buffer.from(stripped.svg).toString('base64')};
    const declared=declaredGroups.find(g=>(g.viewId??g.id)===visual.viewId);
    if(declared?.baselineSha256)assert(declared.baselineSha256===baseHash,`${turn.id}: storyboard SVG baseline hash mismatch`);
  }else{
    baseHash=hash(JSON.stringify(canonical(baseVisual)));
  }
  const declared=declaredGroups.find(g=>(g.viewId??g.id)===visual.viewId);
  assert(declared&&declared.layout===visual.layout&&declared.turnIds.includes(turn.id),`${turn.id}: storyboard group/layout mismatch`);
  if(visual.layout==='settings'){
    const authorBaseline=hash(JSON.stringify(omit(visual,new Set(['highlightRows','highlightFields']))));
    assert(declared.baselineSha256===authorBaseline,`${turn.id}: storyboard settings baseline hash mismatch`);
  }
  const existing=groups.get(visual.viewId);
  if(existing){assert(same(existing.baseVisual,baseVisual),`${turn.id}: settings fields/table/menu or SVG base changed within viewId ${visual.viewId}`);existing.turnIds.push(turn.id);}
  else groups.set(visual.viewId,{viewId:visual.viewId,layout:visual.layout,baseVisual,baselineSha256:baseHash,turnIds:[turn.id]});
  prepared.set(turn.id,{renderVisual,baselineRenderVisual});
}
const fileChecks=[];
for(const [name,expected] of Object.entries(baseline)){
  if(name==='rawAudio')continue;
  assert(typeof expected==='string'&&/^[a-f0-9]{64}$/.test(expected),`Unexpected baseline entry ${name}`);
  const actual=await digest(path.join(out,name));assert(actual===expected,`${name}: changed from settings baseline`);
  fileChecks.push({file:name,sha256:actual});
}
assert(Object.keys(baseline.rawAudio).length===80,'Baseline must contain all 80 raw recordings');
for(const [name,expected] of Object.entries(baseline.rawAudio)){
  assert(path.basename(name)===name&&name.endsWith('-raw.wav'),`Unsafe raw baseline name ${name}`);
  const actual=await digest(path.join(out,name));assert(actual===expected,`${name}: raw audio changed`);
  fileChecks.push({file:name,sha256:actual});
}
const rasterChecks=[],basePixels=new Map();
for(const [index,turn] of body.entries()){
  const {renderVisual,baselineRenderVisual}=prepared.get(turn.id);
  const args={phase:'body',episodeNumber:2,episodeLabel:script.episodeLabel??'第2回',seriesLabel:script.seriesLabel,title:script.topic??script.title,speaker:turn.speaker,chapter:script.chapters.find(c=>c.id===turn.chapterId),subtitleLines:turn.subtitleChunks?.[0]?.lines??turn.subtitleLines??[turn.caption]};
  const renderHash=async(visual,time)=>{
    const svg=await renderLessonFrame({...args,visual,time,turnTime:time,sceneTime:time});
    return hash(await sharp(Buffer.from(svg)).extract({left:65,top:204,width:1207,height:511}).ensureAlpha().raw().toBuffer());
  };
  const temporal=[];for(const time of [0,2,10])temporal.push(await renderHash(renderVisual,time));
  assert(new Set(temporal).size===1,`${turn.id}: body crop changes with time`);
  const base=await renderHash(baselineRenderVisual,0);
  assert(!basePixels.has(turn.visual.viewId)||basePixels.get(turn.visual.viewId)===base,`${turn.id}: baseline body pixels change within viewId`);
  basePixels.set(turn.visual.viewId,base);
  rasterChecks.push({id:turn.id,viewId:turn.visual.viewId,sampleSeconds:[0,2,10],bodySha256:temporal[0],baselineBodySha256:base});
  if((index+1)%15===0)console.log(`Checked fixed body raster ${index+1}/${body.length}`);
}
let timelineCheck=null;
if(!sourceOnly){
  const timeline=await read(path.join(out,'lesson-timeline.json'));
  const oldTimeline=await read(path.join(out,'versions/diagram-v1/lesson-timeline.json'));
  assert(timeline.visualStyle==='teaching-settings-v2'&&timeline.bodyCamera==='fixed','Full build timeline does not identify the fixed settings edition');
  assert(!timeline.screenMotionTimeline?.length,'Unexpected body motion timeline');
  for(const field of ['duration','frameCount','fps','width','height'])assert(close(timeline[field],oldTimeline[field]),`Programme ${field} changed`);
  assert(timeline.scenes.length===oldTimeline.scenes.length,'Scene count changed');
  for(let i=0;i<timeline.scenes.length;i++){
    const scene=timeline.scenes[i],old=oldTimeline.scenes[i];
    assert(scene.id===old.id,`Scene order changed at ${i}`);
    for(const field of ['start','end','frameCount','duration'])assert(close(scene[field],old[field]),`${scene.id}: ${field} changed`);
    const source=body.find(t=>t.id===scene.id);
    if(source){
      const normalized=omit(scene.visual,new Set(['svgSha256','svgDataUri']));
      assert(same(normalized,source.visual),`${scene.id}: timeline visual differs from source`);
    }
  }
  assert(same(timeline.subtitleCues,oldTimeline.subtitleCues),'Subtitle timing or text changed');
  timelineCheck={scenes:timeline.scenes.length,durationSeconds:timeline.duration,frameCount:timeline.frameCount,subtitleCues:timeline.subtitleCues.length,matchedOriginalSceneTiming:true};
}
assert(await digest(scriptFile)===initialScriptSha256,'Source script changed during the audit; rerun after editing is complete');
const report={status:'passed',visualStyle:script.visualStyle,bodyCamera:'fixed',checkedAt:new Date().toISOString(),script:relative(scriptFile),scriptSha256:await digest(scriptFile),originalScript:relative(oldFile),originalScriptSha256:await digest(oldFile),nonVisualTurnsUnchanged:80,bodyTurns:body.length,bodyViews:groups.size,bodyTimeSamples:[0,2,10],continuousPreviewReviewed:false,scope:'Source and raster preservation audit; core media validation and human conversation/audio review are separate.',fileChecks,groups:[...groups.values()].map(({baseVisual,...rest})=>rest),rasterChecks,timeline:timelineCheck};
if(sourceOnly){report.mode='source-only';console.log(JSON.stringify(report));}
else{
  await fs.writeFile(path.join(episode,'settings-verification.json'),JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({status:report.status,report:path.join(episode,'settings-verification.json'),bodyTurns:report.bodyTurns,bodyViews:report.bodyViews,nonVisualTurnsUnchanged:report.nonVisualTurnsUnchanged,timeline:timelineCheck}));
}
