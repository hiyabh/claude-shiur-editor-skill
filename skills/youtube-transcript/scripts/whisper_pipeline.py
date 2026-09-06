"""
Local Whisper transcription of a long audio/video file, built to survive both
low memory and mid-run crashes.

Usage (also importable — get_transcript.py calls run_pipeline directly):
    python whisper_pipeline.py <audio_or_video> [--lang he] [--model NAME]
                               [--fast] [--outdir DIR] [--workdir DIR]

Pipeline
--------
1. ffmpeg -> 16 kHz mono WAV (what Whisper wants; avoids a resample per chunk).
2. ffmpeg silencedetect -> cut points, so chunks never split mid-sentence.
3. Chunk length chosen from FREE RAM, not total RAM (see pick_chunk_seconds).
4. Each chunk transcribed by a fresh `transcribe_chunk.py` process.
5. A `.done` marker per chunk makes the whole run resumable.
6. Merge by global offset -> SRT + timestamped text + flowing text.

Why chunking at all: Whisper's feature extractor builds the STFT for the WHOLE
input up front. A 26-minute chunk needs a ~450 MB complex128 array, which can
fail even with several GB nominally free.
"""
import argparse
import ctypes
import json
import os
import re
import subprocess
import sys
import time

# Claude Code captures stdout through a pipe. On Windows that makes Python fall
# back to the ANSI code page, so printing a Hebrew path dies with
# UnicodeEncodeError. Force UTF-8 before we print anything.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHUNK_WORKER = os.path.join(SCRIPT_DIR, "transcribe_chunk.py")

HEBREW_MODEL = "ivrit-ai/whisper-large-v3-turbo-ct2"
GENERIC_MODEL = "Systran/faster-whisper-large-v3"
FAST_MODEL = "Systran/faster-whisper-medium"
HEBREW_LANGS = {"he", "iw", "heb", "hebrew"}

SILENCE_NOISE_DB = "-32dB"
SILENCE_MIN_DURATION = "0.6"
PARAGRAPH_GAP_SECONDS = 2.5
CHUNK_RETRIES = 3
RETRY_COOLDOWN_SECONDS = 20
SEAM_WARN_SECONDS = 3.0


# --------------------------------------------------------------------------
# environment probing
# --------------------------------------------------------------------------

class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def available_ram_gb():
    """Free physical RAM in GB. Total RAM is the wrong number to plan with -
    a 32 GB box routinely has only 8-12 GB actually free."""
    if not sys.platform.startswith("win"):
        try:
            return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1e9
        except (ValueError, AttributeError, OSError):
            return 8.0
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return status.ullAvailPhys / 1e9


def pick_chunk_seconds(ram_gb):
    if ram_gb >= 16:
        return 600
    if ram_gb >= 8:
        return 300
    return 180


def pick_threads():
    return max(2, min(16, os.cpu_count() or 4))


def pick_model(lang, fast=False, override=None):
    if override:
        return override
    if fast:
        return FAST_MODEL
    if (lang or "").lower() in HEBREW_LANGS:
        return HEBREW_MODEL
    return GENERIC_MODEL


# --------------------------------------------------------------------------
# audio preparation
# --------------------------------------------------------------------------

def _run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kwargs)


def probe_duration(path):
    proc = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", path])
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"ffprobe failed on {path}: {proc.stderr.strip()[:300]}")
    return float(proc.stdout.strip().splitlines()[0])


def to_wav16k(src, dst):
    proc = _run(["ffmpeg", "-y", "-v", "error", "-i", src,
                 "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", dst])
    if proc.returncode != 0 or not os.path.exists(dst):
        raise RuntimeError(f"ffmpeg failed to convert audio: {proc.stderr.strip()[:300]}")
    return dst


_SILENCE_RE = re.compile(r"silence_start:\s*([0-9.]+)")


