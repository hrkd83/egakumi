"""Plan and apply the authorized cleanup for completed episode 15 only.

Only enumerated previews/*.png and the two derived WAVs may be deleted.
Original audio, requests, completion evidence and episode sources are retained.
"""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import json
from pathlib import Path
import shutil

EP=Path(__file__).resolve().parent
BASE=EP.parent.parent
OUT=BASE/'output/lesson-15-gemini'
PLAN=EP/'cleanup-plan.json'
REPORT=EP/'cleanup-result.json'
def now():return datetime.now(ZoneInfo('Asia/Tokyo')).isoformat()
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for data in iter(lambda:f.read(1024*1024),b''):h.update(data)
 return h.hexdigest()
def entry(path):return {'path':str(path.resolve()),'bytes':path.stat().st_size,'sha256':sha(path)}
def load(path):return json.loads(path.read_text())
def idle():
 assert not OUT.is_symlink()
 assert all(not (OUT/k).exists() for k in ['.lesson-build.lock','.gemini-generator.lock'])
 assert not (OUT/'render-work').exists()
 v=load(EP/'verification.json');e=load(OUT/'lesson-export.json');g=load(OUT/'generation.json')
 assert v['status']=='passed' and not v['warnings'] and g['generationState']=='complete'
 assert len(g['results'])==78 and all(r['status']=='succeeded' for r in g['results'])
 assert e['sha256']==v['sha256']==sha(OUT/e['file'])
 assert load(OUT/'conversation-preview.json')['status']=='passed'
 assert '完成動画・字幕・再生ページを制作済み' in (EP/'production.md').read_text()
def permitted(path):
 assert not path.is_symlink() and path.resolve().is_relative_to(OUT.resolve())
 rel=path.relative_to(OUT)
 return (len(rel.parts)==2 and rel.parts[0]=='previews' and path.suffix=='.png') or rel.as_posix() in ['lesson-narration.wav','lesson-mix.wav']
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')

parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
idle()
if not args.apply:
 assert not PLAN.exists() and not REPORT.exists()
 # Remove unavailable audio-only controls from this episode's player. Rebuild
 # restores them together with the derived WAVs; the completed MP4 is untouched.
 player=OUT/'index.html';old=player.read_text();oldhash=sha(player)
 audio='<p>BGMを含む音声</p><audio controls preload="none" src="lesson-mix.wav"></audio><p>声のみ</p><audio controls preload="none" src="lesson-narration.wav"></audio>'
 assert audio in old
 updated=old.replace('<summary>音声だけを聴く・資料と制作条件</summary>','<summary>資料と制作条件</summary>').replace(audio,'<p>音声は上の完成動画で再生できます。</p>')
 player.write_text(updated)
 candidates=sorted((OUT/'previews').glob('*.png'))+[OUT/'lesson-narration.wav',OUT/'lesson-mix.wav']
 assert all(permitted(p) and p.is_file() for p in candidates)
 chosen=set(candidates)
 keep=[p for p in OUT.rglob('*') if p.is_file() and p not in chosen]
 # Source QA images live under episodes/15, so the output-only cleanup leaves
 # them in place. Include their source/manifest records as reproduction data.
 keep += [p for p in EP.rglob('*') if p.is_file() and p.suffix in ['.py','.mjs','.svg','.json'] and not p.name.startswith('cleanup')]
 profile=load(BASE/'production-profile.json')
 keep += [BASE/n for n in list(profile['assetSha256'])+list(profile['commonFileSha256'])]
 keep += [BASE/'production-profile.json']
 keep=list(dict.fromkeys(keep))
 plan={'episodeKey':'15','plannedAt':now(),'status':'planned','policy':'CONTENT-PLAN.md / 完成後のデータ整理','scope':str(OUT.resolve()),'delete':[entry(p) for p in candidates],'deleteBytes':sum(p.stat().st_size for p in candidates),'keep':[entry(p) for p in keep],'dependencies':{'externalExactPathReferencesFound':False,'search':'rg lesson-15-gemini/(lesson-mix|lesson-narration)\\.wav in docs; no hits outside the episode record','noOtherEpisodeOrHistoricalMixDependency':True,'audioOnlyPlayback':'Unavailable after cleanup; completed MP4 remains playable','unknownTurn051':'Omitted from accepted edit, original request evidence retained; no synthesis/resume pending'},'playerChange':{'path':str(player.resolve()),'beforeSha256':oldhash,'afterSha256':sha(player),'reason':'Remove audio-only controls for the derived WAVs scheduled for removal'},'restore':'Run episode stream-build-lesson.mjs without --reuse-audio true; retained raw audio is reused without any synthesis. Preview PNGs: shared build-lesson.mjs --preview true.'}
 save(PLAN,plan)
 print(json.dumps({'plannedFiles':len(candidates),'plannedBytes':plan['deleteBytes'],'keptFiles':len(keep),'plan':str(PLAN)},ensure_ascii=False))
