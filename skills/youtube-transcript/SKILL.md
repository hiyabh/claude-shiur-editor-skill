---
name: youtube-transcript
description: >-
  Get a transcript out of ANY YouTube video — including the hard ones that have
  no captions at all, return 403 Forbidden, or trip YouTube's bot check. Climbs
  a ladder automatically: youtube_transcript_api → yt-dlp captions → access
  escalation (version upgrade, player clients, browser cookies) → local Whisper
  on downloaded audio. Also transcribes local audio/video files (phone
  recordings, Zoom exports). Produces SRT + timestamped text + flowing text.
  TRIGGER on Hebrew: 'תמלל את הסרטון', 'תמלול מיוטיוב', 'תוריד תמלול',
  'תמלל לי את ההרצאה', 'תמלול של השיעור', 'תוציא תמלול', 'תמלל את ההקלטה',
  'אין כתוביות לסרטון', 'תמלל קובץ אודיו'. English: 'transcribe this video',
  'youtube transcript', 'get captions', 'transcribe audio', 'no captions
  available', 'whisper transcribe'. USE THIS whenever a transcript is needed
  from a video or audio source. NOT for downloading the video/audio itself
  (that is universal-downloader), NOT for editing an existing transcript into a
  document (that is shiur-editor).
---

# YouTube Transcript — תמלול מכל סרטון

מטרה: URL של יוטיוב (או קובץ אודיו/וידאו מקומי) נכנס — תמלול יוצא. הסקיל מטפס לבד
מהמסלול הזול לאיטי ולא נעצר באמצע.

## נתיבי הסקריפטים — פותרים פעם אחת

הסקריפטים יושבים ב-`scripts/` **של הסקיל הזה**. אל תכתוב נתיב מוחלט מהזיכרון, ואל
תסמוך על `~` בשורת הפקודה — PowerShell ו-cmd ב-Windows לא מרחיבים `~` בתוך מרכאות,
והנתיב מגיע ל-Python כמו שהוא ונכשל. פתור פעם אחת בתוך Python (זהה בכל מערכת ובכל
מעטפת):

```bash
python -c "import os;print(os.path.expanduser('~/.claude/skills/youtube-transcript/scripts'))"
```

בהמשך הקובץ הנתיב שהודפס מסומן `<SCRIPTS>` — **החלף אותו בערך האמיתי בכל פקודה.**

## שימוש

```bash
python "<SCRIPTS>/get_transcript.py" "<url>"
```

| דגל | מתי |
|---|---|
| `--lang en` | שפה שאינה עברית (ברירת מחדל `he`) |
| `--force-whisper` | הכתוביות האוטומטיות גרועות — לדלג עליהן ולתמלל מקומית |
| `--fast` | מודל `medium` במקום `large-v3` — מהיר יותר, מדויק פחות |
| `--model NAME` | מודל faster-whisper מפורש |
| `--outdir DIR` | תיקיית פלט (ברירת מחדל: הנוכחית) |

קובץ מקומי: להעביר נתיב במקום URL — `get_transcript.py "recording.mp3" --lang he`.

**הרץ ברקע כשצפוי Whisper** (`run_in_background: true`). תמלול מקומי רץ ב-~2.4-2.8×
זמן אמת: שיעור של שעה ≈ 25 דקות, שעתיים ≈ 45 דקות (נמדד על מעבד 16-threads בלי GPU;
במכונה איטית יותר זה יותר). מסלול הכתוביות לוקח שניות.

## תלויות

`yt-dlp` ו-`youtube-transcript-api` למסלול הכתוביות — התקנה של שניות. **מסלול Whisper
המקומי כבד:** `faster-whisper` (מאות MB) + הבינאריים `ffmpeg`/`ffprobe` ב-PATH
(Windows: `winget install Gyan.FFmpeg` · macOS: `brew install ffmpeg` · Linux:
`apt install ffmpeg`) + מודל עברית ~1.6GB שיירד בריצה הראשונה. ראה `requirements.txt`.

