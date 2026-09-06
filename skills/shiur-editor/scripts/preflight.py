"""
בדיקת התקנה לחבילת shiur-editor.

Usage:
    python preflight.py

Prints a Hebrew report and exits 0 only when the CORE is usable
(Python 3.8+, python-docx, and the sibling hebrew_docx_template.py).
Optional components - yt-dlp, youtube_transcript_api, ffmpeg, faster-whisper,
the youtube-transcript skill - are reported but NEVER fail the check: each is
needed for one path only. The point of this script is that a recipient gets a
Hebrew sentence and an install command instead of a traceback.
"""
import importlib
import os
import shutil
import subprocess
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILLS_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
PIP = f'"{sys.executable}" -m pip install'
_lines = []


def report(ok, label, note=''):
    _lines.append(('OK  ' if ok else 'XX  ') + label + (f'  - {note}' if note else ''))
    return ok


def version_of(module_name, dist_name=None):
    try:
        importlib.import_module(module_name)
    except Exception:
        return None
    try:
        from importlib.metadata import version
        return version(dist_name or module_name)
    except Exception:
        return 'installed'


def main():
    print('בדיקת התקנה - shiur-editor')
    print('=' * 34)
    core = True

    py_ok = sys.version_info >= (3, 8)
    core &= report(py_ok, f'Python {sys.version.split()[0]}',
                   '' if py_ok else 'נדרש Python 3.8 ומעלה')

    docx_ver = version_of('docx', 'python-docx')
    core &= report(bool(docx_ver), f'python-docx {docx_ver or ""}'.strip(),
                   '' if docx_ver else f'חסר. התקנה: {PIP} python-docx')

    tpl = os.path.join(SCRIPT_DIR, 'hebrew_docx_template.py')
    tpl_ok = os.path.isfile(tpl)
    core &= report(tpl_ok, 'hebrew_docx_template.py',
                   '' if tpl_ok else
                   f'לא נמצא ליד הסקריפט ({SCRIPT_DIR}) - ההתקנה חלקית, התקן מחדש')

    try:
        ytdlp = subprocess.run([sys.executable, '-m', 'yt_dlp', '--version'],
                               capture_output=True, text=True)
        got = ytdlp.returncode == 0
    except Exception:
        got, ytdlp = False, None
    report(got, f'yt-dlp {ytdlp.stdout.strip()}' if got else 'yt-dlp',
           'נדרש רק למסלול יוטיוב' if got
           else f'חסר. נדרש רק ליוטיוב. התקנה: {PIP} --upgrade yt-dlp')

    yta = version_of('youtube_transcript_api', 'youtube-transcript-api')
    report(bool(yta), f'youtube-transcript-api {yta or ""}'.strip(),
           'מאיץ את מסלול הכתוביות' if yta
           else f'אופציונלי. התקנה: {PIP} "youtube-transcript-api>=1.0"')

    ff, fp = shutil.which('ffmpeg'), shutil.which('ffprobe')
    report(bool(ff and fp), 'ffmpeg + ffprobe',
           ff if (ff and fp) else
           'חסר. נדרש רק לתמלול Whisper מקומי - Windows: winget install '
           'Gyan.FFmpeg | macOS: brew install ffmpeg | Linux: apt install ffmpeg')

    fw = version_of('faster_whisper', 'faster-whisper')
    report(bool(fw), f'faster-whisper {fw or ""}'.strip(),
           'מודל העברית יירד בריצה הראשונה (~1.6GB)' if fw
           else f'חסר. נדרש רק כשלסרטון אין כתוביות. התקנה: {PIP} faster-whisper')

    sibling = os.path.join(SKILLS_ROOT, 'youtube-transcript', 'scripts',
                           'get_transcript.py')
    report(os.path.isfile(sibling), 'סקיל youtube-transcript',
           sibling if os.path.isfile(sibling)
           else 'לא מותקן. נדרש רק כשלסרטון אין כתוביות בעברית')

    print('\n'.join(_lines))
    print()
    print('* גופן David: ה-DOCX נבנה תקין בכל מערכת הפעלה - הסקריפט רק כותב את שם')
    print('  הגופן לקובץ ואינו נוגע בגופני המערכת. אם David לא מותקן, Word או')
    print('  LibreOffice יציגו גופן עברי חלופי; ה-RTL והפריסה נשמרים. ב-macOS הגופן')
    print('  מגיע עם Microsoft Office, וב-Linux חבילת culmus נותנת תחליף (David CLM).')
    print()
    print('הליבה תקינה - אפשר לערוך שיעור.' if core
          else 'הליבה חסרה. תקן את שורות ה-XX שלמעלה ונסה שוב.')
    sys.exit(0 if core else 1)


if __name__ == '__main__':
    main()
