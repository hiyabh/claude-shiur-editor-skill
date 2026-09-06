"""
Get a transcript out of any YouTube video — or any local audio/video file.

Usage:
    python get_transcript.py "<youtube-url>"            # default language: he
    python get_transcript.py "<youtube-url>" --lang en
    python get_transcript.py "<youtube-url>" --force-whisper
    python get_transcript.py "recording.mp3" --lang he  # local file

Climbs a ladder from cheapest to most expensive and does not stop in between:

    A1  youtube_transcript_api   seconds, no download, independent of yt-dlp
    A2  yt-dlp captions          seconds, gives a real SRT
    B   access escalation        only when BLOCKED (see classify_failure)
    C   local Whisper            minutes, only when captions truly don't exist

Prints a `key: value` block on stdout for a calling skill to parse, including
`method:` so the caller knows which rung produced the text.
"""
import argparse
import html
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import whisper_pipeline as wp  # noqa: E402

FORBIDDEN_CHARS = r'[\/\\:*?"<>|]'
# Windows caps paths at 260 chars unless LongPathsEnabled is set (off by default).
# Cap the FULL stem, not just the title — the suffixes below add ~30 chars.
MAX_STEM_LEN = 60
LONGEST_SUFFIX = " - עם חותמות זמן.txt"

LANG_VARIANTS = {
    "he": ["he", "iw", "he-IL", "iw-orig", "iw-IL"],
    "en": ["en", "en-US", "en-GB", "en-orig"],
}

# yt-dlp player clients to try in order. `visionos` is what finally worked when
# the default and web/ios/tv clients all returned 403 or "page needs reload".
PLAYER_CLIENTS = ["default", "web,tv,visionos", "visionos", "android,web_safari"]
BROWSERS = ["chrome", "edge", "firefox", "brave"]

BLOCKED_MARKERS = (
    "403", "forbidden", "sign in to confirm", "not a bot",
    "page needs to be reloaded", "po token", "rate-limit", "throttl",
    "unable to download", "precondition check",
)
NO_CAPTION_MARKERS = ("no subtitles", "there are no subtitles", "has no subtitles")


class Blocked(Exception):
    """YouTube refused access — escalation may fix it."""


class NoCaptions(Exception):
    """The video genuinely has no caption track — go straight to Whisper."""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def ytdlp_cmd():
    """Always the pip module, never the `yt-dlp` binary.

    A package-manager shim on PATH (winget/brew) can be older than the pip
    package and shadow it, so calling `yt-dlp` after a successful
    `pip install --upgrade yt-dlp` still runs the stale build and reproduces
    the very 403 the upgrade just fixed.
    """
    return [sys.executable, "-m", "yt_dlp"]


def run(cmd, timeout=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=timeout)


def sanitize_stem(title, video_id):
    cleaned = re.sub(FORBIDDEN_CHARS, "", title or "video").strip()
    cleaned = re.sub(r"\s+", " ", cleaned).rstrip(". ")
    tag = f" [{video_id}]" if video_id else ""
    budget = MAX_STEM_LEN - len(tag)
    if len(cleaned) > budget:
        cleaned = cleaned[:budget].rstrip()
    return cleaned + tag


def language_candidates(lang):
    lang = (lang or "he").lower()
    variants = list(LANG_VARIANTS.get(lang, [lang]))
    if lang not in variants:
        variants.insert(0, lang)
    return variants


def extract_video_id(url):
    for pattern in (r"[?&]v=([A-Za-z0-9_-]{11})",
                    r"youtu\.be/([A-Za-z0-9_-]{11})",
                    r"/(?:embed|shorts|live)/([A-Za-z0-9_-]{11})"):
        found = re.search(pattern, url)
        if found:
            return found.group(1)
    return ""


def classify_failure(text):
    """Blocked vs no-captions. Getting this wrong wastes minutes escalating
    access for a video that simply has no captions."""
    low = (text or "").lower()
    if any(marker in low for marker in NO_CAPTION_MARKERS):
        return NoCaptions(text)
    if any(marker in low for marker in BLOCKED_MARKERS):
        return Blocked(text)
    return NoCaptions(text)


