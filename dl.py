#!/usr/bin/env python3
"""
dl.py - download Spotify playlists as MP3 files with spotDL.
ONE file. Works on Windows, Linux and macOS.

RUN
    Windows:        double-click dl.py   (or: py dl.py)
    Linux / macOS:  python3 dl.py

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
    python3 dl.py                   download, then sort into genre folders
    python3 dl.py --sort            sort only
    python3 dl.py --sort --dry-run  preview the sort, move nothing
    python3 dl.py --dedupe          move duplicate songs to _Duplicates (nothing is deleted)
    python3 dl.py --dedupe --dry-run  short summary, then type --expand (list the
                                    duplicates), --dedupe (move them) or --exit
    python3 dl.py --report          library report (sizes, formats, weakest files)
    python3 dl.py --colors          show the colours that are in use (KDE Plasma colours if available)
    python3 dl.py --colors NAME     preview a KDE colour scheme that you have, active or not
    python3 dl.py --themes          list the KDE colour schemes you can use as  theme = NAME
    python3 dl.py --upgrade         SoundCloud upgrade only (songs still waiting from earlier runs)
    python3 dl.py --help            show this text

SOURCES
    YouTube is tried first. Songs that came from YouTube are then tried on SoundCloud. A SoundCloud
    file replaces the YouTube file only if it is not DRM protected, is about as long, is at least
    60 s and is not a cover, remix or live version. Switch it off in config.ini
    ([sources] prefer_soundcloud = no).

DUPLICATES
    At the end of every run, dl.py previews duplicate songs (same artist, title and length,
    or the same Spotify link) but moves nothing. To move them, run:  python3 dl.py --dedupe
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
import urllib.request
import venv
import zipfile
from datetime import datetime
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
# Cyan for good and bold red for bad: easy to tell apart with most kinds of colour blindness.
COLOR_ON = False
ACTIVE_THEME = "auto"
PALETTE = {"ok": "36", "err": "1;31", "warn": "33", "bold": "1", "dim": "2"}
DEFAULT_PALETTE = dict(PALETTE)


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
        title = f"Preview of the colour scheme '{scheme}'" if pal else "standard terminal colours"
    else:
        if PALETTE == DEFAULT_PALETTE:
            title = "standard terminal colours"
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
    if theme.lower() == "ansi":
        return
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
_DIM_START = ("Skipped", "Skipping", "Found ", "Processing query", "Waiting ", "Saved ", "Checking ",
              "Library check", "Nothing was moved")
_WARN_START = ("SoundCloud failed", "No close match", "Kept the YouTube", "NOTE", "Note:", "Fallback")


def colorize_line(line):
    "Colour one line of output by what it says. Lines that already have colour are left alone."
    if not COLOR_ON or "\033" in line:
        return line
    t = re.sub(r"^\[\d\d:\d\d\]\s*", "", line.strip())
    if not t:
        return line
    if t.startswith(_WARN_START) or "WARNING" in t:
        kind = "warn"
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

[display]
# Coloured text on screen (cyan = good, yellow = warning, bold red = problem).
# The log files never contain colours.
# auto = colours in a terminal, none when the output goes to a file (or NO_COLOR is set).
# yes = always. no = never. Default: auto
color = auto

# Which colours to use.
#   auto = copy the KDE Plasma colour scheme that is active now (only on Plasma, in a true-colour
#          terminal such as Konsole or Kitty). Otherwise the standard terminal colours.
#   NAME = copy ANY KDE colour scheme you have installed, even if it is not the active one.
#          Example:  theme = CatppuccinMocha     (a .colors file in ~/.local/share/color-schemes)
#          Or a full path:  theme = /home/me/Downloads/Scheme.colors
#          See the choices:  dl.py --themes     Preview one:  dl.py --colors NAME
#   ansi = always the standard terminal colours.
# Scheme colours are brightened where needed so they stay easy to read on your background.
theme = auto

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
# GOOD TO KNOW
#   - Playlists download from top to bottom. Put the ones you want first at the top.
#   - Safe to run again and again: songs you already have are skipped, so only new
#     songs download. Add new playlists at the bottom whenever you like.
#   - New here? Try ONE small playlist first, then add the rest.
#   - Each song is looked up on YouTube first, then SoundCloud. If SoundCloud has a good
#     version it replaces the YouTube one. See [sources] in config.ini.
#   - At the end of a run the script lists duplicate songs but moves nothing.
#     Move them with:  python3 dl.py --dedupe    (see [dedupe] in config.ini)
#   - Other commands:  --sort  --dedupe  --report  --upgrade  (python3 dl.py --help)
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
        "theme": cp.get("display", "theme", fallback="auto").strip().strip("\"'") or "auto",
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


def load_ids(path):
    """Read playlist IDs. Accepts bare IDs, full links, spotify: URIs.
    Strips quotes, commas, spaces and ?si=... Skips bad lines. Removes duplicates."""
    ids, seen = [], set()
    for n, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = LINK_ID_RE.search(line)
        if m:
            pid = m.group(1)
        else:
            pid = re.split(r"[?\s,;]", line.strip("\"'` ,;"))[0]
        where = f"{path.name} line {n}"
        if pid.startswith("37i9dQZF"):
            print(f"SKIPPED {where}: Spotify-made playlists do not work ({pid})")
        elif not ID_RE.fullmatch(pid):
            print(f"SKIPPED {where}: not a playlist ID or link: {line}")
        elif pid not in seen:
            seen.add(pid)
            ids.append(pid)
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


def run_playlist(cmd, cwd, log, max_block, marks=None):
    """Run spotDL. Copy output to screen and log. Stop it if too many block errors.
    marks: if given, gets the DL-OK lines of finished downloads (not shown, not logged).
    Returns (exit code, stopped by us, block-error lines)."""
    block_lines, hit = [], False
    p = subprocess.Popen(cmd, cwd=cwd, env=CHILD_ENV, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace")
    try:
        for line in p.stdout:
            if line.startswith("DL-OK "):
                if marks is not None:
                    marks.append(line.rstrip("\r\n")[6:])
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
                "title": one("title"), "album": one("album"), "genre": one("genre"),
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


def _dd_move(src, music_dir, sub=""):
    "Move src into _Duplicates (same folder layout). Never overwrites."
    dest = music_dir / DUP_DIR / sub / src.relative_to(music_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest = _dd_unique(dest)
    shutil.move(str(src), str(dest))
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
        cache = raw["files"] if raw.get("v") == 2 else {}
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
            print("Nothing was moved. To see the list and choose:  python3 dl.py --dedupe --dry-run")
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


def _dd_save_cache(path, cache):
    try:
        part = path.with_name(path.name + ".part")
        part.write_text(json.dumps({"v": 2, "files": {k: v for k, v in cache.items() if os.path.exists(k)}}),
                        encoding="utf-8")
        os.replace(part, path)
    except OSError:
        pass


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
            rc, hit, block_lines = run_playlist(cmd, music, log, cfg["max_block"], marks)
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

    if cfg["dd_preview"]:
        say("Checking for duplicate songs (preview only, nothing is moved)...")
        run_inner(py, "--dedupe-inner", "--dry-run", "--no-ask")


if __name__ == "__main__":
    args = sys.argv[1:]
    try:
        _c = load_config()
        init_color(_c["color"], _c["theme"], quiet=any(a.endswith("-inner") for a in args))
        rest = [a for i, a in enumerate(args) if not (i and args[i - 1] == "--colors" and not a.startswith("-"))]
        known = {"-h", "--help", "--sort", "--dry-run", "--upgrade", "--dedupe", "--report",
                 "--sort-inner", "--seed-inner", "--upgrade-inner", "--dedupe-inner", "--report-only",
                 "--no-ask", "--colors", "--themes"}
        if "-h" in args or "--help" in args:
            print(__doc__)
        elif set(rest) - known or ("--dry-run" in args and not {"--sort", "--sort-inner", "--dedupe", "--dedupe-inner"} & set(args)):
            sys.exit("Usage: dl.py [--sort | --dedupe [--dry-run]] [--report] [--upgrade] [--colors [NAME]] [--themes] [--help]\n"
                     "(unknown option, or --dry-run used without --sort)")
        elif "--sort-inner" in args:            # runs inside the private venv (has mutagen)
            _cfg = load_config()
            sort_library(_cfg["music_dir"], _cfg["overrides_file"], "--dry-run" in args)
        elif "--themes" in args:                # list the colour schemes
            show_themes()
        elif "--colors" in args:                # show the colours in use, or preview a scheme
            i = args.index("--colors")
            show_colors(args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("-") else None)
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
            run_sorter(ensure_spotdl(_cfg["venv_dir"]), "--dry-run" in args)
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
