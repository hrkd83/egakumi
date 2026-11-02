#!/usr/bin/env python3
"""Check the shared production profile locally; never reads tokens or calls APIs."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--episode')
    args = parser.parse_args()
    profile = json.loads((HERE / 'production-profile.json').read_text())
    for file, expected in {**profile['assetSha256'], **profile['commonFileSha256']}.items():
        path = HERE / file
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Shared production source missing or changed: ' + file)
    cast = json.loads((HERE / profile['cast']).read_text())
    for person, expected in profile['speech']['members'].items():
        actual = cast['members'][person]
        if any(actual.get(k) != v for k, v in expected.items()):
            raise ValueError('Selected cast differs from profile: ' + person)
    tools = {name: shutil.which(name) is not None for name in ('python3', 'node', 'ffmpeg', 'ffprobe', 'gcloud')}
    if not all(tools.values()):
        raise ValueError('Missing production tools: ' + ', '.join(k for k, v in tools.items() if not v))
    checked = subprocess.run(
        ['node', '--input-type=module', '-e',
         "import{createRequire}from'node:module';"
         "const r=createRequire(new URL('./package.json',import.meta.url));r('sharp');"],
        cwd=HERE, capture_output=True, text=True,
    )
    if checked.returncode:
        raise ValueError('Study toolkit sharp dependency is unavailable. Run npm ci --prefix docs/study/video-sample --include=optional from the repository root.')
    result = {'status': 'passed', 'profile': profile['id'], 'tools': tools,
              'visualStyle': profile.get('visualStyle'),
              'sharp': True, 'hostPlatform': sys.platform,
              'availableDiskGiB': round(shutil.disk_usage(HERE).free / 1024 ** 3, 2),
              'fontEnvironment': 'Same macOS font environment as the reference' if sys.platform == 'darwin'
                                 else 'Different host: visually review Japanese fonts and all layout frames',
              'authenticationRead': False, 'networkRequestsMade': False}
    if args.episode:
        spec = importlib.util.spec_from_file_location('study_episode_scaffold', HERE / 'new-episode.py')
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        entry = helper.catalog()[helper.episode_key(args.episode)]
        result['episode'] = entry
        result['script'] = f'episodes/{entry["key"]}/script.json'
        result['output'] = f'output/lesson-{entry["key"]}-gemini'
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, FileNotFoundError) as error:
        raise SystemExit('Stopped: ' + str(error))
