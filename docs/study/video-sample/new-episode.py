#!/usr/bin/env python3
"""Create an episode-local draft from CONTENT-PLAN.md, without API calls."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
PLAN = HERE.parent / 'CONTENT-PLAN.md'


def episode_key(value):
    value = str(value).strip().lower()
    if value.isdigit() and 1 <= int(value) <= 23:
        return f'{int(value):02}'
    if re.fullmatch(r'(?:a[1-4]|b[1-3])', value):
        return value
    raise ValueError('Content number must be 1–23, A1–A4, or B1–B3.')


def catalog(plan=PLAN):
    result = {}
    for line in plan.read_text().splitlines():
        if not line.startswith('|'):
            continue
        cells = [s.strip() for s in line.strip().strip('|').split('|')]
        if not re.fullmatch(r'(?:[1-9]|1[0-9]|2[0-3]|A[1-4]|B[1-3])', cells[0]):
            continue
        if len(cells) not in (3, 4):
            raise ValueError('Unexpected content table columns: ' + cells[0])
        key = episode_key(cells[0])
        if key in result:
            raise ValueError('Duplicate content number in plan: ' + key)
        refs = []
        for href in re.findall(r'\]\(([^)]+)\)', cells[-1]):
            file, _, anchor = href.partition('#')
            if not re.fullmatch(r'0[1-4]-[a-z-]+\.html', file):
                raise ValueError('Unexpected source path in plan: ' + file)
            if not (plan.parent / file).is_file():
                raise ValueError('Missing source material: ' + file)
            ref = {'file': '../' + file}
            if anchor:
                ref['anchor'] = anchor
            if ref not in refs:
                refs.append(ref)
        if not refs:
            raise ValueError('Content needs at least one source link: ' + key)
        result[key] = {
            'key': key, 'number': int(key) if key.isdigit() else key.upper(),
            'title': cells[1], 'objective': cells[2] if len(cells) == 4 else None,
            'sourceTableCell': cells[-1], 'sourceReferences': refs,
        }
    if set(result) != ({f'{n:02}' for n in range(1, 24)}
                       | {f'a{n}' for n in range(1, 5)}
                       | {f'b{n}' for n in range(1, 4)}):
        raise ValueError('Plan must define content IDs 1–23, A1–A4, and B1–B3 exactly once.')
    return result


def draft(entry):
    key = entry['key']
    chapters = [
        ('opening', '今回の問い'), ('purpose', '目的と見る順番'),
        ('mechanism', '設定と補助図で仕組みを追う'), ('walkthrough', '具体例で確かめる'),
        ('check', '理解を確かめる'), ('summary', 'まとめと次の問い'),
    ]
    # These are authoring placeholders, never production narration.
    seeds = [('mako', 'opening'), ('manabu', 'opening'), ('ikumi', 'opening'),
             ('mako', 'purpose'), ('ikumi', 'mechanism'), ('manabu', 'walkthrough'),
             ('mako', 'check'), ('ikumi', 'summary')]
    turns = []
    for i, (speaker, chapter) in enumerate(seeds, 1):
        caption = f'TODO: {entry["title"]}の{dict(chapters)[chapter]}を会話として執筆する。'
        turns.append({
            'id': f'lesson{key}-turn-{i:03}-{speaker}', 'speaker': speaker,
            'chapterId': chapter, 'speech': caption, 'caption': caption,
            'subtitleLines': [caption], 'gapAfterSeconds': .14,
            'delivery': 'TODO: この場面の質問・反応・返答の意図を記述する。',
            'visual': {'layout': 'hologram' if chapter == 'opening' else 'settings',
                       'heading': entry['title'],
                       **({} if chapter == 'opening' else {
                           'viewId': f'lesson{key}-{chapter}',
                           'areaLabel': '設定メニュー',
                           'title': 'TODO: 今回確認する設定・操作',
                           'kind': 'TODO: 設定の対象',
                           'section': '概要', 'menu': ['概要'],
                           'notice': 'TODO: 質問から回答まで、この設定ビューを保持する。',
                           'fields': [{'id': 'target', 'label': 'TODO: 確認する項目',
                                       'value': 'TODO: 資料に基づく設定や状態'}],
                           'highlightFields': []
                       })},
        })
    return {
        'schemaVersion': 1, 'productionProfile': 'study-series-v1',
        'visualStyle': 'teaching-settings-v2',
        'episodeKey': key, 'episodeNumber': entry['number'], 'episodeId': key,
        'title': f'第{entry["number"]}回 {entry["title"]}', 'topic': entry['title'],
        'status': 'draft', 'cast': 'podcast-cast-gemini.json', 'speed': 1,
        'targetDurationSeconds': 900,
        'durationPolicy': '通常速度の実音声で確定。尺合わせの加速や長い無音の追加は行わない。',
        'logoIntroSeconds': 4, 'logoClosingSeconds': 8,
        'speechSettings': {'language': 'ja-JP', 'speed': 1, 'enhancePronunciation': None},
        'scope': '現行リポジトリ定義・公開設定例に基づく教材。稼働環境の取得値・実測結果ではない。',
        'learningObjectives': [entry['objective'] or 'TODO: 応用編の到達点を資料から定義する。'],
        'sourceReferences': entry['sourceReferences'],
        'chapters': [{'id': key_, 'title': title, 'goal': 'TODO: この章の到達点',
                      'sourceRefs': entry['sourceReferences']} for key_, title in chapters],
        'firstCut': {'omitTurnIds': [], 'sourceRecordingsKept': True},
        'turns': turns,
    }


def create(entry, base=HERE / 'episodes'):
    folder = base / entry['key']
    # mkdir is exclusive: two tasks cannot silently overwrite one draft.
    folder.mkdir(parents=True, exist_ok=False)
    (folder / 'diagrams').mkdir()
    (folder / 'script.json').write_text(json.dumps(draft(entry), ensure_ascii=False, indent=2) + '\n')
    objective = entry['objective'] or '元資料から、この回で説明できるようにする到達点を定義する。'
    (folder / 'brief.md').write_text(
        f'# 第{entry["number"]}回 {entry["title"]}\n\n'
        f'出典：`docs/study/CONTENT-PLAN.md`。到達点：{objective}\n\n'
        f'対応資料：{entry["sourceTableCell"]}\n\n'
        '資料リンクは`docs/study/`基準。台本のsourceReferences.fileはvideo-sample/基準。\n\n'
        '共通仕様は../../PRODUCTION-GUIDE.md、画面は../../SETTINGS-VIEW.md、配役は../../podcast-cast-gemini.jsonを読む。\n'
        '本編はteaching-settings-v2。layout:settingsを基本にし、質問と回答で同じviewId・構図を保つ。\n'
        '本編の自動ズーム・パン・pulse・模擬ポインターは使わず、赤枠は説明対象に合わせる。\n'
        '設定と補助図を対応付け、40〜70秒程度の連続した会話を動画で確認する。\n'
        'script.jsonは制作開始用の未完成原稿。TODOをすべて置き換え、約15分の対話・図・字幕を執筆する。\n'
        'レビューとプレビュー後にstatusをready-for-generationへ変更する。\n'
        '共通ファイルは変更せず、このフォルダーと担当回のoutputだけを編集する。\n'
    )
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--episode', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    entry = catalog()[episode_key(args.episode)]
    if args.dry_run:
        print(json.dumps(dict(entry, script=f'episodes/{entry["key"]}/script.json',
                              output=f'lesson-{entry["key"]}-gemini', networkRequestsMade=False), ensure_ascii=False))
        return
    folder = create(entry)
    print(json.dumps({'folder': str(folder), 'status': 'draft',
                      'next': 'Author and preview script.json before preparing speech.',
                      'networkRequestsMade': False}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, FileExistsError) as error:
        raise SystemExit('Stopped: ' + (str(error) if isinstance(error, ValueError)
                         else 'Episode folder already exists. Review and resume it; no files overwritten.'))