else:
 assert PLAN.is_file() and not REPORT.exists()
 plan=load(PLAN);assert plan['episodeKey']=='15' and plan['status']=='planned'
 # All hashes and completion state are checked again immediately before deletion.
 for row in plan['keep']+plan['delete']:
  p=Path(row['path']);assert p.is_file() and not p.is_symlink() and p.stat().st_size==row['bytes'] and sha(p)==row['sha256'],str(p)
 idle();before=shutil.disk_usage(OUT).free
 for row in plan['delete']:
  p=Path(row['path']);assert permitted(p)
  assert all(not (OUT/k).exists() for k in ['.lesson-build.lock','.gemini-generator.lock'])
  assert sha(p)==row['sha256'];p.unlink()
 for row in plan['keep']:
  p=Path(row['path']);assert p.is_file() and sha(p)==row['sha256']
 assert all(not Path(row['path']).exists() for row in plan['delete'])
 after=shutil.disk_usage(OUT).free
 result={'episodeKey':'15','status':'cleaned','completedAt':now(),'planSha256':sha(PLAN),'deletedFiles':len(plan['delete']),'deletedBytes':plan['deleteBytes'],'deletedMiB':round(plan['deleteBytes']/1024**2,3),'keptFilesVerified':len(plan['keep']),'keptHashesMatch':True,'finishedVideoSha256':sha(OUT/'egakumi-study-15.mp4'),'freeBytesBefore':before,'freeBytesAfter':after,'observedFreeDeltaBytes':after-before,'otherEpisodeFilesDeleted':False,'sourcesAndQAManifestsKept':True,'audioOnlyPlaybackAvailable':False,'fullVerificationEvidenceKept':str((EP/'verification.json').resolve()),'fullVerificationAfterCleanup':'Requires WAV reconstruction first; pre-cleanup passed evidence is retained.'}
 save(REPORT,result)
 with (EP/'production.md').open('a') as f:
  f.write(f"\n## 完成後のデータ整理\n\n{result['completedAt']}（JST）、完成状態と整理済み状態を照合した。CONTENT-PLAN.mdの追加手順に従い、第15回出力内の確認用PNGと合成済みWAVだけを削除。削除{result['deletedFiles']}件、{result['deletedBytes']:,}bytes（{result['deletedMiB']}MiB）。削除前の一覧・容量・全SHAはcleanup-plan.json、削除後の保持検査と空き容量はcleanup-result.jsonに記録した。\n\n保持{result['keptFilesVerified']}件の存在・SHA一致を確認。完成MP4、連続プレビュー、共通終了、ポスター、字幕、台本、タイムライン、書出し・検証結果、原音78件とメタデータ、requests-sent、生成設定/結果/試行記録、audio-cache、制作ソース・SVG・共通素材を保持。他回は変更していない。episode内の最終QA画像とJSONも保持した。\n\n音声単独のWAV再生は利用できない。再生ページの音声単独コントロールを外し、完成MP4で音声を再生する。復元は上記stream-build-lesson.mjsの通常ビルド（--reuse-audio trueなし）で、保持原音から再合成する。音声APIを再送しない。完全再検証も、まずWAVを復元してから実行する。整理前の合格検証結果をそのまま保持した。\n")
 print(json.dumps(result,ensure_ascii=False))