הסקריפט מריץ `pip install --upgrade yt-dlp` מעצמו כשהוא נתקל בחסימה — זה מה שפותר 403
בפועל. אם pip נחסם (סביבה מנוהלת / PEP 668) הוא מדווח וממשיך עם הגרסה הקיימת. לביטול
מוחלט: `SHIUR_NO_PIP=1`.

## הפלט

שלושה קבצים ליד ה-`--outdir`, בשם `<כותרת> [<id>]`:

| קובץ | שימוש |
|---|---|
| `.srt` | כתוביות מסונכרנות |
| ` - עם חותמות זמן.txt` | שורה למשפט עם `[HH:MM:SS]` — לניווט וציטוט |
| ` - טקסט רצוף.txt` | פסקאות בלי חותמות — **זה הקלט ל-`shiur-editor`** |

ובנוסף בלוק `key: value` ל-stdout. **תמיד לקרוא את `method:`** — הוא אומר מאיזה מסלול
הגיע התמלול, וזו אינדיקציה ישירה לאיכות:

| `method` | משמעות | איכות |
|---|---|---|
| `captions-api` / `captions-ytdlp` | כתוביות של יוטיוב | ידניות = מצוין; אוטומטיות = בינוני |
| `whisper` | תמלול מקומי | טוב מאוד בעברית (מודל ייעודי) |

שדות נוספים: `title`, `video_id`, `segments`, `words`, `model`, `worst_seam_gap`,
`elapsed_minutes`.

## סולם המסלולים

```
A1  youtube_transcript_api ──┐ שניות, בלי הורדה, לא תלוי ב-yt-dlp
A2  yt-dlp captions ─────────┤ שניות, מחזיר SRT אמיתי
B   הסלמת גישה ──────────────┤ רק כשחסום
C   Whisper מקומי ───────────┘ דקות, רק כשבאמת אין כתוביות
```

**A1 קודם בכוונה:** `youtube_transcript_api` פונה ישירות ל-HTTP ולא עובר דרך yt-dlp,
ולכן הוא עובד גם כשyt-dlp חסום לגמרי.

### ההבחנה הקריטית: "אין כתוביות" מול "חסום"

`classify_failure()` מפריד ביניהן, ורק **חסימה** מסלימה. סרטון בלי כתוביות קופץ ישר
ל-Whisper. בלי ההבחנה הזאת היינו מבזבזים דקות על עדכוני גרסה וקוקיז בשביל סרטון שפשוט
אין לו מה להוריד. נמדד: זיהוי "אין כתוביות" לוקח 1.4 שניות.

### שלב B — הסלמת גישה

1. `python -m yt_dlp` במקום `yt-dlp` (ראה מלכודת הגרסאות)
2. `player_client`: `default` → `web,tv,visionos` → `visionos` → `android,web_safari`
3. `pip install --upgrade yt-dlp`
4. `--cookies-from-browser chrome|edge|firefox|brave`
5. `--no-check-certificates` (תמיד)

## ⚠️ מלכודת הגרסאות של yt-dlp

```
yt-dlp --version           → גרסה ישנה   (shim של winget/brew, ב-PATH)
python -m yt_dlp --version → גרסה עדכנית (pip)
```
(לדוגמה בלבד — המספרים משתנים.)

shim של מנהל חבילות יכול להקדים את חבילת ה-pip ב-PATH. **אחרי
`pip install --upgrade yt-dlp` הפקודה `yt-dlp` עדיין מריצה את הבינארי הישן** ותיכשל
שוב באותו 403 — וייראה כאילו העדכון לא עזר. סיבה שנייה, נפוצה לא פחות: אחרי התקנת pip
הבינארי לרוב **בכלל לא ב-PATH**, ואז קריאה ל-`yt-dlp` נכשלת ב-FileNotFoundError.

הסקריפטים כאן קוראים תמיד `[sys.executable, '-m', 'yt_dlp']`. אם אתה מריץ yt-dlp ידנית
בהקשר אחר — עשה אותו דבר.

