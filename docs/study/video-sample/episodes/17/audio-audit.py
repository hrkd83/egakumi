#!/usr/bin/env python3
"""Inspect episode 17 raw PCM files locally; this does not assess pronunciation.

Run only after generation completes:
  python3 docs/study/video-sample/episodes/17/audio-audit.py

Writes audio-audit.json beside this script, never changes source audio, and uses
only Python's standard library. Partial generation writes status=incomplete and
exits nonzero. A review flag is a screening heuristic, not a finding of audible
damage or an instruction to synthesize the turn again. No API calls are made.
"""
from __future__ import annotations

import argparse
from array import array
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys
import wave

EPISODE = Path(__file__).resolve().parent
BASE = EPISODE.parent.parent
OUT = BASE / "output" / "lesson-17-gemini"
SILENCE_DBFS = -50.0
WINDOW_SECONDS = 0.01
MODEL = "gemini-3.8-flash-tts"
VOICES = {"mako": "Aoede", "manabu": "Charon", "ikumi": "Orus"}
ENDPOINT = "https://aiplatform.googleapis.com/v1/projects/egakumi-example/locations/global/publishers/google/models/gemini-3.8-flash-tts:generateContent"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def checked_path(relative):
    value = (OUT / relative).resolve()
    if not value.is_relative_to(OUT.resolve()):
        raise ValueError("Recording request path escapes episode output")
    return value


def incomplete_report(destination, script, config, generation, reason):
    """Replace any previous successful report so partial work cannot look complete."""
    rows = generation.get("results", [])
    report = {
        "status": "incomplete", "episodeKey": "17",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "scope": "Local raw-audio and generation-metadata screening only",
        "generationState": generation.get("generationState", "unavailable"),
        "reason": reason, "networkRequestsMade": False,
        "sourceScriptSha256": sha(EPISODE / "script.json"),
        "generationConfigSha256": sha(EPISODE / "generation.json"),
        "listeningAssessment": "not performed",
        "pronunciationAssessment": "未実施。音声認識・聴取による発音、欠落、演技、自然さの確認は行っていない。",
        "summary": {"expectedTurns": len(config.get("candidates", [])),
                    "successfulMetadataTurns": sum(r.get("status") == "succeeded" and r.get("httpStatus") == 200 for r in rows),
                    "auditedTurns": 0, "structuralChecksPassed": False,
                    "allHeuristicsClear": False},
        "turnStates": [{"id": r.get("id"), "status": r.get("status"), "httpStatus": r.get("httpStatus")} for r in rows],
    }
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(destination), "status": "incomplete", "reason": reason,
                      **report["summary"]}, ensure_ascii=False))
    return 2


def dbfs(amplitude):
    return round(20 * math.log10(amplitude / 32768), 3) if amplitude else None


