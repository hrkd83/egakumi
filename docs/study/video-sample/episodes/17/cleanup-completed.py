#!/usr/bin/env python3
"""Remove only episode 17's listed, reproducible output derivatives after QA."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import argparse
import hashlib
import json
import os
import shutil
import stat

EPISODE = Path(__file__).resolve().parent
BASE = EPISODE.parent.parent
OUT = BASE / 'output/lesson-17-gemini'
RECORD = EPISODE / 'cleanup-record.json'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text())


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def now():
    return datetime.now(ZoneInfo('Asia/Tokyo')).isoformat()


def snapshot(path):
    require(path.is_file() and not path.is_symlink(), 'Expected a regular file: ' + str(path))
    a = path.stat()
    require(stat.S_ISREG(a.st_mode), 'Not a regular file: ' + str(path))
    result = dict(path=str(path), bytes=a.st_size, allocatedBytes=a.st_blocks * 512,
                  sha256=sha(path), identity=[a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns])
    b = path.stat()
    require(result['identity'] == [b.st_dev, b.st_ino, b.st_size, b.st_mtime_ns],
            'File changed during inspection: ' + str(path))
    return result


def unchanged(item):
    current = snapshot(Path(item['path']))
    require(current['identity'] == item['identity'] and current['sha256'] == item['sha256'],
            'Inspected file changed: ' + item['path'])


def completed():
    require(not (OUT / '.gemini-generator.lock').exists(), 'Generation is active.')
    require(not (OUT / 'render-work').exists(), 'A rendering workspace remains; review before cleanup.')
    require('完成。' in (EPISODE / 'production.md').read_text()[:200], 'Production is not recorded complete.')
    export = read(OUT / 'lesson-export.json')
    verify = read(EPISODE / 'verification.json')
    preview = read(OUT / 'conversation-preview.json')
    browser = read(EPISODE / 'browser-review.json')
    generation = read(OUT / 'generation.json')
    master = OUT / export['file']
    require(export['file'] == 'egakumi-study-17.mp4', 'Wrong master filename.')
    require(sha(master) == export['sha256'] == verify['sha256'] == preview['sourceSha256']
            == browser['sourceSha256'], 'Master and completed QA records differ.')
    require(verify['status'] == preview['status'] == browser['status'] == 'passed', 'Completed QA is required.')
    require(browser['ended'] is True and browser['playbackRate'] == 1, 'Continuous playback is not confirmed.')
    require(generation['generationState'] == 'complete'
            and all(r['status'] == 'succeeded' for r in generation['results']), 'Audio generation is incomplete.')
    return export


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    require(not RECORD.exists(), 'A cleanup record already exists; review it instead of overwriting.')
    require(not (OUT / '.lesson-build.lock').exists(), 'Rendering is active.')
    export = completed()
    script = read(EPISODE / 'script.json')
    names = ['cast-establish.png'] + [turn['id'] + '.png' for turn in script['turns']]
    targets = [OUT / 'previews' / name for name in names]
    targets += [OUT / 'lesson-narration.wav', OUT / 'lesson-mix.wav']
    require(len(targets) == 78 and len(set(targets)) == 78, 'Unexpected deletion selection.')
    for path in targets:
        require(path.resolve().is_relative_to(OUT.resolve()), 'Deletion path escapes this episode output.')
        require(path.stat().st_nlink == 1, 'A candidate has another hard link; keep it.')
    candidates = [snapshot(path) for path in targets]
    protected = sorted(set(p for p in OUT.rglob('*') if p.is_file() and p not in targets)
                       | set(p for p in EPISODE.rglob('*') if p.is_file()
                             and p.name not in {'production.md', 'cleanup-record.json'}))
    # Also protect all shared source and asset dependencies named by the profile.
    profile = read(BASE / 'production-profile.json')
    for name, expected in (profile['commonFileSha256'] | profile['assetSha256']).items():
        require(sha(BASE / name) == expected, 'A shared dependency differs: ' + name)
        protected.append(BASE / name)
    protected += [BASE / 'production-profile.json']
    retained = [snapshot(p) for p in sorted(set(protected))]
    planned = sum(item['bytes'] for item in candidates)
    if not args.apply:
        print(json.dumps(dict(status='inspected', candidateFiles=len(candidates),
                              candidateBytes=planned, retainedFiles=len(retained)), ensure_ascii=False))
        return
    # Take the normal per-episode build exclusion only after confirming that
    # no production process owns it. This also blocks a generator starting now.
    lock = OUT / '.lesson-build.lock'
    descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(descriptor, str(os.getpid()).encode())
    lock_inode = os.fstat(descriptor).st_ino
    os.close(descriptor)
    record = dict(schemaVersion=1, episodeKey='17', status='planned', createdAt=now(),
                  policy='docs/study/CONTENT-PLAN.md#完成後のデータ整理',
                  scope=str(OUT), completedMasterSHA256=export['sha256'],
                  candidateFiles=candidates, candidateBytes=planned,
                  retainedFiles=retained, deletedPaths=[],
                  diskFreeBytesBefore=shutil.disk_usage(OUT).free,
                  mutableAuditFiles=['production.md', 'cleanup-record.json'],
                  restoreAudioCommand='node docs/study/video-sample/episodes/17/restore-audio.mjs',
                  restoreImagesCommand='node docs/study/video-sample/build-lesson.mjs --script episodes/17/script.json --output lesson-17-gemini --preview true',
                  additionalSynthesisRequests=0)
    def save():
        temporary = RECORD.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
        os.replace(temporary, RECORD)
    try:
        completed()
        for item in candidates + retained:
            unchanged(item)
        save()  # Full deletion list, sizes, hashes and retained paths precede deletion.
        for item in candidates:
            require(not (OUT / '.gemini-generator.lock').exists(), 'Generation started; stop cleanup.')
            require(lock.stat().st_ino == lock_inode and lock.read_text() == str(os.getpid()),
                    'Cleanup lost its episode exclusion.')
            unchanged(item)
            Path(item['path']).unlink()
            record['deletedPaths'].append(item['path'])
        for item in retained:
            unchanged(item)
        require(all(not p.exists() for p in targets), 'A deletion target remains.')
        record.update(status='completed', completedAt=now(), retainedHashesVerified=True,
                      deletedFiles=len(record['deletedPaths']), deletedBytes=planned,
                      diskFreeBytesAfter=shutil.disk_usage(OUT).free)
        record['diskFreeBytesDelta'] = record['diskFreeBytesAfter'] - record['diskFreeBytesBefore']
        save()
        print(json.dumps({k: record[k] for k in ['status', 'deletedFiles', 'deletedBytes',
                                                'retainedHashesVerified', 'diskFreeBytesDelta']}, ensure_ascii=False))
    except BaseException as error:
        record.update(status='interrupted', error=str(error), updatedAt=now())
        save()
        raise
    finally:
        if lock.exists() and lock.stat().st_ino == lock_inode and lock.read_text() == str(os.getpid()):
            lock.unlink()


if __name__ == '__main__':
    main()