## למה Whisper מפוצל למקטעים

Whisper בונה את ה-STFT של **כל** הקלט מראש. מקטע של 26 דקות דורש מערך complex128 של
~450MB, וזה נופל גם כששמונה ג'יגה נראים פנויים (`mkl_malloc: failed to
allocate memory`, `numpy._core._exceptions._ArrayMemoryError`).

לכן:

1. **חיתוך בנקודות שקט** (`silencedetect`) ולא בזמנים שרירותיים — מונע חיתוך באמצע משפט.
2. **גודל מקטע לפי RAM פנוי** (לא לפי RAM כולל — במכונה עם 32GB לרוב רק 8-12 באמת
   פנויים): ≥16GB → 10 דק' · 8-16GB → 5 דק' · <8GB → 3 דק'.
3. **תהליך נפרד לכל מקטע.** זו הנקודה הקריטית: באותו תהליך הזיכרון נצבר — נמדד 109 → 151
   → 162 שניות למקטע ואז קריסה במקטע הרביעי. בתהליכים נפרדים ~125 שניות יציב לאורך 21
   מקטעים. **אל תמזג את `transcribe_chunk.py` חזרה לתוך הלולאה** — גבול התהליך הוא התיקון.
4. **סמן `.done` לכל מקטע** + 3 ניסיונות עם צינון של 20 שניות.

### המשך אחרי קריסה

ריצה חוזרת באותה תיקייה מדלגת על מקטעים שהושלמו. נמדד: ריצה מלאה 299 שניות; אחרי מחיקת
מקטע אחד, הריצה החוזרת 124 שניות ותמללה רק אותו. **אם ריצה נכשלה — פשוט הרץ שוב את אותה
פקודה**, אל תתחיל מאפס ואל תמחק את `<stem>.work/`.

תיקיית העבודה `<stem>.work/` מכילה את ה-WAV, המקטעים והחלקים. אפשר למחוק אותה אחרי שהפלט
נוצר, אבל אז ריצה חוזרת תתחיל מההתחלה.

## מודלים

| שפה | מודל | הערה |
|---|---|---|
| עברית | `ivrit-ai/whisper-large-v3-turbo-ct2` | מאומן ייעודית לעברית — הפרש ניכר במונחים תורניים |
| אחר | `Systran/faster-whisper-large-v3` | |
| `--fast` | `Systran/faster-whisper-medium` | |

המודלים יורדים אוטומטית בריצה הראשונה ונשמרים בקאש (`~/.cache/huggingface/hub/`) —
המודל העברי ~1.6GB. ריצה על CPU בלבד; מספר ה-threads נבחר לפי מספר הליבות (עד 16).

## אימות אחרי תמלול Whisper

לפני שמוסרים תמלול, לבדוק שני דברים:

- **`worst_seam_gap`** בפלט — הפער הגדול ביותר בין סוף מקטע לתחילת הבא. מעל ~3 שניות
  מרמז שאבד דיבור בתפר. נמדד בפועל: 1.06 שניות.
- **כיסוי** — שהמקטע האחרון מגיע קרוב לאורך הסרטון. כיסוי דיבור של 70-80% מהאורך הכולל
  תקין בהרצאה (שתיקות ונשימות); כיסוי של 40% מרמז על בעיה.

## קבצים

```
scripts/
├── get_transcript.py      # כניסה יחידה — סולם המסלולים
├── whisper_pipeline.py    # אודיו → מקטעים → מיזוג (מתחדש)
└── transcribe_chunk.py    # מקטע אחד ויוצא (בידוד זיכרון)
```

## סקילים קשורים

- **`shiur-editor`** — הופך תמלול למסמך ערוך. מפעיל את הסקיל הזה אוטומטית כשאין כתוביות.
  העבר לו את `- טקסט רצוף.txt`.
- **`universal-downloader`** — כשרוצים את הווידאו/אודיו עצמו ולא תמלול. סקיל נפרד
  שאינו חלק מהחבילה הזאת.
