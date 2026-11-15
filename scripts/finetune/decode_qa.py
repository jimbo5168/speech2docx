"""Reference-free speech-density and repeated-phrase QA experiments.

Thresholds match the round-five measurements. Call ``run_qa`` with segment rows,
first-pass hypotheses, and a decode(row, start, end, kind) callback. Rows need
id/audio/start/end only. References never enter the selection rules.
"""

import difflib
import re
import statistics
import subprocess

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from transcribe import find_runaway_repeat

FFMPEG = "/opt/homebrew/bin/ffmpeg"


def compact(text):
    return re.sub(r"[^\u3400-\u9fff0-9A-Za-z]", "", text)


def repeat(text, min_times=3, sensitive=False):
    text = compact(text)
    if sensitive:
        single = re.search(r"(.)\1{11,}", text)
        if single:
            return single[1], len(single[0])
        hit = find_runaway_repeat(text, min_times=2)
        return hit if hit and len(hit[0]) >= 6 and len(hit[0]) * hit[1] >= 16 else None
    hit = find_runaway_repeat(text, min_times=min_times)
    return hit if hit and len(hit[0]) * hit[1] >= 18 else None


def speech_regions(audio, duration):
    process = subprocess.run(
        [FFMPEG, "-nostdin", "-i", str(audio), "-af",
         "silencedetect=noise=-32dB:d=0.20", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    silences = []
    start = None
    for line in process.stderr.splitlines():
        beginning = re.search(r"silence_start: ([\d.]+)", line)
        ending = re.search(r"silence_end: ([\d.]+)", line)
        if beginning:
            start = float(beginning[1])
        if ending and start is not None:
            silences.append((start, min(duration, float(ending[1]))))
            start = None
    if start is not None:
        silences.append((start, duration))
    regions = []
    position = 0
    for start, end in silences:
        if start > position:
            regions.append((position, start))
        position = max(position, end)
    if position < duration:
        regions.append((position, duration))
    return regions


def windows(regions, duration):
    """Split at silence midpoints, targeting seven seconds with 150 ms overlap."""
    cuts = [(left[1] + right[0]) / 2 for left, right in zip(regions, regions[1:])]
    result = []
    start = 0
    while start < duration - 0.1:
        eligible = [cut for cut in cuts if start + 3 <= cut <= start + 10]
        end = min(eligible, key=lambda cut: abs(cut - start - 7)) if eligible else min(start + 8, duration)
        if duration - end < 3 and duration - start <= 10:
            end = duration
        result.append((max(0, start - 0.15), min(duration, end + 0.15)))
        start = end
    return result


def join_overlap(parts):
    result = ""
    for part in parts:
        if not result:
            result = part
            continue
        left, right = compact(result), compact(part)
        overlap = 0
        for length in range(min(20, len(left), len(right)), 2, -1):
            if left[-length:] == right[:length]:
                overlap = length
                break
        if overlap:
            count = 0
            cut = 0
            for cut, character in enumerate(part, 1):
                count += bool(compact(character))
                if count >= overlap:
                    break
            part = part[cut:]
        result += part
    return result


def assess(original, short, reason, sensitive=False):
    original_chars, short_chars = compact(original), compact(short)
    if not short_chars or repeat(short, sensitive=sensitive):
        return original, False
    similarity = difflib.SequenceMatcher(None, original_chars, short_chars, autojunk=False).ratio()
    if reason == "repeat":
        old = repeat(original, sensitive=sensitive)
        if old and short_chars.count(old[0]) < old[1]:
            return short, similarity < 0.60
        return original, False
    if (len(short_chars) > len(original_chars)
            and len(short_chars) <= max(len(original_chars) * 1.45, len(original_chars) + 12)
            and similarity >= 0.65):
        return short, False
    return original, similarity < 0.60 and abs(len(original_chars) - len(short_chars)) >= 12


def run_qa(rows, hyps, decode, mode="combined"):
    """Select text without references and mark unresolved large disagreements."""
    sensitive = mode.endswith("_sensitive")
    mode = mode.removesuffix("_sensitive")
    regions = [speech_regions(row["audio"], row["end"] - row["start"]) for row in rows]
    rates = [
        len(compact(hypothesis)) / max(sum(end - start for start, end in speech), 0.2)
        for hypothesis, speech in zip(hyps, regions) if compact(hypothesis)
    ]
    median = statistics.median(rates) if rates else 3.0
    output, events = [], []
    for row, hypothesis, speech_regions_for_row in zip(rows, hyps, regions):
        duration = row["end"] - row["start"]
        speech_duration = sum(end - start for start, end in speech_regions_for_row)
        rate = len(compact(hypothesis)) / max(speech_duration, 0.2)
        reason, tail = None, ""
        if mode in ("omission", "combined") and speech_duration >= 2:
            if rate < median * 0.68 or len(compact(hypothesis)) < max(3, speech_duration):
                reason = "omission"
            # ASR timestamps cover entire chunks. Probe the audio ending separately.
            if not reason and rate < median * 0.90 and speech_regions_for_row:
                end = speech_regions_for_row[-1][1]
                tail = decode(row, max(0, end - 4.5), end, "tail")
                tail_chars = compact(tail)
                ending_chars = compact(hypothesis)[-max(20, len(tail_chars) * 2):]
                match = difflib.SequenceMatcher(None, tail_chars, ending_chars, autojunk=False).find_longest_match()
                if len(tail_chars) >= 6 and match.size < min(5, len(tail_chars) * 0.6):
                    reason = "early_end"
        if mode in ("repetition", "combined") and repeat(hypothesis, sensitive=sensitive):
            reason = "repeat"
        chosen, uncertain = hypothesis, False
        if reason:
            short = join_overlap([
                decode(row, start, end, "short")
                for start, end in windows(speech_regions_for_row, duration)
            ])
            chosen, uncertain = assess(hypothesis, short, reason, sensitive)
            if uncertain:
                chosen += "〔待回听〕"
            events.append(dict(
                id=row["id"], reason=reason, cps=rate, median=median, tail=tail,
                original=hypothesis, redecode=short, chosen=chosen, uncertain=uncertain,
            ))
        output.append(chosen)
    return output, events
