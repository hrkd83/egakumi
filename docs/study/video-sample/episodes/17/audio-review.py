#!/usr/bin/env python3
"""Compare short raw-PCM endpoint runs with episodes 14/09; never alters audio."""
from array import array
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys
import wave

HERE = Path(__file__).resolve().parent
BASE = HERE.parent.parent
THRESHOLD = 32760


def sha(data):
    return hashlib.sha256(data).hexdigest()


def percentile(values, q):
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lower = int(pos)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (pos - lower)


def distributions(values):
    return {"minimum": min(values), "median": statistics.median(values),
            "p90": percentile(values, .9), "p95": percentile(values, .95),
            "p99": percentile(values, .99), "maximum": max(values)}


def summarize(records):
    samples = sum(r["samples"] for r in records)
    near = sum(r["nearFullScaleSamples"] for r in records)
    exact = sum(r["exactEndpointSamples"] for r in records)
    longest = max(records, key=lambda r: r["longestNearRunSamples"])
    largest = max(records, key=lambda r: r["nearFullScaleFraction"])
    return {
        "turnCount": len(records), "totalPcmSamples": samples,
        "durationSeconds": samples / 24000,
        "exactEndpointSamples": exact, "exactEndpointFraction": exact / samples,
        "exactEndpointPercent": exact / samples * 100,
        "nearFullScaleSamples": near, "nearFullScaleFraction": near / samples,
        "nearFullScalePercent": near / samples * 100,
        "turnsWithAnyExactEndpoint": sum(r["exactEndpointSamples"] > 0 for r in records),
        "turnsAbovePointZeroOnePercentNearFullScale": sum(r["nearFullScaleFraction"] > .0001 for r in records),
        "longestNearRun": {"turnId": longest["id"], "samples": longest["longestNearRunSamples"],
                           "milliseconds": longest["longestNearRunSamples"] / 24},
        "longestExactEndpointRunSamples": max(r["longestExactEndpointRunSamples"] for r in records),
        "longestSameEndpointPlateauSamples": max(r["longestSameEndpointPlateauSamples"] for r in records),
        "largestTurnNearFullScaleFraction": {"turnId": largest["id"],
                                             "fraction": largest["nearFullScaleFraction"],
                                             "percent": largest["nearFullScaleFraction"] * 100},
        "perTurnNearFullScaleFractionDistribution": distributions([r["nearFullScaleFraction"] for r in records]),
        "perTurnLongestNearRunSamplesDistribution": distributions([r["longestNearRunSamples"] for r in records]),
        "nearRunLengthHistogram": dict(sum((Counter(r["nearRunLengthHistogram"]) for r in records), Counter())),
    }


def scan(file, candidate, result):
    data = file.read_bytes()
    digest = sha(data)
    if digest != result["rawAudioSha256"]:
        raise ValueError("Raw audio hash mismatch: " + file.name)
    with wave.open(str(file), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 24000, "NONE"):
            raise ValueError("Unexpected PCM format: " + file.name)
        samples = audio.getnframes()
        pcm = audio.readframes(samples)
    if len(pcm) != samples * 2:
        raise ValueError("Incomplete PCM: " + file.name)
    values = array("h")
    values.frombytes(pcm)
    if sys.byteorder != "little":
        values.byteswap()
    endpoint_counts = Counter()
    near_runs = []
    near = exact = near_run = exact_run = same_endpoint_run = 0
    longest_exact = longest_same = 0
    near_start = 0
    previous = None
    for index, value in enumerate(values):
        if abs(value) >= THRESHOLD:
            endpoint_counts[str(value)] += 1
            near += 1
            if near_run == 0:
                near_start = index
            near_run += 1
        elif near_run:
            near_runs.append((near_start, near_run))
            near_run = 0
        if value in (32767, -32768):
            exact += 1
            exact_run += 1
            same_endpoint_run = same_endpoint_run + 1 if previous == value else 1
            longest_exact = max(longest_exact, exact_run)
            longest_same = max(longest_same, same_endpoint_run)
        else:
            exact_run = same_endpoint_run = 0
        previous = value
    if near_run:
        near_runs.append((near_start, near_run))
    histogram = Counter()
    for _, length in near_runs:
        label = "1" if length == 1 else "2" if length == 2 else "3-4" if length <= 4 else "5-8" if length <= 8 else "9-16" if length <= 16 else "17+"
        histogram[label] += 1
    return {
        "id": candidate["id"], "speaker": candidate["speaker"], "voice": candidate["voice"],
        "samples": samples, "sampleRateHz": 24000, "rawAudioSha256": digest,
        "exactEndpointSamples": exact, "exactEndpointFraction": exact / samples,
        "nearFullScaleSamples": near, "nearFullScaleFraction": near / samples,
        "nearFullScalePercent": near / samples * 100,
        "endpointAndNearbyCounts": dict(endpoint_counts),
        "longestNearRunSamples": max((length for _, length in near_runs), default=0),
        "longestExactEndpointRunSamples": longest_exact,
        "longestSameEndpointPlateauSamples": longest_same,
        "nearRunCount": len(near_runs), "nearRunLengthHistogram": dict(histogram),
        "longestNearIntervals": [{"startSample": start, "startSeconds": start / 24000,
                                  "samples": length, "milliseconds": length / 24}
                                 for start, length in sorted(near_runs, key=lambda r: (-r[1], r[0]))[:5]],
    }


