import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
const hash=data=>crypto.createHash('sha256').update(data).digest('hex');
const files=['lesson-narration.wav','lesson-mix.wav'];
export function audioSourceSignature(script,generation,music){
  return hash(JSON.stringify({mixVersion:2,logo:script.logoIntroSeconds??4,establish:1.1,chapterTitle:1.25,closing:script.logoClosingSeconds??8,omitted:script.firstCut?.omitTurnIds??[],turns:script.turns.map(t=>({id:t.id,speaker:t.speaker,speech:t.speech,chapterId:t.chapterId,gap:t.gapAfterSeconds??.1,pause:t.pauseAfterSeconds??0})),provider:generation.provider??'egakumi-gateway',model:generation.modelId??'DragonHDLatestNeural',endpoint:generation.endpoint,recordings:generation.results.map(r=>[r.id,r.speech,r.voice,r.speed,r.parameters,r.requestSha256,r.rawAudioSha256]),music:music.assets.map(a=>[a.path,a.derivative.sha256]),closingSource:music.mixPolicy.closing.brandSourceSeconds}));
}
export async function saveAudioCache(out,signature){
  const hashes=Object.fromEntries(await Promise.all(files.map(async f=>[f,hash(await fs.readFile(path.join(out,f)))])));
  await fs.writeFile(path.join(out,'audio-cache.json'),JSON.stringify({signature,files:hashes},null,2)+'\n');
}
export async function checkAudioCache(out,signature){
  const cached=JSON.parse(await fs.readFile(path.join(out,'audio-cache.json'),'utf8'));
  if(cached.signature!==signature)throw new Error('Saved audio belongs to a different speech/music plan.');
  for(const file of files)if(hash(await fs.readFile(path.join(out,file)))!==cached.files[file])throw new Error(`Changed cached audio: ${file}`);
  return true;
}