def detect_silences(wav_path):
    proc = _run(["ffmpeg", "-v", "info", "-i", wav_path, "-af",
                 f"silencedetect=n={SILENCE_NOISE_DB}:d={SILENCE_MIN_DURATION}",
                 "-f", "null", "-"])
    return [float(m) for m in _SILENCE_RE.findall(proc.stderr or "")]


def plan_splits(duration, silences, target_seconds):
    """Boundaries snapped to the nearest detected silence, so no chunk edge
    lands mid-sentence. Falls back to even cuts when no silence was found."""
    count = max(1, round(duration / target_seconds))
    if count == 1:
        return [(0.0, duration)]
    points = [0.0]
    for index in range(1, count):
        ideal = duration * index / count
        if silences:
            ideal = min(silences, key=lambda s: abs(s - ideal))
        # Never let a snapped point run backwards past the previous boundary.
        points.append(max(ideal, points[-1] + 1.0))
    points.append(duration)
    return [(points[i], points[i + 1]) for i in range(count)]


def cut_chunks(wav_path, splits, workdir):
    paths = []
    for index, (start, end) in enumerate(splits):
        out = os.path.join(workdir, f"chunk{index:03d}.wav")
        if not os.path.exists(out):
            proc = _run(["ffmpeg", "-y", "-v", "error", "-i", wav_path,
                         "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
                         "-c", "copy", out])
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg failed cutting chunk {index}: "
                                   f"{proc.stderr.strip()[:300]}")
        paths.append((out, start))
    return paths


# --------------------------------------------------------------------------
# transcription
# --------------------------------------------------------------------------

def transcribe_chunks(chunks, workdir, model, lang, threads, on_progress=None):
    """One subprocess per chunk. `.done` markers make the run resumable."""
    total = len(chunks)
    for index, (chunk_path, offset) in enumerate(chunks):
        part = os.path.join(workdir, f"part{index:03d}.jsonl")
        marker = part + ".done"
        if os.path.exists(marker):
            if on_progress:
                on_progress(index + 1, total, "skipped (already done)")
            continue
        last_error = ""
        for attempt in range(1, CHUNK_RETRIES + 1):
            proc = _run([sys.executable, CHUNK_WORKER, chunk_path, part,
                         f"{offset:.3f}", model, lang or "", str(threads)])
            if proc.returncode == 0 and os.path.exists(part):
                with open(marker, "w", encoding="utf-8") as fh:
                    fh.write("ok")
                if on_progress:
                    on_progress(index + 1, total, proc.stdout.strip())
                break
            last_error = (proc.stderr or proc.stdout or "").strip()[-300:]
            if attempt < CHUNK_RETRIES:
                time.sleep(RETRY_COOLDOWN_SECONDS)
        else:
            raise RuntimeError(f"chunk {index} failed after {CHUNK_RETRIES} "
                               f"attempts: {last_error}")


