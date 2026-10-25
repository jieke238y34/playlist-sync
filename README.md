# dl.py

Download Spotify playlists as MP3 files with spotDL, then sort them into genre folders.
One file, no manual installs. Works on Windows, Linux and macOS.

## Use

    python3 dl.py                   # download, then sort into genre folders
    python3 dl.py --sort            # sort only
    python3 dl.py --sort --dry-run  # preview the sort, move nothing
    python3 dl.py --help

Needs Python 3.10 to 3.14. On first run the script creates `config.ini` and
`playlists.txt` next to itself, then stops. Edit both and run it again. It installs
spotDL and yt-dlp into a private folder and downloads ffmpeg if the system has none.

- `playlists.txt`: one public Spotify playlist ID (or link) per line.
  Spotify-made playlists (IDs starting `37i9dQZF`) do not work.
- `config.ini`: folders, timing and optional YouTube cookies.
- `genre_overrides.txt`: optional `Artist=Folder` rules that beat the automatic genre.

Sources: YouTube first. Songs that came from YouTube are then tried on SoundCloud, and a
SoundCloud file replaces the YouTube one only if it passes strict checks (no DRM, at least
60 s, about the same length, not a cover or remix). Turn it off with `prefer_soundcloud = no`
in `config.ini`. `python3 dl.py --upgrade` runs just that step for songs still waiting.

Duplicates: at the end of every run `dl.py` previews duplicate songs (same artist, title and
length, or the same Spotify link) and moves nothing. `python3 dl.py --dedupe` moves the extra
copies into `_Duplicates/` (nothing is deleted); `--dedupe --dry-run` shows a short summary, then you type `--expand` to list the duplicates, `--dedupe` to move them or `--exit` to quit. The best copy
stays (lossless, higher bitrate, cover art, full tags) and live/remix versions are kept. It also
cleans empty or broken files, fills a missing genre tag on the kept copy, and `--report`
shows library stats. Optional audio fingerprints (`fingerprint = yes` in `[dedupe]`, downloads a
small helper on Windows, Linux and macOS) find copies whose names differ.

Optional: node (fewer YouTube errors) and a cookies file (age-restricted songs).

These three files are personal and listed in `.gitignore`.

## License

GNU General Public License, version 2. See [LICENSE](LICENSE).
