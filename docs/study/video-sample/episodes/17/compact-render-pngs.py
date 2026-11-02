#!/usr/bin/env python3
"""Losslessly compact stable episode-17 render PNGs; stop gracefully with SIGTERM.

The CLI monitors only output/lesson-17-gemini/render-work. It never deletes
frames, changes their decoded image data, or touches another episode.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import stat
import struct
import tempfile
import time
import zlib

EPISODE = Path(__file__).resolve().parent
RENDER_ROOT = EPISODE.parent.parent / 'output' / 'lesson-17-gemini' / 'render-work'
SIGNATURE = b'\x89PNG\r\n\x1a\n'
STABLE_SECONDS = 2.0
stopping = False


def fingerprint(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns, value.st_mode)


def regular_fingerprint(path: Path) -> tuple[int, ...]:
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode):
        raise ValueError('Source is not a regular file.')
    return fingerprint(value)


def chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    """Validate all chunk lengths/CRCs and a complete, static PNG structure."""
    if not data.startswith(SIGNATURE):
        raise ValueError('Invalid PNG signature.')
    result = []
    offset = len(SIGNATURE)
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError('Incomplete PNG chunk header.')
        length = struct.unpack_from('>I', data, offset)[0]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError('Incomplete PNG chunk payload.')
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack_from('>I', data, offset + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            raise ValueError('PNG chunk CRC mismatch.')
        if not all(65 <= b <= 90 or 97 <= b <= 122 for b in kind):
            raise ValueError('Invalid PNG chunk type.')
        result.append((kind, payload))
        offset = end
        if kind == b'IEND':
            if payload or offset != len(data):
                raise ValueError('Invalid IEND or data after IEND.')
            break
    kinds = [kind for kind, _ in result]
    if (not result or result[0][0] != b'IHDR' or len(result[0][1]) != 13
            or kinds.count(b'IHDR') != 1 or kinds.count(b'IEND') != 1
            or result[-1][0] != b'IEND' or b'IDAT' not in kinds):
        raise ValueError('Incomplete PNG structure.')
    if any(kind in kinds for kind in (b'acTL', b'fcTL', b'fdAT')):
        raise ValueError('Animated PNG is outside the static-frame scope.')
    idat_positions = [i for i, kind in enumerate(kinds) if kind == b'IDAT']
    if idat_positions != list(range(idat_positions[0], idat_positions[-1] + 1)):
        raise ValueError('Noncontiguous PNG IDAT chunks.')
    return result


def unpack_idat(parts: list[tuple[bytes, bytes]]) -> bytes:
    inflater = zlib.decompressobj()
    raw = inflater.decompress(b''.join(payload for kind, payload in parts if kind == b'IDAT'))
    raw += inflater.flush()
    if not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
        raise ValueError('Incomplete or trailing IDAT zlib stream.')
    return raw


def pack_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)


def recompress(data: bytes) -> bytes:
    source_chunks = chunks(data)
    raw = unpack_idat(source_chunks)
    compressed = zlib.compress(raw, level=9)
    # Both before writing and after assembling the complete PNG, compare every
    # decompressed byte. This preserves PNG filters as well as the pixel data.
    if zlib.decompress(compressed) != raw:
        raise ValueError('IDAT round-trip verification failed.')
    assembled = [SIGNATURE]
    written = False
    for kind, payload in source_chunks:
        if kind == b'IDAT':
            if not written:
                assembled.append(pack_chunk(kind, compressed))
                written = True
        else:
            assembled.append(pack_chunk(kind, payload))
    result = b''.join(assembled)
    verified = chunks(result)
    if unpack_idat(verified) != raw:
        raise ValueError('Final PNG decompressed data differs.')
    if [(k, p) for k, p in verified if k != b'IDAT'] != [(k, p) for k, p in source_chunks if k != b'IDAT']:
        raise ValueError('Non-IDAT chunk changed.')
    return result


@dataclass
class Result:
    status: str
    before: int
    after: int
    current_fingerprint: tuple[int, ...]


def compact_file(path: Path, expected: tuple[int, ...], allowed_root: Path) -> Result:
    """Compact one caller-confirmed stable PNG using a same-directory rename."""
    resolved_root = allowed_root.resolve(strict=True)
    if not path.resolve(strict=True).is_relative_to(resolved_root) or path.suffix != '.png':
        raise ValueError('PNG is outside the permitted render directory.')
    if regular_fingerprint(path) != expected:
        return Result('changed', 0, 0, regular_fingerprint(path))
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as source:
        original_stat = os.fstat(source.fileno())
        if fingerprint(original_stat) != expected:
            return Result('changed', 0, 0, regular_fingerprint(path))
        data = source.read()
        if fingerprint(os.fstat(source.fileno())) != expected or regular_fingerprint(path) != expected:
            return Result('changed', 0, 0, regular_fingerprint(path))
    if len(data) != original_stat.st_size:
        raise ValueError('PNG source size differs from the complete read.')
    packed = recompress(data)
    if len(packed) >= len(data):
        return Result('unchanged', len(data), len(data), regular_fingerprint(path))
    temp_path = None
    try:
        handle, temp_name = tempfile.mkstemp(prefix='.' + path.name + '.compact-', suffix='.tmp', dir=path.parent)
        temp_path = Path(temp_name)
        with os.fdopen(handle, 'wb') as target:
            os.fchmod(target.fileno(), stat.S_IMODE(original_stat.st_mode))
            target.write(packed)
            target.flush()
            os.fsync(target.fileno())
        os.utime(temp_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns), follow_symlinks=False)
        # Recheck immediately before replacement. The two-second stable-file
        # gate also ensures the builder has finished its normal write.
        if regular_fingerprint(path) != expected:
            return Result('changed', len(data), len(data), regular_fingerprint(path))
        os.replace(temp_path, path)
        temp_path = None
        return Result('compacted', len(data), len(packed), regular_fingerprint(path))
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def stop(_number, _frame):
    global stopping
    stopping = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--poll-seconds', type=float, default=1.0)
    args = parser.parse_args()
    if not 0.2 <= args.poll_seconds <= 10:
        parser.error('--poll-seconds must be between 0.2 and 10.')
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    observed = {}
    processed = set()
    count = saved = 0
    last_report = time.monotonic()
    print(json.dumps({'watch':str(RENDER_ROOT),'stableSeconds':STABLE_SECONDS,'losslessZlibLevel':9}), flush=True)
    while not stopping:
        now = time.monotonic()
        if RENDER_ROOT.is_symlink():
            raise ValueError('The fixed render directory must not be a symlink.')
        for path in sorted(RENDER_ROOT.rglob('*.png')) if RENDER_ROOT.exists() else []:
            if stopping:
                break
            current = None
            try:
                current = regular_fingerprint(path)
                if (path, current) in processed:
                    continue
                previous = observed.get(path)
                if previous is None or previous[0] != current:
                    observed[path] = (current, now)
                    continue
                if now - previous[1] < STABLE_SECONDS:
                    continue
                result = compact_file(path, current, RENDER_ROOT)
                if result.status == 'changed':
                    observed[path] = (result.current_fingerprint, time.monotonic())
                    continue
                processed.add((path, result.current_fingerprint))
                observed.pop(path, None)
                if result.status == 'compacted':
                    count += 1
                    saved += result.before - result.after
            except FileNotFoundError:
                observed.pop(path, None)
            except (OSError, ValueError, zlib.error) as error:
                # A malformed/inaccessible fingerprint is reported once, then
                # retried only if the file changes. Never replace it.
                if current is not None:
                    processed.add((path, current))
                print(json.dumps({'skipped':str(path.relative_to(RENDER_ROOT)),'reason':str(error)}), flush=True)
        if time.monotonic() - last_report >= 30:
            print(json.dumps({'compactedFiles':count,'savedMiB':round(saved/1048576,2)}), flush=True)
            last_report = time.monotonic()
        # Short sleeps allow prompt graceful signal handling.
        deadline = time.monotonic() + args.poll_seconds
        while not stopping and time.monotonic() < deadline:
            time.sleep(min(0.1, max(0, deadline-time.monotonic())))
    print(json.dumps({'stopped':True,'compactedFiles':count,'savedMiB':round(saved/1048576,2)}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
