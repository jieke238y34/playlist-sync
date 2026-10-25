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

Optional: node (fewer YouTube errors) and a cookies file (age-restricted songs).

These three files are personal and listed in `.gitignore`.
