#!/usr/bin/env python3
"""ダウンロード済みの1回分の本文証跡を保護領域内で復元する。"""
from __future__ import annotations

import os
import sys

if __name__ == '__main__':
    sys.dont_write_bytecode = True
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
from uuid import UUID

from deployment.helpers.configuration import load_configuration_source
from deployment.helpers.paths import REPOSITORY, local_paths
from deployment.helpers.private_permissions import check_private_permissions
from deployment.lifecycle import protected_json


def recover(directory, checked):
    """区切りの欠落があっても、検証できた断片と終了状態を返します。"""
    capture_id = str(UUID(directory.name))
    request_id = str(UUID(directory.parent.name))
    grouped = defaultdict(list)
    sequences = []
    operation = None
    for source in sorted(directory.glob('*.json')):
        if not re.fullmatch(r'[0-9]{9}\.json', source.name):
            raise ValueError('Unexpected record name')
        source = checked(source)
        check_private_permissions(source)
        if source.stat().st_size > 64 * 1024:
            raise ValueError('Audit record is larger than the envelope limit')
        item = json.loads(source.read_text(encoding='utf-8'))
        sequence = int(source.stem)
        if (item['version'] != 1 or item['requestId'] != request_id or item['captureId'] != capture_id
                or item['sequence'] != sequence or not 0 <= item['part'] < item['parts']
                or item['part'] > sequence or not isinstance(item['data'], str)):
            raise ValueError('Audit envelope or record order differs from the selected capture')
        if operation is not None and operation != item['operationId']:
            raise ValueError('Conflicting operations in the selected capture')
        operation = item['operationId']
        sequences.append(sequence)
        grouped[sequence - item['part']].append(item)
    if not sequences:
        raise ValueError('The selected capture has no audit records')

    documents = []
    incomplete = []
    stream_text = defaultdict(list)
    for start, items in sorted(grouped.items()):
        first = items[0]
        if (len(items) != first['parts'] or any(item['part'] != index
                or any(item[key] != first[key] for key in ('parts', 'kind', 'timeUtc'))
                for index, item in enumerate(items))):
            incomplete.append(start)
            continue
        payload = json.loads(''.join(item['data'] for item in items))
        documents.append({'sequence': start, 'kind': first['kind'], 'timeUtc': first['timeUtc'], 'payload': payload})
        if first['kind'] == 'stream-segment':
            for channel, value in payload.get('text', {}).items():
                stream_text[channel].append(value)
    gap = sequences[0] != 0 or any(nxt != prev + 1 for prev, nxt in zip(sequences, sequences[1:]))
    completed = bool(documents and not gap and not incomplete and documents[0]['kind'] == 'request'
                     and documents[-1]['kind'] in ('response', 'stream-end') and
                     (documents[-1]['kind'] != 'stream-end' or documents[-1]['payload'].get('state') == 'complete'))
    return {'requestId': request_id, 'captureId': capture_id, 'operationId': operation,
            'complete': completed, 'sequenceGap': gap, 'incompleteGroups': incomplete,
            'documents': documents, 'streamText': {key: ''.join(values) for key, values in stream_text.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--records', type=Path, required=True)
    args = parser.parse_args()
    try:
        config = load_configuration_source(args.config)
        workspace = local_paths(config, REPOSITORY)
        workspace.checked(args.config)
        workspace.check_selection(config)
        root = workspace.checked(args.records)
        if not root.is_relative_to(workspace.evidence) or not root.is_dir():
            raise ValueError('Keep audit evidence within the selected environment evidence directory')
        check_private_permissions(root, directory=True)
        observed = recover(root, workspace.checked)
        protected_json(workspace.checked(root.with_suffix('.reconstructed.json')), observed)
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(1, '本文証跡の復元に失敗しました。対象環境・証跡の形式・保護権限を確認してください。\n')
    print('本文証跡を保護領域に復元しました。')


if __name__ == '__main__':
    main()