def merge_parts(workdir, count):
    segments = []
    for index in range(count):
        part = os.path.join(workdir, f"part{index:03d}.jsonl")
        if not os.path.exists(part):
            continue
        with open(part, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    segments.append(json.loads(line))
    segments.sort(key=lambda s: s["start"])
    return [s for s in segments if s.get("text")]


def seam_report(segments, splits):
    """Largest gap across a chunk boundary — the check that proves no speech
    was lost at a cut."""
    worst = 0.0
    for start, _ in splits[1:]:
        before = [s for s in segments if s["end"] <= start + 0.1]
        after = [s for s in segments if s["start"] >= start - 0.1]
        if before and after:
            worst = max(worst, after[0]["start"] - before[-1]["end"])
    return worst


# --------------------------------------------------------------------------
# output formats
# --------------------------------------------------------------------------

def _hms(seconds):
    return (f"{int(seconds // 3600):02d}:{int(seconds % 3600 // 60):02d}:"
            f"{int(seconds % 60):02d}")


def _srt_time(seconds):
    return f"{_hms(seconds)},{int((seconds % 1) * 1000):03d}"


def write_outputs(segments, base_path):
    srt_path = base_path + ".srt"
    with open(srt_path, "w", encoding="utf-8") as fh:
        for number, seg in enumerate(segments, 1):
            fh.write(f"{number}\n{_srt_time(seg['start'])} --> "
                     f"{_srt_time(seg['end'])}\n{seg['text']}\n\n")

    stamped_path = f"{base_path} - עם חותמות זמן.txt"
    with open(stamped_path, "w", encoding="utf-8") as fh:
        for seg in segments:
            fh.write(f"[{_hms(seg['start'])}] {seg['text']}\n")

    flowing_path = f"{base_path} - טקסט רצוף.txt"
    with open(flowing_path, "w", encoding="utf-8") as fh:
        buffer, previous_end = [], None
        for seg in segments:
            if previous_end is not None and seg["start"] - previous_end > PARAGRAPH_GAP_SECONDS and buffer:
                fh.write(" ".join(buffer) + "\n\n")
                buffer = []
            buffer.append(seg["text"])
            previous_end = seg["end"]
        if buffer:
            fh.write(" ".join(buffer) + "\n")

    return srt_path, stamped_path, flowing_path


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

def run_pipeline(source, base_path, lang="he", model=None, fast=False,
                 workdir=None, on_progress=None, on_stage=None):
    """Transcribe `source` and write outputs beside `base_path`.
    Returns dict with paths, segment count and the worst seam gap."""
    workdir = workdir or (base_path + ".work")
    os.makedirs(workdir, exist_ok=True)
    say = on_stage or (lambda *_: None)

    wav_path = os.path.join(workdir, "audio16k.wav")
    if not os.path.exists(wav_path):
        say("converting audio to 16kHz mono WAV")
        to_wav16k(source, wav_path)

    duration = probe_duration(wav_path)
    ram_gb = available_ram_gb()
    target = pick_chunk_seconds(ram_gb)
    threads = pick_threads()
    model_name = pick_model(lang, fast=fast, override=model)
    say(f"duration {duration / 60:.1f} min | free RAM {ram_gb:.1f} GB | "
        f"chunk {target // 60} min | threads {threads} | model {model_name}")

    say("detecting silence points for clean cuts")
    silences = detect_silences(wav_path)
    splits = plan_splits(duration, silences, target)
    say(f"{len(silences)} silence points -> {len(splits)} chunks")

    chunks = cut_chunks(wav_path, splits, workdir)
    transcribe_chunks(chunks, workdir, model_name, lang, threads, on_progress)

    segments = merge_parts(workdir, len(splits))
    if not segments:
        raise RuntimeError("transcription produced no segments")
    srt_path, stamped_path, flowing_path = write_outputs(segments, base_path)

    return {
        "srt_path": srt_path,
        "timestamped_path": stamped_path,
        "flowing_path": flowing_path,
        "segments": len(segments),
        "words": sum(len(s["text"].split()) for s in segments),
        "duration": duration,
        "model": model_name,
        "worst_seam_gap": seam_report(segments, splits),
        "workdir": workdir,
    }


def main():
    parser = argparse.ArgumentParser(description="Local Whisper transcription")
    parser.add_argument("source")
    parser.add_argument("--lang", default="he")
    parser.add_argument("--model", default=None)
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--outdir", default=".")
    parser.add_argument("--workdir", default=None)
    args = parser.parse_args()

    stem = os.path.splitext(os.path.basename(args.source))[0]
    base = os.path.join(args.outdir, stem)
    result = run_pipeline(args.source, base, lang=args.lang, model=args.model,
                          fast=args.fast, workdir=args.workdir,
                          on_progress=lambda i, n, msg: print(f"[{i}/{n}] {msg}", flush=True),
                          on_stage=lambda msg: print(f"* {msg}", flush=True))
    for key in ("srt_path", "timestamped_path", "flowing_path", "segments",
                "words", "model", "worst_seam_gap"):
        print(f"{key}: {result[key]}")


if __name__ == "__main__":
    main()
