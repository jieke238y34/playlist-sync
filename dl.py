#!/usr/bin/env python3
"""
dl.py - download Spotify playlists as MP3 files with spotDL.
ONE file. Works on Windows, Linux and macOS.

RUN
    Windows:        double-click dl.py   (or: py dl.py)
    Linux / macOS:  python dl.py

REQUIRED
    Python 3.10 to 3.14. Nothing else to install by hand.
    Windows with no Python:  winget install Python.Python.3.13, then open a NEW window.
    (or download it from python.org and tick "Add python.exe to PATH").
    If the default Python is too new or old, dl.py looks for a good one and uses it.
    On first run the script installs spotDL and yt-dlp into a private
    folder, and downloads ffmpeg if the system has none.

FIRST RUN
    The script creates config.ini and playlists.txt next to dl.py, then
    stops. Edit both files, then run it again.

COMMANDS
    python dl.py                   download, then sort into genre folders
    python dl.py --sort            sort only
    python dl.py --sort --dry-run  preview the sort, move nothing
    python dl.py --dedupe          move duplicate songs to _Duplicates (nothing is deleted)
    python dl.py --dedupe --dry-run  short summary, then type --expand (list the
                                    duplicates), --dedupe (move them) or --exit
    python dl.py --genres          fill in missing genres from MusicBrainz/Last.fm and move songs out of
                                    Other/Unknown. Add --dry-run to preview.
    python dl.py --lyrics          look for lyrics files (.lrc / .txt) for songs in the library (off in config by default)
    python dl.py --schedule daily  run dl.py every night at 03:00 (or weekly). --schedule status, --unschedule
    python dl.py --ego             a short portrait of your library in words
    python dl.py --gaps            which playlist songs are missing from your library, and why
    python dl.py --playlists       build the playlist files (.m3u8). Add --dry-run to see what is combined.
    python dl.py --report          library report (sizes, formats, weakest files)
    python dl.py --colors          show the colours that are in use (KDE Plasma colours if available)
    python dl.py --colors NAME     preview a KDE colour scheme that you have, active or not
    python dl.py --themes          list the KDE colour schemes you can use as  theme = NAME
    python dl.py --upgrade         SoundCloud upgrade only (songs still waiting from earlier runs)
    python dl.py --help            show this text

SOURCES
    YouTube is tried first. Songs that came from YouTube are then tried on SoundCloud. A SoundCloud
    file replaces the YouTube file only if it is not DRM protected, is about as long, is at least
    60 s and is not a cover, remix or live version. Switch it off in config.ini
    ([sources] prefer_soundcloud = no).

DUPLICATES
    At the end of every run, dl.py previews duplicate songs (same artist, title and length,
    or the same Spotify link) but moves nothing. To move them, run:  python dl.py --dedupe
    The best copy stays (lossless, higher bitrate, cover art, full tags). Live, remix and
    other versions are kept. Extra copies go to <music_dir>/_Duplicates. Also cleans empty
    or broken files and empty folders, fills a missing genre tag on the kept copy, and writes
    a library report. Optional audio fingerprints: config.ini [dedupe] fingerprint = yes.

SPEED
    To avoid YouTube blocks, yt-dlp waits 15 to 40 seconds between tracks. Large
    playlists take a long time. This is on purpose.

OPTIONAL
    node (nodejs.org)  - fewer YouTube errors
    cookies file       - helps with age-restricted songs
"""
import configparser
import hashlib
import json
import locale
import math
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import venv
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

# spotDL needs Python 3.10 to 3.14 (checked on PyPI, 2026-10). Change MAX_PY if spotDL adds support.
MIN_PY, MAX_PY = (3, 10), (3, 14)
_PROBE = ("import sys; print(sys.executable) if (%d,%d) <= sys.version_info[:2] <= (%d,%d) else None"
          % (MIN_PY + MAX_PY))


def pause_if_own_window():
    "Windows double-click opens a console that closes at exit. Wait for Enter."
    if os.name != "nt" or os.environ.get("DL_CHILD"):   # a relaunched copy: the parent waits
        return
    try:
        import ctypes
        pids = (ctypes.c_uint * 4)()
        own = ctypes.windll.kernel32.GetConsoleProcessList(pids, 4) <= 2
    except Exception:
        own = False
    if own:
        try:
            input("\nPress Enter to close this window.")
        except (EOFError, KeyboardInterrupt):
            pass


def _other_python():
    "Find another installed Python 3.10 to 3.14. Return its path, or None."
    cands = []
    if os.name == "nt":
        for v in ("3.13", "3.12", "3.11", "3.10", "3.14"):
            cands.append(["py", "-" + v])
            for root in (os.environ.get("LOCALAPPDATA", "") + r"\Programs\Python",
                         os.environ.get("ProgramFiles", r"C:\Program Files") + r"\Python" + v.replace(".", "")):
                cands.append([root + r"\Python" + v.replace(".", "") + r"\python.exe"])
                cands.append([root + r"\python.exe"])
        cands.append(["python"])
    else:
        for v in ("3.13", "3.12", "3.11", "3.10", "3.14"):
            cands.append(["python" + v])
        cands += [["python3"], ["python"]]
    for c in cands:
        try:
            r = subprocess.run(c + ["-c", _PROBE], capture_output=True, text=True, timeout=30,
                               stdin=subprocess.DEVNULL)   # a prompt gets no answer, no hang
        except (OSError, subprocess.SubprocessError):
            continue
        out = r.stdout.strip().splitlines()
        if r.returncode == 0 and out and out[-1] != sys.executable and os.path.isfile(out[-1]):
            return out[-1]
    return None


if not MIN_PY <= sys.version_info[:2] <= MAX_PY:
    # The default Python is wrong (for example the newest one). Look for a good one and run there.
    _good = _other_python()
    if _good:
        print(f"Default Python is {sys.version_info[0]}.{sys.version_info[1]} (not supported). "
              f"Using {_good} instead.")
        _rc = subprocess.run([_good, os.path.abspath(__file__)] + sys.argv[1:],
                             env=dict(os.environ, DL_CHILD="1")).returncode
        pause_if_own_window()
        sys.exit(_rc)
    print(f"This Python is {sys.version_info[0]}.{sys.version_info[1]} ({sys.executable}).")
    print("spotDL needs Python 3.10 to 3.14, and no other suitable Python was found.")
    print("Windows:  winget install Python.Python.3.13")
    print("          Then CLOSE this window, open a NEW one, and start dl.py again.")
    print("          Check with:  py -0   (the list must show 3.10 to 3.14)")
    print("Linux:    use your package manager, for example: sudo dnf install python3")
    pause_if_own_window()
    sys.exit(1)

# Song titles can hold characters that Windows consoles cannot print.
for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(errors="replace")

IS_WIN = os.name == "nt"
HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "config.ini"
TEST_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"   # used to test for YouTube blocks
BLOCK_RE = re.compile(
    r"403|Forbidden|JSONDecodeError|Requested format is not available"
    r"|Too Many Requests|HTTP Error 429|not a bot"
)
ID_RE = re.compile(r"[A-Za-z0-9]{22}")
# Lines that are never block errors, even when a song title holds "403" or "Forbidden".
NOT_BLOCK_RE = re.compile(r"^\s*(Downloaded|Skipping|Found \d+ songs|Processing query)")
URL_RE = re.compile(r"https?://\S+")


def is_block(line):
    "True if a spotDL output or error line shows that YouTube blocks us."
    return not NOT_BLOCK_RE.match(line) and bool(BLOCK_RE.search(URL_RE.sub("", line)))


# ---------------------------------------------------------------------
# COLOURS (screen only: dl.log and the error files stay plain text)
# config.ini [display] color = auto | yes | no.  auto = on in a terminal, off when the output
# goes to a file or NO_COLOR is set. Change the numbers below to change a colour.
# Default colours (true colour): deep dark magenta = good, yellow = warning, bold neon cyan = problem.
# Terminals without true colour get the standard 16 colours (ANSI_PALETTE) instead.
COLOR_ON = False
ACTIVE_THEME = "default"
PALETTE = {"ok": "38;2;176;32;176", "err": "1;38;2;0;255;255", "warn": "38;2;255;214;0",
           "bold": "1", "dim": "2", "blue": "38;2;66;153;255", "blue_hi": "38;2;150;200;255",
           "blue_lo": "38;2;36;92;190", "blue_sh": "38;2;28;58;120"}
DEFAULT_PALETTE = dict(PALETTE)
ANSI_PALETTE = {"ok": "35", "err": "1;96", "warn": "33", "bold": "1", "dim": "2", "blue": "94", "blue_hi": "1;94",
                "blue_lo": "34", "blue_sh": "2;34"}


def _lum(c):
    def f(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])


