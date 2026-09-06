"""
Fetch a YouTube video's Hebrew transcript via its own captions (auto or
human), with no video/audio download at all.

Usage:
    python fetch_youtube_transcript.py "<youtube-url>"

Replaces the manual step of copying the YouTube caption panel into a Word
document. Writes two files into the current working directory:
    - "<sanitized title> [<id>].<lang>.vtt"      (raw captions, kept as source;
                                                    <lang> is whichever of he/iw/
                                                    iw-orig the video actually has)
    - "<sanitized title> [<id>].transcript.txt"  (flattened plain text)

Then prints a small key: value block to stdout for the calling skill to read:
    title: <exact YouTube title>
    sanitized_title: <filesystem-safe title>
    video_id: <id>
    url: <the input url>
    captions_path: <absolute path>
    transcript_path: <absolute path>

On failure (no Hebrew captions available), prints an ERROR line and exits 1.
Does NOT fall back to downloading audio or running Whisper — that is the
separate youtube-transcript skill, which ships in the same bundle.
"""
import sys
import os
import re
import json
import html
import subprocess

# Claude Code captures stdout through a pipe. On Windows that makes Python fall
# back to the ANSI code page, so printing a Hebrew title dies with
# UnicodeEncodeError. Force UTF-8 before we print anything.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')

FORBIDDEN_CHARS = r'[\/\\:*?"<>|]'
# Filenames must stay at or under 80 chars (Windows caps paths at 260 unless
# LongPathsEnabled is set, which it is not by default). The title is not the
# whole filename — ' [<11-char video id>].transcript.txt' adds 30 more — so the
# title itself has to be capped low enough that the ASSEMBLED name still fits.
MAX_FILENAME_LEN = 80
LONGEST_SUFFIX = ' [12345678901].transcript.txt'
MAX_TITLE_LEN = MAX_FILENAME_LEN - len(LONGEST_SUFFIX)


def sanitize_title(title):
    cleaned = re.sub(FORBIDDEN_CHARS, '', title).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    if len(cleaned) > MAX_TITLE_LEN:
        cleaned = cleaned[:MAX_TITLE_LEN].rstrip()
    return cleaned


def ytdlp_cmd():
    """Always the pip module, never a `yt-dlp` binary on PATH.

    Two failure modes this avoids: (1) pip's console script often is not on
    PATH, and a bare ['yt-dlp', ...] then raises an uncaught FileNotFoundError
    instead of the `ERROR:` line the calling skill branches on; (2) a package
    manager shim (winget/brew) can shadow a newer pip install and reproduce the
    very 403 an upgrade just fixed.
    """
    return [sys.executable, '-m', 'yt_dlp']


def run(cmd):
    # PYTHONIOENCODING/PYTHONUTF8: without them a child writing a Hebrew title
    # to a pipe on Windows encodes with the ANSI code page and we get mojibake.
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', env=env)


def require_ytdlp():
    """Turn a missing dependency into the documented contract — ERROR + exit 1 —
    so shiur-editor's SKILL.md escalates to the youtube-transcript skill instead
    of the user getting a traceback."""
    if run(ytdlp_cmd() + ['--version']).returncode != 0:
        print('ERROR: החבילה yt-dlp אינה מותקנת.')
        print(f'  התקנה: "{sys.executable}" -m pip install --upgrade yt-dlp')
        sys.exit(1)


def fetch_metadata(url):
    proc = run(ytdlp_cmd() + ['--no-check-certificates', '--skip-download',
                              '--dump-json', url])
    if proc.returncode != 0:
        print('ERROR: failed to fetch video metadata via yt-dlp.')
        print(proc.stderr.strip())
        sys.exit(1)
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    return data.get('title', 'שיעור מיוטיוב'), data.get('id', '')


