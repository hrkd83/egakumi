#!/usr/bin/env python3
"""Read-only, standard-library waveform audit for A1's generated original WAVs.

This finds candidates for review; it does not recognize speech or assess delivery.
Run again after generation completes to replace the partial audit report.
"""

import argparse
from array import array
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import statistics
import sys
import unicodedata
import wave


THRESHOLDS = {
    "envelopeWindowSeconds": 0.02,
    "activeWindowRmsDbfs": -48.0,
    "nearSilentOverallRmsDbfs": -55.0,
    "leadingSilenceCandidateSeconds": 1.2,
    "trailingSilenceCandidateSeconds": 1.5,
    "internalSilenceCandidateSeconds": 2.0,
    "clipAbsoluteAmplitudeFraction": 0.9999,
    "clipConsecutiveSamples": 3,
    "clipSampleFraction": 0.0001,
    "minimumSpokenCharactersForRateCheck": 20,
    "extremeMinimumCharactersPerActiveSpanSecond": 1.5,
    "extremeMaximumCharactersPerActiveSpanSecond": 18.0,
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dbfs(fraction):
    return round(20.0 * math.log10(fraction), 3) if fraction > 0 else None


def spoken_character_count(text):
    """Count letters/numbers, including Japanese, excluding punctuation/spacing."""
    return sum(unicodedata.category(c)[0] in ("L", "N") for c in text)


def pcm_values(pcm, width):
    if width == 1:
        return [v - 128 for v in pcm]
    if width in (2, 4):
        result = array("h" if width == 2 else "i")
        result.frombytes(pcm)
        if sys.byteorder != "little":
            result.byteswap()
        return result
    if width == 3:
        return [int.from_bytes(pcm[i:i + 3], "little", signed=True)
                for i in range(0, len(pcm), 3)]
    raise ValueError("Unsupported PCM sample width: %s" % width)


def analyze_wav(data, text):
    """Decode PCM without altering it and compute envelope/clipping candidates."""
    with wave.open(io.BytesIO(data), "rb") as stream:
        channels = stream.getnchannels()
        width = stream.getsampwidth()
        rate = stream.getframerate()
        frames = stream.getnframes()
        compression = stream.getcomptype()
        pcm = stream.readframes(frames)
    if compression != "NONE" or min(channels, width, rate) <= 0:
        raise ValueError("Invalid or unsupported WAV format")
    expected_bytes = frames * channels * width
    if len(pcm) != expected_bytes:
        raise ValueError("Truncated PCM: expected %d bytes, got %d" % (expected_bytes, len(pcm)))
    values = pcm_values(pcm, width)
    full_scale = float(1 << (width * 8 - 1))
    duration = frames / rate
    flags = []
    if not values:
        return {"frames": 0, "durationSeconds": 0, "flags": ["empty_wav"]}
    square_sum = 0
    peak = 0
    clip_count = 0
    longest_clip = 0
    # Keep channel runs separate, so interleaved channels don't produce false runs.
    clip_runs = [0] * channels
    clip_boundary = full_scale * THRESHOLDS["clipAbsoluteAmplitudeFraction"]
    for i, value in enumerate(values):
        absolute = abs(value)
        square_sum += value * value
        peak = max(peak, absolute)
        channel = i % channels
        if absolute >= clip_boundary:
            clip_count += 1
            clip_runs[channel] += 1
            longest_clip = max(longest_clip, clip_runs[channel])
        else:
            clip_runs[channel] = 0
    rms = math.sqrt(square_sum / len(values)) / full_scale
    window_frames = max(1, round(rate * THRESHOLDS["envelopeWindowSeconds"]))
    active_threshold = 10 ** (THRESHOLDS["activeWindowRmsDbfs"] / 20.0)
    windows = []
    for start_frame in range(0, frames, window_frames):
        end_frame = min(frames, start_frame + window_frames)
        portion = values[start_frame * channels:end_frame * channels]
        level = math.sqrt(sum(v * v for v in portion) / len(portion)) / full_scale
        windows.append((start_frame, end_frame, level >= active_threshold))
    active = [i for i, window in enumerate(windows) if window[2]]
    if active:
        first, last = active[0], active[-1]
        leading = windows[first][0] / rate
        trailing = (frames - windows[last][1]) / rate
        active_span = duration - leading - trailing
        internal_longest = internal_run = 0
        for start, end, is_active in windows[first:last + 1]:
            if is_active:
                internal_run = 0
            else:
                internal_run += end - start
                internal_longest = max(internal_longest, internal_run)
        internal_silence = internal_longest / rate
    else:
        leading = trailing = duration
        active_span = internal_silence = 0.0
    characters = spoken_character_count(text)
    cps = characters / active_span if active_span > 0 else None
    if peak == 0:
        flags.append("all_zero_pcm")
    elif not active or rms < 10 ** (THRESHOLDS["nearSilentOverallRmsDbfs"] / 20.0):
        flags.append("near_silent_candidate")
    if (longest_clip >= THRESHOLDS["clipConsecutiveSamples"]
            or clip_count / len(values) >= THRESHOLDS["clipSampleFraction"]):
        flags.append("clipping_candidate")
    if leading > THRESHOLDS["leadingSilenceCandidateSeconds"]:
        flags.append("long_leading_silence_candidate")
    if trailing > THRESHOLDS["trailingSilenceCandidateSeconds"]:
        flags.append("long_trailing_silence_candidate")
    if internal_silence > THRESHOLDS["internalSilenceCandidateSeconds"]:
        flags.append("long_internal_silence_candidate")
    if characters >= THRESHOLDS["minimumSpokenCharactersForRateCheck"] and cps is not None:
        if cps > THRESHOLDS["extremeMaximumCharactersPerActiveSpanSecond"]:
            flags.append("extremely_short_for_text_candidate")
        elif cps < THRESHOLDS["extremeMinimumCharactersPerActiveSpanSecond"]:
            flags.append("extremely_long_for_text_candidate")
    return {
        "channels": channels, "sampleRateHz": rate, "sampleWidthBytes": width,
        "compression": compression, "frames": frames, "pcmBytes": len(pcm),
        "durationSeconds": round(duration, 6),
        "peakDbfs": dbfs(peak / full_scale), "rmsDbfs": dbfs(rms),
        "clippedSamples": clip_count,
        "clippedSampleFraction": round(clip_count / len(values), 8),
        "longestClippedRunSamplesPerChannel": longest_clip,
        "leadingSilenceSeconds": round(leading, 6),
        "trailingSilenceSeconds": round(trailing, 6),
        "longestInternalSilenceSeconds": round(internal_silence, 6),
        "activeSpanSeconds": round(active_span, 6),
        "activeWindowFraction": round(len(active) / len(windows), 6),
        "spokenCharacterCount": characters,
        "charactersPerActiveSpanSecond": round(cps, 6) if cps is not None else None,
        "flags": flags,
    }


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    here = Path(__file__).resolve().parent
    toolkit = here.parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=here / "generation.json")
    parser.add_argument("--output", type=Path, default=toolkit / "output/lesson-a1-gemini/audio-audit.json")
    args = parser.parse_args()
    config_bytes = args.config.read_bytes()
    config = json.loads(config_bytes)
    raw_directory = toolkit / "output" / config["output"]
    generation_path = raw_directory / "generation.json"
    generation = read_json(generation_path) if generation_path.exists() else {}
    records = []
    for turn in config["candidates"]:
        turn_id = turn["id"]
        metadata_path = raw_directory / (turn_id + "-raw.json")
        audio_path = raw_directory / (turn_id + "-raw.wav")
        record = {"id": turn_id, "speaker": turn["speaker"], "file": audio_path.name}
        if not metadata_path.exists() or not audio_path.exists():
            record.update(status="pending", reason="Raw WAV and successful metadata not both present")
            records.append(record)
            continue
        try:
            metadata = read_json(metadata_path)
            if metadata.get("status") != "succeeded":
                record.update(status="pending", reason="Raw metadata not marked succeeded")
                records.append(record)
                continue
            before = audio_path.stat()
            data = audio_path.read_bytes()
            after = audio_path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                record.update(status="pending", reason="WAV changed during read; rerun audit")
                records.append(record)
                continue
            digest = sha(data)
            record.update(status="audited", bytes=len(data), sha256=digest,
                          metadataSha256=sha(metadata_path.read_bytes()))
            record.update(analyze_wav(data, turn["speech"]))
            if digest != metadata.get("rawAudioSha256"):
                record["flags"].append("raw_metadata_hash_mismatch")
            if (record.get("sampleRateHz"), record.get("channels"), record.get("sampleWidthBytes")) != (config["rawSampleRateHz"], 1, 2):
                record["flags"].append("unexpected_pcm_format")
        except (OSError, ValueError, EOFError, wave.Error) as error:
            record.update(status="error", flags=["wav_or_metadata_read_error"], error=str(error))
        records.append(record)
    audited = [r for r in records if r["status"] == "audited"]
    pending = [r["id"] for r in records if r["status"] == "pending"]
    flagged = [{"id": r["id"], "flags": r["flags"]} for r in records if r.get("flags")]
    complete = len(audited) == len(records) and generation.get("generationState") == "complete"
    duration = sum(r["durationSeconds"] for r in audited)
    rates = [r["charactersPerActiveSpanSecond"] for r in audited
             if r.get("charactersPerActiveSpanSecond") is not None]
    report = {
        "schemaVersion": 1,
        "episodeKey": "a1",
        "auditedAt": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if complete else "partial",
        "result": ("review_candidates_found" if flagged else "no_machine_anomaly_candidates") if complete else "partial_audit",
        "generationStateAtStart": generation.get("generationState", "not_started"),
        "configurationSha256": sha(config_bytes),
        "sourceScriptSha256": config.get("sourceScriptSha256"),
        "waveformChanged": False,
        "recognitionOrListeningPerformed": False,
        "limitations": [
            "Signal measurements only; not speech recognition, pronunciation, word-omission, or naturalness verification.",
            "Silence uses a fixed RMS envelope threshold; quiet speech can be classified as silence.",
            "Text-rate checks are broad candidate filters, not an estimate of intelligibility or normal speaking speed.",
            "Partial reports only describe successful raw files available at audit start/read time; rerun after generation completes.",
        ],
        "thresholds": THRESHOLDS,
        "expectedTurnCount": len(records), "auditedTurnCount": len(audited),
        "pendingTurnIds": pending,
        "errorTurnCount": sum(r["status"] == "error" for r in records),
        "candidateCount": len(flagged), "candidates": flagged,
        "auditedRawDurationSeconds": round(duration, 6),
        "medianCharactersPerActiveSpanSecond": round(statistics.median(rates), 6) if rates else None,
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({k: report[k] for k in ("status", "result", "expectedTurnCount", "auditedTurnCount", "errorTurnCount", "candidateCount", "candidates", "auditedRawDurationSeconds")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
