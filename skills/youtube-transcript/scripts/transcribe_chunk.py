"""
Transcribe ONE audio chunk, write its segments as JSONL, then exit.

Usage:
    python transcribe_chunk.py <chunk_wav> <out_jsonl> <offset_seconds> <model> <lang> [threads]

Why this is a separate process
------------------------------
faster-whisper / CTranslate2 accumulate memory across successive transcribe()
calls in the same process. Measured on a 21-chunk run: 109s -> 151s -> 162s per
chunk, then a hard `mkl_malloc: failed to allocate memory` on chunk 4. Running
each chunk in a fresh process that exits afterwards returns every allocation to
the OS and keeps the per-chunk time flat (~125s).

Do not "optimise" this by looping over chunks here — the process boundary IS
the fix.
"""
import json
import sys
import time

from faster_whisper import WhisperModel

VAD_MIN_SILENCE_MS = 500
BEAM_SIZE = 5
DEFAULT_THREADS = 16


def transcribe(chunk_path, out_path, offset, model_name, lang, threads):
    model = WhisperModel(model_name, device="cpu", compute_type="int8",
                         cpu_threads=threads)
    segments, info = model.transcribe(
        chunk_path,
        language=lang or None,
        beam_size=BEAM_SIZE,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=VAD_MIN_SILENCE_MS),
        # Each chunk is transcribed independently, so there is no previous
        # context to condition on; leaving this True makes a chunk inherit
        # hallucinated text from its own earlier segments.
        condition_on_previous_text=False,
    )
    count = 0
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            record = {"start": seg.start + offset, "end": seg.end + offset,
                      "text": text}
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    # Atomic rename so a killed process never leaves a half-written part that
    # the pipeline would mistake for a completed chunk.
    import os
    os.replace(tmp_path, out_path)
    return count, info.language


def main():
    if len(sys.argv) < 6:
        print(__doc__)
        sys.exit(2)
    chunk_path, out_path = sys.argv[1], sys.argv[2]
    offset = float(sys.argv[3])
    model_name, lang = sys.argv[4], sys.argv[5]
    threads = int(sys.argv[6]) if len(sys.argv) > 6 else DEFAULT_THREADS

    started = time.time()
    count, detected = transcribe(chunk_path, out_path, offset, model_name,
                                 lang, threads)
    print(f"segments={count} language={detected} seconds={time.time() - started:.0f}",
          flush=True)


if __name__ == "__main__":
    main()