# --------------------------------------------------------------------------
# stage A1 — youtube_transcript_api
# --------------------------------------------------------------------------

def fetch_via_api(video_id, lang):
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        import youtube_transcript_api as yta
    except ImportError:
        raise NoCaptions("youtube_transcript_api not installed")

    no_caption_errors = (yta.TranscriptsDisabled, yta.NoTranscriptFound)
    blocked_errors = (yta.IpBlocked, yta.RequestBlocked, yta.AgeRestricted,
                      yta.VideoUnplayable, yta.PoTokenRequired,
                      yta.YouTubeRequestFailed, yta.FailedToCreateConsentCookie)
    try:
        listing = YouTubeTranscriptApi().list(video_id)
        transcript = _pick_transcript(listing, language_candidates(lang))
        fetched = transcript.fetch()
    except no_caption_errors as exc:
        raise NoCaptions(str(exc))
    except blocked_errors as exc:
        raise Blocked(str(exc))
    except Exception as exc:  # unknown API error — treat conservatively
        raise classify_failure(str(exc))

    segments = [{"start": s.start, "end": s.start + s.duration,
                 "text": s.text.strip()}
                for s in fetched if s.text and s.text.strip()]
    if not segments:
        raise NoCaptions("caption track was empty")
    return segments, getattr(transcript, "language_code", lang)


def _pick_transcript(listing, candidates):
    """Manual in the wanted language first, then generated, then anything,
    then a translation. Manual tracks are meaningfully more accurate."""
    for finder in (listing.find_manually_created_transcript,
                   listing.find_generated_transcript):
        try:
            return finder(candidates)
        except Exception:
            pass
    available = list(listing)
    if not available:
        raise RuntimeError("no subtitles listed for this video")
    for transcript in available:
        if not getattr(transcript, "is_generated", True):
            return transcript
    first = available[0]
    if candidates and getattr(first, "is_translatable", False):
        try:
            return first.translate(candidates[0])
        except Exception:
            pass
    return first


# --------------------------------------------------------------------------
# stage A2 / B — yt-dlp
# --------------------------------------------------------------------------

def ytdlp_flags(client=None, browser=None):
    flags = ["--no-check-certificates", "--ignore-config"]
    if client and client != "default":
        flags += ["--extractor-args", f"youtube:player_client={client}"]
    if browser:
        flags += ["--cookies-from-browser", browser]
    return flags


def fetch_metadata(url, client=None, browser=None):
    proc = run(ytdlp_cmd() + ytdlp_flags(client, browser) +
               ["--skip-download", "--print", "%(title)s", "--print", "%(id)s",
                "--print", "%(duration)s", url], timeout=180)
    lines = [l for l in (proc.stdout or "").strip().splitlines() if l.strip()]
    if proc.returncode != 0 or len(lines) < 2:
        raise classify_failure(proc.stderr or proc.stdout)
    duration = 0.0
    if len(lines) > 2:
        try:
            duration = float(lines[2])
        except ValueError:
            pass
    return lines[0].strip(), lines[1].strip(), duration


def fetch_captions_ytdlp(url, stem, lang, outdir, client=None, browser=None):
    langs = ",".join(language_candidates(lang))
    template = os.path.join(outdir, f"{stem}.%(ext)s")
    proc = run(ytdlp_cmd() + ytdlp_flags(client, browser) +
               ["--write-subs", "--write-auto-subs", "--sub-langs", langs,
                "--skip-download", "-o", template, url],
               timeout=600)
    # NOTE: glob treats [ and ] as a character class; the stem embeds the video
    # id in literal brackets, so match with plain string comparison.
    # --convert-subs is an ffmpeg post-processor and YouTube serves VTT, so we
    # take the captions as-is and let parse_srt read VTT too. Keeps the common
    # path free of ffmpeg. Match on the id token, not the stem: on macOS APFS
    # returns NFD names while our stem is NFC, so a niqqud title never matches.
    tag = stem[stem.rfind("["):] if "[" in stem else stem
    found = sorted(f for f in os.listdir(outdir)
                   if tag in f and f.lower().endswith((".vtt", ".srt")))
    if not found:
        raise classify_failure(proc.stderr or proc.stdout)
    manual = [f for f in found if "auto" not in f.lower()]
    return os.path.join(outdir, (manual or found)[0])


