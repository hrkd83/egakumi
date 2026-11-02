"""Episode-zero identity extension for the unchanged shared production tools."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

TOOLKIT = Path(__file__).resolve().parents[2]
EPISODE = TOOLKIT / 'episodes' / '00'
SCRIPT = EPISODE / 'script.json'
OUTPUT = 'lesson-00-gemini'


def load_tool(filename):
    """Load only a named, existing shared toolkit module."""
    if filename not in {
        'prepare-gemini-lesson.py', 'generate-gemini-lesson.py',
        'make-conversation-preview.py',
    }:
        raise ValueError('Unknown shared introduction tool.')
    spec = spec_from_file_location('intro_' + filename.replace('-', '_'), TOOLKIT / filename)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def enable_intro_identity(production):
    """Allow this exact introductory script and output; keep all other checks."""
    if production.HERE != TOOLKIT:
        raise ValueError('Unexpected shared production toolkit.')

    def validate_identity(source, source_file, output_name):
        production.audio.local_name(output_name)
        if SCRIPT.resolve() != SCRIPT or source_file != SCRIPT:
            raise ValueError('Introduction wrapper requires canonical episodes/00/script.json.')
        if output_name != OUTPUT:
            raise ValueError('Introduction output must be lesson-00-gemini.')
        if type(source.get('episodeNumber')) is not int or source['episodeNumber'] != 0:
            raise ValueError('Introduction episodeNumber must be integer 0.')
        if source.get('episodeKey') != '00':
            raise ValueError('Introduction episodeKey must be 00.')

    production.validate_episode_identity = validate_identity


def intro_episode_key(value):
    """The preview wrapper accepts the introduction only."""
    if str(value).strip() in {'0', '00'}:
        return '00'
    raise ValueError('Introduction preview requires --episode 0.')
