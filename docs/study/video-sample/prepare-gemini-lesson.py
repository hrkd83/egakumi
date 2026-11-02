#!/usr/bin/env python3
"""Prepare one episode's fixed-voice Gemini requests locally; never calls an API.

Paths are relative to docs/study/video-sample. Complete the episode script and
set its status to ready-for-generation before preparing. An existing different config
is preserved unless --replace is explicitly supplied.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
module_spec = importlib.util.spec_from_file_location('egakumi_gemini_lesson', HERE / 'generate-gemini-lesson.py')
production = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(production)
ROLES = {'mako': 'Moderator', 'manabu': 'Product Manager', 'ikumi': 'Solution Architect'}
READY_STATUSES = {'ready-for-generation', 'first-cut-produced'}
TAG_INSTRUCTIONS = {
    'laughter': 'この台詞の笑いの指定は、理解がつながった短い柔らかな笑いとして表現する。長い笑いや大きな歓声にはしない。',
    'breathing': 'この台詞の息継ぎの指定は、考えや話題の切れ目で自然に短く息を取る。息の音を強調しない。',
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def prepare_document(script_file, cast_file, output_name):
    script_bytes, cast_bytes = script_file.read_bytes(), cast_file.read_bytes()
    source, cast = json.loads(script_bytes), json.loads(cast_bytes)
    production.validate_episode_source(source)
    production.validate_episode_identity(source, script_file, output_name)
    require(source.get('status') in READY_STATUSES,
            'Complete and review the script, then set status to ready-for-generation before preparing.')
    require(source.get('speed') == 1, 'Every episode must use normal speed.')
    require(cast.get('provider') == production.PROVIDER and cast.get('modelId') == production.MODEL_ID
            and cast.get('speech', {}).get('speed') == 1,
            'Use the selected Gemini 3.8 Flash cast and normal speed.')
    members = cast['members']
    require(set(members) == set(production.EXPECTED_VOICES), 'Expected the selected three-person cast.')
    for speaker, voice in production.EXPECTED_VOICES.items():
        member = members[speaker]
        require(member.get('voiceId') == voice and member.get('speed') == 1
                and member.get('name') == speaker.upper() and member.get('role') == ROLES[speaker]
                and member.get('status') == 'selected' and bool(member.get('style')),
                'Cast voice, name, role, selection, or delivery differs from the common production.')
    global_style = cast['speech']['globalStyle']
    require(isinstance(global_style, str) and global_style.strip(), 'Missing common speech style.')
    # Preserve the pronunciation policy of the accepted first-episode recording.
    global_style += ' カタカナで書かれた技術用語も、日本語の会話の中で明瞭に読む。相手への返答として話し、各文を毎回タイトル読みのように強調しない。'
    turns = source['turns']
    require(isinstance(turns, list) and turns, 'The authored episode needs speech turns.')
    ids = [turn['id'] for turn in turns]
    require(len(set(ids)) == len(ids), 'Duplicate turn IDs.')
    omitted_ids = source.get('firstCut', {}).get('omitTurnIds', [])
    require(len(set(omitted_ids)) == len(omitted_ids) and set(omitted_ids) <= set(ids),
            'Editorial omission IDs must be unique and name existing turns.')
    active = [turn for turn in turns if turn['id'] not in set(omitted_ids)]
    require(active, 'At least one speech turn must remain in the edit.')
    chapters = {chapter['id']: chapter for chapter in source['chapters']}
    candidates = []
    previous = None
    for number, turn in enumerate(active, 1):
        production.audio.identifier(turn['id'])
        speaker = turn['speaker']
        require(speaker in members, 'Unknown speaker: ' + speaker)
        require(turn['chapterId'] in chapters, 'Unknown chapter: ' + turn['chapterId'])
        original = turn['speech']
        require(isinstance(original, str) and original.strip() and isinstance(turn['caption'], str)
                and turn['caption'].strip(), 'Every turn needs authored speech and caption text.')
        speech = production.clean_transcript(original)
        require(speech and not re.search(r'\[[^\]]+\]', speech),
                'Use natural-language delivery instructions; unsupported bracket tags remain in the speech.')
        require(not re.search(r'\b(?:mako|manabu|ikumi)\b', speech, re.I),
                'Use マコ／マナブ／イクミ in spoken text; keep Latin names in captions.')
        member, chapter = members[speaker], chapters[turn['chapterId']]
        fallback = {
            'mako': '視聴者を代表する問いや反応として、相手へ確かめるように自然に話す。',
            'manabu': '直前の問いに答え、目的や理由を一緒に整理する。穏やかに結論を返す。',
            'ikumi': '直前の問いへの返答として、技術的な仕組みを明瞭で穏やかに説明する。',
        }[speaker]
        delivery = turn.get('delivery') or turn.get('style') or fallback
        require(isinstance(delivery, str), 'Per-turn delivery must be natural-language text.')
        stage_tags = re.findall(r'\[(laughter|breathing)\]', original)
        scene_style = '今回の場面: ' + chapter.get('title', turn['chapterId']) + '。' + delivery
        if turn['chapterId'] == 'opening':
            scene_style += ' 冒頭の顔合わせの掛け合い。相手の反応を受けて親しみのある自然な声で話す。'
        elif turn['chapterId'] == 'summary':
            scene_style += ' 今日の理解を短く確かめ、次の学びへつなぐ会話。'
        context = ('直前の' + members[previous['speaker']]['spokenName']
                   + 'の発言（文脈のみ。読み上げない）: ' + production.clean_transcript(previous['speech'])
                   if previous else 'この回の最初の問いから始める。')
        style = '\n'.join([global_style, member['style'], scene_style, context]
                          + [TAG_INSTRUCTIONS[tag] for tag in dict.fromkeys(stage_tags)]
                          + ['読み上げるのは本文のみ。文脈や指示は声に出さず、本文の言葉は追加・省略しない。'])
        candidates.append({
            'id': turn['id'], 'number': number, 'person': speaker, 'speaker': speaker,
            'label': turn['id'] + ' / ' + turn['chapterId'], 'role': member['role'],
            'voice': member['voiceId'], 'speed': 1, 'speech': speech, 'sourceSpeech': original,
            'sourceSpeechSha256': production.sha(original.encode()), 'caption': turn['caption'],
            'chapterId': turn['chapterId'], 'parameters': {'language': 'ja-JP', 'style': style},
            'paralinguisticsConvertedToStyle': stage_tags,
        })
        previous = turn
    return {
        'schemaVersion': 1, 'provider': production.PROVIDER, 'modelId': production.MODEL_ID,
        'modelKey': production.MODEL_KEY, 'apiFamily': production.FAMILY,
        'projectId': production.PROJECT, 'account': production.ACCOUNT,
        'episodeId': source.get('episodeId'), 'episodeNumber': source.get('episodeNumber'),
        'sourceScript': str(script_file.relative_to(HERE)), 'sourceScriptSha256': production.sha(script_bytes),
        'cast': str(cast_file.relative_to(HERE)), 'castSha256': production.sha(cast_bytes),
        'language': 'ja-JP', 'speed': 1, 'output': output_name, 'rawSampleRateHz': 24000,
        'sourceTurnCount': len(turns), 'activeTurnCount': len(active), 'omittedTurnIds': omitted_ids,
        'transcriptPolicy': '意味・人名の読みを保ち、Azure固有の笑い・息継ぎタグの意図は自然言語のstyleへ移す。',
        'speedPolicy': '通常速度を自然言語で指示。生成後の速度・ピッチ変換はしない。',
        'globalStyle': global_style,
        'members': {speaker: {'name': member['name'], 'spokenName': member['spokenName'],
                              'role': member['role'], 'voice': member['voiceId'],
                              'style': member['style'], 'selectionStatus': 'selected'}
                    for speaker, member in members.items()},
        'candidates': candidates,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--script', required=True, help='Episode script path relative to video-sample.')
    parser.add_argument('--config', required=True, help='Episode config path relative to video-sample.')
    parser.add_argument('--output', required=True, help='Unique simple output directory name, e.g. lesson-02-gemini.')
    parser.add_argument('--cast', default='podcast-cast-gemini.json')
    parser.add_argument('--replace', action='store_true', help='Replace this episode config after an intentional script edit.')
    args = parser.parse_args()
    script_file, cast_file = production.local_path(args.script), production.local_path(args.cast)
    config_file = production.local_path(args.config, must_exist=False)
    output_name = production.audio.local_name(args.output)
    require(config_file != script_file and config_file != cast_file,
            'The request configuration must be a separate file from the script and common cast.')
    if script_file.relative_to(HERE).parts[0] == 'episodes':
        require(config_file.parent == script_file.parent and config_file.name == 'generation.json',
                'Keep each episode configuration in its own episodes/<key>/generation.json.')
    runtime_out = HERE / 'output' / output_name
    require(not any((runtime_out / name).exists() for name in ('.gemini-generator.lock', '.lesson-build.lock')),
            'This episode is generating or rendering; preserve its current configuration until the task finishes.')
    config = prepare_document(script_file, cast_file, output_name)
    data = (json.dumps(config, ensure_ascii=False, indent=2) + '\n').encode()
    if config_file.exists() and config_file.read_bytes() != data and not args.replace:
        raise ValueError('Different configuration exists; preserve it or explicitly use --replace after reviewing the script change.')
    config_file.parent.mkdir(parents=True, exist_ok=True)
    if not config_file.exists() or config_file.read_bytes() != data:
        production.audio.write_bytes(config_file, data)
    # Validate the exact file that the paid generator will subsequently load.
    production.prepare(config_file, production.PROJECT, production.ACCOUNT)
    print(json.dumps({'configuration': str(config_file.relative_to(HERE)), 'output': output_name,
                      'requests': len(config['candidates']), 'voices': production.EXPECTED_VOICES,
                      'speed': 1, 'networkRequestsMade': False, 'authenticationRead': False}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, FileNotFoundError, TypeError) as error:
        print('Stopped: ' + str(error) if isinstance(error, ValueError)
              else f'Stopped: {type(error).__name__}. No network request was made.', file=sys.stderr)
        sys.exit(1)