def download_audio(url, stem, outdir, client=None, browser=None):
    template = os.path.join(outdir, f"{stem}.%(ext)s")
    proc = run(ytdlp_cmd() + ytdlp_flags(client, browser) +
               ["-f", "bestaudio/best", "-x", "--audio-format", "mp3",
                "-o", template, url], timeout=5400)
    found = sorted(f for f in os.listdir(outdir)
                   if f.startswith(stem) and f.endswith(".mp3"))
    if not found:
        raise classify_failure(proc.stderr or proc.stdout)
    return os.path.join(outdir, found[0])


def escalate(operation, say):
    """Run `operation(client, browser)` up the access ladder.

    NoCaptions propagates immediately — escalating access cannot conjure a
    caption track that does not exist.
    """
    last = None
    for client in PLAYER_CLIENTS:
        try:
            return operation(client, None)
        except NoCaptions:
            raise
        except Blocked as exc:
            last = exc
            say(f"blocked with player_client={client}; escalating")

    if os.environ.get("SHIUR_NO_PIP"):
        say("skipping the yt-dlp upgrade (SHIUR_NO_PIP is set)")
    else:
        say("upgrading yt-dlp - this is what fixed the 403 last time "
            "(set SHIUR_NO_PIP=1 to skip)")
        _up = run([sys.executable, "-m", "pip", "install", "--upgrade", "--quiet",
                   "--disable-pip-version-check", "yt-dlp"], timeout=600)
        if _up.returncode != 0:
            say("pip upgrade failed (externally-managed environment?) - "
                "continuing with the version already installed")
    for client in PLAYER_CLIENTS[:2]:
        try:
            return operation(client, None)
        except NoCaptions:
            raise
        except Blocked as exc:
            last = exc

    for browser in BROWSERS:
        try:
            say(f"retrying with cookies from {browser}")
            return operation(PLAYER_CLIENTS[1], browser)
        except NoCaptions:
            raise
        except Blocked as exc:
            last = exc
    raise last or Blocked("all access escalation attempts failed")


# --------------------------------------------------------------------------
# SRT parsing
# --------------------------------------------------------------------------

_SRT_TIME = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
                       r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