def inspect_pcm(path):
    with wave.open(str(path), "rb") as audio:
        frames, rate = audio.getnframes(), audio.getframerate()
        channels, width = audio.getnchannels(), audio.getsampwidth()
        if (channels, width, rate, audio.getcomptype()) != (1, 2, 24000, "NONE"):
            raise ValueError("Expected mono 24 kHz 16-bit PCM")
        payload = audio.readframes(frames)
        if len(payload) != frames * 2 or frames == 0:
            raise ValueError("Empty or incomplete PCM payload")
    samples = array("h")
    samples.frombytes(payload)
    if sys.byteorder != "little":
        samples.byteswap()
    peak = max(abs(x) for x in samples)
    rms = math.sqrt(sum(x * x for x in samples) / frames)
    exact_full_scale = near_full_scale = 0
    near_runs = []
    run_start = None
    for index, value in enumerate(samples):
        exact_full_scale += value in (-32768, 32767)
        if abs(value) >= 32760:
            near_full_scale += 1
            if run_start is None:
                run_start = index
        elif run_start is not None:
            near_runs.append((run_start, index - run_start))
            run_start = None
    if run_start is not None:
        near_runs.append((run_start, frames - run_start))
    longest_near = max((length for _, length in near_runs), default=0)
    limit_squared = (32768 * 10 ** (SILENCE_DBFS / 20)) ** 2
    window = round(rate * WINDOW_SECONDS)
    # Keep the actual duration of the last partial window.
    windows = []
    for start in range(0, frames, window):
        block = samples[start:start + window]
        energy = sum(x * x for x in block) / len(block)
        windows.append((energy < limit_squared, len(block) / rate))
    leading = 0.0
    for quiet, duration in windows:
        if not quiet:
            break
        leading += duration
    trailing = 0.0
    for quiet, duration in reversed(windows):
        if not quiet:
            break
        trailing += duration
    total = frames / rate
    # Detect interior quiet runs separately from leading/trailing padding.
    longest_inside = run = elapsed = 0.0
    for quiet, duration in windows:
        if quiet and elapsed >= leading and elapsed < total - trailing - 1e-6:
            run += duration
            longest_inside = max(longest_inside, run)
        else:
            run = 0.0
        elapsed += duration
    quiet_seconds = sum(duration for quiet, duration in windows if quiet)
    return {
        "durationSeconds": round(total, 6), "frames": frames,
        "sampleRateHz": rate, "channels": channels, "sampleWidthBytes": width,
        "samplePeakDbfs": dbfs(peak), "rmsDbfs": dbfs(rms),
        "exactFullScaleSamples": exact_full_scale,
        "nearFullScaleSamples": near_full_scale,
        "nearFullScaleRatio": round(near_full_scale / frames, 8),
        "nearFullScaleLongestRunSamples": longest_near,
        "nearFullScaleLongestRunMilliseconds": round(longest_near / rate * 1000, 4),
        "nearFullScaleLongestIntervals": [
            {"startSeconds": round(start / rate, 6), "samples": length,
             "durationMilliseconds": round(length / rate * 1000, 4)}
            for start, length in sorted(near_runs, key=lambda interval: (-interval[1], interval[0]))[:5]
        ],
        "quietSeconds": round(quiet_seconds, 6),
        "quietWindowRatio": round(quiet_seconds / total, 6),
        "leadingQuietSeconds": round(leading, 6),
        "trailingQuietSeconds": round(trailing, 6),
        "longestInteriorQuietSeconds": round(longest_inside, 6),
        "nonPaddingDurationSeconds": round(max(0, total - leading - trailing), 6),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default="audio-audit.json",
                        help="JSON filename inside episodes/17 (default: audio-audit.json)")
    args = parser.parse_args()
    if Path(args.report).name != args.report or not args.report.endswith(".json"):
        parser.error("--report must be a JSON filename inside episodes/17")
    script = read(EPISODE / "script.json")
    config = read(EPISODE / "generation.json")
    destination = EPISODE / args.report
    try:
        generation = read(OUT / "generation.json")
    except (OSError, json.JSONDecodeError):
        return incomplete_report(destination, script, config, {}, "Completed generation record is unavailable")
    # Never label partially written generation output as a completed audit.
    if generation.get("generationState") != "complete":
        return incomplete_report(destination, script, config, generation, "Episode 17 generation is not complete")
    results = {row["id"]: row for row in generation["results"]}
    errors, rows = [], []
    candidates = config["candidates"]
    active_turns = [t for t in script["turns"] if t["id"] not in set(script.get("firstCut", {}).get("omitTurnIds", []))]
    source_turns = {t["id"]: t for t in active_turns}
    cast = read(BASE / "podcast-cast-gemini.json")
    journal = read(OUT / "gemini-attempts.json") if (OUT / "gemini-attempts.json").exists() else {}
    if [r["id"] for r in candidates] != [r["id"] for r in active_turns]:
        errors.append("Candidate IDs/order do not match active source turns")
    if len(results) != len(generation["results"]):
        errors.append("Duplicate generated turn IDs")
    if config["sourceScriptSha256"] != sha(EPISODE / "script.json"):
        errors.append("Current script hash differs from generation config")
    if generation.get("sourceScriptSha256") != sha(EPISODE / "script.json"):
        errors.append("Generated output script hash differs from current script")
    if generation.get("configurationSha256") != sha(EPISODE / "generation.json"):
        errors.append("Generated output configuration hash differs from current config")
    if [r["id"] for r in generation["results"]] != [r["id"] for r in candidates]:
        errors.append("Generated turn IDs/order do not match candidate IDs/order")
    if any(generation.get(k) != value for k, value in {
        "provider": "google-cloud", "modelId": MODEL, "endpoint": ENDPOINT,
        "projectId": "egakumi-example", "speed": 1,
        "sourceAudioSpeedChanged": False, "credentialsPersisted": False,
        "automaticRetries": 0, "sourceScript": "episodes/17/script.json",
        "configuration": "episodes/17/generation.json"}.items()):
        errors.append("Generation provider/model/endpoint/speed/provenance differs from approved production")
    if script.get("speed") != 1 or any(p.get("speed") != 1 for p in cast["members"].values()):
        errors.append("Source script/cast is not normal speed")
    if generation.get("activeTurnCount") != len(candidates):
        errors.append("Generation active-turn count differs from config")
    for candidate in candidates:
        identifier = candidate["id"]
        row = {"id": identifier, "speaker": candidate["speaker"],
               "voice": candidate["voice"], "errors": [], "reviewFlags": []}
        rows.append(row)
        try:
            meta = read(OUT / f"{identifier}-raw.json")
            generated = results[identifier]
            raw = OUT / f"{identifier}-raw.wav"
            source = source_turns[identifier]
            voice = VOICES[candidate["speaker"]]
            style = candidate["parameters"]["style"]
            request_path = checked_path(generated["requestFile"])
            request = read(request_path)
            request_hash = sha(request_path)
            if not style or candidate["voice"] != voice or candidate["speed"] != 1:
                row["errors"].append("Candidate voice/style/speed differs from approved cast")
            if cast["members"][candidate["speaker"]]["voiceId"] != voice:
                row["errors"].append("Shared cast differs from selected episode voices")
            if candidate["sourceSpeech"] != source["speech"] or candidate["sourceSpeechSha256"] != text_sha(source["speech"]):
                row["errors"].append("Candidate source speech differs from current script")
            part = request["contents"][0]["parts"][0]
            speech_config = request["generationConfig"]["speechConfig"]
            if (part.get("text") != candidate["speech"]
                    or part.get("speechMetadata", {}).get("style") != style
                    or speech_config.get("voiceConfig", {}).get("voice") != voice
                    or request["generationConfig"].get("responseModalities") != ["AUDIO"]):
                row["errors"].append("Actual sent request text/style/voice/modalities differs from candidate")
            if identifier not in journal:
                row["errors"].append("Attempt journal is missing this turn")
            for saved in (meta, generated):
                if saved.get("status") != "succeeded" or saved.get("httpStatus") != 200:
                    row["errors"].append("Generation did not report successful HTTP 200")
                if saved.get("rawAudioSha256") != sha(raw):
                    row["errors"].append("Raw audio hash mismatch")
                if saved.get("voice") != candidate["voice"] or saved.get("speed") != 1:
                    row["errors"].append("Voice or normal-speed metadata mismatch")
                if saved.get("speech") != candidate["speech"]:
                    row["errors"].append("Recorded speech metadata differs from candidate")
                if saved.get("sourceSpeech") != source["speech"] or saved.get("sourceSpeechSha256") != text_sha(source["speech"]):
                    row["errors"].append("Recorded source speech differs from current script")
                if (saved.get("style") != style or saved.get("parameters", {}).get("style") != style
                        or saved.get("styleSha256") != text_sha(style)):
                    row["errors"].append("Recorded style metadata differs from candidate")
                if saved.get("requestSha256") != request_hash:
                    row["errors"].append("Sent request hash mismatch")
                if saved.get("textSha256") != text_sha(candidate["speech"]):
                    row["errors"].append("Sent text hash mismatch")
                if saved.get("caption") != candidate["caption"] or candidate["caption"] != source["caption"]:
                    row["errors"].append("Recorded caption differs from current candidate/script")
                if saved.get("endpoint") != ENDPOINT or saved.get("modelId") != MODEL:
                    row["errors"].append("Recorded endpoint/model mismatch")
                if saved.get("waveformChanged") is not False:
                    row["errors"].append("Source waveform preservation not attested")
                if saved.get("sourceAudioSpeedChanged") is not False:
                    row["errors"].append("Source-speed preservation not attested")
            if identifier in journal and (journal[identifier].get("status") != "succeeded"
                    or journal[identifier].get("requestSha256") != request_hash
                    or journal[identifier].get("rawAudioSha256") != sha(raw)):
                row["errors"].append("Attempt journal does not match successful request/raw audio")
            metrics = inspect_pcm(raw)
            row.update(metrics)
            if abs(meta["rawDuration"] - metrics["durationSeconds"]) > 1 / 24000:
                row["errors"].append("Recorded duration differs from PCM frame count")
            text_chars = len(re.sub(r"[\s、。！？,.!?「」『』（）()]", "", candidate["speech"]))
            row["speechCharacterCount"] = text_chars
            row["charactersPerNonPaddingSecond"] = round(
                text_chars / max(0.001, metrics["nonPaddingDurationSeconds"]), 4)
            if metrics["samplePeakDbfs"] is None or metrics["nonPaddingDurationSeconds"] == 0:
                row["errors"].append("No signal above quiet threshold")
            flags = row["reviewFlags"]
            if metrics["exactFullScaleSamples"]:
                flags.append("Samples reach signed PCM full scale; inspect for clipping")
            if metrics["nearFullScaleRatio"] > 0.0001:
                flags.append("Over 0.01% of samples are near full scale")
            if metrics["quietWindowRatio"] > 0.4:
                flags.append("Over 40% of 10 ms windows are below -50 dBFS RMS")
            if metrics["leadingQuietSeconds"] > 1.5:
                flags.append("Leading quiet exceeds 1.5 seconds")
            if metrics["trailingQuietSeconds"] > 2.5:
                flags.append("Trailing quiet exceeds 2.5 seconds")
            if metrics["longestInteriorQuietSeconds"] > 1.5:
                flags.append("Interior quiet run exceeds 1.5 seconds")
            if not 1.5 <= row["charactersPerNonPaddingSecond"] <= 15:
                flags.append("Duration/text ratio outside broad screening range")
        except (OSError, ValueError, KeyError, IndexError, TypeError, wave.Error) as error:
            row["errors"].append(f"Cannot complete PCM/metadata audit: {error}")
    by_speaker = defaultdict(list)
    for row in rows:
        if not row["errors"]:
            by_speaker[row["speaker"]].append(row)
    medians = {}
    for speaker, samples in by_speaker.items():
        median = statistics.median(r["charactersPerNonPaddingSecond"] for r in samples)
        medians[speaker] = round(median, 4)
        for row in samples:
            ratio = row["charactersPerNonPaddingSecond"] / median
            row["rateRatioToSpeakerMedian"] = round(ratio, 4)
            if ratio < 0.55 or ratio > 1.8:
                row["reviewFlags"].append("Duration/text ratio is an outlier for this speaker")
    failed = [r["id"] for r in rows if r["errors"]]
    flagged = [r["id"] for r in rows if r["reviewFlags"]]
    report = {
        "status": "failed" if errors or failed else ("review_required" if flagged else "passed"),
        "episodeKey": "17", "createdAt": datetime.now(timezone.utc).isoformat(),
        "scope": "Local raw-audio and generation-metadata screening only",
        "listeningAssessment": "not performed",
        "pronunciationAssessment": "未実施。音声認識・聴取による発音、欠落、演技、自然さの確認は行っていない。",
        "networkRequestsMade": False,
        "limitations": [
            "No speech recognition or pronunciation, omissions, acting, or naturalness assessment",
            "Quiet windows include natural pauses and quiet phonemes; thresholds are review heuristics",
            "Sample peaks do not measure intersample true peaks; final mix loudness is verified separately",
            "Character rate is not Japanese mora rate and cannot by itself establish normal speaking speed",
            "Review flags do not authorize or require another synthesis request",
        ],
        "method": {"quietThresholdDbfsRms": SILENCE_DBFS,
                   "windowSeconds": WINDOW_SECONDS,
                   "expectedFormat": "mono 24 kHz 16-bit little-endian PCM"},
        "sourceScriptSha256": sha(EPISODE / "script.json"),
        "generationConfigSha256": sha(EPISODE / "generation.json"),
        "generationRecordSha256": sha(OUT / "generation.json"),
        "summary": {"expectedTurns": len(candidates), "auditedTurns": len(rows),
                    "rawDurationSeconds": round(sum(r.get("durationSeconds", 0) for r in rows), 6),
                    "failedTurnIds": failed, "reviewTurnIds": flagged,
                    "speakerMedianCharactersPerNonPaddingSecond": medians,
                    "structuralChecksPassed": not errors and not failed,
                    "allHeuristicsClear": not errors and not failed and not flagged},
        "errors": errors, "turns": rows,
    }
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(destination), "status": report["status"], **report["summary"], "errors": errors},
                     ensure_ascii=False, indent=2))
    return 2 if errors or failed else (1 if flagged else 0)


if __name__ == "__main__":
    sys.exit(main())