def _contrast(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _readable(c, bg, want=4.5):
    "Move colour c toward white (dark background) or black (light one) until it is easy to read."
    target = (255, 255, 255) if _lum(bg) < 0.4 else (0, 0, 0)
    for _ in range(40):
        if _contrast(c, bg) >= want:
            break
        c = tuple(round(v + (t - v) * 0.08) for v, t in zip(c, target))
    return c


def _truecolor():
    return os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit") or bool(os.environ.get("WT_SESSION"))


def _read_scheme(path):
    "The [Colors:View] colours of a KDE colour scheme file as {name: (r, g, b)}, or None."
    try:
        got, sec = {}, ""
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                sec = line[1:-1]
            elif sec == "Colors:View" and "=" in line:
                k, v = line.split("=", 1)
                parts = v.split(",")
                if len(parts) == 3:
                    got[k] = tuple(max(0, min(255, int(x))) for x in parts)
        return got or None
    except (OSError, ValueError):
        return None


def _scheme_dirs():
    data = [Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")]
    data += [Path(d) for d in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if d]
    return [d / "color-schemes" for d in data]


def installed_schemes():
    "{name: path} of the KDE colour schemes on this computer."
    found = {}
    for d in _scheme_dirs():
        try:
            for f in sorted(d.glob("*.colors")):
                found.setdefault(f.stem, f)
        except OSError:
            pass
    return found


def _find_scheme(name):
    "Path of an installed KDE colour scheme (by name), or of a .colors file given as a path."
    p = Path(os.path.expanduser(name))
    if p.suffix == ".colors" and p.is_file():
        return p
    want = name.lower().replace(" ", "")
    for stem, f in installed_schemes().items():
        if stem.lower().replace(" ", "") == want:
            return f
    return None


def kde_palette(scheme=None):
    """Colours of a KDE colour scheme, adjusted to read well. None if not possible.
    scheme=None: the Plasma scheme that is active now (only on Plasma). Or the name of any
    installed scheme (or the path of a .colors file), active or not."""
    if not _truecolor():
        return None
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    current = _read_scheme(config / "kdeglobals")
    if scheme:
        f = _find_scheme(scheme)
        got = _read_scheme(f) if f else None
    elif os.name == "nt" or sys.platform == "darwin" or "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
        return None
    else:
        got = current
    if not got:
        return None
    bg = (current or got).get("BackgroundNormal") or got.get("BackgroundNormal")   # your real terminal background
    if not bg:
        return None

    def pick(*names):
        for n in names:
            if n in got:
                return got[n]
        return None
    ok = pick("ForegroundPositive")
    if ok is None or max(ok) - min(ok) < 40:         # a grey "positive" colour looks like plain text
        ok = pick("ForegroundLink", "DecorationFocus") or ok
    err, warn = pick("ForegroundNegative"), pick("ForegroundNeutral")
    dim, norm = pick("ForegroundInactive"), pick("ForegroundNormal")
    if not (ok and err and warn and dim and norm):
        return None
    ok, err, warn, dim, norm = (_readable(c, bg) for c in (ok, err, warn, dim, norm))
    code = lambda c: f"38;2;{c[0]};{c[1]};{c[2]}"
    pal = {"ok": code(ok), "err": "1;" + code(err), "dim": code(dim), "bold": "1;" + code(norm)}
    link = pick("ForegroundLink", "DecorationFocus")
    if link:                                       # a scheme's link colour is usually a blue: its shades make the 3D art
        base = _readable(link, bg)
        mix = lambda c, t, k: tuple(round(v + (t - v) * k) for v in c)
        pal.update({"blue": code(base), "blue_hi": code(mix(base, 255, 0.45)),
                    "blue_lo": code(mix(base, 0, 0.40)), "blue_sh": code(mix(base, 0, 0.62))})
    far = lambda a, b, n: sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5 > n
    if far(warn, ok, 60) and far(warn, err, 45):       # keep warnings apart from the other two
        pal["warn"] = code(warn)
    return pal


def show_colors(scheme=None):
    "dl.py --colors [NAME]: print the colours in use, or how a scheme would look."
    if scheme:
        pal = kde_palette(scheme)
        if pal is None:
            print(f"Colour theme '{scheme}' was not found (or this terminal has no true colour). "
                  "See the list with:  dl.py --themes")
        PALETTE.update(DEFAULT_PALETTE)
        PALETTE.update(pal or {})
        title = f"preview of the colour scheme '{scheme}'" if pal else "the default colours"
    elif PALETTE == ANSI_PALETTE:
        title = "standard terminal colours (16 colours)"
    elif PALETTE == DEFAULT_PALETTE:
        title = "the default colours (dark magenta, yellow, neon cyan)"
    elif ACTIVE_THEME.lower() == "auto":
        title = "your active KDE Plasma colour scheme"
    else:
        title = f"colour scheme '{ACTIVE_THEME}' (from config.ini)"
    print("Colour theme:", title)
    for kind, text in (("ok", "Downloaded / KEEP / good news"), ("warn", "WARNING / MOVE / careful"),
                       ("err", "ERROR / failed / problem"), ("dim", "Skipped / waiting / low importance"),
                       ("bold", "Headings and labels")):
        print(f"  {kind:5} {paint(text, kind)}   [{PALETTE[kind]}]")
    if not COLOR_ON:
        print("(Colours are off here: no terminal, NO_COLOR is set, or color = no.)")


def show_themes():
    "dl.py --themes: the KDE colour schemes that can be used as  theme = NAME."
    names = installed_schemes()
    if not names:
        print("No KDE colour schemes found in ~/.local/share/color-schemes or /usr/share/color-schemes.")
        print("You can also set  theme = /full/path/to/Scheme.colors")
        return
    print(f"{len(names)} colour schemes found. Use one in config.ini as  theme = NAME  "
          "(preview: dl.py --colors NAME):")
    print("  " + ", ".join(names))


def init_color(mode, theme="auto", quiet=False):
    "Switch colours on or off for this process. On Windows this also enables ANSI codes."
    global COLOR_ON, ACTIVE_THEME
    ACTIVE_THEME = theme
    mode = (mode or "auto").lower()
    if mode == "no" or os.environ.get("NO_COLOR") is not None and mode != "yes":
        COLOR_ON = False
        return
    if mode == "auto" and (not sys.stdout.isatty() or os.environ.get("TERM") == "dumb"):
        COLOR_ON = False
        return
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.GetStdHandle(-11)
            m = ctypes.c_uint()
            ok = bool(k.GetConsoleMode(h, ctypes.byref(m))) and bool(k.SetConsoleMode(h, m.value | 0x0004))
        except Exception:
            ok = False
        if not ok and mode == "auto":
            COLOR_ON = False
            return
    COLOR_ON = True
    if theme.lower() == "ansi" or not _truecolor():
        PALETTE.update(ANSI_PALETTE)             # standard 16 colours
        return
    if theme.lower() == "default":
        return                                   # the built-in colours above
    if theme.lower() == "auto":
        PALETTE.update(kde_palette() or {})
    else:                                        # the name of any installed colour scheme, active or not
        pal = kde_palette(theme)
        if pal is None and not quiet:
            print(f"NOTE: colour theme '{theme}' was not found, or this terminal has no true colour. "
                  "Using standard colours. List the themes with:  dl.py --themes")
        PALETTE.update(pal or {})


def paint(text, kind):
    return f"\033[{PALETTE[kind]}m{text}\033[0m" if COLOR_ON else text


_OK_START = ("Downloaded ", "Upgraded", "SoundCloud upgrade finished", "Moved ", "All passes finished",
             "Created ", "No failed songs", "Nothing to clean up")
_DIM_START = ("Playlist files", "Skipped", "Skipping", "Found ", "Processing query", "Waiting ", "Saved ", "Checking ",
              "Library check", "Nothing was moved")
_WARN_START = ("SoundCloud failed", "No close match", "Kept the YouTube", "NOTE", "Note:", "Fallback")


def colorize_line(line):
    "Colour one line of output by what it says. Lines that already have colour are left alone."
    if not COLOR_ON or "\033" in line:
        return line
    t = re.sub(r"^\[\d\d:\d\d\]\s*", "", line.strip())
    if not t:
        return line
    if t.startswith(_WARN_START) or t.startswith("ETA ") or "WARNING" in t:
        kind = "warn"                                                   # warnings and the ETA line: yellow
    elif (t.startswith(("Reason:", "LookupError", "Traceback", "Stopped"))
          or "ERROR" in t or "failed" in t.lower() and not t.startswith("No failed") or is_block(t)):
        kind = "err"
    elif t.startswith(_OK_START) or "Upgraded" in t or t.startswith("Pass ") and "finished" in t:
        kind = "ok"
    elif t.startswith(_DIM_START):
        kind = "dim"
    elif t.startswith("---") or re.match(r"Pass \d", t):
        kind = "bold"
    else:
        return line
    return paint(line, kind)


def cprint(text=""):
    print(colorize_line(text))


LINK_ID_RE = re.compile(r"playlist[/:]([A-Za-z0-9]{22})")   # link or spotify: URI
CHILD_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1")

# Runs spotDL with these changes:
# 1. When a SoundCloud download fails (DRM, removed track, ...), the song is searched on the
#    other sources of the run (default: YouTube) and downloaded from there at once.
# 2. When no source gives a close match, a loose YouTube match is used: length within 15 %
#    (at least 20 s), a word of the title and of the artist in the result, and no cover,
#    karaoke, live or remix version unless the Spotify title has that word too.
#    spotDL alone rejects many official videos (the uploader name does not match).
# 3. The real reason of a failed download is printed (spotDL hides it).
# 4. A song is skipped when a file with the same title and artist is in any folder of the
#    music folder (file names "Title - Artist - Album"). This finds songs that have no
#    Spotify link in their tags (renamed, retagged or from other sources).
# Each YouTube search runs once per song. The fallback reuses its results (no extra request).
# If spotDL changes inside and the changes cannot be applied, spotDL runs unchanged and
# the retry passes in main() still send failed songs to YouTube.
SPOTDL_PATCH = r"""
try:
    import re as _re
    import spotdl.download.downloader as _D
    from spotdl.providers.audio import YouTube as _YT, SoundCloud as _SC
    from spotdl.providers.audio.base import AudioProvider as _AP, AudioProviderError as _AErr
    from spotdl.utils.formatter import create_song_title as _title
    _seen = {}
    _cache = {}
    _NOFB = bool(__import__("os").environ.get("DL_NO_FALLBACK"))   # the SoundCloud upgrade run
    def _mark(song, url):
        "One line per finished download. dl.py reads it: Spotify link, download link, name."
        try:
            print("DL-OK " + song.url + " " + url + " " + song.display_name, flush=True)
        except Exception:
            pass
    _o_results = _YT.get_results
    def _results(self, term, *a, **k):
        if term not in _cache:                       # one YouTube search per song
            _cache[term] = _o_results(self, term, *a, **k)
        return _cache[term]
    _YT.get_results = _results
    _BAD = ("cover", "karaoke", "instrumental", "reaction", "live", "8d", "slowed",
            "sped up", "nightcore", "remix", "tutorial", "lesson")
    def _words(t):
        return [w for w in _re.findall(r"[a-z0-9]+", (t or "").lower()) if len(w) > 2 and w != "the"]
    def _loose(song, results):
        want = song.duration or 0
        name = (song.name or "").lower()
        words = _words(song.name) or _re.findall(r"[a-z0-9]+", name)
        art = _words(song.artist)
        for r in results[:5]:
            title = (r.name or "").lower()
            text = title + " " + (r.author or "").lower()
            if want and r.duration and abs(r.duration - want) > max(20, want * 0.15):
                continue
            if words and not any(w in title for w in words):
                continue
            if art and not any(w in text for w in art):
                continue
            if _bad(title, name):
                continue
            return r.url
        return None
    def _bad(title, name):
        "True if the title is a cover, karaoke, live, ... version that the Spotify title does not name."
        return any(_re.search(r"\b" + b + r"\b", title) and not _re.search(r"\b" + b + r"\b", name)
                   for b in _BAD)
    def _clean(song, url):
        "spotDL can pick a karaoke or cover video. Swap it for a clean match when one exists."
        try:
            return _clean2(song, url)
        except Exception:                            # never break a normal download
            return url
    def _clean2(song, url):
        res = _cache.get(_title(song.name, song.artists).lower()) or []
        pick = next((r for r in res if r.url == url), None)
        if pick is None or not _bad((pick.name or "").lower(), (song.name or "").lower()):
            return url
        better = _loose(song, res)
        if better and better != url:
            print("Skipped a cover/karaoke/live video for " + song.display_name + ". Using a clean match.",
                  flush=True)
            return better
        return url
    def _find(dl, song):
        try:
            return _find2(dl, song)
        except Exception as e:                       # never break a normal download
            print("  Fallback search failed: " + repr(e)[:150], flush=True)
            return None, None
    def _find2(dl, song):
        provs = [p for p in dl.audio_providers if not isinstance(p, _SC)]
        if not any(isinstance(p, _YT) for p in provs):
            st = dl.settings
            provs.append(_YT(output_format=st["format"], cookie_file=st["cookie_file"],
                             search_query=st["search_query"], filter_results=st["filter_results"],
                             yt_dlp_args=st["yt_dlp_args"]))
        for p in provs:                              # strict match first
            try:
                u = p.search(song, dl.settings["only_verified_results"])
            except Exception as e:
                print("  " + p.name + " search failed: " + str(e)[:150], flush=True)
                u = None
            if u:
                return _clean(song, u), p.name
        for p in provs:                              # then a loose YouTube match
            if isinstance(p, _YT):
                try:
                    res = p.get_results(_title(song.name, song.artists).lower())
                except Exception:
                    res = []
                u = _loose(song, res)
                if u:
                    return u, "YouTube (loose match)"
                print("  YouTube: " + str(len(res)) + " results, none close enough for "
                      + song.display_name, flush=True)
        return None, None
    import os as _os
    _EXT = {".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav"}
    _lib = {}
    def _norm(t):
        return " ".join(_re.findall(r"[^\W_]+", (t or "").lower()))
    _NZ = _re.compile(r"\b(?:\d{4}\s+)?(?:digital\s+)?(?:remaster(?:ed)?|mono|stereo)\b(?:\s+(?:19|20)\d\d)?(?:\s+version)?")
    _VW = _re.compile(r"\b(live|remix|acoustic|instrumental|karaoke|slowed|sped|nightcore|8d|demo|cover|edit|bootleg|mashup|reverb|unplugged|extended|radio|mix|dub|orchestral|piano|cappella|version|reprise|medley|session|remake|rework|flip)\b")
    def _core(t):
        "Title and artist without remaster tags and (feat. ...) brackets. Live, remix... stay."
        s = _NZ.sub(" ", (t or "").lower())
        s = _re.sub(r"[\(\[]\s*(?:(?:feat|ft|featuring|with|prod|from|remaster|remastered|deluxe|explicit|clean|bonus|mono|stereo|single|album)\b[^\)\]]*)?[\)\]]", " ", s)   # only harmless brackets go
        s = _re.sub(r"\b(?:feat|ft|featuring)\b\.?[^-]*", " ", s)
        return " ".join(_re.findall(r"[^\W_]+", s))
    def _ak(t):
        "First artist only: files can hold 'A, B', Spotify gives the first one."
        return _norm(_re.split(r"\s*(?:[;,&/]|\bfeat\b\.?)\s*", (t or "").lower())[0])
    def _keys():
        "Set of 'title|first artist' keys of every song file (plain and cleaned). Built once."
        if "k" not in _lib:
            ks, seen = set(), set()
            for d, dirs, files in _os.walk(".", followlinks=True):   # spotDL runs in the music folder
                dirs[:] = [x for x in dirs if x != "_Duplicates" and not x.startswith(".")]
                real = _os.path.realpath(d)
                if real in seen:                     # a folder link that loops: stop here
                    dirs[:] = []
                    continue
                seen.add(real)
                for f in files:
                    stem, ext = _os.path.splitext(f)
                    if ext.lower() in _EXT:
                        parts = stem.split(" - ")    # Title - Artist - Album. A title can hold " - ".
                        for n in range(1, len(parts)):
                            title, artist = " - ".join(parts[:n]), _ak(parts[n])
                            ks.add(_norm(title) + "|" + artist)
                            ks.add(_core(title) + "|" + artist)
            _lib["k"] = ks
        return _lib["k"]
    def _have(song):
        art = _ak(song.artist)
        if not art:
            return False
        ks = _keys()
        return any(k and k + "|" + art in ks for k in (_norm(song.name), _core(song.name)))
    _o_dms = _D.Downloader.download_multiple_songs
    def _dms(self, songs):
        try:
            keep, skip = [], 0
            for s in songs:
                if s.url not in self.url_archive and _have(s):
                    skip += 1
                    self.url_archive.add(s.url)      # spotDL saves the archive at the end
                else:
                    keep.append(s)
            if skip:
                print("Skipped " + str(skip) + " songs that are already in your music folders "
                      "(same title and artist).", flush=True)
            songs = keep
            print("DL-PLAN " + str(sum(1 for s in keep if s.url not in self.url_archive)), flush=True)
        except Exception as e:                       # never break the download
            print("Note: the library name check is off (" + repr(e)[:150] + ").", flush=True)
        return _o_dms(self, songs)
    _D.Downloader.download_multiple_songs = _dms
    _o_search = _D.Downloader.search
    def _search(self, song):
        try:
            url = _clean(song, _o_search(self, song))
        except LookupError:
            if _NOFB:
                raise
            url, how = _find(self, song)
            if not url:
                raise
            print("No close match for " + song.display_name + ". Using " + how + ".", flush=True)
            song.download_url = url                  # the log line and the tags show this link
        _seen[url] = (self, song)
        return url
    _D.Downloader.search = _search
    _o_get = _AP.get_download_metadata
    def _why(e):
        c = e.__cause__ if e.__cause__ is not None else e
        t = str(c).strip().splitlines()
        return (t[0] if t else repr(c))[:200]
    def _get(self, url, download=False):
        try:
            out = _o_get(self, url, download)
            if download and url in _seen:
                _mark(_seen[url][1], url)
            return out
        except _AErr as e:
            if download:                             # spotDL hides this. dl.py needs it to see blocks.
                print("  Reason: " + _why(e), flush=True)
            if _NOFB or not (download and "soundcloud.com" in url and url in _seen):
                raise
            dl, song = _seen[url]
            new, how = _find(dl, song)
            if not new:
                print("SoundCloud failed for " + song.display_name
                      + " and YouTube has no match. A retry pass tries again.", flush=True)
                raise
            print("SoundCloud failed for " + song.display_name + ". Using " + how + ".", flush=True)
            song.download_url = new                  # the log line and the tags show the real source
            try:
                out = _o_get(self, new, download)
                _mark(song, new)
                return out
            except _AErr as e2:
                print("  Reason: " + _why(e2), flush=True)
                raise
    _AP.get_download_metadata = _get
except Exception as _e:
    print("Note: the instant YouTube fallback is off (" + repr(_e) + ").", flush=True)
"""
SPOTDL_RUNNER = SPOTDL_PATCH + """
import sys
sys.argv[0] = "spotdl"
try:
    from spotdl.console.entry_point import console_entry_point
except ImportError:                          # newer spotDL moved it: run it the normal way
    import runpy
    runpy.run_module("spotdl", run_name="__main__", alter_sys=True)
else:
    console_entry_point()
"""

CONFIG_TEMPLATE = """\
# dl.py settings. Lines that start with # are ignored.
# Leave a value empty to use the default.
# Use full paths, without quotes. On Windows, forward slashes work:
#   music_dir = C:/Users/Jack/Music

[paths]
# Folder where songs are saved. Default: your Music folder (the one File Explorer
# shows, OneDrive included).
music_dir =

# Text file with your playlist IDs. Default: playlists.txt next to dl.py
playlists_file =

# OPTIONAL - text file with your own playlist groups, one per line:
#   Autumn Vibes = chill autumn vibes, cozy autumn vibes
# (the names are the playlists' Spotify names, or their IDs). Default: playlist_groups.txt
# next to dl.py. The file does not need to exist.
playlist_groups_file =

# Folder for logs and error lists. Default: <home>/.dl-music
state_dir =

# Folder for the private spotDL install. Default: <state_dir>/venv
# KEEP BLANK FOR WINDOWS. The script builds this private environment for you,
# so you do not need to fill it in. If you set it: use the venv folder itself
# (the one that holds pyvenv.cfg), not its bin or Scripts folder.
venv_dir =

# List of finished songs. Songs in it are skipped on the next run.
# Default: <state_dir>/archive.txt
archive_file =

# OPTIONAL - text file with your own genre rules, one per line: Artist=Folder
# Example line:   Hans Zimmer=Soundtrack
# These rules win over the automatic genre. Default: genre_overrides.txt next
# to dl.py. The file does not need to exist.
genre_overrides_file =

# OPTIONAL - YouTube cookies file. SKIP THIS unless downloads fail with
# "not a bot" or "age-restricted" errors. Leave empty to skip it.
# Used only in the retry passes that run after the long wait.
#
# HOW TO MAKE THE FILE (about 3 minutes)
#  1. Install a browser extension that exports "cookies.txt".
#     Firefox: "cookies.txt".  Chrome / Edge: "Get cookies.txt LOCALLY".
#     Allow it to run in private / incognito windows.
#  2. Open a NEW private window. Log in to YouTube (use a spare account).
#  3. In the same tab, open  https://www.youtube.com/robots.txt
#  4. Click the extension. Export the cookies for youtube.com. Save the file
#     as youtube-cookies.txt in a folder you will remember. Close the window.
#     Do not log in to that account in a browser again.
#  5. Put the full path of the file below, for example:
#       cookies_file = C:/Users/Jack/youtube-cookies.txt
#     Avoid spaces in the path.
#
# SAFETY: this file gives access to the account. Never share it or upload it.
# Delete it when you are finished. The first line of the file must be
# "# Netscape HTTP Cookie File". The script checks this.
cookies_file =

[timing]
# Seconds to wait between playlists
pause_seconds = 120
# Seconds to wait after a failed YouTube test download
gate_wait_seconds = 600
# Seconds to wait before each retry pass
pass_wait_seconds = 1800
# This many block-type errors in one playlist = playlist counts as failed
max_block_errors = 8
# This many failed playlists in a row = stop everything
max_failed_in_row = 3

[sources]
# Where to look for each song, in order. The first source that has the song wins.
# Words: soundcloud, youtube, youtube-music. Separate them with spaces.
# Default: youtube soundcloud
audio_sources = youtube soundcloud

# yes = songs that came from YouTube are tried on SoundCloud afterwards. If SoundCloud
# has a good match (no DRM, about the same length, not a cover or remix), it replaces
# the YouTube file. The check is strict because SoundCloud also has remixes, covers and
# 30 second previews. Needs soundcloud in audio_sources. Default: yes
# The replacement is a fresh download: its tags are written again by spotDL.
# no = keep the YouTube file. SoundCloud is then only used when YouTube has no match.
prefer_soundcloud = yes

[updates]
# yes = update spotDL and yt-dlp once a day (recommended: YouTube changes often,
# and an old yt-dlp gets more errors). no = never update.
auto_update = yes

[sorting]
# yes = after the downloads finish, move songs into genre folders inside
# music_dir (Pop, Rock, Hip-Hop, ...). no = leave all songs in music_dir.
# To sort without downloading:  dl.py --sort
# To preview without moving:    dl.py --sort --dry-run
sort_after_download = yes

[playlists]
# Playlist files: one .m3u8 file per playlist, linking every song that is in your library, so
# any music player can play the playlist. They are rebuilt after every run (after sorting and
# after moving duplicates), so the links always point to where each song is now.
# yes = every playlist gets a file, unless you put X after its ID in playlists.txt.
# no  = only playlists that have P after the ID get a file.
playlist_files = yes

# yes = playlists with similar names are combined into one file: "chill autumn vibes" and
# "cozy autumn vibes" become "Autumn Vibes". A song that is in both is listed once.
# Check what would be combined first:  dl.py --playlists --dry-run
# Control it yourself: S after an ID = never combine it, =Group name after an ID = put it in that
# group, or write your own groups in playlist_groups.txt (see [paths]).
merge_similar = yes

# Folder for the playlist files, inside your music folder. A full path also works.
folder = Playlists

# yes = also make "smart" playlists from your tags, in Playlists/Smart: one per genre folder, decade,
# language (by the alphabet of the title) and favourite artist (10+ songs). Only lists with
# smart_min or more songs are made (language and artist lists need 10).
smart_playlists = yes
smart_min = 25

# yes = after each run, check which songs of your playlists are missing from the library and why
# (no match, DRM, blocked, not downloaded yet). Saved to playlist-gaps.txt in the state folder.
# Run it by hand with:  dl.py --gaps
gap_report = yes

[genres]
# Songs in the folders Other and Unknown, and songs with no genre tag, get a genre from the web
# (MusicBrainz, free, or Last.fm). The genre is written into the file and the song moves to the
# matching genre folder. Every move is written to genre-repair-log.txt in the state folder.
# Preview:  dl.py --genres --dry-run      Do it now:  dl.py --genres
genre_repair = yes

# How many artists to look up per run. MusicBrainz allows one question a second, so 100 takes a few
# minutes. The answers are saved, so every run finishes more of the library.
genre_repair_limit = 100

# OPTIONAL - a free Last.fm API key (last.fm/api). It is faster and has more tags than MusicBrainz.
lastfm_api_key =

[lyrics]
# yes = after a run has finished, look for lyrics for the songs that are in your library and save them
# next to the song: song.lrc (timed, scrolls along in many players) or song.txt (plain).
# Songs that did not download get nothing. Default: no.   By hand:  dl.py --lyrics
lyrics = no

# Where to look, in this order. lrclib = free, timed lyrics. synced = several lyrics sites through the
# syncedlyrics package that spotDL installs. genius = needs genius_token below.
lyrics_sources = lrclib synced

# How many songs to look up per run. A song with no result is asked again after 30 days.
lyrics_limit = 300

# OPTIONAL - your own Genius API access token (genius.com/api-clients).
genius_token =

[display]
# Coloured text on screen (cyan = good, yellow = warning, bold red = problem).
# The log files never contain colours.
# auto = colours in a terminal, none when the output goes to a file (or NO_COLOR is set).
# yes = always. no = never. Default: auto
color = auto

# Which colours to use.
#   default = deep dark magenta for good news, yellow for warnings, bold neon cyan for problems.
#          (This is what you get if you leave it as it is. Needs a true-colour terminal.)
#   auto = copy the KDE Plasma colour scheme that is active now (only on Plasma, in a true-colour
#          terminal such as Konsole or Kitty). Otherwise the standard terminal colours.
#   NAME = copy ANY KDE colour scheme you have installed, even if it is not the active one.
#          Example:  theme = CatppuccinMocha     (a .colors file in ~/.local/share/color-schemes)
#          Or a full path:  theme = /home/me/Downloads/Scheme.colors
#          See the choices:  dl.py --themes     Preview one:  dl.py --colors NAME
#   ansi = the standard 16 terminal colours (magenta, yellow, bright cyan).
# Scheme colours are brightened where needed so they stay easy to read on your background.
# Terminals without true colour always get the standard 16 colours.
theme = default

[dedupe]
# Duplicate songs, broken files and a library report. Nothing is ever deleted: extra
# copies move to the folder _Duplicates inside music_dir. Delete that folder yourself.
# Same song = same Spotify link, or same artist + title + about the same length.
# Live, remix, acoustic... versions are different songs and stay.
# The best copy stays: lossless, then higher bitrate, then cover art, then full tags.
#   Preview now:  dl.py --dedupe --dry-run       Move the copies:  dl.py --dedupe
#   Report only:  dl.py --report

# yes = at the end of every run, show the duplicates that were found. Moves nothing.
# no = never show it (you can still run dl.py --dedupe yourself).
preview_at_end = yes

# yes = also compare the sound of songs by the same artist. Finds copies whose names
# differ. The first run is slow (about 0.3 s per song, saved afterwards). It downloads
# a small helper (fpcalc, about 2 MB) once. Works on Windows, Linux and macOS.
fingerprint = no

# yes = move empty or unreadable song files and leftover .new files to _Duplicates/_Broken,
# and remove empty folders inside the genre folders.
cleanup = yes

# yes = when a copy moves away, give the kept copy its genre if it has none.
merge_tags = yes

# yes = write library-report.txt (song count, size, formats, weakest files) when you run
# --dedupe or --report.
library_report = yes
"""

PLAYLISTS_TEMPLATE = """\
# =====================================================================
#  PLAYLISTS TO DOWNLOAD
# =====================================================================
# HOW TO USE
#   1. Scroll down to the line  ADD YOUR PLAYLISTS BELOW THIS LINE.
#   2. Add ONE playlist per line. Nothing else on the line.
#   3. Save this file. Then run dl.py again.
#
# WHAT TO PASTE
#   Easiest: paste the whole playlist link. The script finds the ID in it.
#     https://open.spotify.com/playlist/1r9DRLBzZqkPU0aDBVycY8?si=abc123
#   Or paste only the ID. It is the 22 characters between "/playlist/"
#   and "?si=". Do not add "?si=" or anything after it.
#     1r9DRLBzZqkPU0aDBVycY8
#   To get the link in Spotify: open the playlist, click the ... menu,
#   choose Share, then Copy link to playlist.
#
# WHAT WORKS
#   - Public playlists, yours or other people's.
#
# WHAT DOES NOT WORK
#   - Spotify-made playlists: Daily Mix, Discover Weekly, Release Radar,
#     "This Is ...", mood and genre lists. Their IDs start with 37i9dQZF.
#     Fix: make your own playlist, copy the songs into it, use that one.
#   - Private playlists. Set the playlist to public first.
#   - Liked Songs. It has no link. Copy the songs into a public playlist.
#
# RULES SO NOTHING BREAKS
#   - Lines that start with # are ignored. Do not remove the # signs.
#   - Quotes, commas and spaces around a link or ID are removed for you.
#   - Duplicates are ignored. Bad lines are skipped with a message.
#   - You can add a note after a link, behind a #. Example:
#       1r9DRLBzZqkPU0aDBVycY8   # road trip mix
#
# PLAYLIST FILES (OPTIONAL CONTROLS)
#   Every playlist also gets a playlist file (.m3u8) in your music folder, linking its songs, and
#   playlists with similar names are combined ("chill autumn vibes" + "cozy autumn vibes" =
#   "Autumn Vibes"). You do not have to do anything. To control it, add a letter after the ID:
#       1r9DRLBzZqkPU0aDBVycY8 X                no playlist file for this one
#       1r9DRLBzZqkPU0aDBVycY8 S                its own file, never combined
#       1r9DRLBzZqkPU0aDBVycY8 P                make a file (needed if playlist_files = no)
#       1r9DRLBzZqkPU0aDBVycY8 P=Road Trip      put it in the group "Road Trip"
#   The letter can also touch the ID: 1r9DRLBzZqkPU0aDBVycY8X. A 22 character ID is always
#   complete, so an ID that happens to end in P or X is safe.
#   See what would be combined, without writing anything:  python dl.py --playlists --dry-run
#
# GOOD TO KNOW
#   - Playlists download from top to bottom. Put the ones you want first at the top.
#   - Safe to run again and again: songs you already have are skipped, so only new
#     songs download. Add new playlists at the bottom whenever you like.
#   - New here? Try ONE small playlist first, then add the rest.
#   - Each song is looked up on YouTube first, then SoundCloud. If SoundCloud has a good
#     version it replaces the YouTube one. See [sources] in config.ini.
#   - At the end of a run the script lists duplicate songs but moves nothing.
#     Move them with:  python dl.py --dedupe    (see [dedupe] in config.ini)
#   - Other commands:  --sort  --dedupe  --report  --upgrade  (python dl.py --help)
#
# >>>>>>>>>>>>>>>>  ADD YOUR PLAYLISTS BELOW THIS LINE  <<<<<<<<<<<<<<<<

"""


def default_music_dir():
    "Windows: the Music folder that File Explorer shows (can be inside OneDrive)."
    if IS_WIN:
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
            value, _ = winreg.QueryValueEx(key, "My Music")
            winreg.CloseKey(key)
            return Path(os.path.expandvars(value))
        except Exception:
            pass
    return Path.home() / "Music"


def load_config():
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(CONFIG_FILE, encoding="utf-8")
    home = Path.home()

    def path(key, default):
        # "Copy as path" in Windows adds quotes. Remove them.
        v = cp.get("paths", key, fallback="").strip().strip("\"'").strip()
        if not v:
            return default
        p = Path(os.path.expanduser(v))
        return p if p.is_absolute() else (HERE / p).resolve()

    def number(key, default):
        v = cp.get("timing", key, fallback="").strip()
        try:
            return int(v) if v else default
        except ValueError:
            sys.exit(f"config.ini: [timing] {key} must be a whole number.")

    def number_in(section, key, default):
        v = cp.get(section, key, fallback="").strip()
        try:
            return int(v) if v else default
        except ValueError:
            sys.exit(f"config.ini: [{section}] {key} must be a whole number.")

    def yes_no(key, default, section="sorting"):
        v = cp.get(section, key, fallback="").strip().lower()
        if not v:
            return default
        if v not in ("yes", "no", "true", "false", "1", "0", "on", "off"):
            sys.exit(f"config.ini: [{section}] {key} must be yes or no.")
        return v in ("yes", "true", "1", "on")

    def sources(key, default):
        v = cp.get("sources", key, fallback="").replace(",", " ").split()
        v = [w.lower() for w in v] or default
        bad = [w for w in v if w not in ("soundcloud", "youtube", "youtube-music")]
        if bad:
            sys.exit(f"config.ini: [sources] {key}: unknown source {bad[0]}. "
                     "Use soundcloud, youtube, youtube-music.")
        return list(dict.fromkeys(v))            # remove repeats, keep the order

    state = path("state_dir", home / ".dl-music")
    return {
        "music_dir": path("music_dir", default_music_dir()),
        "playlists_file": path("playlists_file", HERE / "playlists.txt"),
        "state_dir": state,
        "venv_dir": path("venv_dir", state / "venv"),
        "archive_file": path("archive_file", state / "archive.txt"),
        "cookies_file": path("cookies_file", None),
        "pause": number("pause_seconds", 120),
        "gate_wait": number("gate_wait_seconds", 600),
        "pass_wait": number("pass_wait_seconds", 1800),
        "max_block": number("max_block_errors", 8),
        "max_bad": number("max_failed_in_row", 3),
        "overrides_file": path("genre_overrides_file", HERE / "genre_overrides.txt"),
        "sort_after": yes_no("sort_after_download", True),
        "audio": sources("audio_sources", ["youtube", "soundcloud"]),
        "prefer_sc": yes_no("prefer_soundcloud", True, "sources"),
        "auto_update": yes_no("auto_update", True, "updates"),
        "color": cp.get("display", "color", fallback="auto").strip().lower() or "auto",
        "theme": cp.get("display", "theme", fallback="auto").strip().strip("\"'") or "default",
        "gen_repair": yes_no("genre_repair", True, "genres"),
        "gen_limit": number_in("genres", "genre_repair_limit", 100),
        "lastfm_key": cp.get("genres", "lastfm_api_key", fallback="").strip(),
        "gap_report": yes_no("gap_report", True, "playlists"),
        "smart": yes_no("smart_playlists", True, "playlists"),
        "smart_min": number_in("playlists", "smart_min", 25),
        "lyrics": yes_no("lyrics", False, "lyrics"),
        "lyrics_sources": [w.lower() for w in cp.get("lyrics", "lyrics_sources", fallback="lrclib synced genius").replace(",", " ").split()],
        "lyrics_limit": number_in("lyrics", "lyrics_limit", 300),
        "genius_token": cp.get("lyrics", "genius_token", fallback="").strip(),
        "pl_files": yes_no("playlist_files", True, "playlists"),
        "pl_merge": yes_no("merge_similar", True, "playlists"),
        "pl_folder": cp.get("playlists", "folder", fallback="Playlists").strip().strip("\"'") or "Playlists",
        "groups_file": path("playlist_groups_file", HERE / "playlist_groups.txt"),
        "dd_preview": yes_no("preview_at_end", True, "dedupe"),
        "dd_fingerprint": yes_no("fingerprint", False, "dedupe"),
        "dd_cleanup": yes_no("cleanup", True, "dedupe"),
        "dd_merge": yes_no("merge_tags", True, "dedupe"),
        "dd_report": yes_no("library_report", True, "dedupe"),
    }


def create_missing_files(need_playlists=True):
    made = []
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        made.append(CONFIG_FILE)
    pf = load_config()["playlists_file"]
    if need_playlists and not pf.exists():
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(PLAYLISTS_TEMPLATE, encoding="utf-8")
        made.append(pf)
    if made:
        print("Created:")
        for f in made:
            print("  ", f)
        print("Edit these files, then run dl.py again.")
        if IS_WIN:
            for f in made:                       # open each file in the default editor
                try:
                    os.startfile(str(f))
                except OSError:
                    pass
            print("The files are open in your editor now. Save them, then start again.")
        sys.exit(0)


def load_ids(path, flags=None):
    """Read playlist IDs. Accepts bare IDs, full links, spotify: URIs.
    Strips quotes, commas, spaces and ?si=... Skips bad lines. Removes duplicates.
    After the ID you can add a playlist-file flag: P (make a playlist file), X (do not),
    S (make one, never merged) and =Group name (put it in that group). Examples:
        1r9DRLBzZqkPU0aDBVycY8 X        1r9DRLBzZqkPU0aDBVycY8P        link S
        1r9DRLBzZqkPU0aDBVycY8 P=Autumn Vibes
    A 22 character ID is always complete. A 23rd character P, S or X is the flag.
    flags (a dict, if given) gets  id -> (flag or "", group name or "")."""
    ids, seen = [], set()
    for n, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = LINK_ID_RE.search(line)
        if m:
            pid, rest = m.group(1), line[m.end():]
        else:
            cleaned = line.strip("\"'` ,;")
            pid = re.split(r"[?\s,;=]", cleaned)[0]
            rest = cleaned[len(pid):]
            if len(pid) == 23 and pid[-1].upper() in "PSX" and ID_RE.fullmatch(pid[:22]):
                pid, rest = pid[:22], pid[22:] + " " + rest
        rest = re.sub(r"^\?\S*", "", rest.strip(" ,;")).strip(" ,;")
        fm = re.match(r"^([PSXpsx])?\s*(?:=\s*(.+))?$", rest)
        flag, group = ((fm.group(1) or "").upper(), (fm.group(2) or "").strip()) if fm else ("", "")
        where = f"{path.name} line {n}"
        if pid.startswith("37i9dQZF"):
            print(f"SKIPPED {where}: Spotify-made playlists do not work ({pid})")
        elif not ID_RE.fullmatch(pid):
            print(f"SKIPPED {where}: not a playlist ID or link: {line}")
        elif pid not in seen:
            seen.add(pid)
            ids.append(pid)
            if flags is not None:
                flags[pid] = (flag, group)
        elif flags is not None:                                   # the same ID twice: P wins, a group fills in
            old_flag, old_group = flags.get(pid, ("", ""))
            flags[pid] = ("P" if "P" in (flag, old_flag) else (flag or old_flag), old_group or group)
    return ids


def ensure_spotdl(venv_dir):
    "Create a private venv with spotDL on first run. Return its Python."
    if (not (venv_dir / "pyvenv.cfg").exists() and venv_dir.name.lower() in ("bin", "scripts")
            and (venv_dir.parent / "pyvenv.cfg").exists()):
        print(f"Note: venv_dir is the {venv_dir.name} folder of a venv. Using {venv_dir.parent}.")
        venv_dir = venv_dir.parent
    py = venv_dir / ("Scripts/python.exe" if IS_WIN else "bin/python")
    check = [str(py), "-c", "import spotdl, yt_dlp, mutagen"]
    if py.exists() and subprocess.run(check, capture_output=True).returncode == 0:
        return py
    print(f"First run: installing spotDL into {venv_dir}")
    print("This takes 2 to 5 minutes. Pip output is shown below. Do not close this window.")
    try:
        venv.create(venv_dir, with_pip=True)
    except Exception as e:
        sys.exit(f"Could not create the venv: {e}\n"
                 "On Debian/Ubuntu run: sudo apt install python3-venv")
    r = subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "pip", "spotdl"])
    if r.returncode != 0 or subprocess.run(check, capture_output=True).returncode != 0:
        sys.exit("spotDL install failed. Check your internet connection, then start again.")
    print("spotDL installed.")
    return py