def fetch_hebrew_captions(url, sanitized_title, video_id):
    out_template = f"{sanitized_title} [{video_id}].%(ext)s"
    # No --convert-subs: that is an ffmpeg post-processor, and YouTube serves
    # VTT. Parsing VTT ourselves (see flatten_captions) keeps the common path
    # free of ffmpeg entirely, so a single pip install is enough to run this.
    cmd = ytdlp_cmd() + [
        '--no-check-certificates',
        '--write-subs', '--write-auto-subs', '--sub-langs', 'he,iw,iw-orig',
        '--skip-download',
        '-o', out_template, url,
    ]
    proc = run(cmd)
    # Match on the ASCII video-id token, not the title: glob.glob() would treat
    # '[' / ']' as a character class, and startswith() on a Hebrew title breaks
    # on macOS, where APFS returns NFD-normalized names while the title we built
    # is NFC — niqqud then never matches and we would wrongly report "no
    # captions" on macOS only.
    tag = f"[{video_id}]"
    candidates = sorted(
        f for f in os.listdir('.')
        if tag in f and f.lower().endswith(('.vtt', '.srt'))
    )
    if not candidates:
        print('ERROR: no Hebrew captions (he/iw) found for this video — '
              'neither manual nor auto-generated.')
        if proc.stderr.strip():
            print(proc.stderr.strip())
        sys.exit(1)
    # Prefer a human-made track (no ".he-auto" style suffix) if both exist.
    non_auto = [c for c in candidates if 'auto' not in c.lower()]
    return (non_auto or candidates)[0]


_SRT_INDEX_RE = re.compile(r'^\d+$')
# Accepts SRT (00:00:01,234) and WebVTT (00:00:01.234 or 01:23.456). VTT's
# trailing cue settings ('align:start position:0%') are ignored by re.match.
_SRT_TIME_RE = re.compile(
    r'^(?:\d{1,2}:)?\d{2}:\d{2}[,.]\d{3}\s*-->\s*(?:\d{1,2}:)?\d{2}:\d{2}[,.]\d{3}')
_VTT_HEADER_RE = re.compile(r'^(WEBVTT|Kind:|Language:|NOTE\b|STYLE\b|REGION\b)')


def flatten_captions(path):
    """Flatten an .srt or a YouTube .vtt caption file into one flowing line."""
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.read().splitlines()
    seen_lines = []
    last = None
    for raw in lines:
        s = raw.strip()
        if (not s or _SRT_INDEX_RE.match(s) or _SRT_TIME_RE.match(s)
                or _VTT_HEADER_RE.match(s)):
            continue
        s = re.sub(r'<[^>]+>', '', s)  # strip inline caption markup
        # VTT carries HTML entities (&gt;&gt; marks a speaker change, &amp;, &nbsp;).
        # ffmpeg's --convert-subs used to decode these for us; we do it ourselves.
        s = html.unescape(s).strip()
        if not s or s == last:  # collapse YouTube's rolling duplicate lines
            continue
        seen_lines.append(s)
        last = s
    return ' '.join(seen_lines)


def main():
    if len(sys.argv) < 2:
        print('Usage: python fetch_youtube_transcript.py "<youtube-url>"')
        sys.exit(1)
    url = sys.argv[1]

    require_ytdlp()
    title, video_id = fetch_metadata(url)
    sanitized_title = sanitize_title(title)

    captions_path = fetch_hebrew_captions(url, sanitized_title, video_id)
    transcript_text = flatten_captions(captions_path)
    if not transcript_text.strip():
        print('ERROR: captions file was found but contained no text after parsing.')
        sys.exit(1)

    transcript_path = f"{sanitized_title} [{video_id}].transcript.txt"
    with open(transcript_path, 'w', encoding='utf-8') as f:
        f.write(transcript_text)

    # Absolute paths: the caller's cwd need not match ours, and a relative name
    # it cannot resolve would send it looking for a file that "does not exist".
    print(f'title: {title}')
    print(f'sanitized_title: {sanitized_title}')
    print(f'video_id: {video_id}')
    print(f'url: {url}')
    print(f'captions_path: {os.path.abspath(captions_path)}')
    print(f'transcript_path: {os.path.abspath(transcript_path)}')


if __name__ == '__main__':
    main()