def parse_srt(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    segments, start, end, buffer = [], None, None, []

    def flush():
        text = re.sub(r"<[^>]+>", "", " ".join(buffer))
        # VTT carries HTML entities (&gt;&gt; marks a speaker change, &amp;).
        # We read VTT directly now, so decode them ourselves.
        text = html.unescape(text).strip()
        if text and start is not None:
            if not segments or segments[-1]["text"] != text:
                segments.append({"start": start, "end": end, "text": text})

    for line in raw.splitlines():
        stripped = line.strip()
        stamp = _SRT_TIME.match(stripped)
        if stamp:
            flush()
            buffer = []
            nums = [int(n) for n in stamp.groups()]
            start = nums[0] * 3600 + nums[1] * 60 + nums[2] + nums[3] / 1000
            end = nums[4] * 3600 + nums[5] * 60 + nums[6] + nums[7] / 1000
        elif not stripped or stripped.isdigit():
            continue
        else:
            buffer.append(stripped)
    flush()
    return segments


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def emit(result):
    for key in ("title", "video_id", "url", "method", "language", "segments",
                "words", "srt_path", "timestamped_path", "flowing_path",
                "model", "worst_seam_gap", "workdir"):
        if result.get(key) not in (None, ""):
            print(f"{key}: {result[key]}")


def from_segments(segments, base, method, language, extra=None):
    srt, stamped, flowing = wp.write_outputs(segments, base)
    result = {"method": method, "language": language,
              "segments": len(segments),
              "words": sum(len(s["text"].split()) for s in segments),
              "srt_path": srt, "timestamped_path": stamped,
              "flowing_path": flowing}
    result.update(extra or {})
    return result


def handle_local_file(args, say):
    source = os.path.abspath(args.source)
    stem = sanitize_stem(os.path.splitext(os.path.basename(source))[0], "")
    base = os.path.join(args.outdir, stem)
    say(f"local file — going straight to Whisper: {os.path.basename(source)}")
    outcome = wp.run_pipeline(source, base, lang=args.lang, model=args.model,
                              fast=args.fast, on_progress=progress, on_stage=say)
    outcome.update({"title": stem, "method": "whisper", "language": args.lang,
                    "url": source})
    return outcome


def progress(index, total, message):
    print(f"[{index}/{total}] {message}", flush=True)


def try_captions(url, video_id, stem, args, say):
    """Stage A1 then A2. Raises NoCaptions if the video has none."""
    if video_id:
        try:
            say("trying captions via youtube_transcript_api")
            segments, language = fetch_via_api(video_id, args.lang)
            return from_segments(segments, os.path.join(args.outdir, stem),
                                 "captions-api", language)
        except NoCaptions as exc:
            say(f"no captions via API ({exc})")
            raise
        except Blocked as exc:
            say(f"API blocked ({exc}); falling back to yt-dlp")

    say("trying captions via yt-dlp")
    srt = escalate(lambda client, browser: fetch_captions_ytdlp(
        url, stem, args.lang, args.outdir, client, browser), say)
    segments = parse_srt(srt)
    if not segments:
        raise NoCaptions("SRT parsed to nothing")
    return from_segments(segments, os.path.join(args.outdir, stem),
                         "captions-ytdlp", args.lang)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="YouTube URL or local audio/video path")
    parser.add_argument("--lang", default="he")
    parser.add_argument("--model", default=None)
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--force-whisper", action="store_true",
                        help="skip captions entirely (use when auto-captions are poor)")
    parser.add_argument("--outdir", default=".")
    args = parser.parse_args()

    # Absolute: the caller's cwd need not match ours, and a relative path it
    # cannot resolve would send it looking for a file that "does not exist".
    args.outdir = os.path.abspath(args.outdir)
    os.makedirs(args.outdir, exist_ok=True)
    started = time.time()
    say = lambda msg: print(f"* {msg}", flush=True)  # noqa: E731

    if os.path.exists(args.source):
        result = handle_local_file(args, say)
        result["elapsed_minutes"] = round((time.time() - started) / 60, 1)
        emit(result)
        return

    url = args.source
    video_id = extract_video_id(url)
    title, resolved_id, _duration = escalate(
        lambda client, browser: fetch_metadata(url, client, browser), say)
    video_id = resolved_id or video_id
    stem = sanitize_stem(title, video_id)
    say(f"title: {title}")

    result = None
    if not args.force_whisper:
        try:
            result = try_captions(url, video_id, stem, args, say)
        except NoCaptions:
            say("this video has no caption track — transcribing locally")

    if result is None:
        audio = escalate(lambda client, browser: download_audio(
            url, stem, args.outdir, client, browser), say)
        say(f"audio downloaded: {os.path.basename(audio)}")
        result = wp.run_pipeline(audio, os.path.join(args.outdir, stem),
                                 lang=args.lang, model=args.model,
                                 fast=args.fast, on_progress=progress,
                                 on_stage=say)
        result.update({"method": "whisper", "language": args.lang})

    result.update({"title": title, "video_id": video_id, "url": url,
                   "elapsed_minutes": round((time.time() - started) / 60, 1)})
    emit(result)


if __name__ == "__main__":
    main()