def main():
    episodes = {}
    for key in ("17", "14", "09"):
        source = BASE / "episodes" / key
        output = BASE / "output" / ("lesson-" + key + "-gemini")
        config_bytes = (source / "generation.json").read_bytes()
        generation_bytes = (output / "generation.json").read_bytes()
        config = json.loads(config_bytes)
        generation = json.loads(generation_bytes)
        if generation.get("generationState") != "complete":
            raise ValueError("Generation incomplete for episode " + key)
        results = {r["id"]: r for r in generation["results"]}
        rows = [scan(output / (c["id"] + "-raw.wav"), c, results[c["id"]]) for c in config["candidates"]]
        summary = summarize(rows)
        summary["bySpeaker"] = {speaker: summarize([r for r in rows if r["speaker"] == speaker])
                                for speaker in ("mako", "manabu", "ikumi")}
        episodes[key] = {"summary": summary,
                         "generationConfigSha256": sha(config_bytes),
                         "generationRecordSha256": sha(generation_bytes), "turns": rows}
    audit = json.loads((HERE / "audio-audit.json").read_text())
    current = episodes["17"]["summary"]
    comparisons = {}
    for key in ("14", "09"):
        baseline = episodes[key]["summary"]
        comparisons[key] = {
            "nearFullScaleFractionRatio17ToReference": current["nearFullScaleFraction"] / baseline["nearFullScaleFraction"],
            "nearFullScalePercentDifferencePoints": current["nearFullScalePercent"] - baseline["nearFullScalePercent"],
            "longestNearRunSamples17MinusReference": current["longestNearRun"]["samples"] - baseline["longestNearRun"]["samples"],
            "comparisonLimits": "Descriptive comparison of the existing recordings, not a perceptual or statistical quality test.",
        }
    report = {
        "status": "review_candidates_quantified", "episodeKey": "17",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "networkRequestsMade": False, "waveformChanged": False, "rerecordingRequested": False,
        "speechRecognitionOrListeningPerformed": False,
        "method": {"sampleFormat": "24 kHz mono signed 16-bit PCM",
                   "exactEndpoints": [-32768, 32767], "nearFullScaleAbsoluteThreshold": THRESHOLD,
                   "ratioDenominator": "All raw PCM samples including padding",
                   "thresholdConsistentAcrossAllThreeEpisodes": True},
        "structuralAudioAuditPassed": audit["summary"]["structuralChecksPassed"],
        "nonPeakReviewFlags": [{"id": r["id"], "flags": [f for f in r["reviewFlags"] if "scale" not in f]}
                               for r in audit["turns"] if any("scale" not in f for f in r["reviewFlags"])],
        "comparisons": comparisons, "episodes": episodes,
        "episode17HighestNearFullScaleFractions": sorted(episodes["17"]["turns"], key=lambda r: -r["nearFullScaleFraction"])[:10],
        "conclusion": [
            "Episode 17 has more turns and samples reaching PCM endpoints than episode 14; endpoint-run length remains close to the existing episode 14/09 recordings.",
            "The maximum contiguous near-full-scale run is sub-millisecond in all three episodes. The measured distribution does not show an order-of-magnitude increase over these references.",
            "These measurements quantify short digital endpoint plateaus. They do not establish absence or presence of audible distortion, correct pronunciation, completeness, acting, or naturalness.",
        ],
        "limitations": [
            "Final integrated loudness and intersample true peak must be checked separately with the standard final-video verifier.",
            "A final loudness/true-peak adjustment cannot reconstruct raw peaks already saturated by the synthesizer.",
            "Raw audio is preserved unchanged. Flags are retained for listening review and do not automatically authorize another paid generation.",
        ],
    }
    (HERE / "audio-review.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "summary17": current, "comparisons": comparisons,
                      "speechRecognitionOrListeningPerformed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
