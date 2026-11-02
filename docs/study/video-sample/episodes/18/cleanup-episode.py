#!/usr/bin/env python3
"""Plan/apply the authorized post-completion cleanup for episode 18 only.

Default and --plan never delete. --apply requires an unchanged ready plan,
completed production evidence and exclusive ownership of both production locks.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys

EPISODE = Path(__file__).resolve().parent
BASE = EPISODE.parent.parent
OUT = BASE / 'output/lesson-18-gemini'
RECORDS = EPISODE / 'cleanup'
PLAN = RECORDS / 'plan.json'
RESULT = RECORDS / 'result.json'
LOCKS = (OUT / '.lesson-build.lock', OUT / '.gemini-generator.lock')
WAVS = {'lesson-narration.wav', 'lesson-mix.wav'}
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp'}

def clock():
    return datetime.now(timezone.utc).isoformat()

def read(path):
    return json.loads(path.read_text())

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for part in iter(lambda: source.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('Refusing symlink record: ' + str(path))
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def ensure_real(path):
    if path.is_symlink() or path.resolve() != path.absolute():
        raise ValueError('Refusing a symlink path: ' + str(path))
    if not path.is_file():
        raise ValueError('Required regular file missing: ' + str(path))

def snapshot(path):
    ensure_real(path)
    before = path.stat()
    value = {'path': str(path), 'bytes': before.st_size,
             'allocatedBytes': before.st_blocks * 512,
             'mtimeNs': before.st_mtime_ns, 'inode': before.st_ino,
             'device': before.st_dev, 'sha256': digest(path)}
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError('File changed while hashing: ' + str(path))
    return value

def eligible(path):
    if path.parent == OUT and path.name in WAVS:
        return 'reconstructible-composite-wav'
    if path.is_relative_to(OUT / 'previews') and path.suffix.lower() in IMAGE_SUFFIXES:
        return 'reconstructible-preview-image'
    return None

def files_under(root):
    for folder, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(d for d in directories if not (Path(folder) / d).is_symlink())
        for name in sorted(files):
            yield Path(folder) / name

def external_dependencies(paths):
    """Find explicit links to candidates from other versions/episodes.

    Generic builder/cache filenames are reconstructibility metadata. Explicit
    references from another output or episode make a candidate ineligible.
    """
    hits = {str(path): [] for path in paths}
    needles = {str(path): (str(path), path.relative_to(BASE).as_posix()) for path in paths}
    for scope in (BASE / 'episodes', BASE / 'output'):
        for folder, directories, files in os.walk(scope, followlinks=False):
            current = Path(folder)
            if current == EPISODE or current == OUT:
                # Keep history/versions/revisions in scope; omit this current edition.
                directories[:] = [d for d in directories if d in {'history', 'versions', 'revisions'}]
                continue
            directories[:] = [d for d in directories if d not in {'node_modules', 'render-work', 'previews', 'qa', 'final-frames', 'requests-sent'} and not (current / d).is_symlink()]
            for name in files:
                file = current / name
                if file.suffix.lower() not in {'.json', '.html', '.md', '.txt', '.yaml', '.yml'} or file.is_symlink():
                    continue
                content = file.read_text(errors='replace')
                for target, variants in needles.items():
                    if any(needle in content for needle in variants):
                        hits[target].append(str(file))
    return hits

def candidates():
    proposed = [p for p in files_under(OUT) if eligible(p)]
    dependencies = external_dependencies(proposed)
    selected, excluded = [], []
    for path in proposed:
        if path.is_symlink() or path.stat().st_nlink != 1 or dependencies[str(path)]:
            excluded.append({'path': str(path), 'reason': 'symlink/hardlink or explicit external dependency',
                             'externalReferences': dependencies[str(path)]})
            continue
        item = snapshot(path)
        item.update(reason=eligible(path), externalReferences=[])
        selected.append(item)
    return sorted(selected, key=lambda x: x['path']), excluded

def retained(excluded_paths):
    selected = []
    for root in (OUT, EPISODE):
        for path in files_under(root):
            if path.is_relative_to(RECORDS) or path in LOCKS or str(path) in excluded_paths:
                continue
            selected.append(path)
    profile = read(BASE / 'production-profile.json')
    names = set(profile['assetSha256']) | set(profile['commonFileSha256'])
    names |= {'production-profile.json', 'podcast-cast-gemini.json', 'prepare-gemini-lesson.py',
              'generate-gemini-lesson.py', 'make-conversation-preview.py', 'serve-episode.py',
              'settings-visual.mjs', 'SETTINGS-VIEW.md', 'PODCAST-FORMAT.md', 'package.json'}
    selected += [BASE / name for name in sorted(names)]
    selected += [BASE.parent / 'CONTENT-PLAN.md', BASE.parent / 'AGENTS.md']
    return [snapshot(path) for path in sorted(set(selected))]

def preconditions(owned_locks=()):
    errors, evidence = [], {}
    for path in (OUT, EPISODE):
        if path.is_symlink() or path.resolve() != path.absolute():
            errors.append('Scoped directory is a symlink: ' + str(path))
    for lock in LOCKS:
        if lock.exists() and lock not in owned_locks:
            errors.append('Production lock exists: ' + str(lock))
    if (OUT / '.conversation-preview.lock').exists():
        errors.append('Conversation preview extraction lock exists')
    if (OUT / 'render-work').exists():
        errors.append('render-work remains; production may be pending')
    try:
        readiness = read(EPISODE / 'cleanup-readiness.json')
        export = read(OUT / 'lesson-export.json')
        verification = read(EPISODE / 'verification.json')
        preview = read(OUT / 'conversation-preview.json')
        playback = read(EPISODE / 'playback-review.json')
        production = (EPISODE / 'production.md').read_text()
        video = OUT / 'egakumi-study-18.mp4'
        current_hash = digest(video)
        required = [
            (readiness.get('status') == 'complete', 'readiness is not complete'),
            (readiness.get('noPendingProduction') is True, 'production is pending/uncertain'),
            (readiness.get('currentWavNotUsedByOtherVersion') is True, 'WAV dependencies are not confirmed'),
            (readiness.get('audioWavDeletionPermitted') is True, 'WAV cleanup has not been reviewed'),
            (export.get('file') == video.name, 'unexpected adopted video'),
            (current_hash == export.get('sha256') == readiness.get('adoptedVideoSha256'), 'adopted MP4/hash mismatch'),
            (verification.get('status') == 'passed' and verification.get('sha256') == current_hash, 'verification is stale/failed'),
            (preview.get('status') == 'passed' and preview.get('sourceSha256') == current_hash, 'preview is stale/failed'),
            (preview.get('sourceTimelineSha256') == digest(OUT / 'lesson-timeline.json'), 'preview timeline changed'),
            (preview.get('sourceExportSha256') == digest(OUT / 'lesson-export.json'), 'preview export changed'),
            (preview.get('sha256') == digest(OUT / 'conversation-preview.mp4'), 'preview media changed'),
            (preview.get('continuousConversation') is True and preview.get('cutsWithinExcerpt') is False and preview.get('speed') == 1, 'preview continuity/speed is not verified'),
            (preview.get('verification', {}).get('status') == 'passed', 'preview decode is not verified'),
            (playback.get('status') == 'passed' and playback.get('sourceVideoSha256') == current_hash, 'playback source is stale/unverified'),
            (playback.get('previewSha256') == preview.get('sha256'), 'playback excerpt mismatch'),
            (playback.get('playbackRate') == 1 and playback.get('ended') is True, 'continuous playback is incomplete'),
            (current_hash in production and video.name in production, 'production does not identify the adopted MP4/hash'),
            ('完成' in '\n'.join(production.splitlines()[:12]) and not re.search('制作中|再開待ち|修正中|再合成中|未完成', '\n'.join(production.splitlines()[:12])), 'production header does not confirm completion'),
        ]
        errors += [message for passed, message in required if not passed]
        evidence = {'adoptedVideoSha256': current_hash, 'previewSha256': preview.get('sha256'),
                    'verification': str(EPISODE / 'verification.json'),
                    'playback': str(EPISODE / 'playback-review.json')}
        html = (OUT / 'index.html').read_text()
        if re.search(r'<audio\b', html, re.I) or '音声は完成動画で再生できます' not in html or re.search(r'(?:src|href)=["\']lesson-(?:mix|narration)\.wav["\']', html):
            errors.append('Player still offers WAV audio or does not explain video playback')
    except (FileNotFoundError, KeyError, ValueError) as error:
        errors.append('Completion evidence unavailable: ' + str(error))
    return errors, evidence

@contextmanager
def exclusive_production_locks():
    owned = []
    token = f'{os.getpid()} episode18-post-completion-cleanup {clock()}\n'.encode()
    try:
        for path in LOCKS:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.write(descriptor, token)
            os.close(descriptor)
            owned.append(path)
        yield tuple(owned)
    finally:
        for path in reversed(owned):
            if path.is_file() and not path.is_symlink() and path.read_bytes() == token:
                path.unlink()

def compare_snapshots(expected, current):
    if expected != current:
        raise ValueError('File inventory/hash/metadata changed. Create and review a new plan.')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', action='store_true', help='Create a dry plan; never deletes.')
    parser.add_argument('--apply', action='store_true', help='Apply the unchanged ready plan after completion.')
    args = parser.parse_args()
    if args.apply and args.plan:
        parser.error('Choose plan or apply.')
    if RESULT.exists():
        raise ValueError('A cleanup result already exists; preserve it and review before another cleanup.')
    if not args.apply:
        errors, evidence = preconditions()
        selected, excluded = candidates()
        inventory = retained({item['path'] for item in selected})
        document = {'schemaVersion': 1, 'episode': '18', 'createdAt': clock(),
                    'status': 'blocked' if errors else 'ready', 'errors': errors,
                    'scope': str(OUT), 'executionAuthorizedBy': 'CONTENT-PLAN.md / 完成後のデータ整理; parent applies only after final completion',
                    'completionEvidence': evidence, 'candidates': selected, 'excludedCandidates': excluded,
                    'candidateCount': len(selected), 'candidateLogicalBytes': sum(x['bytes'] for x in selected),
                    'candidateAllocatedBytes': sum(x['allocatedBytes'] for x in selected),
                    'retained': inventory, 'retainedCount': len(inventory),
                    'diskFreeBytes': shutil.disk_usage(OUT).free, 'deletionPerformed': False,
                    'retentionScope': 'All output including history; all episode source/QA files except mutable cleanup records; approved common tools/assets; source plan and instructions.'}
        write(PLAN, document)
        print(json.dumps({k: document[k] for k in ('status', 'candidateCount', 'candidateLogicalBytes', 'retainedCount', 'errors')}, ensure_ascii=False))
        return 0
    ensure_real(PLAN)
    plan_hash = digest(PLAN)
    plan = read(PLAN)
    if plan.get('status') != 'ready' or plan.get('scope') != str(OUT) or plan.get('deletionPerformed') is not False:
        raise ValueError('Only an unchanged ready dry plan may be applied.')
    with exclusive_production_locks() as owned:
        errors, evidence = preconditions(owned)
        if errors or evidence != plan['completionEvidence']:
            raise ValueError('Completion recheck failed: ' + '; '.join(errors))
        selected, excluded = candidates()
        compare_snapshots(plan['candidates'], selected)
        compare_snapshots(plan['excludedCandidates'], excluded)
        compare_snapshots(plan['retained'], retained({item['path'] for item in selected}))
        if digest(PLAN) != plan_hash:
            raise ValueError('Cleanup plan changed during recheck.')
        before_free = shutil.disk_usage(OUT).free
        result = {'status': 'started', 'startedAt': clock(), 'planSha256': plan_hash,
                  'adoptedVideoSha256': evidence['adoptedVideoSha256'], 'deleted': [],
                  'diskFreeBytesBefore': before_free}
        write(RESULT, result)  # Durable list/retention plan exists before any deletion.
        try:
            for item in selected:
                path = Path(item['path'])
                if not path.is_relative_to(OUT) or eligible(path) != item['reason']:
                    raise ValueError('Candidate escaped the authorized allowlist.')
                compare_snapshots({k: v for k, v in item.items() if k not in ('reason', 'externalReferences')}, snapshot(path))
                path.unlink()  # Only this explicitly inventoried regular file; never directories.
                result['deleted'].append(item)
                write(RESULT, result)
            retained_after = retained({item['path'] for item in selected})
            compare_snapshots(plan['retained'], retained_after)
            result.update(status='complete', completedAt=clock(), retainedHashesMatch=True,
                          retainedCount=len(retained_after), deletedCount=len(result['deleted']),
                          deletedLogicalBytes=sum(x['bytes'] for x in result['deleted']),
                          deletedAllocatedBytes=sum(x['allocatedBytes'] for x in result['deleted']),
                          diskFreeBytesAfter=shutil.disk_usage(OUT).free)
            result['observedDiskFreeDeltaBytes'] = result['diskFreeBytesAfter'] - before_free
            production = EPISODE / 'production.md'
            before_production_hash = digest(production)
            note = ('\n\n## 完成後のデータ整理\n\n'
                    + result['completedAt'] + '。完成後の整理を実施。削除は本回出力のpreviews内の再生成可能画像と、直下のlesson-narration.wav・lesson-mix.wavに限定。\n\n'
                    + f"削除{result['deletedCount']}件、論理容量{result['deletedLogicalBytes']} bytes。保持{result['retainedCount']}件の削除前後SHA-256一致を確認。実測空き容量差{result['observedDiskFreeDeltaBytes']} bytesは他処理にも影響される。詳細はcleanup/plan.jsonとcleanup/result.json。\n\n"
                    + '採用MP4、共通クロージング、ポスター、再生ページ、字幕、原稿、タイムライン、生成raw WAV/JSON、requests-sent、生成結果、audio-cache、history/full-94、episodes側QA画像と制作ソースは保持。音声単独WAV再生は整理後に利用できず、音声は完成動画で再生できます。\n\n'
                    + '復元：保持した原音・台本・設定・共通素材からローカル合成する。通常のbuild-lesson.mjsに本回の--script/--generation-config/--cast/--outputを指定し、--reuse-audio trueを付けずに実行する。音声生成APIは実行しない。容量制約時のbuild-lossless.mjsはreuse-audio必須のため、整理直後のWAV復元には使わない。WAV再合成後に共通verifyを再実行する。確認画像は同じ台本の--preview trueまたは完成MP4から再抽出する。\n')
            production.write_text(production.read_text() + note)
            result['productionRecordAppend'] = {'path': str(production), 'beforeSha256': before_production_hash,
                                               'afterSha256': digest(production), 'intentionalChange': 'Append cleanup completion and restoration instructions after retained hash verification'}
            write(RESULT, result)
        except BaseException as error:
            result.update(status='incomplete', error=str(error), stoppedAt=clock())
            write(RESULT, result)
            raise
    print(json.dumps({k: result[k] for k in ('status', 'deletedCount', 'deletedLogicalBytes', 'retainedCount', 'retainedHashesMatch')}, ensure_ascii=False))
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, FileExistsError, FileNotFoundError) as error:
        print('Stopped: ' + str(error), file=sys.stderr)
        sys.exit(1)
