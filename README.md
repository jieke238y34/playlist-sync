# dl.py

Download your Spotify playlists as MP3 files, sort them into genre folders, find and clean up
duplicates, and keep the best quality. One Python file, nothing to install by hand.
Works on Windows, Linux and macOS. It was developed and tested on Linux.

Under the hood it uses [spotDL](https://github.com/spotDL/spotify-downloader) and
[yt-dlp](https://github.com/yt-dlp/yt-dlp). The script installs both into a private folder, and
downloads ffmpeg if your system has none.

## Quick start

1. Install Python 3.10 to 3.14.
2. Run `python3 dl.py` (Windows: double-click it, or `py dl.py`).
3. The first run creates `config.ini` and `playlists.txt` next to the script, then stops.
4. Put your playlist links or IDs in `playlists.txt`, one per line, and check `config.ini`.
5. Run `python3 dl.py` again.

Playlists must be **public**. Spotify-made playlists (Daily Mix, Discover Weekly, "This Is ...",
IDs starting `37i9dQZF`) do not work: copy their songs into your own public playlist first.

## What it does

| Step | What happens |
|---|---|
| Download | Each playlist is downloaded as 192 kbps MP3 with tags and cover art. Songs you already have are skipped, so it is safe to run again and again. |
| Sources | YouTube first, then SoundCloud. Songs found on YouTube are tried on SoundCloud afterwards, and a SoundCloud file replaces the YouTube one only if it passes strict checks (see below). |
| Retries | Songs that failed are retried in later passes, with waits in between so YouTube does not block you. |
| Sort | Songs are moved into genre folders (Pop, Rock, Hip-Hop, ...). Your own `Artist=Folder` rules win. |
| Duplicates | A preview of duplicate songs is shown at the end. You decide whether to move them. |

## Commands

| Command | What it does |
|---|---|
| `python3 dl.py` | Download, upgrade from SoundCloud, sort, preview duplicates |
| `python3 dl.py --sort` | Sort only. Add `--dry-run` to preview. |
| `python3 dl.py --dedupe --dry-run` | Short summary, then type `--expand` to list the duplicates, `--dedupe` to move them, or `--exit` |
| `python3 dl.py --dedupe` | Move duplicate songs to `_Duplicates/` straight away (nothing is deleted) |
| `python3 dl.py --report` | Library report: songs, hours, size, formats, folders, weakest files |
| `python3 dl.py --upgrade` | Run only the SoundCloud upgrade step for songs still waiting |
| `python3 dl.py --colors [NAME]` | Show the colours in use, or preview a KDE colour scheme |
| `python3 dl.py --themes` | List the KDE colour schemes you can use |
| `python3 dl.py --help` | Show the built-in help |

## SoundCloud upgrade

SoundCloud audio is often better than YouTube audio, but it also has remixes, covers, 30 second
previews and DRM-protected tracks. So a SoundCloud file replaces the YouTube file only if it:

- is not DRM protected,
- is at least 60 seconds long and within about 8% of the YouTube length,
- is the same file type, and
- does not look like a cover, remix, edit or live version that the Spotify title does not name.

Anything else stays as it was. Turn it off with `prefer_soundcloud = no` under `[sources]`.
A replaced file gets fresh tags from spotDL.

## Duplicates

- **Same song** means the same Spotify link, or the same artist and title with about the same
  length. Remaster tags and `(feat. ...)` are ignored. Live, remix, acoustic and `(Part 2)`
  versions are different songs and are kept.
- **The best copy stays**: lossless first, then higher bitrate, then cover art, then complete
  tags, then the copy already in a genre folder, then the older file.
- **Nothing is deleted.** Extra copies go to `<music folder>/_Duplicates/`. Delete that folder
  yourself when you are happy. The preview moves nothing.
- **Cleanup** (optional): empty or unreadable song files and leftover `.new` files go to
  `_Duplicates/_Broken`, empty folders are removed, and a kept copy with no genre gets one from a
  copy that moves away.
- **Fingerprints** (optional, `fingerprint = yes` under `[dedupe]`): also finds copies whose names
  differ, by comparing the sound of songs by the same artist. It downloads a small
  [Chromaprint](https://acoustid.org/chromaprint) helper once, checked against a fixed checksum.
- Before downloading, the script also skips songs you already have under another album, a remaster
  tag or a different `(feat. ...)` spelling.

## Settings (`config.ini`)

| Section | Controls |
|---|---|
| `[paths]` | Music folder, playlists file, state folder, archive, genre rules, cookies |
| `[timing]` | Waits between playlists and passes, and when to give up on YouTube blocks |
| `[sources]` | `audio_sources` order and `prefer_soundcloud` |
| `[updates]` | Automatic daily update of spotDL and yt-dlp |
| `[sorting]` | Sort into genre folders after downloading |
| `[dedupe]` | Preview at the end, fingerprints, cleanup, tag merge, library report |
| `[display]` | Colours and theme |

Every setting is explained in the file itself. `config.ini`, `playlists.txt` and
`genre_overrides.txt` are personal: they are in `.gitignore` and are not part of this repository.

## Colours

Output is coloured in a terminal: cyan for good news, yellow for warnings, bold red for
problems, dim for low-importance lines. Log files stay plain text.

- On **KDE Plasma** with a true-colour terminal, it copies your Plasma colour scheme, brightened
  where needed so it stays easy to read on your background.
- `theme = NAME` under `[display]` copies **any installed KDE colour scheme**, even one that is
  not active. See the choices with `--themes` and preview one with `--colors NAME`.
- `theme = ansi` forces the standard terminal colours. `color = no` or the `NO_COLOR` setting turns
  colours off.

## Good to know

- To avoid YouTube blocks, yt-dlp waits 15 to 40 seconds between tracks. Large playlists take a
  long time. This is on purpose.
- Optional extras: [node](https://nodejs.org) gives fewer YouTube errors, and a YouTube cookies
  file helps with age-restricted songs (see `config.ini`).
- While a run is going, the computer is kept awake (Windows, macOS, and Linux with
  `systemd-inhibit`).
- Windows and macOS are supported by design but were not tested by the author.

## Responsible use

This tool fetches audio from YouTube and SoundCloud. Only download music you have the right to
copy, and follow the terms of the services you use. You are responsible for how you use it.

## License

GNU General Public License, version 2. See [LICENSE](LICENSE).
