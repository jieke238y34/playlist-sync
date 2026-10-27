# Security

## Reporting a problem

If you find a security problem in `dl.py`, please open a private security advisory on GitHub
(Security tab -> "Report a vulnerability") rather than a public issue. Include what you did
and what happened. You will get an answer as soon as possible.

## What the script does, and does not do

- It runs only on your computer. It sends requests to Spotify (playlist data), YouTube and
  SoundCloud (downloads), MusicBrainz, Apple and Last.fm (genre names), and lrclib (lyrics).
  Nothing else, and no data of yours goes to any server run by this project.
- Every other program is started with an argument list, never through a shell, so a song or
  playlist name cannot run commands.
- Files are only moved or tagged inside your music folder. Every genre move is written to
  `genre-repair-log.txt` so it can be undone.
- Downloaded helper programs (audio fingerprint tool) are checked against a pinned SHA-256
  before they are used.

## Your secrets

- `config.ini` (Last.fm key, Genius token), `playlists.txt` and any cookies file are listed in
  `.gitignore` and are never committed. Keep it that way.
- If `config.ini` holds a key, or you use a cookies file, others on the same computer must not be
  able to read it. The script warns at the start of a run when they can:  `chmod 600 config.ini`.
- Keys and tokens are hidden (`***`) in anything the script prints.
- A GitHub token for pushing should never be pasted into a file or a command line. Use a
  credential helper, or type it at a prompt that does not echo it.

## Updating

By default the script keeps spotDL and yt-dlp up to date from PyPI (`[updates] auto_update` in
`config.ini`). Set it to `no` if you want to review those updates yourself.
