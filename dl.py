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
    python3 dl.py --help            show this text

SPEED
    To avoid YouTube blocks, yt-dlp waits 15 to 40 seconds between tracks. Large
    playlists take a long time. This is on purpose.

OPTIONAL
    node (nodejs.org)  - fewer YouTube errors
    cookies file       - helps with age-restricted songs
"""
import configparser
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import venv
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
    import bisect as _bisect
    _EXT = {".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav"}
    _lib = {}
    def _norm(t):
        return " ".join(_re.findall(r"[a-z0-9]+", (t or "").lower()))
    def _keys():
        "Sorted 'title artist' keys of every audio file in all folders (built once per run of spotDL)."
        if "k" not in _lib:
            ks, seen = set(), set()
            for d, dirs, files in _os.walk(".", followlinks=True):   # spotDL runs in the music folder
                real = _os.path.realpath(d)
                if real in seen:                     # a folder link that loops: stop here
                    dirs[:] = []
                    continue
                seen.add(real)
                for f in files:
                    stem, ext = _os.path.splitext(f)
                    if ext.lower() in _EXT:
                        parts = stem.split(" - ")
                        ks.add(_norm(stem))
                        for n in range(2, len(parts) + 1):   # titles can hold " - " too
                            ks.add(_norm(" - ".join(parts[:n])))
            _lib["k"] = sorted(ks)
        return _lib["k"]
    def _have(song):
        key = _norm((song.name or "") + " " + (song.artist or ""))
        ks = _keys()
        i = _bisect.bisect_left(ks, key)
        return bool(key) and i < len(ks) and (ks[i] == key or ks[i].startswith(key + " "))
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
            return _o_get(self, url, download)
        except _AErr as e:
            if download:                             # spotDL hides this. dl.py needs it to see blocks.
                print("  Reason: " + _why(e), flush=True)
            if not (download and "soundcloud.com" in url and url in _seen):
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
                return _o_get(self, new, download)
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
# Default: soundcloud youtube
# Note: SoundCloud audio can be lower quality, and some songs there are only
# 30 second previews. Put youtube first if you care more about quality.
audio_sources = soundcloud youtube

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
        "audio": sources("audio_sources", ["soundcloud", "youtube"]),
        "auto_update": yes_no("auto_update", True, "updates"),
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


def yt_args(js, cookies):
    """Build the yt-dlp argument string that spotDL passes on."""
    parts = list(js)
    if cookies:
        # Forward slashes and double quotes survive spotDL's argument splitting on Windows.
        p = f'"{cookies.as_posix()}"' if IS_WIN else shlex.quote(str(cookies))
        parts += ["--cookies", p]
    parts += ["--sleep-interval", "15", "--max-sleep-interval", "40", "--sleep-requests", "2"]
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


def run_playlist(cmd, cwd, log, max_block):
    """Run spotDL. Copy output to screen and log. Stop it if too many block errors.
    Returns (exit code, stopped by us, block-error lines)."""
    block_lines, hit = [], False
    p = subprocess.Popen(cmd, cwd=cwd, env=CHILD_ENV, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace")
    try:
        for line in p.stdout:
            sys.stdout.write(line)
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

    def say(msg):
        line = f"[{datetime.now():%H:%M}] {msg}"
        print(line)
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
        elif "Netscape" not in cookies.read_text(encoding="utf-8", errors="replace")[:200]:
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
    if "soundcloud" in sources:
        plan = [(sources, 0, False), (fallback, cfg["pause"], False),
                (fallback, cfg["pass_wait"], True), (fallback, cfg["pass_wait"], True)]
    else:
        plan = [(sources, 0, False), (sources, cfg["pass_wait"], True),
                (sources, cfg["pass_wait"], True)]
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
            rc, hit, block_lines = run_playlist(cmd, music, log, cfg["max_block"])

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

    if cfg["sort_after"]:
        say("Sorting songs into genre folders...")
        run_sorter(py)


if __name__ == "__main__":
    args = sys.argv[1:]
    try:
        known = {"-h", "--help", "--sort", "--dry-run", "--sort-inner", "--seed-inner"}
        if "-h" in args or "--help" in args:
            print(__doc__)
        elif set(args) - known or ("--dry-run" in args and not {"--sort", "--sort-inner"} & set(args)):
            sys.exit("Usage: dl.py [--sort [--dry-run]] [--help]\n"
                     "(--dry-run only works together with --sort)")
        elif "--sort-inner" in args:            # runs inside the private venv (has mutagen)
            _cfg = load_config()
            sort_library(_cfg["music_dir"], _cfg["overrides_file"], "--dry-run" in args)
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
