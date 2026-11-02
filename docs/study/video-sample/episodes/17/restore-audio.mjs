// Restore episode 17's locally reproducible WAVs from retained original audio.
// No authentication, synthesis, network, source edit or video render occurs.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {spawnSync} from 'node:child_process';

const episode=path.dirname(fileURLToPath(import.meta.url));
const base=path.resolve(episode,'../..');
const sourceFile=path.join(base,'build-lesson.mjs');
const generatedFile=path.join(episode,'.restore-audio.generated.mjs');
const flags=process.argv.slice(2);
const checkOnly=flags.length===1&&flags[0]==='--check';
const explicitRebuild=flags.length===2&&flags[0]==='--reuse-audio'&&flags[1]==='false';
if(flags.length&&!checkOnly&&!explicitRebuild)throw new Error('Use no arguments to restore local WAVs, or --check to validate without rebuilding.');
const profile=JSON.parse(await fs.readFile(path.join(base,'production-profile.json'),'utf8'));
const sourceBytes=await fs.readFile(sourceFile);
const sourceSHA256=crypto.createHash('sha256').update(sourceBytes).digest('hex');
if(sourceSHA256!==profile.commonFileSha256['build-lesson.mjs'])throw new Error('Shared builder differs from the verified production profile.');
const transforms=[];
function replaceOnce(source,oldText,newText,label){
  const count=source.split(oldText).length-1;
  if(count!==1)throw new Error('Refusing transform '+label+': expected 1 match, got '+count);
  transforms.push(label);
  return source.replace(oldText,newText);
}
let source=sourceBytes.toString('utf8');
source=replaceOnce(source,"from './lesson-visual.mjs'","from "+JSON.stringify(pathToFileURL(path.join(base,'lesson-visual.mjs')).href),'absolute visual import');
source=replaceOnce(source,"from './lesson-audio-cache.mjs'","from "+JSON.stringify(pathToFileURL(path.join(base,'lesson-audio-cache.mjs')).href),'absolute audio-cache import');
source=replaceOnce(source,'const here=path.dirname(fileURLToPath(import.meta.url));','const here='+JSON.stringify(base)+';','original input/asset base');
source=replaceOnce(source,'const require=createRequire(import.meta.url);','const require=createRequire('+JSON.stringify(pathToFileURL(sourceFile).href)+');','original dependency base');
const lockMarker="if(fsSync.existsSync(generationLock))throw new Error('Audio generation started while acquiring the build lock');";
source=replaceOnce(source,lockMarker,lockMarker+String.raw`
// Keep the old manifest in memory even when both derivative WAVs are absent.
// These expected hashes must survive a failed rebuild unchanged on disk.
const restoreExpectedCache=await read(path.join(out,'audio-cache.json'));
const restoreWAVs=['lesson-narration.wav','lesson-mix.wav'];
if(!/^[a-f0-9]{64}$/.test(restoreExpectedCache.signature??''))throw new Error('A retained audio-cache signature is required.');
for(const name of restoreWAVs)if(!/^[a-f0-9]{64}$/.test(restoreExpectedCache.files?.[name]??''))throw new Error('Retained expected WAV hash is missing: '+name);
const restoreExpectedHashes=Object.freeze(Object.fromEntries(restoreWAVs.map(name=>[name,restoreExpectedCache.files[name]])));
`,'retain original expected WAV hashes under episode build lock');
const signatureMarker='const audioSignature=audioSourceSignature(script,generation,music);';
source=replaceOnce(source,signatureMarker,signatureMarker+String.raw`
if(restoreExpectedCache.signature!==audioSignature)throw new Error('Retained WAV hashes belong to a different speech/music plan.');
if(options['--reuse-audio']!=='false')throw new Error('The audio restoration requires local recomposition, with --reuse-audio false.');
`,'require unchanged source plan and local recomposition');
source=replaceOnce(source,'await saveAudioCache(out,audioSignature);',String.raw`
// Verify before saving the new cache, so failure preserves the original
// expected hashes for diagnosis and a later restoration attempt.
for(const name of restoreWAVs){
  const restoredHash=hash(await fs.readFile(path.join(out,name)));
  if(restoredHash!==restoreExpectedHashes[name])throw new Error('Restored WAV SHA-256 differs from the retained original: '+name);
}
await saveAudioCache(out,audioSignature);`,'verify exact WAV hashes before replacing cache');
const cutMarker='const cueList=[],visualEntries=[],cameraTimeline=[];';
if(source.split(cutMarker).length!==2)throw new Error('Shared audio/render boundary changed.');
source=source.slice(0,source.indexOf(cutMarker))+String.raw`
// The original builder ends here for this restoration. All narration, raw
// recording provenance, timing and mix checks above remain the shared code.
const restoredCache=await read(path.join(out,'audio-cache.json'));
if(restoredCache.signature!==restoreExpectedCache.signature)throw new Error('Restored audio cache signature differs.');
for(const name of restoreWAVs)if(restoredCache.files[name]!==restoreExpectedHashes[name])throw new Error('Restored cache WAV hash differs: '+name);
await fs.rm(work,{recursive:true,force:true});
console.log(JSON.stringify({episode:'17',restoredFiles:restoreExpectedHashes,duration,frameCount:cursor,sourcePlanUnchanged:true,networkRequestsMade:false,authenticationRead:false,videoOrCaptionFilesWritten:false,renderWorkRemoved:true}));
`;
transforms.push('cut at exact audio/render boundary; clean only episode render-work');
if(source.includes('const cueList=')||source.includes('lesson-timeline.json')||source.includes('lesson-captions.vtt')||source.includes('lesson-export.json')||source.includes("function player("))throw new Error('Video/metadata generation remains after the audio-only cut.');
await fs.writeFile(generatedFile,source);
const check=spawnSync(process.execPath,['--check',generatedFile],{encoding:'utf8'});
if(check.status!==0)throw new Error(check.stderr||'Generated audio-only module failed syntax checking.');
console.log(JSON.stringify({episode:'17',sourceBuilderSHA256:sourceSHA256,transforms,localAudioRestore:true,reuseAudio:false,networkRequestsMade:false,checkOnly}));
if(checkOnly)process.exit(0);
process.argv=[process.execPath,generatedFile,'--script','episodes/17/script.json','--generation-config','episodes/17/generation.json','--cast','podcast-cast-gemini.json','--output','lesson-17-gemini','--reuse-audio','false'];
await import(pathToFileURL(generatedFile).href);