def update_tools(py, state_dir):
    "Update spotDL and yt-dlp once a day. YouTube changes often, and an old yt-dlp gets more errors."
    stamp = state_dir / "last-update"
    try:
        if stamp.exists() and time.time() - stamp.stat().st_mtime < 86400:
            return
    except OSError:
        pass
    print("Checking for spotDL and yt-dlp updates (once a day)...")
    try:
        # Both together, so pip keeps the yt-dlp version that spotDL supports.
        r = subprocess.run([str(py), "-m", "pip", "install", "-q", "--upgrade",
                            "spotdl", "yt-dlp[default]"], timeout=600)
        ok = r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if ok:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(datetime.now().isoformat(), encoding="utf-8")
    else:
        print("Update check failed. Continuing with the installed versions.")


def find_ffmpeg(py):
    "Return the path of ffmpeg (system, or the copy spotDL keeps), or None."
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    # spotDL knows where it keeps its own copy. This differs by OS (Linux: ~/.config/spotdl).
    r = subprocess.run(
        [str(py), "-c", "from spotdl.utils.ffmpeg import get_ffmpeg_path; print(get_ffmpeg_path() or '')"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = r.stdout.strip().splitlines()
    path = Path(lines[-1]) if lines and lines[-1].strip() else None
    return path if path and path.is_file() else None


def ensure_ffmpeg(py):
    "Make sure ffmpeg exists. spotDL downloads its own copy if the system has none."
    ff = find_ffmpeg(py)
    if ff:
        return ff
    print("ffmpeg not found. Downloading it with spotDL (about 1 minute)...")
    subprocess.run([str(py), "-m", "spotdl", "--download-ffmpeg"])
    ff = find_ffmpeg(py)
    if not ff:
        sys.exit("ffmpeg download failed. Check your internet connection, then start again.\n"
                 "Or install it yourself: winget install Gyan.FFmpeg")
    print(f"ffmpeg ready: {ff}")
    return ff


def yt_args(js, cookies, slow=True):
    """Build the yt-dlp argument string that spotDL passes on."""
    parts = list(js)
    if cookies:
        # Forward slashes and double quotes survive spotDL's argument splitting on Windows.
        p = f'"{cookies.as_posix()}"' if IS_WIN else shlex.quote(str(cookies))
        parts += ["--cookies", p]
    if slow:                                     # YouTube blocks fast clients
        parts += ["--sleep-interval", "15", "--max-sleep-interval", "40", "--sleep-requests", "2"]
    else:                                        # SoundCloud needs far less
        parts += ["--sleep-interval", "1", "--max-sleep-interval", "3"]
    return " ".join(parts)


def test_download(py, js, ffmpeg, cookies=None):
    "Return (True, '', False) if YouTube lets us download. Else (False, reason, looks like a block)."
    # -f bestaudio/best = the same file that -x picks, without the slow conversion afterwards.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        r = subprocess.run(
            [str(py), "-m", "yt_dlp", "-q", *js, "--ffmpeg-location", str(ffmpeg),
             "-f", "bestaudio/best", *(["--cookies", str(cookies)] if cookies else []),
             "-o", str(Path(d) / "test.%(ext)s"), TEST_URL],
            env=CHILD_ENV, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    if r.returncode == 0:
        return True, "", False
    lines = [x.strip() for x in r.stderr.splitlines() if x.strip()]
    return False, (lines[-1][:150] if lines else f"exit code {r.returncode}"), bool(BLOCK_RE.search(r.stderr))


def _fmt_secs(sec):
    "A length of time for people: minutes, hours, days, and weeks only from 14 days on."
    m = int(sec) // 60
    h, m = divmod(m, 60)
    days = h // 24
    if days >= 730:
        return "over 2 years"
    if days >= 14:
        weeks, rest = divmod(days, 7)
        return f"{weeks} weeks {rest} days" if rest else f"{weeks} weeks"
    if h >= 48:
        return f"{days} days {h % 24} h"
    return f"{h} h {m} min" if h else (f"{m} min" if m else "under 1 min")


def local_when(end, now=None):
    """A finish time in the user's own style: day and month names in the system language, 12 or 24 hour clock as
    the system does it, the time zone name, the date only when it is not today, the year only when it is not this year."""
    now = now or datetime.now()
    try:
        locale.setlocale(locale.LC_TIME, "")                     # the system's language and format
    except locale.Error:
        pass
    twelve = "15" not in datetime(2000, 1, 1, 15, 0).strftime("%X")
    clock = end.strftime("%I:%M %p").lstrip("0") if twelve else end.strftime("%H:%M")
    sample = datetime(2000, 11, 23).strftime("%x")                 # 11/23/2000 in the US, 23/11/2000 in the UK...
    month_first = 0 <= sample.find("11") < sample.find("23")
    date = ""
    if end.date() != now.date():
        date = end.strftime("%a %b %d" if month_first else "%a %d %b") + (end.strftime(" %Y") if end.year != now.year else "")
    try:
        tz = end.astimezone().strftime("%Z")                      # a plain datetime counts as local time
    except (OSError, ValueError, OverflowError):
        tz = ""
    return " ".join(x for x in (date, clock, tz) if x)


class Eta:
    """Rough time left in a pass. Learns the seconds per song during the run and the playlist
    sizes from earlier runs (playlist-sizes.json in the state folder)."""

    def __init__(self, state_dir, pause):
        self.file, self.pause, self.rates = state_dir / "playlist-sizes.json", pause, []
        try:
            self.sizes = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.sizes = {}

    def begin_pass(self, todo):
        self.todo, self.found_sum, self.new_sum = list(todo), 0, 0

    def begin_playlist(self, n, pid):
        self.pos, self.pid, self.found, self.new, self.done = n, pid, None, None, 0
        self.t_last = time.time()

    def end_playlist(self):
        self.found_sum += self.found or 0
        self.new_sum += self.new if self.new is not None else (self.found or 0)

    def line(self, raw):
        "Called with every spotDL output line. Returns an ETA line to show, or None."
        t = raw.strip()
        m = re.match(r"Found (\d+) songs", t)
        if m:
            self.found = int(m.group(1))
            self.sizes[self.pid] = self.found
            try:
                self.file.write_text(json.dumps(self.sizes), encoding="utf-8")
            except OSError:
                pass
        elif t.startswith("DL-PLAN "):
            self.new = int(t.split()[1])
            self.t_last = time.time()
            return self.text()
        elif t.startswith("DL-OK "):
            now = time.time()
            self.rates = (self.rates + [now - self.t_last])[-30:]
            self.t_last, self.done = now, self.done + 1
            return self.text()
        return None

    def text(self):
        ordered = sorted(self.rates)                             # the middle value: one stall cannot ruin it
        per = min(600.0, max(5.0, ordered[len(ordered) // 2])) if ordered else 45.0
        cur_new = self.new if self.new is not None else (self.found or 0)
        later = self.todo[self.pos:]
        known = sorted(self.sizes[p] for p in self.todo if p in self.sizes)
        guess = known[len(known) // 2] if len(known) >= 5 else 150      # unread playlists: the typical size
        unread = sum(1 for p in later if p not in self.sizes)
        seen = self.found_sum + (self.found or 0)
        ratio = min(1.0, max(0.05, (self.new_sum + cur_new) / seen)) if seen else 1.0   # how many songs are really new
        songs = max(0, cur_new - self.done) + sum(self.sizes.get(p, guess) for p in later) * ratio
        secs = songs * per + len(later) * self.pause
        end = datetime.now() + timedelta(seconds=secs)
        when = local_when(end)
        guessed = f" | {unread} playlists not read yet (guessed)" if unread else ""
        return (f"ETA about {_fmt_secs(secs)} left in this pass (done around {when}) | playlist "
                f"{self.pos}/{len(self.todo)} | {self.done} of {cur_new} new songs | ~{per:.0f} s/song (rough){guessed}")


def run_playlist(cmd, cwd, log, max_block, marks=None, on_line=None):
    """Run spotDL. Copy output to screen and log. Stop it if too many block errors.
    marks: if given, gets the DL-OK lines of finished downloads (not shown, not logged).
    on_line: if given, called with every line. It may return an ETA line to show (screen only).
    Returns (exit code, stopped by us, block-error lines)."""
    block_lines, hit = [], False
    p = subprocess.Popen(cmd, cwd=cwd, env=CHILD_ENV, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace")
    try:
        for line in p.stdout:
            eta_line = on_line(line) if on_line else None
            if line.startswith("DL-OK ") or line.startswith("DL-PLAN "):
                if marks is not None and line.startswith("DL-OK "):
                    marks.append(line.rstrip("\r\n")[6:])
                if eta_line:
                    sys.stdout.write(colorize_line(eta_line) + "\n")
                continue
            body = line.rstrip("\r\n")
            sys.stdout.write(colorize_line(body) + line[len(body):])
            log.write(line)
            log.flush()
            if is_block(line):
                block_lines.append(line.rstrip())
                if len(block_lines) >= max_block:
                    hit = True
                    p.terminate()
                    break
    except KeyboardInterrupt:
        p.terminate()
        raise
    try:
        p.wait(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
    return p.returncode, hit, block_lines


# ---------------------------------------------------------------------
# GENRE SORTING
# Moves loose songs in music_dir into genre folders (Pop, Rock, ...).
# Runs after the downloads, or alone:  dl.py --sort
# Only songs at the top level of music_dir are moved. Songs that are
# already inside a folder are left alone.
#
# The genre comes from the song's genre tag (spotDL writes it from
# Spotify). A song without a usable tag gets the genre of the same
# artist's other songs. If that fails too, it goes to "Other".
# ---------------------------------------------------------------------
AUDIO_EXT = {".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav"}
DEFAULT_GENRE = "Other"

# EDIT HERE to change the folders. Each row: folder name, then the words that
# put a song in it. Matching is not case sensitive. The FIRST row that matches
# wins, so keep specific rows above general ones. A word matches at the start
# of a word ("metal" also matches "metalcore"). A word that ends with ! must
# match a whole word ("emo!" does not match "emotional").
GENRE_RULES = [
    ("Soundtrack",  ["soundtrack", "score", "film", "movie", "musical", "video game", "anime", "ost!"]),
    ("Classical",   ["classical", "baroque", "opera", "neoclassical", "orchestra", "symphon", "chamber"]),
    ("Metal",       ["metal"]),
    ("Punk",        ["punk"]),
    ("Hip-Hop",     ["hip hop", "hip-hop", "rap", "trap", "drill", "grime"]),
    ("R&B",         ["r&b", "rnb", "soul", "funk", "motown"]),
    ("Country",     ["country", "bluegrass", "americana"]),
    ("Latin",       ["latin", "reggaeton", "salsa", "bachata", "cumbia", "flamenco"]),
    ("Reggae",      ["reggae", "dancehall", "ska!"]),
    ("Jazz",        ["jazz", "bebop", "swing"]),
    ("Blues",       ["blues"]),
    ("Folk",        ["folk", "singer-songwriter", "acoustic"]),
    ("Pop",         ["pop", "synthpop", "electropop", "hyperpop", "boy band", "girl group"]),
    ("Electronic",  ["electro", "edm", "house", "techno", "trance", "dubstep", "drum and bass",
                     "dnb!", "synthwave", "ambient", "downtempo", "idm!", "uk garage", "dance",
                     "disco", "lo-fi", "lofi", "big room"]),
    ("Alternative", ["alternative", "indie", "emo!", "grunge", "shoegaze", "new wave",
                     "britpop", "screamo", "post-hardcore"]),
    ("Rock",        ["rock", "psychedelic"]),
]


def _compile_rules():
    rules = []
    for folder, words in GENRE_RULES:
        parts = []
        for w in words:
            exact = w.endswith("!")
            parts.append(r"(?<![a-z])" + re.escape(w.rstrip("!")) + (r"(?![a-z])" if exact else ""))
        rules.append((folder, re.compile("|".join(parts))))
    return rules


def folder_for_genre(text, rules):
    """Return the folder for a genre tag, or None if no rule matches."""
    text = text.lower().replace("_", " ")
    for folder, rx in rules:
        if rx.search(text):
            return folder
    return None


def folder_for_genres(genres, rules):
    """Try the genre tags in the order they are stored. The first one that has a rule wins."""
    for genre in genres:
        folder = folder_for_genre(genre, rules)
        if folder:
            return folder
    return None


def _clean_name(name):
    """Make a folder name that is safe on Windows, Linux and macOS."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .")
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", name):
        name = "_" + name
    return name or DEFAULT_GENRE


def _load_overrides(path):
    """genre_overrides.txt lines look like:  Artist=Folder"""
    out = {}
    if path and path.is_file():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.split("#", 1)[0].strip()
            if "=" in line:
                artist, folder = line.split("=", 1)
                if artist.strip() and folder.strip():
                    out[artist.strip().lower()] = _clean_name(folder)
    return out


def _tags(mutagen_file, path):
    """Return (primary artist in lower case, list of genre tags). Empty if missing."""
    try:
        audio = mutagen_file(path, easy=True)
        tags = audio.tags if audio is not None and audio.tags is not None else {}
        artist = (tags.get("artist") or tags.get("albumartist") or [""])[0]
        genre = list(tags.get("genre") or [])
    except Exception:
        return "", []
    artist = re.split(r"[/;]", artist)[0].strip().lower()
    return artist, genre


DUP_DIR = "_Duplicates"          # copies of songs that are already in a genre folder go here


def _existing_folders(music_dir):
    "Map lower case name -> real name of the folders in music_dir (so Pop and pop are one folder)."
    return {d.name.lower(): d.name for d in music_dir.iterdir() if d.is_dir()}


def create_genre_folders(music_dir, overrides_file):
    "Make every genre folder (also the empty ones). Reuses a folder that exists in any letter case."
    names = [_clean_name(folder) for folder, _ in GENRE_RULES]
    names += [DEFAULT_GENRE] + sorted(set(_load_overrides(overrides_file).values()))
    have = _existing_folders(music_dir)
    made = []
    for name in dict.fromkeys(names):                # remove repeats, keep the order
        if name.lower() not in have:
            (music_dir / name).mkdir(parents=True, exist_ok=True)
            have[name.lower()] = name
            made.append(name)
    return made


def sort_library(music_dir, overrides_file, dry_run=False):
    """Move loose songs into genre folders. Needs mutagen (in the private venv)."""
    try:
        from mutagen import File as mutagen_file
    except ImportError:
        sys.exit("mutagen is missing. Run dl.py once so it can set itself up.")
    if not music_dir.is_dir():
        print(f"Music folder not found: {music_dir}. Nothing to sort.")
        return
    rules = _compile_rules()
    overrides = _load_overrides(overrides_file)
    if not dry_run:
        create_genre_folders(music_dir, overrides_file)
    loose = sorted(p for p in music_dir.iterdir()
                   if p.is_file() and p.suffix.lower() in AUDIO_EXT)
    if not loose:
        print("No loose songs to sort.")
        return

    votes = {}                       # artist -> {folder: count}
    plan = {}                        # song -> folder
    unknown = []
    for song in loose:
        artist, genre = _tags(mutagen_file, song)
        folder = overrides.get(artist) or folder_for_genres(genre, rules)
        if folder:
            plan[song] = folder
            if artist:
                votes.setdefault(artist, {})
                votes[artist][folder] = votes[artist].get(folder, 0) + 1
        else:
            unknown.append((song, artist))

    if unknown:                      # learn each artist's folder from songs already sorted
        for sub in (p for p in music_dir.iterdir()
                    if p.is_dir() and not p.name.startswith((".", "_"))):
            for song in sub.iterdir():
                if song.is_file() and song.suffix.lower() in AUDIO_EXT:
                    artist, _ = _tags(mutagen_file, song)
                    if artist:
                        votes.setdefault(artist, {})
                        votes[artist][sub.name] = votes[artist].get(sub.name, 0) + 1
    guessed = 0
    for song, artist in unknown:
        best = max(votes[artist], key=votes[artist].get) if artist and artist in votes else None
        plan[song] = best or DEFAULT_GENRE
        guessed += 1 if best else 0

    # Songs that are already in a genre folder (any folder, same file name) are duplicates.
    # They are not sorted again. They move to _Duplicates, so you can check them and delete them.
    have = _existing_folders(music_dir)
    known = {}
    for sub in music_dir.iterdir():
        if sub.is_dir() and sub.name != DUP_DIR and not sub.name.startswith("."):
            for f in sub.iterdir():
                if f.is_file() and f.suffix.lower() in AUDIO_EXT:
                    known.setdefault(f.name.lower(), sub.name)
    counts, skipped, dups = {}, 0, 0
    for song, planned in plan.items():
        folder = have.get(planned.lower(), planned)      # reuse a folder in any letter case
        where = known.get(song.name.lower())
        if where:
            print(f"DUPLICATE (already in {where}): {song.name}")
            dups += 1
            if not dry_run:
                try:
                    dd = music_dir / DUP_DIR
                    dd.mkdir(exist_ok=True)
                    dest = dd / song.name
                    n = 1
                    while dest.exists():                 # keep every copy, never overwrite
                        dest = dd / f"{song.stem} ({n}){song.suffix}"
                        n += 1
                    shutil.move(str(song), str(dest))
                except OSError as e:
                    print(f"SKIPPED (cannot move: {e}): {song.name}")
            continue
        target = music_dir / folder / song.name
        if not dry_run:
            try:
                target.parent.mkdir(exist_ok=True)
                shutil.move(str(song), str(target))
            except OSError as e:             # file in use by a player, for example
                print(f"SKIPPED (cannot move: {e}): {song.name}")
                skipped += 1
                continue
        known[song.name.lower()] = folder                # two loose copies: the second is a duplicate
        counts[folder] = counts.get(folder, 0) + 1
    verb = "Would move" if dry_run else "Moved"
    print(f"{verb} {sum(counts.values())} songs "
          f"({guessed} by artist, {skipped} skipped, {dups} duplicates"
          f"{' -> ' + DUP_DIR if dups else ''}):")
    for folder, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {folder}: {n}")


def _spotify_url(mutagen_file, path):
    "The Spotify track link that spotDL writes into each file, or None."
    try:
        audio = mutagen_file(path)
        tags = getattr(audio, "tags", None)
        if not tags:
            return None
        if hasattr(tags, "getall"):                       # mp3 (ID3 frame WOAS)
            vals = [f.url for f in tags.getall("WOAS")]
        else:                                             # m4a, flac, ogg, opus
            vals = []
            for k in ("----:spotdl:WOAS", "woas"):
                if k in tags:
                    v = tags[k]
                    vals += v if isinstance(v, list) else [v]
    except Exception:
        return None
    for v in vals:
        v = (bytes(v).decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else str(v)).strip()
        if v.startswith("https://open.spotify.com/track/"):
            return v
    return None


def audio_files(root):
    "Every audio file under root, in all subfolders. Follows folder links, never loops."
    seen = set()
    for d, dirs, files in os.walk(root, followlinks=True):
        real = os.path.realpath(d)
        if real in seen:
            dirs[:] = []
            continue
        seen.add(real)
        for f in files:
            if os.path.splitext(f)[1].lower() in AUDIO_EXT:
                yield Path(d) / f


def seed_archive(music_dir, archive_file):
    """Add every song that is already in the library (any folder) to the archive.
    spotDL then skips them before it searches. Runs once per run. This replaces
    spotDL's --scan-for-songs, which scans the library again for every playlist and
    searches Spotify for every file that has no link in its tags."""
    try:
        from mutagen import File as mutagen_file
    except ImportError:
        return
    if not music_dir.is_dir():
        return
    have = set()
    text = ""
    if archive_file.is_file():
        text = archive_file.read_text(encoding="utf-8", errors="replace")
        have = {x.strip() for x in text.splitlines() if x.strip()}
    new, total, linked, folders = [], 0, 0, set()
    for f in audio_files(music_dir):
        total += 1
        folders.add(f.parent)
        url = _spotify_url(mutagen_file, f)
        if url:
            linked += 1
            if url not in have:
                have.add(url)
                new.append(url)
    if new:
        archive_file.parent.mkdir(parents=True, exist_ok=True)
        with archive_file.open("a", encoding="utf-8") as fh:
            if text and not text.endswith("\n"):          # never join two links on one line
                fh.write("\n")
            fh.write("".join(u + "\n" for u in new))
    print(f"Library check: {total} songs in {len(folders)} folders under {music_dir}.")
    print(f"  {linked} have a Spotify link in their tags ({len(new)} new in the archive).")
    if total - linked:
        print(f"  {total - linked} have no link. They are matched by title and artist "
              f"during the download.")


# ---------------------------------------------------------------------
# SOUNDCLOUD UPGRADE
# Songs that came from YouTube are tried on SoundCloud afterwards. A SoundCloud file
# replaces the YouTube file only if it passes the checks below. Anything else stays.
UPGRADE_FILE = "soundcloud-upgrade.txt"       # in state_dir: Spotify links still to try
UPGRADE_BATCH = 100
UPGRADE_BAD = ("cover", "karaoke", "instrumental", "reaction", "live", "8d", "slowed",
               "sped up", "nightcore", "remix", "edit", "bootleg", "mashup", "tutorial", "lesson")


def queue_upgrades(state, marks):
    "Remember the songs that came from YouTube (not SoundCloud) in this playlist run."
    urls = []
    for m in marks:
        parts = m.split(" ", 2)
        if (len(parts) >= 2 and parts[0].startswith("https://open.spotify.com/track/")
                and "soundcloud.com" not in parts[1]):
            urls.append(parts[0])
    if urls:
        with (state / UPGRADE_FILE).open("a", encoding="utf-8") as f:
            f.write("".join(u + "\n" for u in urls))


def _bad_version(link, name):
    "True if the SoundCloud link names a cover, remix, live... that the Spotify title does not."
    slug = link.split("soundcloud.com/", 1)[-1].split("/", 1)[-1].lower()   # skip the uploader
    slug = re.sub(r"[^a-z0-9]+", " ", slug)
    name = re.sub(r"[^a-z0-9]+", " ", name.lower())
    return any(re.search(rf"\b{w}\b", slug) and not re.search(rf"\b{w}\b", name)
               for w in UPGRADE_BAD)


def _length(mutagen_file, path):
    return float(mutagen_file(path).info.length)


def _upgrade_ok(mutagen_file, new, old, link, name):
    "(True, '') if the SoundCloud file new may replace the YouTube file old. Else (False, reason)."
    try:
        n, o = _length(mutagen_file, new), _length(mutagen_file, old)
    except Exception as e:
        return False, f"could not read the length ({e!r:.60})"
    if new.suffix.lower() != old.suffix.lower():
        return False, "different file type"
    if n < 60:
        return False, f"only {n:.0f} s long (a preview?)"
    if abs(n - o) > max(10, o * 0.08):
        return False, f"length differs ({n:.0f} s on SoundCloud, {o:.0f} s on YouTube)"
    if _bad_version(link, name):
        return False, "the SoundCloud link looks like a cover, remix or live version"
    return True, ""


def upgrade_from_soundcloud(music_dir, state_dir):
    "Runs inside the private venv (it has mutagen)."
    pending = state_dir / UPGRADE_FILE
    if not pending.is_file():
        return
    try:
        from mutagen import File as mutagen_file
    except ImportError:
        return
    urls = list(dict.fromkeys(x.strip() for x in
                              pending.read_text(encoding="utf-8", errors="replace").splitlines()
                              if x.strip()))
    wanted = set(urls)
    index = {}                                   # Spotify link -> the file in the library
    tmp = state_dir / "upgrade-tmp"
    shutil.rmtree(tmp, ignore_errors=True)       # left over from a stopped run
    for f in audio_files(music_dir):
        u = _spotify_url(mutagen_file, f)
        if u in wanted:
            index.setdefault(u, f)
            if len(index) == len(wanted):        # every song found: stop reading tags
                break
    todo = [u for u in urls if u in index]
    print(f"SoundCloud upgrade: {len(todo)} songs to try "
          f"({len(urls) - len(todo)} are no longer in the library).")
    js = ["--js-runtimes", "node"] if shutil.which("node") else []
    CHILD_ENV["DL_NO_FALLBACK"] = "1"            # SoundCloud only: no quiet switch back to YouTube
    log = open(state_dir / "dl.log", "a", encoding="utf-8")
    try:
        replaced = _upgrade_batches(todo, index, tmp, js, log, pending, mutagen_file)
    finally:
        log.close()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"SoundCloud upgrade finished: {replaced} of {len(todo)} songs replaced.")


def _upgrade_batches(todo, index, tmp, js, log, pending, mutagen_file):
    replaced = 0
    for i in range(0, len(todo), UPGRADE_BATCH):
        batch = todo[i:i + UPGRADE_BATCH]
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        marks = []
        cmd = [sys.executable, "-c", SPOTDL_RUNNER, "download", *batch,
               "--threads", "1", "--max-retries", "3", "--audio", "soundcloud",
               "--format", "mp3", "--bitrate", "192k",
               "--output", "{title} - {artist} - {album}.{output-ext}",
               "--yt-dlp-args", yt_args(js, None, slow=False)]
        rc, _, _ = run_playlist(cmd, tmp, log, 10 ** 9, marks)
        if rc != 0:                              # spotDL crashed: keep this batch for next time
            cprint(f"WARNING: spotDL stopped (exit code {rc}). The rest stays queued. "
                  "Run  dl.py --upgrade  to try again.")
            break
        got = {}                                 # Spotify link -> (SoundCloud link, name)
        for m in marks:
            parts = m.split(" ", 2)
            if len(parts) == 3:
                got[parts[0]] = (parts[1], parts[2])
        for f in audio_files(tmp):
            u = _spotify_url(mutagen_file, f)
            if u not in got or u not in index:
                continue
            link, name = got[u]
            ok, why = _upgrade_ok(mutagen_file, f, index[u], link, name)
            if ok:
                stage = index[u].with_name(index[u].name + ".new")
                try:
                    shutil.move(str(f), str(stage))   # same folder first, so the swap is one step
                    os.replace(stage, index[u])
                    replaced += 1
                    cprint(f"  Upgraded to SoundCloud: {name}")
                except OSError as e:                  # file in use, disk full...: keep the old one
                    stage.unlink(missing_ok=True)
                    cprint(f"  Kept the YouTube version of {name}: could not replace it ({e})")
            else:
                cprint(f"  Kept the YouTube version of {name}: {why}")
        rest = todo[i + UPGRADE_BATCH:]          # progress survives a stop
        pending.write_text("".join(u + "\n" for u in rest), encoding="utf-8")
    else:
        pending.unlink(missing_ok=True)          # every batch ran
    return replaced


# ---------------------------------------------------------------------
# DUPLICATES, CLEANUP AND LIBRARY REPORT
#   dl.py --dedupe            move duplicate songs to _Duplicates (nothing is deleted)
#   dl.py --dedupe --dry-run  only show what would move
#   dl.py --report            only the library report
# Same song = same Spotify link, or same artist + title + about the same length.
# Live, remix, acoustic... versions are other songs and stay. The best copy stays:
# lossless first, then the higher bitrate, then cover art, then complete tags, then the
# copy that is already in a genre folder, then the oldest file.
# Optional (config.ini [dedupe] fingerprint = yes): audio fingerprints also find copies
# whose names differ. They are compared between songs of the same artist.
CACHE_V = 3                      # version of dedupe-cache.json (3 added the date tag)
BROKEN_DIR = "_Broken"           # inside _Duplicates: empty or unreadable files, leftovers
FP_URL = "https://github.com/acoustid/chromaprint/releases/download/v1.5.1/chromaprint-fpcalc-1.5.1-"
FP_SHA256 = {                               # of the files at FP_URL, so a changed download is refused
    "windows-x86_64.zip": "36b478e16aa69f757f376645db0d436073a42c0097b6bb2677109e7835b59bbc",
    "linux-x86_64.tar.gz": "4d7433a7f778e5946d7225230681cbcd634e153316ecac87c538c33ac32387a5",
    "macos-universal.tar.gz": "d4d8faff4b5f7c558d9be053da47804f9501eaa6c2f87906a9f040f38d61c860",
}
_DD_NOISE = re.compile(r"\b(?:\d{4}\s+)?(?:digital\s+)?(?:remaster(?:ed)?|mono|stereo)\b"
                       r"(?:\s+(?:19|20)\d\d)?(?:\s+version)?")
_DD_VERSION = re.compile(
    r"\b(live|remix|acoustic|instrumental|karaoke|slowed|sped|nightcore|8d|demo|cover|edit|"
    r"bootleg|mashup|reverb|unplugged|extended|radio|mix|dub|orchestral|piano|cappella|"
    r"version|reprise|medley|session|remake|rework|flip)\b")


def _dd_flags(title):
    "The version words in a title (live, remix...). Two copies must have the same ones."
    return frozenset(_DD_VERSION.findall(_DD_NOISE.sub(" ", title.lower())))


def _dd_core(title):
    "Title for matching: no remaster tags, no (feat. ...) brackets. (Live), (Remix), (Part 2) stay."
    s = _DD_NOISE.sub(" ", title.lower())
    s = re.sub(r"[\(\[]\s*(?:(?:feat|ft|featuring|with|prod|from|remaster|remastered|deluxe|explicit|clean|bonus|mono|stereo|single|album)\b[^\)\]]*)?[\)\]]", " ", s)   # only harmless brackets go
    s = re.sub(r"\b(?:feat|ft|featuring)\b\.?[^-]*", " ", s)
    return " ".join(re.findall(r"[^\W_]+", s))


def _dd_artist(artist):
    "The first artist, in lower case, for matching."
    first = re.split(r"\s*(?:[;,/&]|\bfeat\b\.?|\bft\b\.?|\bx\b)\s*", artist.lower())[0]
    return " ".join(re.findall(r"[^\W_]+", first))


def _dd_probe(mutagen_file, path):
    "Facts about one song file. None = broken file. False = could not look right now."
    try:
        st = path.stat()
        audio = mutagen_file(path, easy=True)
    except OSError:
        return False                             # locked or busy right now: look again next time
    except Exception:
        return None
    try:
        if audio is None or audio.info is None:
            return None
        tags = audio.tags or {}

        def one(key):
            v = tags.get(key) or [""]
            return str(v[0]).strip()
        length = float(audio.info.length)
        kbps = (getattr(audio.info, "bitrate", 0) or 0) / 1000
        if not kbps and length:
            kbps = st.st_size * 8 / length / 1000
        return {"size": st.st_size, "mtime": st.st_mtime, "len": length, "kbps": round(kbps),
                "ll": path.suffix.lower() in (".flac", ".wav"), "artist": one("artist") or one("albumartist"),
                "title": one("title"), "album": one("album"), "genre": one("genre"), "date": one("date"),
                "url": _spotify_url(mutagen_file, path) or ""}
    except Exception:
        return None


def _dd_has_cover(mutagen_file, path):
    try:
        audio = mutagen_file(path)
        tags = audio.tags
        if tags is not None and hasattr(tags, "getall"):
            return bool(tags.getall("APIC"))
        if tags is not None and "covr" in tags:
            return bool(tags["covr"])
        return bool(getattr(audio, "pictures", None)) or (tags is not None and "metadata_block_picture" in tags)
    except Exception:
        return False


def ensure_fpcalc(state_dir):
    "Path to Chromaprint's fpcalc: from the system, or downloaded once into state_dir. None if not possible."
    found = shutil.which("fpcalc")
    if found:
        return Path(found)
    exe = "fpcalc.exe" if IS_WIN else "fpcalc"
    local = state_dir / "fpcalc" / exe
    if local.is_file():
        return local
    machine = platform.machine().lower()
    intel = machine in ("x86_64", "amd64")
    if IS_WIN and intel:
        asset = "windows-x86_64.zip"
    elif sys.platform == "darwin":
        asset = "macos-universal.tar.gz"
    elif sys.platform.startswith("linux") and intel:
        asset = "linux-x86_64.tar.gz"
    else:
        print("Fingerprints: no fpcalc download for this system. Install Chromaprint (fpcalc) "
              "yourself, then run again.")
        return None
    try:
        print("Fingerprints: downloading fpcalc (Chromaprint, about 2 MB, one time)...")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            archive = Path(d) / asset
            with urllib.request.urlopen(FP_URL + asset, timeout=60) as resp:
                blob = resp.read(20_000_001)
            if len(blob) > 20_000_000 or hashlib.sha256(blob).hexdigest() != FP_SHA256[asset]:
                raise OSError("the download does not match the expected checksum")
            archive.write_bytes(blob)
            data = None
            if asset.endswith(".zip"):
                with zipfile.ZipFile(archive) as z:
                    for info in z.infolist():
                        if info.filename.rsplit("/", 1)[-1] == exe and info.file_size < 50_000_000:
                            data = z.read(info)
            else:
                with tarfile.open(archive) as t:
                    for m in t.getmembers():
                        if m.isfile() and m.name.rsplit("/", 1)[-1] == exe and m.size < 50_000_000:
                            data = t.extractfile(m).read()
        if not data:
            raise OSError("fpcalc not found in the download")
        local.parent.mkdir(parents=True, exist_ok=True)
        part = local.with_name(exe + ".part")    # write fully, then rename: no half-written program
        part.write_bytes(data)
        part.chmod(0o755)
        os.replace(part, local)
        return local
    except Exception as e:
        print(f"Fingerprints: could not get fpcalc ({e}). Continuing without fingerprints.")
        return None


def _dd_fingerprint(fpcalc, path):
    try:
        r = subprocess.run([str(fpcalc), "-raw", "-length", "120", "-json", str(path)],
                           capture_output=True, text=True, timeout=180)
        return [int(x) & 0xFFFFFFFF for x in json.loads(r.stdout).get("fingerprint", [])]
    except Exception:
        return None                              # fpcalc crashed or timed out: try again next run


def _dd_same_audio(a, b):
    "True if two raw fingerprints are the same recording (tries a few small shifts)."
    if len(set(a[:400])) < 200 or len(set(b[:400])) < 200:
        return False                             # silence or a repeating sound: tells nothing
    best = 0.0
    for shift in range(-3, 4):
        x, y = a[max(0, shift):][:400], b[max(0, -shift):][:400]
        n = min(len(x), len(y))
        if n < 300:                              # about 37 s of sound at least
            continue
        diff = sum((p ^ q).bit_count() for p, q in zip(x, y))
        best = max(best, 1 - diff / (32 * n))
    return best >= 0.85


def find_duplicate_groups(recs, use_fp=False):
    "Groups of copies of the same song. recs: list of dicts from _dd_probe (plus 'fp' when known)."
    parent = list(range(len(recs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    by_url, by_key, by_artist = {}, {}, {}
    for i, r in enumerate(recs):
        if r["url"]:
            if r["url"] in by_url:
                union(by_url[r["url"]], i)
            else:
                by_url[r["url"]] = i
        title = r["title"] or r["name"].split(" - ")[0]
        artist, core = _dd_artist(r["artist"]), _dd_core(title)
        if artist and core:
            by_key.setdefault((artist, core, _dd_flags(title)), []).append(i)
            by_artist.setdefault(artist, []).append(i)
    for idx in by_key.values():                  # same title: lengths must be close
        idx.sort(key=lambda i: recs[i]["len"])
        for a, b in zip(idx, idx[1:]):
            if recs[b]["len"] - recs[a]["len"] <= max(3.0, 0.02 * recs[b]["len"]):
                union(a, b)
    for idx in by_artist.values():               # fingerprints: names may differ
        idx = [i for i in idx if use_fp and recs[i].get("fp")]
        for n, a in enumerate(idx):
            for b in idx[n + 1:]:
                if (find(a) != find(b) and abs(recs[a]["len"] - recs[b]["len"]) <= 2
                        and _dd_flags(recs[a]["title"] or recs[a]["name"]) == _dd_flags(recs[b]["title"] or recs[b]["name"])
                        and _dd_same_audio(recs[a]["fp"], recs[b]["fp"])):
                    union(a, b)
    groups = {}
    for i in range(len(recs)):
        groups.setdefault(find(i), []).append(recs[i])
    return [g for g in groups.values() if len(g) > 1]


def _dd_rank(r, cover, music_dir):
    tags = sum(1 for k in ("artist", "title", "album", "genre", "url") if r[k])
    in_folder = Path(r["p"]).parent != music_dir
    return (r["ll"], r["kbps"], cover, tags, in_folder, -r["mtime"])


_DD_WHY = ("lossy copy of a lossless file", "lower bitrate", "no cover art", "fewer tags",
           "not in a genre folder", "newer file")


def _dd_reason(best, other, music_dir):
    for n, (x, y) in enumerate(zip(best, other)):
        if x != y:
            return _DD_WHY[n]
    return "same quality"


def _dd_unique(path):
    if not path.exists():
        return path
    n = 2
    while path.with_name(f"{path.stem} ({n}){path.suffix}").exists():
        n += 1
    return path.with_name(f"{path.stem} ({n}){path.suffix}")


SIDECARS = (".lrc", ".txt")          # lyrics files that sit next to a song


def _move_sidecars(src, dest):
    "Move the lyrics files of a song along with it."
    for ext in SIDECARS:
        side = src.with_suffix(ext)
        if side.is_file():
            try:
                shutil.move(str(side), str(dest.with_suffix(ext)))
            except OSError:
                pass


def _dd_move(src, music_dir, sub=""):
    "Move src into _Duplicates (same folder layout). Never overwrites."
    dest = music_dir / DUP_DIR / sub / src.relative_to(music_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest = _dd_unique(dest)
    shutil.move(str(src), str(dest))
    _move_sidecars(src, dest)
    return dest


def _dd_merge_tags(mutagen_file, keeper, others):
    "Give the kept copy a genre if it has none and a copy that moves away has one."
    try:
        audio = mutagen_file(keeper, easy=True)
        if audio is None or (audio.tags and audio.tags.get("genre")):
            return 0
        genre = None
        for o in others:
            try:
                genre = (mutagen_file(o, easy=True).tags or {}).get("genre")
            except Exception:
                genre = None
            if genre:
                break
        if not genre:
            return 0
        if audio.tags is None:
            audio.add_tags()
        audio.tags["genre"] = genre
        if keeper.suffix.lower() == ".mp3":       # keep the ID3 version the file already has
            from mutagen.id3 import ID3
            v = ID3(keeper).version[1]
            audio.save(v2_version=v if v in (3, 4) else 4)
        else:
            audio.save()
        return 1
    except Exception as e:
        print(f"  Could not update the genre of {keeper.name}: {e}")
        return 0


def _dd_walk(music_dir):
    "(leftover .new files, empty folders two levels down or deeper). Skips _Duplicates and hidden folders."
    new, empty = [], []
    for d, dirs, files in os.walk(music_dir, topdown=True):
        dirs[:] = [x for x in dirs if x != DUP_DIR and not x.startswith(".")]
        new += [Path(d) / f for f in files if f.endswith(".new")]
    for d, dirs, files in os.walk(music_dir, topdown=False):
        p = Path(d)
        rel = p.relative_to(music_dir).parts
        if len(rel) >= 2 and DUP_DIR not in rel and not any(x.startswith(".") for x in rel):
            if not os.listdir(d):
                empty.append(p)
    return new, empty


def library_report(recs, music_dir):
    "Text with counts, sizes and the weakest files."
    lines = []
    total = sum(r["size"] for r in recs)
    lines.append(f"Songs: {len(recs)}   Size: {total / 1e9:.1f} GB   Hours: {sum(r['len'] for r in recs) / 3600:.0f}")
    kinds, folders = {}, {}
    for r in recs:
        p = Path(r["p"])
        kinds[p.suffix.lower()] = kinds.get(p.suffix.lower(), 0) + 1
        rel = p.relative_to(music_dir).parts
        folders[rel[0] if len(rel) > 1 else "(not sorted)"] = folders.get(rel[0] if len(rel) > 1 else "(not sorted)", 0) + 1
    lines.append("Formats: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda x: -x[1])))
    lines.append("Folders: " + ", ".join(f"{k} {v}" for k, v in sorted(folders.items(), key=lambda x: -x[1])[:15]))
    low = sorted((r for r in recs if not r["ll"] and 0 < r["kbps"] < 128), key=lambda r: r["kbps"])
    lines.append(f"Low quality (under 128 kbps): {len(low)}")
    lines += [f"   {r['kbps']:>4} kbps  {Path(r['p']).name}" for r in low[:10]]
    nog = sum(1 for r in recs if not r["genre"])
    nourl = sum(1 for r in recs if not r["url"])
    lines.append(f"No genre tag: {nog}   No Spotify link in tags: {nourl}")
    return "\n".join(lines)


def _dd_summary(recs, music_dir):
    "Short library summary for the screen."
    kinds, folders = {}, {}
    for r in recs:
        p = Path(r["p"])
        kinds[p.suffix.lower()] = kinds.get(p.suffix.lower(), 0) + 1
        rel = p.relative_to(music_dir).parts
        name = rel[0] if len(rel) > 1 else "(not sorted)"
        folders[name] = folders.get(name, 0) + 1
    top = sorted(folders.items(), key=lambda x: -x[1])
    shown = ", ".join(f"{k} {v}" for k, v in top[:8]) + (f", +{len(top) - 8} more" if len(top) > 8 else "")
    low = sum(1 for r in recs if not r["ll"] and 0 < r["kbps"] < 128)
    return [
        f"Library: {len(recs)} songs | {sum(r['len'] for r in recs) / 3600:.0f} hours | "
        f"{sum(r['size'] for r in recs) / 1e9:.1f} GB",
        "Formats: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda x: -x[1])),
        "Folders: " + shown,
        f"Low quality (under 128 kbps): {low} | No genre tag: {sum(1 for r in recs if not r['genre'])} | "
        f"With Spotify link: {sum(1 for r in recs if r['url'])}",
    ]


def _dd_show(lines, kind=None):
    "Print summary lines: the label before the first ':' in bold, the rest plain (or in kind)."
    for line in lines:
        label, sep, rest = line.partition(":")
        print(paint(label + sep, "bold") + (paint(rest, kind) if kind else rest))


def _dd_prompt(show):
    "Ask what to do after a preview. Returns 'apply' or 'exit'. show() prints the full list."
    while True:
        try:
            print("Type " + paint("--expand", "bold") + " to list the duplicates, " + paint("--dedupe", "bold")
                  + " to move them, " + paint("--exit", "bold") + " to quit")
            ans = input("> ").strip().lower().lstrip("-")
        except (EOFError, KeyboardInterrupt):
            print()
            return "exit"
        if ans in ("expand", "e"):
            show()
        elif ans in ("dedupe", "depupe", "d"):
            return "apply"
        elif ans in ("exit", "quit", "q", ""):
            return "exit"
        else:
            print("Please type --expand, --dedupe or --exit.")


def run_dedupe(cfg, dry_run=False, report_only=False, ask=False):
    "Runs inside the private venv (it has mutagen)."
    try:
        from mutagen import File as mutagen_file
    except ImportError:
        print("Duplicates: mutagen is missing. Run dl.py once to install it.")
        return
    music_dir, state = cfg["music_dir"], cfg["state_dir"]
    state.mkdir(parents=True, exist_ok=True)
    cache_file = state / "dedupe-cache.json"
    try:                                         # version 2: files = {path: [mtime, size, facts]}
        raw = json.loads(cache_file.read_text(encoding="utf-8"))
        cache = raw["files"] if raw.get("v") == CACHE_V else {}
    except (OSError, ValueError, KeyError, AttributeError):
        cache = {}
    files = [f for f in audio_files(music_dir)
             if DUP_DIR not in f.relative_to(music_dir).parts
             and not any(x.startswith(".") for x in f.relative_to(music_dir).parts)]
    print(f"Duplicates: reading {len(files)} songs...")
    recs, broken, fresh = [], [], 0
    for n, f in enumerate(files, 1):
        key = str(f)
        try:
            st = f.stat()
        except OSError:
            continue
        hit = cache.get(key)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            r = hit[2]
        else:
            r = _dd_probe(mutagen_file, f)
            if r is False:
                continue
            if r is not None:
                r["fp"] = None
            fresh += 1
            cache[key] = [st.st_mtime_ns, st.st_size, r]
        if r is None or r["size"] == 0:
            broken.append(f)
            continue
        r = dict(r)
        r["p"], r["name"] = key, f.stem
        recs.append(r)
        if fresh and n % 500 == 0:
            print(f"  {n} of {len(files)}...")
    if cfg["dd_fingerprint"] and not report_only:
        fpcalc = ensure_fpcalc(state)
        if fpcalc:
            need = [r for r in recs if r.get("fp") is None]
            if need:
                print(f"Fingerprints: reading {len(need)} songs (first run is slow, the result is saved)...")
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(4) as ex:
                    for r, fp in zip(need, ex.map(lambda r: _dd_fingerprint(fpcalc, Path(r["p"])), need)):
                        r["fp"] = fp
                        if fp is not None:
                            cache[r["p"]][2]["fp"] = fp

    if cfg["dd_report"] or report_only:
        (state / "library-report.txt").write_text(library_report(recs, music_dir) + "\n", encoding="utf-8")
    summary = _dd_summary(recs, music_dir)
    if report_only:
        _dd_show(summary)
        _dd_show([f"Saved to: {state}  (library-report.txt)"])
        _dd_save_cache(cache_file, cache)
        return

    plan, report = [], []                        # plan: (keeper rec, [(loser rec, reason)])
    for group in find_duplicate_groups(recs, cfg["dd_fingerprint"]):
        covers = {r["p"]: _dd_has_cover(mutagen_file, Path(r["p"])) for r in group}
        ranked = sorted(group, key=lambda r: _dd_rank(r, covers[r["p"]], music_dir), reverse=True)
        best = ranked[0]
        bkey = _dd_rank(best, covers[best["p"]], music_dir)
        plan.append((best, [(r, _dd_reason(bkey, _dd_rank(r, covers[r["p"]], music_dir), music_dir))
                            for r in ranked[1:]]))
    plan.sort(key=lambda g: g[0]["name"].lower())
    moving = sum(len(g[1]) for g in plan)
    new_files, empty_dirs = _dd_walk(music_dir) if cfg["dd_cleanup"] else ([], [])
    junk = broken + new_files if cfg["dd_cleanup"] else []

    def short(r):
        p = Path(r["p"])
        return f"{p.parent.name}/{p.name}"
    for keeper, losers in plan:
        report.append(f"KEEP  {keeper['p']}  ({keeper['kbps']} kbps)")
        for r, why in losers:
            report.append(f"MOVE  {r['p']}  ({r['kbps']} kbps)  because: {why}")
        report.append("")
    report += ["BROKEN/LEFTOVER " + str(f) for f in junk] + ["EMPTY FOLDER " + str(d) for d in empty_dirs]
    (state / "dedupe-report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")

    def expand():
        for keeper, losers in plan:
            print(f"  {paint('KEEP', 'ok')} {short(keeper)}  ({keeper['kbps']} kbps)")
            for r, why in losers:
                print(f"    {paint('MOVE', 'warn')} {short(r)}  ({r['kbps']} kbps, {why})")
        for f in junk:
            print(f"  {paint('BROKEN/LEFTOVER', 'warn')} {f}")
        for d in empty_dirs:
            print(f"  {paint('EMPTY FOLDER', 'warn')} {d}")
    _dd_show(summary)
    _dd_show([f"Duplicates: {len(plan)} songs have extra copies ({moving} files to move)"],
             "warn" if plan else "ok")
    if junk or empty_dirs:
        _dd_show([f"Cleanup: {len(junk)} empty, unreadable or leftover files | {len(empty_dirs)} empty folders"],
                 "warn")
    _dd_show([f"Saved to: {state}  (dedupe-report.txt, library-report.txt)"])

    if dry_run:
        if not (moving or junk or empty_dirs):
            print("Nothing to clean up.")
            _dd_save_cache(cache_file, cache)
            return
        if ask:
            if _dd_prompt(expand) != "apply":
                cprint("Nothing was moved.")
                _dd_save_cache(cache_file, cache)
                return
        else:
            print("Nothing was moved. To see the list and choose:  python dl.py --dedupe --dry-run")
            _dd_save_cache(cache_file, cache)
            return

    moved = merged = 0
    for keeper, losers in plan:
        if cfg["dd_merge"]:
            merged += _dd_merge_tags(mutagen_file, Path(keeper["p"]), [Path(r["p"]) for r, _ in losers])
        for r, _ in losers:
            try:
                _dd_move(Path(r["p"]), music_dir)
                cache.pop(r["p"], None)
                moved += 1
            except OSError as e:
                print(f"  Could not move {r['p']}: {e}")
    for f in junk:
        try:
            _dd_move(f, music_dir, BROKEN_DIR)
            cache.pop(str(f), None)
        except (OSError, ValueError) as e:
            print(f"  Could not move {f}: {e}")
    if cfg["dd_cleanup"]:                        # the moves above may have emptied folders
        empty_dirs = _dd_walk(music_dir)[1]
    for d in empty_dirs:
        while len(d.relative_to(music_dir).parts) >= 2:   # a folder that held only an empty folder
            try:
                d.rmdir()
            except OSError:
                break
            d = d.parent
    _dd_save_cache(cache_file, cache)
    cprint(f"Moved {moved} duplicate files and {len(junk)} broken/leftover files to {music_dir / DUP_DIR}. "
           f"Filled {merged} missing tags on kept copies. Delete {DUP_DIR} when you are happy.")
    if moved and (state / "playlist-cache.json").is_file():    # keep the playlist links right
        build_playlists(cfg, refresh=False)


def _dd_save_cache(path, cache):
    try:
        part = path.with_name(path.name + ".part")
        part.write_text(json.dumps({"v": CACHE_V, "files": {k: v for k, v in cache.items() if os.path.exists(k)}}),
                        encoding="utf-8")
        os.replace(part, path)
    except OSError:
        pass


# ---------------------------------------------------------------------
# PLAYLIST FILES
# One .m3u8 file per playlist (or per group of similar playlists) in <music_dir>/Playlists. Every
# song that is in your library is linked by its Spotify link, so the links stay right after sorting,
# upgrading and moving duplicates. Built by:  dl.py --playlists   (also at the end of every run)
PL_GENERIC = frozenset(
    "the a an of to for and in on my your our playlist playlists songs song music mix hits best good "
    "great amazing ultimate perfect peak top chill cozy calm relaxing".split())
PL_SOFT = frozenset("vibes mood season aesthetic edition collection essentials".split())


def _pl_core(name):
    "The meaningful words of a playlist name: no decoration, no filler like 'chill' or 'songs'."
    return tuple(w for w in re.findall(r"[^\W_]+", name.lower()) if w not in PL_GENERIC)


def _pl_similar(a, b):
    "True if two playlist names are about the same thing (same core words, or one only adds 'vibes'...)."
    ca, cb = set(_pl_core(a)), set(_pl_core(b))
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    small, big = (ca, cb) if len(ca) < len(cb) else (cb, ca)
    return small < big and (big - small) <= PL_SOFT


def _pl_title(names):
    "A name for a group: the longest run of words that all the playlist names share, e.g. Autumn Vibes."
    seqs = [re.findall(r"[^\W_]+(?:['’][^\W_]+)*", n.lower()) for n in names]
    first, best = seqs[0], []
    for i in range(len(first)):
        for j in range(len(first), i + len(best), -1):
            run = first[i:j]
            if all(any(sq[k:k + len(run)] == run for k in range(len(sq) - len(run) + 1)) for sq in seqs[1:]):
                best = run
                break
    if not any(w not in PL_GENERIC for w in best):         # only filler words in common: use the core words
        best = list(max((_pl_core(n) for n in names), key=len)) or best
    return " ".join(w[:1].upper() + w[1:] for w in best) if best else names[0]


def _pl_groups_file(path):
    "{group name: [playlist names or IDs]} from the optional playlist_groups.txt."
    out = {}
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                name, members = line.split("=", 1)
                if name.strip():
                    out[name.strip()] = [m.strip() for m in members.split(",") if m.strip()]
    except OSError:
        pass
    return out


def plan_playlists(cfg, meta, ids, flags):
    """Decide which playlists get a file and which are combined. meta: {id: {"name": ...}}.
    Returns (files, skipped): files = {file name: [ids]} in playlists.txt order."""
    named = _pl_groups_file(cfg["groups_file"])
    by_member = {}
    for group, members in named.items():
        for mem in members:
            by_member[mem.lower()] = group
    chosen = {}                                   # id -> explicit group name or ""
    solo, skipped = set(), []
    for pid in ids:
        flag, group = flags.get(pid, ("", ""))
        if pid not in meta:
            continue
        if flag == "X" or (not flag and not cfg["pl_files"]):
            skipped.append(pid)
            continue
        name = meta[pid]["name"]
        if flag == "S":
            solo.add(pid)
        group = group or by_member.get(pid.lower()) or by_member.get(name.lower()) or ""
        chosen[pid] = group
    todo = [p for p in ids if p in chosen]
    parent = {p: p for p in todo}

    def find(p):
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p
    if cfg["pl_merge"]:
        auto = [p for p in todo if not chosen[p] and p not in solo]
        for n, p in enumerate(auto):
            for q in auto[n + 1:]:
                if _pl_similar(meta[p]["name"], meta[q]["name"]):
                    parent[find(q)] = find(p)
    clusters = {}
    for p in todo:
        if chosen[p]:
            key = ("group", chosen[p].lower())
        elif p in solo or not cfg["pl_merge"]:
            key = ("one", p)
        else:
            key = ("auto", find(p))
        clusters.setdefault(key, []).append(p)
    files, used = {}, {}
    for key, members in clusters.items():
        names = [meta[p]["name"] for p in members]
        if key[0] == "group":
            title = next(g for p in members for g in [chosen[p]] if g)
        elif key[0] == "auto" and len(members) > 1:
            title = _pl_title(names)
        else:
            title = names[0]
        fname = _clean_name(title) if re.search(r"[^\W_]", title) else members[0]
        if fname.lower() in used and used[fname.lower()] != key:      # two different playlists, same name
            fname = f"{fname} ({members[0][:6]})"
        used[fname.lower()] = key
        files[fname] = members
    return files, skipped


def _pl_client():
    from spotdl.utils.config import DEFAULT_CONFIG
    from spotdl.utils.spotify import SpotifyClient
    try:
        SpotifyClient.init(client_id=DEFAULT_CONFIG["client_id"], client_secret=DEFAULT_CONFIG["client_secret"],
                           user_auth=False, headless=True)
    except Exception:
        pass                                      # already started


def _pl_fetch(pid):
    "{'name':..., 'tracks': [[link, title, artist, seconds], ...]} for a public playlist."
    from spotdl.types.playlist import Playlist
    meta, songs = Playlist.get_metadata("https://open.spotify.com/playlist/" + pid)
    return {"name": meta.get("name") or pid,
            "tracks": [[x.url, x.name, x.artist, x.duration or 0] for x in songs]}


def _pl_library(music_dir, state, mutagen_file):
    "Facts about every song file in the library, including _Duplicates (shares the dedupe cache)."
    cache_file = state / "dedupe-cache.json"
    try:
        raw = json.loads(cache_file.read_text(encoding="utf-8"))
        cache = raw["files"] if raw.get("v") == CACHE_V else {}
    except (OSError, ValueError, KeyError, AttributeError):
        cache = {}
    recs = []
    for f in audio_files(music_dir):
        try:
            st = f.stat()
        except OSError:
            continue
        hit = cache.get(str(f))
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            r = hit[2]
        else:
            r = _dd_probe(mutagen_file, f)
            if r is False:
                continue
            if r is not None:
                r["fp"] = None
            cache[str(f)] = [st.st_mtime_ns, st.st_size, r]
        if r:
            r = dict(r)
            r["p"], r["name"] = str(f), f.stem
            r["dup"] = DUP_DIR in f.relative_to(music_dir).parts
            recs.append(r)
    _dd_save_cache(cache_file, cache)
    return recs


class _PlLinker:
    "Finds the library file for a Spotify track."

    def __init__(self, recs):
        self.by_url, self.dup_url, self.by_key = {}, {}, {}
        for r in recs:
            title = r["title"] or r["name"].split(" - ")[0]
            key = (_dd_artist(r["artist"]), _dd_core(title), _dd_flags(title))
            r["key"] = key
            if r["dup"]:
                if r["url"]:
                    self.dup_url.setdefault(r["url"], r)
            else:
                if r["url"]:
                    self.by_url.setdefault(r["url"], r)
                if key[0] and key[1]:
                    self.by_key.setdefault(key, []).append(r)

    def _by_key(self, key, length):
        for r in self.by_key.get(key, []):
            if abs(r["len"] - length) <= max(3.0, 0.02 * r["len"]) or not length:
                return r
        return None

    def find(self, track):
        url, name, artist, secs = track
        if url in self.by_url:
            return self.by_url[url]
        gone = self.dup_url.get(url)               # moved to _Duplicates: use the copy that was kept
        if gone is not None:
            kept = self._by_key(gone["key"], gone["len"])
            if kept:
                return kept
        return self._by_key((_dd_artist(artist), _dd_core(name), _dd_flags(name)), secs)


def _pl_meta(cfg, wanted, refresh):
    "{id: {'name', 'tracks'}} for the wanted playlists, from Spotify (refresh) or the saved copy."
    state = cfg["state_dir"]
    state.mkdir(parents=True, exist_ok=True)
    cache_file = state / "playlist-cache.json"
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    meta = {p: cache[p] for p in wanted if p in cache}
    if refresh:
        print(f"Playlist files: reading {len(wanted)} playlists from Spotify...")
        _pl_client()
        from concurrent.futures import ThreadPoolExecutor

        def one(pid):
            try:
                return pid, _pl_fetch(pid)
            except Exception as e:
                return pid, str(e)[:80]
        import contextlib
        import io
        failed = []
        with contextlib.redirect_stdout(io.StringIO()):          # once, not per thread: spotDL prints noise
            with ThreadPoolExecutor(3) as ex:
                for pid, res in ex.map(one, wanted):
                    if isinstance(res, dict):
                        meta[pid] = cache[pid] = res
                    elif pid not in meta:
                        failed.append((pid, res))
        for pid, res in failed:
            cprint(f"WARNING: could not read playlist {pid} ({res}). It is skipped for now.")
        try:
            cache_file.write_text(json.dumps(cache), encoding="utf-8")
        except OSError:
            pass
    return meta


def build_playlists(cfg, refresh=True, dry_run=False):
    "Runs inside the private venv (it has mutagen and spotDL)."
    flags = {}
    ids = load_ids(cfg["playlists_file"], flags)
    wanted = [p for p in ids if flags[p][0] in ("P", "S") or (cfg["pl_files"] and flags[p][0] != "X")]
    if not wanted:
        if cfg["smart"] and not dry_run:
            build_smart(cfg)
        return
    try:
        from mutagen import File as mutagen_file
    except ImportError:
        return
    music_dir, state = cfg["music_dir"], cfg["state_dir"]
    state.mkdir(parents=True, exist_ok=True)
    meta = _pl_meta(cfg, wanted, refresh)
    files, skipped = plan_playlists(cfg, meta, ids, flags)
    if not files:
        print("Playlist files: nothing to do (no playlist data yet: run  dl.py --playlists).")
        return
    folder = Path(os.path.expanduser(cfg["pl_folder"]))
    folder = folder if folder.is_absolute() else music_dir / folder
    merged = sum(1 for m in files.values() if len(m) > 1)
    print(f"Playlist files: {len(wanted)} playlists -> {len(files)} files ({merged} combined groups"
          f"{', ' + str(len(skipped)) + ' marked X' if skipped else ''}).")
    if dry_run:
        for fname, members in sorted(files.items(), key=lambda x: -len(x[1])):
            if len(members) > 1:
                print(f"  {paint(fname, 'bold')} <- " + " + ".join(meta[p]["name"] for p in members))
        print("Nothing was written. Change a group with  S / X / =Group name  after an ID in playlists.txt,"
              " or in playlist_groups.txt. Build the files with:  dl.py --playlists")
        return
    recs = _pl_library(music_dir, state, mutagen_file)
    linker = _PlLinker(recs)
    folder.mkdir(parents=True, exist_ok=True)
    index_file = state / "playlists-index.json"
    try:
        old_index = json.loads(index_file.read_text(encoding="utf-8"))
        old = old_index.get("files", [])
    except (OSError, ValueError, AttributeError):
        old_index, old = {}, []
    written = []
    for fname, members in files.items():
        lines, seen, total, linked = ["#EXTM3U", "#PLAYLIST:" + fname], set(), 0, 0
        for pid in members:
            for track in meta[pid]["tracks"]:
                total += 1
                r = linker.find(track)
                if r is None or r["p"] in seen:
                    continue
                seen.add(r["p"])
                linked += 1
                try:
                    rel = os.path.relpath(r["p"], folder).replace("\\", "/")
                except ValueError:                 # another drive on Windows
                    rel = Path(r["p"]).as_posix()
                lines.append(f"#EXTINF:{int(r['len'])},{r['artist'] or track[2]} - {r['title'] or track[1]}")
                lines.append(rel)
        target = folder / (fname + ".m3u8")
        part = target.with_name(target.name + ".part")
        part.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(part, target)
        written.append(target.name)
        extra = f" (combines {len(members)} playlists)" if len(members) > 1 else ""
        cprint(f"  {fname}.m3u8: {linked} songs linked, {total - linked} not in the library yet{extra}")
    for name in old:                               # files from an earlier run that are not wanted any more
        if name not in written and name.endswith(".m3u8"):
            try:
                (folder / name).unlink()
            except OSError:
                pass
    try:
        index_file.write_text(json.dumps({"files": written, "smart": old_index.get("smart", [])}), encoding="utf-8")
    except OSError:
        pass
    cprint(f"Playlist files saved in {folder}")
    if cfg["smart"]:
        build_smart(cfg, recs)


def playlists_wanted(cfg):
    "True if at least one playlist should get a playlist file."
    try:
        flags = {}
        ids = load_ids(cfg["playlists_file"], flags)
    except OSError:
        return False
    return any(flags[p][0] in ("P", "S") or (cfg["pl_files"] and flags[p][0] != "X") for p in ids)


# ---------------------------------------------------------------------
# GENRE REPAIR
# Songs in the folders Other and Unknown, and songs with no genre tag, get a genre from the web:
# MusicBrainz (free, no key) or Last.fm (if you put an API key in config.ini). The tag is written
# into the file, and songs in Other or Unknown move to the matching genre folder. Every move is
# written to genre-repair-log.txt in the state folder, so you can undo it. Resumable: each run
# looks up a limited number of artists and remembers the answers.
#   dl.py --genres --dry-run     preview          dl.py --genres     do it
GR_DIRS = ("Other", "Unknown")
GR_RETRY_DAYS = 60                    # an artist that was not found is asked again after this long
MB_UA = {"User-Agent": "dl.py-playlist-sync/1.0 ( https://github.com/jieke238y34/playlist-sync )",
         "Accept": "application/json"}


def _web_json(url, headers=None, tries=3):
    "GET a URL and read JSON. Waits and retries when the service is busy."
    for n in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=25) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and n < tries - 1:
                time.sleep(4 * (n + 1))
                continue
            raise
        except (urllib.error.URLError, OSError, ValueError):
            if n < tries - 1:
                time.sleep(2)
                continue
            raise


def _gr_musicbrainz(artist):
    "Genre names of an artist from MusicBrainz, most voted first. [] if it is not found."
    want = _dd_artist(artist)
    d = _web_json("https://musicbrainz.org/ws/2/artist/?query="
                  + urllib.parse.quote('artist:"' + artist + '"') + "&fmt=json&limit=3", MB_UA)
    hit = next((a for a in d.get("artists", [])
                if a.get("score", 0) >= 90 and _dd_artist(a.get("name", "")) == want), None)
    if hit is None:
        return []
    time.sleep(1.1)                                  # MusicBrainz allows one request a second
    full = _web_json("https://musicbrainz.org/ws/2/artist/" + hit["id"] + "?inc=genres+tags&fmt=json", MB_UA)
    out = []
    for group in (full.get("genres", []), full.get("tags", [])):
        out += [x["name"] for x in sorted(group, key=lambda x: -x.get("count", 0)) if x.get("name")]
    return list(dict.fromkeys(out))


def _gr_lastfm(artist, key):
    d = _web_json("https://ws.audioscrobbler.com/2.0/?method=artist.gettoptags&format=json&api_key="
                  + urllib.parse.quote(key) + "&artist=" + urllib.parse.quote(artist))
    return [t["name"] for t in d.get("toptags", {}).get("tag", []) if int(t.get("count", 0) or 0) >= 20]


def pick_genre(tags, rules):
    "(tag, folder) for the first tag that maps to a genre folder other than Other, or (None, None)."
    for tag in tags:
        folder = folder_for_genre(tag, rules)
        if folder and folder != DEFAULT_GENRE:
            return tag, folder
    return None, None


def _save_tags(audio, path):
    "Save easy tags and keep the ID3 version an MP3 already has."
    if path.suffix.lower() == ".mp3":
        from mutagen.id3 import ID3
        v = ID3(path).version[1]
        audio.save(v2_version=v if v in (3, 4) else 4)
    else:
        audio.save()


def repair_genres(cfg, dry_run=False):
    "Runs inside the private venv (it has mutagen)."
    try:
        from mutagen import File as mutagen_file
    except ImportError:
        return
    music_dir, state = cfg["music_dir"], cfg["state_dir"]
    state.mkdir(parents=True, exist_ok=True)
    recs = [r for r in _pl_library(music_dir, state, mutagen_file) if not r["dup"]]
    rules = _compile_rules()
    targets = {}                                     # artist key -> [(rec, folder it is in)]
    for r in recs:
        rel = Path(r["p"]).relative_to(music_dir).parts
        top = rel[0] if len(rel) > 1 else ""
        key = _dd_artist(r["artist"])
        if key and top and not top.startswith(("_", ".")) and (top in GR_DIRS or not r["genre"]):
            targets.setdefault(key, []).append((r, top))
    n_songs = sum(len(v) for v in targets.values())
    if not n_songs:
        print("Genre repair: every song already has a genre.")
        return
    cache_file = state / "genre-cache.json"
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    now = time.time()
    stale = lambda e: (not e["tags"]) and now - e.get("ts", 0) > GR_RETRY_DAYS * 86400
    todo = [k for k in sorted(targets, key=lambda k: -len(targets[k]))
            if k not in cache or stale(cache[k])]
    batch = todo[:max(0, cfg["gen_limit"])]
    looked = 0
    if batch and not dry_run:
        src = "Last.fm" if cfg["lastfm_key"] else "MusicBrainz"
        print(f"Genre repair: asking {src} about {len(batch)} of {len(todo)} artists "
              f"({n_songs} songs need a genre)...")
        for k in batch:
            name = targets[k][0][0]["artist"].split(";")[0].strip()
            try:
                tags = _gr_lastfm(name, cfg["lastfm_key"]) if cfg["lastfm_key"] else _gr_musicbrainz(name)
            except (urllib.error.URLError, OSError, ValueError) as e:
                print(f"  {src} is busy or unreachable ({str(e)[:50]}). The rest waits for the next run.")
                break
            cache[k] = {"tags": tags[:8], "ts": now, "src": src}
            looked += 1
            time.sleep(0.3 if cfg["lastfm_key"] else 1.1)
        try:
            cache_file.write_text(json.dumps(cache), encoding="utf-8")
        except OSError:
            pass
    tagged = moved = unknown = 0
    preview = []
    log_lines = []
    for k, items in targets.items():
        entry = cache.get(k)
        tag, folder = pick_genre(entry["tags"], rules) if entry else (None, None)
        if not folder:
            unknown += len(items)
            continue
        for r, top in items:
            path = Path(r["p"])
            go = top in GR_DIRS and top != folder
            if dry_run:
                preview.append(f"{r['artist'][:28]:28} -> {folder} ({tag})" + ("" if go or not r["genre"] else ""))
                continue
            try:
                if not r["genre"]:
                    audio = mutagen_file(path, easy=True)
                    if audio is not None:
                        if audio.tags is None:
                            audio.add_tags()
                        audio.tags["genre"] = tag.title()
                        _save_tags(audio, path)
                        tagged += 1
                if go:
                    dest = _dd_unique(music_dir / folder / path.name)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), str(dest))
                    _move_sidecars(path, dest)
                    moved += 1
                    log_lines.append(f"{datetime.now():%Y-%m-%d %H:%M} | {path} -> {dest} | {tag}")
            except Exception as e:
                print(f"  Could not update {path.name}: {e}")
    if log_lines:
        with (state / "genre-repair-log.txt").open("a", encoding="utf-8") as f:
            f.write("\n".join(log_lines) + "\n")
    pending = len([k for k in todo if k not in cache])
    if dry_run:
        fixable = sum(len(targets[k]) for k in targets if cache.get(k) and pick_genre(cache[k]["tags"], rules)[1])
        print(f"Genre repair (preview): {n_songs} songs need a genre. {fixable} can be fixed from answers already "
              f"saved. {len(todo)} artists still have to be asked (up to {cfg['gen_limit']} per run).")
        for line in preview[:8]:
            print("  " + line)
        print("Nothing was changed. Run:  dl.py --genres")
        return
    cprint(f"Genre repair: {tagged} tags written, {moved} songs moved out of Other/Unknown, {unknown} still unknown"
           f"{', ' + str(pending) + ' artists left for the next run' if pending else ''}. "
           f"Log: {state / 'genre-repair-log.txt'}")


# ---------------------------------------------------------------------
# SMART PLAYLISTS
# Playlist files built from the tags, in <playlist folder>/Smart: one per genre folder, decade,
# language (by the alphabet of the title) and favourite artist (10+ songs). Only lists with
# enough songs are made. Settings: [playlists] smart_playlists, smart_min.
SCRIPTS = (("Japanese", r"[぀-ヿ]"), ("Korean", r"[가-힯]"), ("Chinese", r"[一-鿿]"),
           ("Cyrillic", r"[Ѐ-ӿ]"), ("Arabic", r"[؀-ۿ]"))


def smart_groups(recs, music_dir, min_n):
    "{playlist name: [songs]} for the smart playlists. Songs that were moved to _Duplicates are left out."
    out, artists = {}, {}
    for r in recs:
        if r.get("dup"):
            continue
        rel = Path(r["p"]).relative_to(music_dir).parts
        top = rel[0] if len(rel) > 1 else ""
        if top and not top.startswith(("_", ".")) and top not in GR_DIRS:
            out.setdefault(f"Genre - {top}", []).append(r)
        m = re.match(r"(\d{4})", r.get("date") or "")
        if m and 1900 <= int(m.group(1)) <= 2100:
            out.setdefault(f"Decade - {int(m.group(1)) // 10 * 10}s", []).append(r)
        text = (r["title"] or "") + (r["artist"] or "")
        for name, rx in SCRIPTS:
            if re.search(rx, text):
                out.setdefault(f"Language - {name}", []).append(r)
                break
        key = _dd_artist(r["artist"])
        if key:
            artists.setdefault(key, []).append(r)
    for key, songs in artists.items():
        if len(songs) >= 10:
            name = collections_most_common([x["artist"].split(";")[0].strip() for x in songs])
            out[f"Artist - {name}"] = songs
    keep = {}
    for name, songs in out.items():
        need = min_n if name.startswith(("Genre", "Decade")) else 10
        if len(songs) >= max(1, need):
            keep[name] = sorted(songs, key=lambda x: ((x["artist"] or "").lower(), (x["album"] or "").lower(), x["title"].lower()))
    return keep


def collections_most_common(items):
    counts = {}
    for i in items:
        counts[i] = counts.get(i, 0) + 1
    return max(counts, key=lambda k: counts[k])


def _write_m3u8(target, title, songs):
    "Write a playlist file with relative links. Returns nothing."
    lines = ["#EXTM3U", "#PLAYLIST:" + title]
    for r in songs:
        try:
            rel = os.path.relpath(r["p"], target.parent).replace("\\", "/")
        except ValueError:                           # another drive on Windows
            rel = Path(r["p"]).as_posix()
        lines.append(f"#EXTINF:{int(r['len'])},{r['artist']} - {r['title'] or r['name']}")
        lines.append(rel)
    part = target.with_name(target.name + ".part")
    part.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(part, target)


def build_smart(cfg, recs=None):
    "Runs inside the private venv."
    try:
        from mutagen import File as mutagen_file
    except ImportError:
        return
    music_dir, state = cfg["music_dir"], cfg["state_dir"]
    if recs is None:
        recs = _pl_library(music_dir, state, mutagen_file)
    folder = Path(os.path.expanduser(cfg["pl_folder"]))
    folder = (folder if folder.is_absolute() else music_dir / folder) / "Smart"
    groups = smart_groups(recs, music_dir, cfg["smart_min"])
    index_file = state / "playlists-index.json"
    try:
        index = json.loads(index_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = {}
    old, written = index.get("smart", []), []
    if groups:
        folder.mkdir(parents=True, exist_ok=True)
    for name, songs in sorted(groups.items()):
        fname = _clean_name(name) + ".m3u8"
        _write_m3u8(folder / fname, name, songs)
        written.append(fname)
    for fname in old:
        if fname not in written:
            try:
                (folder / fname).unlink()
            except OSError:
                pass
    index["smart"] = written
    try:
        index_file.write_text(json.dumps(index), encoding="utf-8")
    except OSError:
        pass
    if written:
        cprint(f"Smart playlists: {len(written)} made in {folder} "
               f"({sum(1 for n in groups if n.startswith('Genre'))} genres, "
               f"{sum(1 for n in groups if n.startswith('Decade'))} decades, "
               f"{sum(1 for n in groups if n.startswith('Language'))} languages, "
               f"{sum(1 for n in groups if n.startswith('Artist'))} artists)")


# ---------------------------------------------------------------------
# PLAYLIST GAP REPORT
# For every playlist: which songs are not in your library yet, and why. The reason comes from the error
# files of the last run (no match, DRM, blocked...), your archive (downloaded before but the file is gone)
# or "not downloaded yet". Written to playlist-gaps.txt in the state folder.
def gap_reason(text):
    "A short reason for a spotDL error line like 'LookupError: No results found for song: ...'."
    t = text.lower()
    if "drm" in t:
        return "DRM protected"
    if "lookuperror" in t or "no results found" in t:
        return "no match found"
    if re.search(r"sign in|not a bot|403|429|forbidden|too many requests|blocked", t):
        return "blocked by YouTube (try later)"
    if "missing required fields" in t:
        return "incomplete Spotify data"
    return "download failed"


def _gap_errors(state, pid):
    "{track id: reason} from the error file of the last run of a playlist."
    out = {}
    try:
        for line in (state / f"errors-{pid}.txt").read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.search(r"open\.spotify\.com/track/([A-Za-z0-9]{22})\s*-\s*(.*)", line)
            if m:
                out[m.group(1)] = gap_reason(m.group(2))
    except OSError:
        pass
    return out


def gap_report(cfg):
    "Runs inside the private venv (it has mutagen and spotDL)."
    try:
        from mutagen import File as mutagen_file
    except ImportError:
        return
    flags = {}
    ids = load_ids(cfg["playlists_file"], flags)
    wanted = [p for p in ids if flags[p][0] != "X"]
    if not wanted:
        return
    music_dir, state = cfg["music_dir"], cfg["state_dir"]
    meta = _pl_meta(cfg, wanted, refresh=not (state / "playlist-cache.json").is_file())
    linker = _PlLinker(_pl_library(music_dir, state, mutagen_file))
    try:
        archive = {x.strip() for x in cfg["archive_file"].read_text(encoding="utf-8", errors="replace").splitlines()}
    except OSError:
        archive = set()
    rows, reasons, seen, lines = [], {}, set(), []
    for pid in wanted:
        if pid not in meta:
            continue
        errs = _gap_errors(state, pid)
        missing = []
        for track in meta[pid]["tracks"]:
            if linker.find(track) is not None:
                continue
            tid = track[0].rsplit("/", 1)[-1]
            why = errs.get(tid) or ("in your archive, but the file is gone" if track[0] in archive else "not downloaded yet")
            missing.append((track, why))
            if track[0] not in seen:
                seen.add(track[0])
                reasons[why] = reasons.get(why, 0) + 1
        total = len(meta[pid]["tracks"])
        rows.append((len(missing), total, meta[pid]["name"], pid))
        lines.append(f"== {meta[pid]['name']} ({pid}): {total - len(missing)} of {total} in the library")
        lines += [f"   {why:36} {t[2]} - {t[1]}" for t, why in missing]
        lines.append("")
    (state / "playlist-gaps.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    all_tracks = {t[0] for pid in wanted if pid in meta for t in meta[pid]["tracks"]}
    gone = len(seen)
    print(paint("Playlist gaps:", "bold") + f" {len(all_tracks) - gone} of {len(all_tracks)} songs from your playlists are in "
          f"the library, {gone} are missing.")
    for why, n in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  {n:6} {why}")
    for miss, total, name, pid in sorted(rows, reverse=True)[:5]:
        if miss:
            print(f"  most missing: {name[:38]} ({total - miss} of {total}, {100 * (total - miss) // max(total, 1)}%)")
    print(f"Saved to: {state / 'playlist-gaps.txt'}")


# ---------------------------------------------------------------------
# LYRICS (off by default: [lyrics] lyrics = yes)
# After a run has finished, songs that are in your library and have no lyrics file get one, saved next to
# the song with the same name: song.lrc (timed, for players that scroll along) or song.txt (plain).
# Sources, in the order of lyrics_sources: lrclib (free, timed), synced (the syncedlyrics package that
# spotDL installs: several sites), genius (needs your own access token). A song with no result is asked
# again after 30 days. Each run handles at most lyrics_limit songs.  dl.py --lyrics  runs it by hand.
LYRICS_RETRY_DAYS = 30
LR_UA = {"User-Agent": "dl.py-playlist-sync/1.0 ( https://github.com/jieke238y34/playlist-sync )"}


def lyrics_kind(text):
    "'lrc' if the text has time stamps like [01:23.45], else 'txt'."
    return "lrc" if re.search(r"(?m)^\s*\[\d{1,3}:\d{2}", text or "") else "txt"


def _lyr_lrclib(artist, title, album, secs):
    q = urllib.parse.urlencode({"artist_name": artist, "track_name": title, "album_name": album or "",
                                "duration": int(secs)})
    d = None
    try:
        d = _web_json("https://lrclib.net/api/get?" + q, LR_UA, tries=2)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    if not d:
        res = _web_json("https://lrclib.net/api/search?" + urllib.parse.urlencode(
            {"track_name": title, "artist_name": artist}), LR_UA, tries=2)
        d = next((x for x in res if abs((x.get("duration") or 0) - secs) <= 4), None)
    return ((d or {}).get("syncedLyrics") or (d or {}).get("plainLyrics") or "").strip()


def _lyr_synced(artist, title):
    import syncedlyrics
    return (syncedlyrics.search(f"{title} {artist}") or "").strip()


def _lyr_genius(artist, title, token):
    from spotdl.providers.lyrics import Genius
    return (Genius(token).get_lyrics(title, [artist]) or "").strip()


def find_lyrics(cfg, artist, title, album, secs):
    "The lyrics text from the first source that has it, or ''."
    for src in cfg["lyrics_sources"]:
        try:
            if src == "lrclib":
                text = _lyr_lrclib(artist, title, album, secs)
            elif src == "synced":
                text = _lyr_synced(artist, title)
            elif src == "genius" and cfg["genius_token"]:
                text = _lyr_genius(artist, title, cfg["genius_token"])
            else:
                continue
        except Exception:
            continue
        if len(text) > 40:
            return text
    return ""


def fetch_lyrics(cfg, dry_run=False):
    "Runs inside the private venv (it has mutagen and spotDL)."
    try:
        from mutagen import File as mutagen_file
    except ImportError:
        return
    music_dir, state = cfg["music_dir"], cfg["state_dir"]
    recs = [r for r in _pl_library(music_dir, state, mutagen_file) if not r["dup"]]
    cache_file = state / "lyrics-cache.json"
    try:
        misses = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        misses = {}
    now = time.time()
    todo = [r for r in recs if r["artist"] and r["title"]
            and not any(Path(r["p"]).with_suffix(e).exists() for e in SIDECARS)
            and now - misses.get(r["p"], 0) > LYRICS_RETRY_DAYS * 86400]
    batch = todo[:max(0, cfg["lyrics_limit"])]
    if dry_run:
        print(f"Lyrics (preview): {len(todo)} songs have no lyrics file. {len(batch)} would be asked this run "
              f"(sources: {' '.join(cfg['lyrics_sources'])}).")
        return
    if not batch:
        print("Lyrics: every song already has a lyrics file, or was tried recently.")
        return
    print(f"Lyrics: looking for {len(batch)} of {len(todo)} songs ({' '.join(cfg['lyrics_sources'])})...")
    got = {"lrc": 0, "txt": 0}
    for n, r in enumerate(batch, 1):
        text = find_lyrics(cfg, r["artist"].split(";")[0].strip(), r["title"], r["album"], r["len"])
        if text:
            kind = lyrics_kind(text)
            try:
                Path(r["p"]).with_suffix("." + kind).write_text(text + "\n", encoding="utf-8")
                got[kind] += 1
            except OSError:
                pass
        else:
            misses[r["p"]] = now
        if n % 50 == 0:
            print(f"  {n} of {len(batch)}...")
            try:
                cache_file.write_text(json.dumps(misses), encoding="utf-8")
            except OSError:
                pass
        time.sleep(0.3)
    try:
        cache_file.write_text(json.dumps(misses), encoding="utf-8")
    except OSError:
        pass
    cprint(f"Lyrics: {got['lrc']} timed (.lrc) and {got['txt']} plain (.txt) files written, "
           f"{len(batch) - got['lrc'] - got['txt']} songs without a result.")


# ---------------------------------------------------------------------
# SCHEDULED RUNS (off unless you ask)
#   dl.py --schedule daily | weekly     run dl.py by itself at 03:00
#   dl.py --schedule status             is it scheduled?
#   dl.py --unschedule                  remove it
# Linux uses a systemd user timer (or cron if there is none), macOS launchd, Windows Task Scheduler.
SCHED = "dl-playlist-sync"


def schedule_files(when, python, script):
    "The text of the files a scheduler needs, as {name: text}. when is daily or weekly."
    workdir = str(Path(script).parent)
    return {
        SCHED + ".service": f"[Unit]\nDescription=dl.py playlist sync\n\n[Service]\nType=oneshot\n"
                            f"WorkingDirectory={workdir}\nExecStart={python} {shlex.quote(str(script))}\n",
        SCHED + ".timer": f"[Unit]\nDescription=dl.py playlist sync ({when})\n\n[Timer]\n"
                          f"OnCalendar={'*-*-* 03:00:00' if when == 'daily' else 'Sun *-*-* 03:00:00'}\n"
                          "Persistent=true\nRandomizedDelaySec=10min\n\n[Install]\nWantedBy=timers.target\n",
        "cron": f"{'0 3 * * *' if when == 'daily' else '0 3 * * 0'} cd {shlex.quote(workdir)} && {python} "
                f"{shlex.quote(str(script))}  # {SCHED}",
        "plist": '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>\n'
                 f"<key>Label</key><string>{SCHED}</string>\n<key>ProgramArguments</key><array>"
                 f"<string>{python}</string><string>{script}</string></array>\n"
                 f"<key>WorkingDirectory</key><string>{workdir}</string>\n<key>StartCalendarInterval</key><dict>"
                 f"{'<key>Weekday</key><integer>0</integer>' if when == 'weekly' else ''}"
                 "<key>Hour</key><integer>3</integer><key>Minute</key><integer>0</integer></dict>\n</dict></plist>\n",
    }


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def schedule_cmd(action):
    "action: daily, weekly, status or off. Returns a message."
    script, python = Path(__file__).resolve(), sys.executable
    files = schedule_files(action if action in ("daily", "weekly") else "daily", python, script)
    home = Path.home()
    if os.name == "nt":
        if action == "status":
            r = _run(["schtasks", "/Query", "/TN", SCHED])
            return "scheduled (Task Scheduler)" if r.returncode == 0 else "not scheduled"
        if action == "off":
            _run(["schtasks", "/Delete", "/TN", SCHED, "/F"])
            return "Removed the scheduled task."
        r = _run(["schtasks", "/Create", "/F", "/TN", SCHED, "/SC", action.upper(), "/ST", "03:00",
                  "/TR", f'"{python}" "{script}"'])
        return f"Scheduled {action} at 03:00 (Task Scheduler)." if r.returncode == 0 else "Failed: " + r.stderr.strip()
    if sys.platform == "darwin":
        plist = home / "Library" / "LaunchAgents" / (SCHED + ".plist")
        if action == "status":
            return f"scheduled (launchd: {plist})" if plist.exists() else "not scheduled"
        if action == "off":
            _run(["launchctl", "unload", str(plist)])
            plist.unlink(missing_ok=True)
            return "Removed the scheduled run."
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(files["plist"], encoding="utf-8")
        _run(["launchctl", "unload", str(plist)])
        r = _run(["launchctl", "load", str(plist)])
        return f"Scheduled {action} at 03:00 (launchd)." if r.returncode == 0 else "Failed: " + r.stderr.strip()
    unit = home / ".config" / "systemd" / "user"
    has_systemd = shutil.which("systemctl") and _run(["systemctl", "--user", "show-environment"]).returncode == 0
    if has_systemd:
        if action == "status":
            r = _run(["systemctl", "--user", "list-timers", SCHED + ".timer", "--no-pager"])
            return "scheduled (systemd user timer)\n" + r.stdout.strip() if (unit / (SCHED + ".timer")).exists() else "not scheduled"
        if action == "off":
            _run(["systemctl", "--user", "disable", "--now", SCHED + ".timer"])
            for ext in (".service", ".timer"):
                (unit / (SCHED + ext)).unlink(missing_ok=True)
            _run(["systemctl", "--user", "daemon-reload"])
            return "Removed the scheduled run."
        unit.mkdir(parents=True, exist_ok=True)
        for ext in (".service", ".timer"):
            (unit / (SCHED + ext)).write_text(files[SCHED + ext], encoding="utf-8")
        _run(["systemctl", "--user", "daemon-reload"])
        r = _run(["systemctl", "--user", "enable", "--now", SCHED + ".timer"])
        return (f"Scheduled {action} at 03:00 (systemd user timer). Runs only while you are logged in, unless you "
                f"run: loginctl enable-linger {os.environ.get('USER', '')}") if r.returncode == 0 else "Failed: " + r.stderr.strip()
    cur = _run(["crontab", "-l"]).stdout if shutil.which("crontab") else ""
    kept = "\n".join(x for x in cur.splitlines() if "# " + SCHED not in x)
    if action == "status":
        return "scheduled (cron)" if "# " + SCHED in cur else "not scheduled"
    if action != "off":
        kept = (kept + "\n" if kept else "") + files["cron"]
    r = subprocess.run(["crontab", "-"], input=kept + "\n", capture_output=True, text=True)
    if r.returncode != 0:
        return "Failed: " + r.stderr.strip()
    return "Removed the scheduled run." if action == "off" else f"Scheduled {action} at 03:00 (cron)."


# ---------------------------------------------------------------------
# --ego: a short portrait of your library, in words. Everything comes from your own tags and playlist names.
MOODS = (("autumn / fall", r"autumn|fall\b"), ("winter", r"winter|snow|christmas"), ("summer", r"summer|beach"),
         ("spring", r"spring"), ("the gym", r"gym|workout|lift"), ("late nights", r"night|3:33|insomnia|sleep"),
         ("gaming", r"rocket league|gaming|minecraft|skyrim"), ("nostalgia", r"nostalg|throwback|old school|forgot"),
         ("driving and travel", r"road trip|drive|driving|europe"), ("studying and focus", r"study|focus|lo-?fi"))


def library_stats(recs, music_dir, playlist_names=()):
    "Numbers about a library, for --ego. recs: song records (not in _Duplicates)."
    artists, folders, albums, scripts, decades = {}, {}, set(), {}, {}
    for r in recs:
        key = _dd_artist(r["artist"])
        if key:
            artists.setdefault(key, [0, r["artist"].split(";")[0].strip()])[0] += 1
        rel = Path(r["p"]).relative_to(music_dir).parts
        top = rel[0] if len(rel) > 1 else ""
        if top and not top.startswith(("_", ".")):
            folders[top] = folders.get(top, 0) + 1
        if r["album"]:
            albums.add(r["album"].lower())
        text = (r["title"] or "") + (r["artist"] or "")
        for name, rx in SCRIPTS:
            if re.search(rx, text):
                scripts[name] = scripts.get(name, 0) + 1
                break
        m = re.match(r"(\d{4})", r.get("date") or "")
        if m and 1900 <= int(m.group(1)) <= 2100:
            decades[int(m.group(1)) // 10 * 10] = decades.get(int(m.group(1)) // 10 * 10, 0) + 1
    n = len(recs)
    counts = sorted(artists.values(), key=lambda x: -x[0])
    return {"songs": n, "hours": sum(r["len"] for r in recs) / 3600, "artists": len(artists), "albums": len(albums),
            "one_share": sum(1 for c, _ in counts if c == 1) / max(len(counts), 1),
            "top10_share": sum(c for c, _ in counts[:10]) / max(n, 1), "top": [(a, c) for c, a in counts[:8]],
            "folders": sorted(folders.items(), key=lambda x: -x[1]), "scripts": scripts, "decades": decades,
            "unsorted": sum(folders.get(d, 0) for d in GR_DIRS) / max(n, 1),
            "playlists": list(playlist_names)}


def ego_items(st):
    "The portrait as a list of {label, text, bars, hist, kind}. ego_text and ego_pretty both draw it."
    if not st["songs"]:
        return [{"label": "Library", "text": "There are no songs in your library yet, so there is nothing to describe. "
                 "Run dl.py first."}]
    out = [{"label": "Library", "text": f"{st['songs']:,} songs, about {st['hours']:.0f} hours ({st['hours'] / 24:.1f} days "
            f"of non-stop playing), {st['artists']:,} artists and {st['albums']:,} albums."}]
    if st["one_share"] >= 0.7 and st["top10_share"] <= 0.12:
        out.append({"label": "Style", "bars": [("one-song artists", st["one_share"]), ("your top 10 artists", st["top10_share"])],
                    "text": f"It is discovery-driven: {st['one_share']:.0%} of your artists have just one song and your ten "
                            f"biggest artists make up only {st['top10_share']:.1%} of the library. You collect through "
                            "playlists and moments more than through discographies."})
    elif st["top10_share"] >= 0.25:
        out.append({"label": "Style", "bars": [("your top 10 artists", st["top10_share"])],
                    "text": f"It is loyal to a few artists: your ten biggest artists make up {st['top10_share']:.0%} of "
                            "the library."})
    else:
        out.append({"label": "Style", "bars": [("one-song artists", st["one_share"]), ("your top 10 artists", st["top10_share"])],
                    "text": f"It balances both: {st['one_share']:.0%} of your artists have one song, and your ten biggest "
                            f"artists are {st['top10_share']:.0%} of the library."})
    if st["top"]:
        out.append({"label": "Favourites", "text": "Most collected: "
                    + ", ".join(f"{a} ({c})" for a, c in st["top"][:6]) + "."})
    real = [(f, c) for f, c in st["folders"] if f not in GR_DIRS]
    total = sum(c for _, c in real) or 1
    if real:
        shares = [c / total for _, c in real]
        entropy = -sum(p * math.log(p) for p in shares if p > 0) / math.log(len(shares)) if len(shares) > 1 else 0
        spread = "very wide" if entropy >= 0.8 else "wide" if entropy >= 0.6 else "focused"
        out.append({"label": "Genres", "bars": [(f, c / total) for f, c in real[:6]],
                    "text": f"Genre spread is {spread}: " + ", ".join(f"{f} {100 * c // total}%" for f, c in real[:5])
                            + (f", across {len(real)} genre folders." if len(real) > 5 else ".")})
    if st["scripts"]:
        out.append({"label": "Languages", "text": "Beyond Latin letters: " + ", ".join(
            f"{n} titles in {k}" for k, n in sorted(st["scripts"].items(), key=lambda x: -x[1])) + "."})
    known = sum(st["decades"].values())
    if known >= 100:
        top_dec = max(st["decades"], key=st["decades"].get)
        out.append({"label": "Eras", "hist": dict(sorted(st["decades"].items())),
                    "text": f"{len(st['decades'])} decades from the {min(st['decades'])}s to the {max(st['decades'])}s; "
                            f"the {top_dec}s lead with {100 * st['decades'][top_dec] // known}% of the songs that have a date."})
    names = " ".join(st["playlists"]).lower()
    found = [label for label, rx in MOODS if re.search(rx, names)]
    if found:
        out.append({"label": "Moods", "text": "Moods and moments in your playlist names: " + ", ".join(found) + "."})
    if st["unsorted"] >= 0.05:
        out.append({"label": "Careful", "kind": "warn",
                    "text": f"{st['unsorted']:.0%} of the songs sit in Other/Unknown folders, so the genre picture is "
                            "partly missing tags, not taste. dl.py --genres can fill many of them in."})
    out.append({"label": "Note", "kind": "dim",
                "text": "This is a snapshot of the tags in your library, not of everything you like."})
    return out


def ego_text(st):
    "The portrait as plain sentences."
    return [i["text"] for i in ego_items(st)]


def _cassette_front(top, mid, low):
    "The front of a cassette in ASCII (screws, spoked reels, label stripe, tape heads), every row 53 wide."
    w = 50

    def body(inner):                      # one row of the outer shell
        return " |" + inner.center(w)[:w] + "|"
    reel = [" .-----. ",
            "/ \\ | / \\",
            "|--(o)--|",
            "\\ / | \\ /",
            " '-----' "]
    gap = 44 - 2 - 2 * len(reel[0])       # room for the text between the reels
    texts = ["", top, mid, low, ""]
    rows = [" ." + "-" * w + ".",
            body(" (+)" + " " * 40 + "(+) "),
            body("  ." + "-" * 44 + ".  "),
            body("  |" + (" " + ":" * 42 + " ") + "|  ")]
    for i in range(5):
        c = " " + reel[i] + texts[i].center(gap) + reel[i] + " "
        rows.append(body("  |" + c + "|  "))
    rows += [body("  '" + "-" * 44 + "'  "),
             body(" (+)" + " " * 40 + "(+) "),
             body("  _/" + "_" * 40 + "\\_  "),
             body(" /  o  ." + "-" * 10 + "[]" + "-" * 4 + "[]" + "-" * 8 + "[]" + "-" * 5 + ".  o  \\ "),
             " '" + "-" * w + "'"]
    return rows


def cassette_parts(top, mid, low, depth=2):
    """The cassette as a 3D box (oblique view): front face, a lit top face and a shaded side face.
    Returns rows; a row is a list of (text, part) with part front, top or side. All rows have the same width."""
    front = _cassette_front(top, mid, low)
    w, hf, d = len(front[0]), len(front), depth
    total = w + 2 * d
    rows = []
    for g in range(hf + d):
        segs = []
        if g == 0:                                              # back edge of the top face
            segs.append((" " * (2 * d) + "." + "-" * (w - 2) + ".", "top"))
        elif g < d:                                             # top face
            segs.append((" " * (2 * (d - g)) + "/" + ":" * (w - 2) + "/", "top"))
            segs.append(("#" * (2 * g - 1) + "|", "side"))
        else:
            segs.append((front[g - d], "front"))
            if g <= hf - 1:                                     # side face, full width
                segs.append(("#" * (2 * d - 1) + "|", "side"))
            else:                                               # side face tapers at the bottom
                n = 2 * d - 2 * (g - hf + 1)
                if n > 0:
                    segs.append(("#" * (n - 1) + "/", "side"))
        used = sum(len(t) for t, _ in segs)
        segs.append((" " * (total - used), "side"))
        rows.append(segs)
    return rows


def cassette(top, mid, low):
    "The 3D cassette as plain text rows, all the same width."
    return ["".join(t for t, _ in row) for row in cassette_parts(top, mid, low)]


def _bar(frac, width, unicode_ok):
    n = max(0, min(width, round(frac * width)))
    full, empty = ("█", "░") if unicode_ok else ("#", "-")
    return paint(full * n, "blue") + paint(empty * (width - n), "blue_sh")


def ego_pretty(st, unicode_ok=True):
    "The portrait decorated for a terminal: cassette art and bars. Returns a list of lines."
    import textwrap
    lines = [""]
    if st["songs"]:
        hours = f"{st['hours']:.0f} hours"
        tint = {"front": "blue", "top": "blue_hi", "side": "blue_lo"}
        for row in cassette_parts("S O N G S", f"{st['songs']:,}", hours):
            lines.append("   " + "".join(paint(t, tint[part]) if t.strip() else t for t, part in row))
    lines.append("")
    dot = " · " if unicode_ok else " | "
    for item in ego_items(st):
        label = paint(f"{item['label']:<11}", "warn" if item.get("kind") == "warn" else "bold")
        wrapped = textwrap.wrap(item["text"], 62) or [""]
        tint = {"warn": "warn", "dim": "dim"}.get(item.get("kind"))
        lines.append("   " + label + (paint(wrapped[0], tint) if tint else wrapped[0]))
        lines += ["   " + " " * 11 + (paint(w, tint) if tint else w) for w in wrapped[1:]]
        for name, frac in item.get("bars", []):
            lines.append("   " + " " * 11 + f"{name[:19]:<19} {_bar(frac, 22, unicode_ok)} {frac:>5.0%}")
        if item.get("hist"):
            h = item["hist"]
            top = max(h.values())
            blocks = "▁▂▃▄▅▆▇█" if unicode_ok else ".:-=+*#@"
            bars = "".join(blocks[min(7, int(v / top * 7.99))] for v in h.values())
            lines.append("   " + " " * 11 + paint(bars, "blue") + f"   {min(h)}s{dot}{max(h)}s")
        lines.append("")
    return lines


def run_ego(cfg):
    "Runs inside the private venv."
    try:
        from mutagen import File as mutagen_file
    except ImportError:
        return
    recs = [r for r in _pl_library(cfg["music_dir"], cfg["state_dir"], mutagen_file) if not r["dup"]]
    names = []
    try:
        names = [v["name"] for v in json.loads((cfg["state_dir"] / "playlist-cache.json").read_text(encoding="utf-8")).values()]
    except (OSError, ValueError, KeyError):
        pass
    unicode_ok = "utf" in (getattr(sys.stdout, "encoding", "") or "").lower()
    for line in ego_pretty(library_stats(recs, cfg["music_dir"], names), unicode_ok):
        print(line)


def run_inner(py, *args):
    """Run this file again with the private venv's Python (it has mutagen)."""
    rc = subprocess.run([str(py), str(Path(__file__).resolve()), *args], env=CHILD_ENV).returncode
    if rc != 0:
        print(f"WARNING: the helper step failed (exit code {rc}). See the messages above.")
    return rc


def run_sorter(py, dry_run=False):
    """Run sort_library with the private venv's Python."""
    run_inner(py, "--sort-inner", *(["--dry-run"] if dry_run else []))


def main():
    create_missing_files()
    cfg = load_config()
    ids = load_ids(cfg["playlists_file"])
    if not ids:
        sys.exit(f"No valid playlist IDs in {cfg['playlists_file']}")
    print(f"Playlists to download: {len(ids)}")

    state, music = cfg["state_dir"], cfg["music_dir"]
    state.mkdir(parents=True, exist_ok=True)
    music.mkdir(parents=True, exist_ok=True)
    cfg["archive_file"].parent.mkdir(parents=True, exist_ok=True)
    if cfg["sort_after"]:                          # build the genre folders before the first download
        made = create_genre_folders(music, cfg["overrides_file"])
        if made:
            print(f"Created {len(made)} genre folders in {music}: {', '.join(made)}")
    log = open(state / "dl.log", "a", encoding="utf-8")
    if IS_WIN:                                   # stop Windows sleeping during the run
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        except Exception:
            pass
    else:                                        # macOS / Linux: no suspend while we run
        _pid = str(os.getpid())
        if shutil.which("caffeinate"):
            _cmd = ["caffeinate", "-i", "-w", _pid]
        elif shutil.which("systemd-inhibit") and shutil.which("tail"):
            _cmd = ["systemd-inhibit", "--what=sleep:idle", "--who=dl.py",
                    "--why=Downloading playlists", "tail", f"--pid={_pid}", "-f", "/dev/null"]
        else:
            _cmd = None
        if _cmd:                                 # each one ends by itself when this script exits
            try:
                subprocess.Popen(_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            except OSError:
                pass

    def say(msg):
        line = f"[{datetime.now():%H:%M}] {msg}"
        print(colorize_line(line))
        log.write(line + "\n")
        log.flush()

    def notify(msg):
        say(msg)
        if IS_WIN:                               # Windows: warning beep
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass
        elif shutil.which("notify-send"):        # Linux desktop popup, if available
            subprocess.run(["notify-send", "-u", "critical", "Music download", msg])

    def err_file(pid):
        return state / f"errors-{pid}.txt"

    def has_errors(pid):
        f = err_file(pid)
        return f.exists() and f.stat().st_size > 0

    def all_errors():
        "Error lines of the playlists in this run only (old files of removed playlists are ignored)."
        out = []
        for pid in ids:
            if has_errors(pid):
                out += err_file(pid).read_text(encoding="utf-8", errors="replace").splitlines()
        return out

    for _pid in ids:                      # error files left from an older run are not current
        err_file(_pid).unlink(missing_ok=True)

    def abort(reason):
        errs = all_errors()
        types = {}
        for e in errs:
            k = re.sub(r"https?://\S*", "", e)
            types[k] = types.get(k, 0) + 1
        report = state / "failure-report.txt"
        with report.open("w", encoding="utf-8") as f:
            f.write(f"Download stopped: {reason}\n{datetime.now()}\n\n")
            f.write("--- error types across all playlists ---\n")
            for k, n in sorted(types.items(), key=lambda x: -x[1])[:20]:
                f.write(f"{n:6d} {k}\n")
            f.write("\n--- all error lines ---\n" + "\n".join(errs) + "\n")
        notify(f"Stopped: {reason}. Report: {report}")
        if cfg["sort_after"]:                    # sort the songs that did download
            say("Sorting the songs downloaded so far...")
            run_sorter(py)
        sys.exit(1)

    py = ensure_spotdl(cfg["venv_dir"])
    if cfg["auto_update"]:
        update_tools(py, state)
    ff = ensure_ffmpeg(py)
    run_inner(py, "--seed-inner")                # skip songs that are already in the library

    # node helps yt-dlp solve YouTube checks. Without it, expect more errors.
    js = ["--js-runtimes", "node"] if shutil.which("node") else []
    if not js:
        say("WARNING: node not found. YouTube downloads may fail more often.")
        say("  Optional fix: install Node.js LTS from https://nodejs.org, then start again.")

    # Cookies are used only in the retry passes that run after the long wait.
    cookies = cfg["cookies_file"]
    if cookies:
        if not cookies.is_file():
            say(f"WARNING: cookies file not found: {cookies}. Continuing without it.")
            cookies = None
        elif not any(h in cookies.read_text(encoding="utf-8", errors="replace")[:200]
                     for h in ("Netscape", "HTTP Cookie File")):
            say("WARNING: cookies file is not in Netscape format (first line must be "
                "'# Netscape HTTP Cookie File'). Export it again. Continuing without it.")
            cookies = None

    def gate(use=None):
        ok, why, blocked = test_download(py, js, ff, use)
        if ok:
            return True
        if not blocked:     # the test video itself may be gone or region-locked: do not stop for that
            say(f"WARNING: test download failed, but not like a block ({why}). Continuing.")
            return True
        notify(f"Test download failed ({why}). Retrying in {cfg['gate_wait'] // 60} min.")
        time.sleep(cfg["gate_wait"])
        ok, why, blocked = test_download(py, js, ff, use)
        if not ok:
            say(f"Test download failed again: {why}")
        return ok or not blocked

    # Pass 1: all playlists, with the sources from config.ini. Later passes: only playlists
    # that had errors. Plan row = (sources, seconds to wait first, use cookies).
    # If SoundCloud is in the sources, pass 2 runs the failed songs through YouTube at once
    # (short wait: YouTube has not been asked for them yet). The other passes then wait.
    sources = list(cfg["audio"])
    fallback = [x for x in sources if x != "soundcloud"] or ["youtube"]
    if sources[0] == "soundcloud":
        plan = [(sources, 0, False), (fallback, cfg["pause"], False),
                (fallback, cfg["pass_wait"], True), (fallback, cfg["pass_wait"], True)]
    else:
        plan = [(sources, 0, False), (sources, cfg["pass_wait"], True),
                (sources, cfg["pass_wait"], True)]
    # With soundcloud first, a song on YouTube already failed on SoundCloud: no point asking again.
    upgrade_on = cfg["prefer_sc"] and "soundcloud" in sources and sources[0] != "soundcloud"
    bad = 0
    eta = Eta(state, cfg["pause"])
    for pas, (audio, wait, use_cookies) in enumerate(plan, 1):
        pass_cookies = None
        if pas == 1:
            todo = list(ids)
        else:
            todo = [q for q in ids if has_errors(q)]
            if not todo:
                say("No failed songs left. Skipping the retry passes.")
                break
            pass_cookies = cookies if use_cookies else None
            say(f"--- pass {pas}: retrying {len(todo)} playlists with failed songs, waiting "
                f"{wait // 60} min {wait % 60} s, sources: {' '.join(audio)} ---")
            time.sleep(wait)
        yt = yt_args(js, pass_cookies)
        # SponsorBlock works on YouTube only. spotDL crashes with KeyError
        # 'sponsorblock_chapters' on any other source, so it is on only for YouTube-only lists.
        sponsor = [] if set(audio) - {"youtube", "youtube-music"} else ["--sponsor-block"]
        # The YouTube test download runs at the start of a pass, and again only after a
        # playlist that had block-type errors or crashed. A clean playlist proves YouTube works.
        need_gate = True
        eta.begin_pass(todo)

        for n, pid in enumerate(todo, 1):
            err = err_file(pid)
            if audio[0] != "soundcloud" and need_gate and not gate(pass_cookies):   # the test needs YouTube
                abort(f"YouTube blocked (test download failed twice) before playlist {pid}")
            say(f"Pass {pas}: playlist {n} of {len(todo)} ({pid})")
            err.unlink(missing_ok=True)

            cmd = [str(py), "-c", SPOTDL_RUNNER, "download",
                   f"https://open.spotify.com/playlist/{pid}",
                   "--archive", str(cfg["archive_file"]),
                   "--save-errors", str(err),
                   "--threads", "1", "--max-retries", "3",
                   "--audio", *audio,
                   "--format", "mp3", "--bitrate", "192k", *sponsor,
                   "--output", "{title} - {artist} - {album}.{output-ext}",
                   "--yt-dlp-args", yt]
            marks = []
            eta.begin_playlist(n, pid)
            rc, hit, block_lines = run_playlist(cmd, music, log, cfg["max_block"], marks, eta.line)
            eta.end_playlist()
            if upgrade_on:
                queue_upgrades(state, marks)

            with err.open("a", encoding="utf-8") as f:
                if hit:
                    f.write("stopped by script: block-type errors in output\n")
                    f.write("\n".join(block_lines[:50]) + "\n")
                elif rc != 0:
                    f.write(f"crashed (exit {rc})\n")
            n2 = sum(1 for l in err.read_text(encoding="utf-8", errors="replace").splitlines()
                     if is_block(l))

            if hit or rc != 0 or n2 >= cfg["max_block"]:
                bad += 1
                notify(f"Playlist {pid} failed ({bad} in a row)")
            else:
                bad = 0
            if bad >= cfg["max_bad"]:
                abort(f"{cfg['max_bad']} playlists in a row failed with block-type errors")
            need_gate = bool(hit or rc != 0 or n2 or block_lines)
            if n < len(todo):                    # no pause after the last playlist of a pass
                say(f"Waiting {cfg['pause']} s before the next playlist (YouTube rate limit).")
                time.sleep(cfg["pause"])

    errs = all_errors()
    if errs:
        say(f"--- still failing ({len(errs)} lines) ---")
        for e in errs:
            say(e)
    else:
        say("No failed songs.")
    notify(f"All passes finished. Failed tracks are in {state}")

    if upgrade_on:
        say("Trying SoundCloud for songs that came from YouTube...")
        run_inner(py, "--upgrade-inner")

    if cfg["sort_after"]:
        say("Sorting songs into genre folders...")
        run_sorter(py)

    if cfg["gen_repair"]:
        say("Repairing missing genres...")
        run_inner(py, "--genres-inner")

    if playlists_wanted(cfg):
        say("Building playlist files...")
        run_inner(py, "--playlists-inner")

    if cfg["gap_report"] and playlists_wanted(cfg):
        say("Checking which playlist songs are missing...")
        run_inner(py, "--gaps-inner")

    if cfg["dd_preview"]:
        say("Checking for duplicate songs (preview only, nothing is moved)...")
        run_inner(py, "--dedupe-inner", "--dry-run", "--no-ask")

    if cfg["lyrics"]:                              # last: only songs that really are in the library
        say("Looking for lyrics...")
        run_inner(py, "--lyrics-inner")


if __name__ == "__main__":
    args = sys.argv[1:]
    try:
        _c = load_config()
        init_color(_c["color"], _c["theme"], quiet=any(a.endswith("-inner") for a in args))
        rest = [a for i, a in enumerate(args) if not (i and args[i - 1] in ("--colors", "--schedule") and not a.startswith("-"))]
        known = {"-h", "--help", "--sort", "--dry-run", "--upgrade", "--dedupe", "--report",
                 "--sort-inner", "--seed-inner", "--upgrade-inner", "--dedupe-inner", "--report-only",
                 "--no-ask", "--colors", "--themes", "--playlists", "--playlists-inner", "--cached", "--genres", "--genres-inner", "--gaps", "--gaps-inner",
                 "--lyrics", "--lyrics-inner", "--ego", "--ego-inner", "--schedule", "--unschedule"}
        if "-h" in args or "--help" in args:
            print(__doc__)
        elif set(rest) - known or ("--dry-run" in args and not {"--sort", "--sort-inner", "--dedupe", "--dedupe-inner", "--playlists", "--playlists-inner", "--genres", "--genres-inner", "--lyrics", "--lyrics-inner"} & set(args)):
            sys.exit("Usage: dl.py [--sort | --dedupe | --playlists | --genres | --lyrics [--dry-run]] [--gaps] [--report] [--upgrade]\n"
                     "              [--ego] [--schedule daily|weekly|status|off] [--unschedule] [--colors [NAME]] [--themes] [--help]\n"
                     "(unknown option, or --dry-run used without --sort)")
        elif "--sort-inner" in args:            # runs inside the private venv (has mutagen)
            _cfg = load_config()
            sort_library(_cfg["music_dir"], _cfg["overrides_file"], "--dry-run" in args)
        elif "--themes" in args:                # list the colour schemes
            show_themes()
        elif "--colors" in args:                # show the colours in use, or preview a scheme
            i = args.index("--colors")
            show_colors(args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("-") else None)
        elif "--lyrics-inner" in args:          # runs inside the private venv (has mutagen, spotDL)
            fetch_lyrics(load_config(), "--dry-run" in args)
        elif "--lyrics" in args:
            create_missing_files(need_playlists=False)
            _cfg = load_config()
            run_inner(ensure_spotdl(_cfg["venv_dir"]), "--lyrics-inner",
                      *(["--dry-run"] if "--dry-run" in args else []))
        elif "--ego-inner" in args:
            run_ego(load_config())
        elif "--ego" in args:
            create_missing_files(need_playlists=False)
            _cfg = load_config()
            run_inner(ensure_spotdl(_cfg["venv_dir"]), "--ego-inner")
        elif "--unschedule" in args:
            print(schedule_cmd("off"))
        elif "--schedule" in args:
            i = args.index("--schedule")
            when = args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("-") else "status"
            if when not in ("daily", "weekly", "status", "off"):
                sys.exit("Usage: dl.py --schedule daily | weekly | status | off")
            print(schedule_cmd(when))
        elif "--gaps-inner" in args:            # runs inside the private venv (has mutagen, spotDL)
            gap_report(load_config())
        elif "--gaps" in args:
            create_missing_files(need_playlists=True)
            _cfg = load_config()
            run_inner(ensure_spotdl(_cfg["venv_dir"]), "--gaps-inner")
        elif "--genres-inner" in args:          # runs inside the private venv (has mutagen)
            repair_genres(load_config(), "--dry-run" in args)
        elif "--genres" in args:
            create_missing_files(need_playlists=False)
            _cfg = load_config()
            run_inner(ensure_spotdl(_cfg["venv_dir"]), "--genres-inner",
                      *(["--dry-run"] if "--dry-run" in args else []))
        elif "--playlists-inner" in args:       # runs inside the private venv (has mutagen, spotDL)
            build_playlists(load_config(), refresh="--cached" not in args, dry_run="--dry-run" in args)
        elif "--playlists" in args:
            create_missing_files(need_playlists=True)
            _cfg = load_config()
            run_inner(ensure_spotdl(_cfg["venv_dir"]), "--playlists-inner",
                      *(["--dry-run"] if "--dry-run" in args else []))
        elif "--dedupe-inner" in args:          # runs inside the private venv (has mutagen)
            run_dedupe(load_config(), "--dry-run" in args, "--report-only" in args,
                       ask="--no-ask" not in args and sys.stdin.isatty())
        elif "--dedupe" in args or "--report" in args:
            create_missing_files(need_playlists=False)
            _cfg = load_config()
            run_inner(ensure_spotdl(_cfg["venv_dir"]), "--dedupe-inner",
                      *(["--dry-run"] if "--dry-run" in args else []),
                      *(["--report-only"] if "--report" in args else []))
        elif "--upgrade-inner" in args:         # runs inside the private venv (has mutagen)
            _cfg = load_config()
            upgrade_from_soundcloud(_cfg["music_dir"], _cfg["state_dir"])
        elif "--upgrade" in args:               # SoundCloud upgrade only, no download
            create_missing_files(need_playlists=False)
            _cfg = load_config()
            run_inner(ensure_spotdl(_cfg["venv_dir"]), "--upgrade-inner")
        elif "--seed-inner" in args:            # runs inside the private venv (has mutagen)
            _cfg = load_config()
            seed_archive(_cfg["music_dir"], _cfg["archive_file"])
        elif "--sort" in args:                  # sort only, no download
            create_missing_files(need_playlists=False)
            _cfg = load_config()
            _py = ensure_spotdl(_cfg["venv_dir"])
            run_sorter(_py, "--dry-run" in args)
            if "--dry-run" not in args and playlists_wanted(_cfg) and (_cfg["state_dir"] / "playlist-cache.json").is_file():
                run_inner(_py, "--playlists-inner", "--cached")
        else:
            main()
    except KeyboardInterrupt:
        print("\nStopped by user.")
        sys.exit(130)
    except SystemExit as e:
        if isinstance(e.code, str):          # print the message now, before the pause
            print(e.code)
            e.code = 1
        raise
    except Exception:
        traceback.print_exc()                # print the error now, before the pause
        sys.exit(1)
    finally:
        if not any(a.endswith("-inner") for a in args):   # inner runs have no own window
            pause_if_own_window()
