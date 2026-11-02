"""Keep all recorded text and cue timing weights; break display lines at punctuation."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import re

P = Path(__file__).resolve().parent
file = P / 'script.json'
s = json.loads(file.read_text())
before = file.read_bytes()
spoken_before = [(t['id'], t['speech'], t.get('delivery'), t['visual']) for t in s['turns']]

def units(text):
    return sum(.58 if ord(c) < 128 else 1 for c in text)

changes = []
for turn in s['turns']:
    for index, chunk in enumerate(turn['subtitleChunks']):
        text = chunk['caption']
        if units(text) <= 38:
            lines = [text]
        else:
            choices = []
            for match in re.finditer(r'[。？！、：]\s*', text):
                first, second = text[:match.end()], text[match.end():]
                if not second or max(units(first), units(second)) > 42:
                    continue
                sentence = 0 if match[0][0] in '。？！' else 1
                choices.append((sentence, abs(units(first) - units(second)), [first, second]))
            if not choices:
                raise ValueError('Manual subtitle boundary required: ' + turn['id'])
            lines = min(choices)[2]
        assert ''.join(lines) == text
        assert not any(re.match(r'^[、。，．？！）」』】]', line) for line in lines)
        if chunk['lines'] != lines:
            changes.append({'turnId': turn['id'], 'chunk': index, 'before': chunk['lines'], 'after': lines})
        chunk['lines'] = lines
    turn['subtitleLines'] = turn['subtitleChunks'][0]['lines']

assert spoken_before == [(t['id'], t['speech'], t.get('delivery'), t['visual']) for t in s['turns']]
after = (json.dumps(s, ensure_ascii=False, indent=2) + '\n').encode()
file.write_bytes(after)
audit = {
    'updatedAt': datetime.now(timezone.utc).isoformat(),
    'beforeSha256': hashlib.sha256(before).hexdigest(),
    'afterSha256': hashlib.sha256(after).hexdigest(),
    'changedChunkCount': len(changes),
    'speechDeliveryVisualUnchanged': True,
    'captionsAndWeightsUnchanged': True,
    'maximumLineFullWidthUnits': max(units(line) for turn in s['turns'] for chunk in turn['subtitleChunks'] for line in chunk['lines']),
    'changes': changes,
}
(P / 'subtitle-reflow.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({k: v for k, v in audit.items() if k != 'changes'}, ensure_ascii=False))
