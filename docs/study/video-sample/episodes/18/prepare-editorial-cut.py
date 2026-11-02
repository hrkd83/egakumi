#!/usr/bin/env python3
"""Select recorded turns without changing any sent speech, voice, or style.

The retained recording context remains the original recording context. This is
an editorial selection, not a newly synthesized conversation. Every original
recording and its sent request stay in the episode output.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import shutil

EP = Path(__file__).resolve().parent
BASE = EP.parent.parent
OUT = BASE / 'output/lesson-18-gemini'
HISTORY = EP / 'history/full-94'
CUT = {17, 37, 39, 43, 49, 52, 58, 64, 67, 75, 79}

def digest(data):
    return hashlib.sha256(data).hexdigest()

def read(path):
    return json.loads(path.read_text())

for lock in ('.lesson-build.lock', '.gemini-generator.lock', '.conversation-preview.lock'):
    assert not (OUT / lock).exists(), 'Episode is busy: ' + lock
HISTORY.mkdir(parents=True, exist_ok=True)
for name in ('script.json', 'generation.json', 'verification.json', 'preproduction-verification.json', 'audio-audit.json', 'final-visual-review.md'):
    target = HISTORY / name
    if not target.exists():
        shutil.copy2(EP / name, target)
if not (HISTORY / 'generation-result.json').exists():
    shutil.copy2(OUT / 'generation.json', HISTORY / 'generation-result.json')

source = read(HISTORY / 'script.json')
original = read(HISTORY / 'generation.json')
results = read(HISTORY / 'generation-result.json')
assert not source['firstCut']['omitTurnIds']
assert results['generationState'] == 'complete' and len(results['results']) == 94
omitted = [t['id'] for t in source['turns'] if int(t['id'].split('-')[2]) in CUT]
assert len(omitted) == 11
source['firstCut']['omitTurnIds'] = omitted
source['firstCut']['sourceRecordingsKept'] = True
source_bytes = (json.dumps(source, ensure_ascii=False, indent=2) + '\n').encode()
(EP / 'script.json').write_bytes(source_bytes)

config = dict(original)
config['sourceScriptSha256'] = digest(source_bytes)
config['omittedTurnIds'] = omitted
config['candidates'] = [c for c in original['candidates'] if c['id'] not in omitted]
config['activeTurnCount'] = len(config['candidates'])
config['editorialAudioPolicy'] = 'Preserve the original sent text, voice and style, including original recording context; reuse hash-verified original audio only.'
(EP / 'generation.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')

spec = importlib.util.spec_from_file_location('production', BASE / 'generate-gemini-lesson.py')
production = importlib.util.module_from_spec(spec)
spec.loader.exec_module(production)
production.prepare(EP / 'generation.json', production.PROJECT, production.ACCOUNT)
by_id = {r['id']: r for r in results['results']}
for c in config['candidates']:
    old = next(o for o in original['candidates'] if o['id'] == c['id'])
    assert c == old
    raw = OUT / (c['id'] + '-raw.wav')
    assert digest(raw.read_bytes()) == by_id[c['id']]['rawAudioSha256']

report = {'sourceTurns': 94, 'editedTurns': 83, 'omittedTurnIds': omitted,
          'reason': 'Remove repeated paraphrases and reactions; retain all mechanisms, examples, three questions and conclusions within the 14–17 minute target.',
          'independentContentReview': 'All required points retained; avoid cutting turns 008 and 013 because the following response needs them.',
          'sourceRecordingsKept': True, 'retainedRequestsAndAudioUnchanged': True,
          'additionalSynthesisNeeded': False, 'recordingContextPreserved': True,
          'fullScriptSha256': digest((HISTORY / 'script.json').read_bytes()),
          'editedScriptSha256': digest(source_bytes),
          'omittedRawSeconds': round(sum(by_id[i]['rawDuration'] for i in omitted), 6)}
(EP / 'editorial-cut.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(report, ensure_ascii=False))
