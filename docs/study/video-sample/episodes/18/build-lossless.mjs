// Episode-local storage fallback. The common renderer and all lesson logic stay
// unchanged; lossless WebP replaces only the temporary raster files.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';

const episode = path.dirname(fileURLToPath(import.meta.url));
const base = path.resolve(episode, '../..');
const shared = path.join(base, 'build-lesson.mjs');
const sharedUrl = new URL('../../build-lesson.mjs', import.meta.url).href;
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const original = await fs.readFile(shared, 'utf8');
const profile = JSON.parse(await fs.readFile(path.join(base, 'production-profile.json'), 'utf8'));
if (hash(original) !== profile.commonFileSha256['build-lesson.mjs']) {
  throw new Error('Common builder differs from the approved source.');
}
for (const [flag, expected] of [['--script', 'episodes/18/script.json'], ['--generation-config', 'episodes/18/generation.json'], ['--cast', 'podcast-cast-gemini.json'], ['--output', 'lesson-18-gemini']]) {
  if (process.argv.filter(arg => arg === flag).length !== 1 || process.argv[process.argv.indexOf(flag) + 1] !== expected) throw new Error('Episode 18 input/output required exactly once: ' + flag);
}
if (process.argv.filter(arg => arg === '--reuse-audio').length !== 1 || process.argv[process.argv.indexOf('--reuse-audio') + 1] !== 'true'
    || process.argv.includes('--preview') || process.argv.includes('--write-script')) {
  throw new Error('Use the verified audio cache; preview and source writes are not supported.');
}
let source = original;
const changes = [];
function replace(before, after, label) {
  if (source.split(before).length !== 2) throw new Error('Unexpected builder structure: ' + label);
  source = source.replace(before, after);
  changes.push(label);
}
for (const name of ['lesson-visual.mjs', 'lesson-audio-cache.mjs']) {
  replace(`from './${name}'`, `from ${JSON.stringify(new URL('../../' + name, import.meta.url).href)}`, 'shared import ' + name);
}
replace('const here=path.dirname(fileURLToPath(import.meta.url));', `const here=${JSON.stringify(base)};`, 'retain shared asset/input root');
replace('const require=createRequire(import.meta.url);', `const require=createRequire(${JSON.stringify(sharedUrl)});`, 'retain shared dependency root');
replace("const sharp=require('sharp');sharp.concurrency(2);", "const sharp=require('sharp');sharp.concurrency(2);\nconst writeFrame=async(file,buffer)=>fs.writeFile(file,await sharp(buffer).webp({lossless:true,effort:4}).toBuffer());", 'lossless intermediate encoder');
replace("`still-${String(fileIndex++).padStart(4,'0')}.png`", "`still-${String(fileIndex++).padStart(4,'0')}.webp`", 'still suffix');
replace('await fs.writeFile(file,await renderLessonRaster(argsFor(scene,{subtitleLines})));', 'await writeFrame(file,await renderLessonRaster(argsFor(scene,{subtitleLines})));', 'still lossless encoding');
replace("await fs.copyFile(file,path.join(out,'lesson-poster.png'));", "await sharp(file).png().toFile(path.join(out,'lesson-poster.png'));", 'retain PNG poster');
replace("for(let f=1;f<=scene.frameCount;f++)imageEntry(path.join(dir,`${String(f).padStart(4,'0')}.png`),1);", "for(let f=1;f<=scene.frameCount;f++){const png=path.join(dir,`${String(f).padStart(4,'0')}.png`),webp=png.slice(0,-4)+'.webp';await sharp(png).webp({lossless:true,effort:4}).toFile(webp);await fs.unlink(png);imageEntry(webp,1);}", 'convert approved intro frames losslessly');
replace("`${String(f+1).padStart(4,'0')}.png`", "`${String(f+1).padStart(4,'0')}.webp`", 'fallback logo suffix');
replace('await fs.writeFile(file,await renderLessonRaster(argsFor(scene,{sceneTime:f/fps,time:f/fps})));', 'await writeFrame(file,await renderLessonRaster(argsFor(scene,{sceneTime:f/fps,time:f/fps})));', 'fallback logo lossless encoding');
replace("`motion-${scene.id}-${String(frame).padStart(4,'0')}.png`", "`motion-${scene.id}-${String(frame).padStart(4,'0')}.webp`", 'motion suffix');
replace('renderLessonRaster(args).then(buffer=>fs.writeFile(file,buffer))', 'renderLessonRaster(args).then(buffer=>writeFrame(file,buffer))', 'motion lossless encoding');

const derived = path.join(episode, 'build-lossless-derived.mjs');
await fs.writeFile(derived, source);
await fs.writeFile(path.join(episode, 'lossless-builder-audit.json'), JSON.stringify({
  source: shared, sourceSha256: hash(original), derivedSha256: hash(source), changes,
  rationale: 'ENOSPC during temporary PNG rendering. All timing, scene selection, audio, captions, shared rendering and final H.264/AAC settings are inherited unchanged.',
  intermediateFormat: 'lossless WebP',
}, null, 2) + '\n');
if (process.argv.includes('--inspect-only')) console.log(JSON.stringify({derived, changes, renderingStarted: false}));
else await import(new URL('./build-lossless-derived.mjs', import.meta.url).href);
