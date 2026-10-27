"""Tests for dl.py. They check the pure logic (no network, no spotDL, no audio files).
Run:  python -m unittest discover -s tests -v
"""
import contextlib
import email.message
import http.client
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("dl", HERE / "dl.py")
dl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dl)

A, B, C = "1r9DRLBzZqkPU0aDBVycY8", "2fOJrMKIHge9EXfro3o8bZ", "004dqYVVhtQE3YcHUAH2DS"


def song(title, artist="Band", length=200, url="", album="Album", date="", path="/m/Rock/x.mp3", **kw):
    rec = {"title": title, "name": title, "artist": artist, "len": length, "url": url, "album": album,
           "date": date, "p": path, "kbps": 192, "ll": False, "genre": "", "size": 1, "mtime": 1.0, "fp": None,
           "dup": False}
    rec.update(kw)
    return rec


class LoadIds(unittest.TestCase):
    def read(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "playlists.txt"
            p.write_text(text, encoding="utf-8")
            flags = {}
            return dl.load_ids(p, flags), flags

    def test_plain_id_and_link(self):
        ids, flags = self.read(f"{A}\nhttps://open.spotify.com/playlist/{B}?si=abc\n")
        self.assertEqual(ids, [A, B])
        self.assertEqual(flags[A], ("", ""))

    def test_flags(self):
        ids, flags = self.read(f"{A} X\n{B}P\n{C} S # solo\nhttps://open.spotify.com/playlist/{B}?si=1 P=Road Trip\n")
        self.assertEqual(flags[A], ("X", ""))
        self.assertEqual(flags[B], ("P", "Road Trip"))      # P twice: P wins and the group is kept
        self.assertEqual(flags[C], ("S", ""))

    def test_22_chars_is_always_complete(self):
        ids, flags = self.read("0rPcNgfcMCzc8Z8rStIvBX\n")      # a real-looking ID that ends in X
        self.assertEqual(ids, ["0rPcNgfcMCzc8Z8rStIvBX"])
        self.assertEqual(flags["0rPcNgfcMCzc8Z8rStIvBX"], ("", ""))

    def test_flag_glued_to_group(self):
        ids, flags = self.read(f"{A}X=Gym\n")
        self.assertEqual(flags[A], ("X", "Gym"))

    def test_extra_words_after_an_id_are_ignored(self):
        ids, flags = self.read(f"{A} my favourite mix\n{B} X please\n")
        self.assertEqual(ids, [A, B])
        self.assertEqual(flags[A], ("", ""))

    def test_spotify_made_and_bad_lines_are_skipped(self):
        ids, _ = self.read("37i9dQZF1DWVFeEut75IAL\nnot an id\n" + A + "\n" + A + "\n")
        self.assertEqual(ids, [A])


class Playlists(unittest.TestCase):
    def test_similar(self):
        self.assertTrue(dl._pl_similar("chill autumn vibes", "cozy autumn vibes"))
        self.assertTrue(dl._pl_similar("autumn playlist", "chill autumn vibes"))
        self.assertFalse(dl._pl_similar("rap hits", "2014, 2015, 2016 rap hits"))
        self.assertFalse(dl._pl_similar("Winter vibes", "Gym peak Audios"))

    def test_title(self):
        self.assertEqual(dl._pl_title(["chill autumn vibes", "cozy autumn vibes"]), "Autumn Vibes")
        self.assertEqual(dl._pl_title(["Playlist for when you don't know", "A playlist for when you don't know"]),
                         "Playlist For When You Don't Know")

    def plan(self, names, flags=None, **cfg):
        ids = [f"{i:022d}" for i in range(len(names))]
        meta = {p: {"name": n, "tracks": []} for p, n in zip(ids, names)}
        base = {"groups_file": Path("/nonexistent/groups.txt"), "pl_files": True, "pl_merge": True}
        base.update(cfg)
        files, skipped = dl.plan_playlists(base, meta, ids, flags or {})
        return ids, files, skipped

    def test_merge_and_keep_separate(self):
        ids, files, _ = self.plan(["chill autumn vibes", "cozy autumn vibes", "Gym"])
        self.assertEqual(sorted(files), ["Autumn Vibes", "Gym"])
        self.assertEqual(files["Autumn Vibes"], ids[:2])

    def test_x_and_s_flags(self):
        ids = [f"{i:022d}" for i in range(3)]
        flags = {ids[0]: ("X", ""), ids[1]: ("S", "")}
        _, files, skipped = self.plan(["chill autumn vibes", "cozy autumn vibes", "autumn playlist"], flags)
        self.assertEqual(skipped, [ids[0]])
        self.assertEqual(len(files), 2)                     # the S playlist stays on its own

    def test_playlist_files_off_needs_p(self):
        ids = [f"{i:022d}" for i in range(2)]
        _, files, _ = self.plan(["One", "Two"], {ids[1]: ("P", "")}, pl_files=False)
        self.assertEqual(list(files), ["Two"])


class Duplicates(unittest.TestCase):
    def test_core_and_flags(self):
        self.assertEqual(dl._dd_core("Song (Remastered 2011)"), dl._dd_core("Song"))
        self.assertEqual(dl._dd_core("Song (feat. X)"), dl._dd_core("Song"))
        self.assertNotEqual(dl._dd_core("Song (Part 1)"), dl._dd_core("Song (Part 2)"))
        self.assertNotEqual(dl._dd_flags("Song (Live)"), dl._dd_flags("Song"))

    def test_groups(self):
        recs = [song("Hit", url="u1", path="/m/a/1.mp3"), song("Hit - 2011 Remaster", length=201, url="u2", path="/m/b/2.mp3"),
                song("Hit (Live)", url="u3", path="/m/c/3.mp3"), song("Epic (Part 1)", path="/m/d/4.mp3"),
                song("Epic (Part 2)", path="/m/d/5.mp3"), song("Same Link", url="u9", path="/m/e/6.mp3", artist="A"),
                song("Other Name", url="u9", path="/m/e/7.mp3", artist="B")]
        groups = dl.find_duplicate_groups(recs)
        sizes = sorted(len(g) for g in groups)
        self.assertEqual(sizes, [2, 2])                      # Hit + remaster, and the shared Spotify link


class Genres(unittest.TestCase):
    def test_pick_genre(self):
        rules = dl._compile_rules()
        self.assertEqual(dl.pick_genre(["seen live", "dream pop", "indie rock"], rules), ("dream pop", "Pop"))
        self.assertEqual(dl.pick_genre(["seen live"], rules), (None, None))


class Gaps(unittest.TestCase):
    def test_reasons(self):
        self.assertEqual(dl.gap_reason("LookupError: No results found for song: X"), "no match found")
        self.assertEqual(dl.gap_reason("AudioProviderError: This video is DRM protected"), "DRM protected")
        self.assertEqual(dl.gap_reason("HTTP Error 429: Too Many Requests"), "blocked by YouTube (try later)")
        self.assertEqual(dl.gap_reason("something odd"), "download failed")

    def test_error_file(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / f"errors-{A}.txt").write_text(
                "2026-10-25-18-00-00\nhttps://open.spotify.com/track/5zSjh9CFVoMLZDXghPx5x2 - LookupError: No results found\n",
                encoding="utf-8")
            self.assertEqual(dl._gap_errors(Path(d), A), {"5zSjh9CFVoMLZDXghPx5x2": "no match found"})
            self.assertEqual(dl._gap_errors(Path(d), B), {})


class SmartPlaylists(unittest.TestCase):
    def test_groups(self):
        root = Path("/m")
        recs = [song(f"s{i}", artist=f"a{i}", path=f"/m/Rock/{i}.mp3", date="1994-05-01") for i in range(30)]
        recs += [song(f"うた{i}", artist="Yui", path=f"/m/J-Pop/{i}.mp3") for i in range(12)]
        recs += [song("x", path="/m/Other/x.mp3"), song("d", path="/m/Rock/d.mp3", dup=True)]
        groups = dl.smart_groups(recs, root, 25)
        names = sorted(groups)
        self.assertIn("Genre - Rock", names)
        self.assertNotIn("Genre - Other", names)
        self.assertIn("Decade - 1990s", names)
        self.assertIn("Language - Japanese", names)
        self.assertIn("Artist - Yui", names)
        self.assertNotIn("Genre - J-Pop", names)             # only 12 songs, under smart_min


class Lyrics(unittest.TestCase):
    def test_kind(self):
        self.assertEqual(dl.lyrics_kind("[00:12.30] hello\n[00:15.00] world"), "lrc")
        self.assertEqual(dl.lyrics_kind("just words\nmore words"), "txt")
        self.assertEqual(dl.lyrics_kind(""), "txt")


class Schedule(unittest.TestCase):
    def test_files(self):
        daily = dl.schedule_files("daily", "/usr/bin/python", "/h/dl.py")
        weekly = dl.schedule_files("weekly", "/usr/bin/python", "/h/dl.py")
        self.assertIn("OnCalendar=*-*-* 03:00:00", daily["dl-playlist-sync.timer"])
        self.assertIn("Sun *-*-*", weekly["dl-playlist-sync.timer"])
        self.assertTrue(daily["cron"].startswith("0 3 * * *"))
        self.assertTrue(weekly["cron"].startswith("0 3 * * 0"))
        self.assertIn("# dl-playlist-sync", daily["cron"])
        self.assertIn("ExecStart=/usr/bin/python", daily["dl-playlist-sync.service"])


class Ego(unittest.TestCase):
    def test_portrait(self):
        root = Path("/m")
        recs = [song(f"s{i}", artist=f"a{i}", album=f"al{i}", path=f"/m/{'Rock' if i % 2 else 'Pop'}/{i}.mp3",
                     date="2015-01-01") for i in range(200)]
        st = dl.library_stats(recs, root, ["autumn playlist", "ROCKET LEAGUE PEAK"])
        text = " ".join(dl.ego_text(st))
        self.assertIn("200 songs", text)
        self.assertIn("discovery-driven", text)
        self.assertIn("autumn / fall", text)
        self.assertIn("gaming", text)
        self.assertIn("snapshot", text)

    def test_empty(self):
        self.assertIn("no songs", " ".join(dl.ego_text(dl.library_stats([], Path("/m")))))


class EgoArt(unittest.TestCase):
    def stats(self):
        recs = [song(f"s{i}", artist=f"a{i}", album=f"al{i}", path=f"/m/Rock/{i}.mp3", date="2015-01-01") for i in range(150)]
        return dl.library_stats(recs, Path("/m"), ["autumn"])

    def test_cassette_rows_are_equally_wide(self):
        rows = dl.cassette("S O N G S", "3,185", "186 hours")
        self.assertEqual(len({len(r) for r in rows}), 1)
        self.assertTrue(all(ord(c) < 128 for r in rows for c in r))     # the art is plain ASCII

    def test_cassette_is_3d(self):
        rows = dl.cassette_parts("S O N G S", "3,185", "186 hours")
        parts = {part for row in rows for _, part in row}
        self.assertEqual(parts, {"front", "top", "side"})
        plain = dl.cassette("S O N G S", "3,185", "186 hours")
        self.assertEqual(len({len(r) for r in plain}), 1)
        text = "\n".join(plain)
        self.assertIn("3,185", text)
        self.assertIn("186 hours", text)
        self.assertIn("#", text)                                      # the shaded side

    def test_cassette_uses_only_blues(self):
        for pal in (dl.PALETTE, dl.ANSI_PALETTE):
            for key in ("blue", "blue_hi", "blue_lo", "blue_sh"):
                self.assertIn(key, pal)
        old, dl.COLOR_ON = dl.COLOR_ON, True
        try:
            text = "\n".join(dl.ego_pretty(self.stats(), unicode_ok=True)[:16])
            for key in ("blue", "blue_hi", "blue_lo"):
                self.assertIn("\033[" + dl.PALETTE[key] + "m", text)
            self.assertNotIn("\033[" + dl.PALETTE["ok"] + "m", text)        # no magenta in the cassette
        finally:
            dl.COLOR_ON = old

    def test_pretty_ascii_fallback(self):
        dl.COLOR_ON = False
        text = "\n".join(dl.ego_pretty(self.stats(), unicode_ok=False))
        self.assertTrue(all(ord(c) < 128 for c in text))
        self.assertIn("150 songs", text)
        self.assertIn("Genres", text)
        self.assertNotIn("Y O U R", text)                     # the title strip is gone

    def test_pretty_unicode_has_bars(self):
        dl.COLOR_ON = False
        text = "\n".join(dl.ego_pretty(self.stats(), unicode_ok=True))
        self.assertIn("\u2588", text)


class Display(unittest.TestCase):
    def test_contrast_and_readable(self):
        bg = (5, 14, 21)
        dark_red = (170, 0, 0)
        self.assertLess(dl._contrast(dark_red, bg), 4.5)
        self.assertGreaterEqual(dl._contrast(dl._readable(dark_red, bg), bg), 4.5)

    def test_colour_off_is_plain(self):
        dl.COLOR_ON = False
        self.assertEqual(dl.colorize_line("Downloaded x"), "Downloaded x")
        self.assertEqual(dl.paint("hi", "ok"), "hi")

    def test_colour_on(self):
        old = dl.COLOR_ON
        dl.COLOR_ON = True
        try:
            self.assertIn("\033[", dl.colorize_line("  Reason: ERROR: DRM protected"))
            self.assertEqual(dl.colorize_line("Hello"), "Hello")
        finally:
            dl.COLOR_ON = old

    def test_fmt_secs(self):
        self.assertEqual(dl._fmt_secs(30), "under 1 min")
        self.assertEqual(dl._fmt_secs(3600 * 5 + 120), "5 h 2 min")
        self.assertEqual(dl._fmt_secs(3600 * 60), "2 days 12 h")
        self.assertEqual(dl._fmt_secs(86400 * 13 + 3600 * 5), "13 days 5 h")      # under 14 days: days
        self.assertEqual(dl._fmt_secs(86400 * 14), "2 weeks")
        self.assertEqual(dl._fmt_secs(86400 * 45), "6 weeks 3 days")
        self.assertEqual(dl._fmt_secs(86400 * 900), "over 2 years")

    def test_clean_name(self):
        self.assertEqual(dl._clean_name("NUL"), "_NUL")
        self.assertEqual(dl._clean_name("a/b:c"), "abc")


class LocalWhen(unittest.TestCase):
    def test_today_has_no_date(self):
        from datetime import datetime
        now = datetime(2026, 10, 25, 12, 0)
        self.assertNotIn("Oct", dl.local_when(datetime(2026, 10, 25, 23, 30), now))
        self.assertRegex(dl.local_when(datetime(2026, 10, 25, 23, 30), now), r"(23:30|11:30)")

    def test_other_day_has_a_date_and_year_only_when_different(self):
        from datetime import datetime
        now = datetime(2026, 10, 25, 12, 0)
        self.assertRegex(dl.local_when(datetime(2026, 11, 3, 14, 45), now), r"03")
        self.assertNotIn("2026", dl.local_when(datetime(2026, 11, 3, 14, 45), now))
        self.assertIn("2027", dl.local_when(datetime(2027, 1, 3, 14, 45), now))


class Eta(unittest.TestCase):
    def test_one_stall_does_not_ruin_the_estimate(self):
        with tempfile.TemporaryDirectory() as d:
            eta = dl.Eta(Path(d), 120)
            eta.begin_pass(["p1", "p2"])
            eta.begin_playlist(1, "p1")
            eta.line("Found 100 songs in X (Playlist)")
            eta.line("DL-PLAN 100")
            eta.rates = [40.0] * 9 + [200000.0]                  # nine normal songs and a 55 hour stall
            text = eta.text()
            self.assertIn("~40 s/song", text)
            self.assertNotIn("weeks", text)

    def test_unread_playlists_are_not_assumed_huge(self):
        with tempfile.TemporaryDirectory() as d:
            eta = dl.Eta(Path(d), 120)
            eta.begin_pass([f"p{i}" for i in range(93)])
            eta.begin_playlist(1, "p0")
            eta.line("Found 10237 songs in Big (Playlist)")
            text = eta.line("DL-PLAN 7092")
            self.assertIn("92 playlists not read yet", text)
            m = re.search(r"about (?:(\d+) days )?(?:(\d+) h)?", text)
            hours = int(m.group(1) or 0) * 24 + int(m.group(2) or 0)
            self.assertLess(hours, 400)                   # the old bug said 5499 hours

    def test_eta_line_is_yellow(self):
        old = dl.COLOR_ON
        dl.COLOR_ON = True
        try:
            self.assertTrue(dl.colorize_line("ETA about 2 h left").startswith("\033[" + dl.PALETTE["warn"]))
        finally:
            dl.COLOR_ON = old

    def test_line(self):
        with tempfile.TemporaryDirectory() as d:
            eta = dl.Eta(Path(d), 120)
            eta.begin_pass(["p1", "p2"])
            eta.begin_playlist(1, "p1")
            eta.line("Found 10 songs in X (Playlist)")
            text = eta.line("DL-PLAN 4")
            self.assertIn("playlist 1/2", text)
            self.assertIn("0 of 4 new songs", text)
            self.assertIn("1 of 4", eta.line("DL-OK https://open.spotify.com/track/x https://y Song"))
            self.assertTrue((Path(d) / "playlist-sizes.json").exists())


class FdWatch(unittest.TestCase):
    def test_report_for_this_process(self):
        import os
        rep = dl.fd_report(os.getpid())
        if Path("/proc/self/fd").exists():
            self.assertTrue(rep.startswith("FD-WATCH "), rep)
        else:
            self.assertEqual(rep, "")                # not Linux: no report, no crash

    def test_report_for_missing_process(self):
        self.assertEqual(dl.fd_report(2 ** 22 + 12345), "")


class OutOfHandles(unittest.TestCase):
    def run_cmd(self, code):
        import io, sys
        with tempfile.TemporaryDirectory() as d:
            log = io.StringIO()
            old, sys.stdout = sys.stdout, io.StringIO()
            try:
                return dl.run_playlist([sys.executable, "-c", code], d, log, 8), log.getvalue()
            finally:
                sys.stdout = old

    def test_errno_24_stops_the_run(self):
        code = "for _ in range(50): print('OSError: [Errno 24] Too many open files', flush=True)"
        (rc, hit, lines), _ = self.run_cmd(code)
        self.assertTrue(hit)
        self.assertEqual(lines, [dl.NOFILE_MARK])

    def test_a_few_errno_24_lines_do_not_stop_it(self):
        code = "for _ in range(3): print('OSError: [Errno 24] Too many open files', flush=True)\nprint('done')"
        (rc, hit, lines), log = self.run_cmd(code)
        self.assertFalse(hit)
        self.assertIn("done", log)


try:
    import mutagen  # noqa: F401
    HAVE_MUTAGEN = True
except ImportError:
    HAVE_MUTAGEN = False

MPEG_FRAME = bytes([0xFF, 0xFB, 0x90, 0x00]) + bytes(413)      # one silent-looking MP3 frame, no ID3 header


class GenreFolderFromWebTags(unittest.TestCase):
    def setUp(self):
        self.rules = dl._compile_rules()

    def test_empty_tag_list_gives_no_folder(self):
        self.assertEqual(dl.pick_genre([], self.rules), (None, None))

    def test_only_other_is_never_picked(self):
        self.assertEqual(dl.pick_genre(["seen live", "favorites"], self.rules), (None, None))

    def test_tag_with_underscores_and_capitals_still_maps(self):
        self.assertEqual(dl.pick_genre(["Hip_Hop"], self.rules), ("Hip_Hop", "Hip-Hop"))

    def test_exact_words_do_not_match_inside_other_words(self):
        self.assertIsNone(dl.folder_for_genre("dnbfoo", self.rules))
        self.assertEqual(dl.folder_for_genre("dnb", self.rules), "Electronic")

    def test_unicode_tag_does_not_crash(self):
        self.assertEqual(dl.pick_genre(["日本語", "j-pop"], self.rules), ("j-pop", "Pop"))

    def test_artist_key_with_quotes_and_lucene_characters(self):
        for name in ('Guns N\' "Roses"', "AC/DC", "Florence + the Machine", "!!!", "P!nk", "(Sandy) Alex G", "Beyoncé"):
            key = dl._dd_artist(name)
            self.assertIsInstance(key, str)
            self.assertNotIn('"', key)
        self.assertEqual(dl._dd_artist("AC/DC"), "ac")             # first artist only: the "/" splits it
        self.assertEqual(dl._dd_artist("!!!"), "")                 # empty key: repair_genres skips the song

    def test_musicbrainz_query_escapes_quotes_and_lucene_characters(self):
        seen = []
        old = dl._web_json
        dl._web_json = lambda url, headers=None, tries=3: (seen.append(url), {"artists": []})[1]
        try:
            self.assertEqual(dl._gr_musicbrainz('Say "Hi" AND (x) OR y:z\\'), [])
        finally:
            dl._web_json = old
        self.assertEqual(len(seen), 1)
        self.assertNotIn('"Hi"', seen[0])                # raw quotes would break out of artist:"..."
        self.assertNotIn(" ", seen[0])
        self.assertIn("%22Hi%22", seen[0])


class RepairGenresMoves(unittest.TestCase):
    """repair_genres with the web answers already in genre-cache.json and the library scan replaced,
    so nothing touches the network or needs real audio."""

    def run_repair(self, files, artists, tags, dry_run=False, make_sidecar=False, prefill=None, id3=True):
        import io, json, sys, time
        out = {}
        with tempfile.TemporaryDirectory() as d:
            music, state = Path(d) / "Music", Path(d) / "state"
            recs = []
            for rel, artist in zip(files, artists):
                f = music / rel
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(MPEG_FRAME * 5)
                if id3:                                      # a real (empty-ish) ID3 header, as spotDL writes
                    from mutagen.id3 import ID3, TIT2
                    tags_ = ID3()
                    tags_.add(TIT2(encoding=3, text=["t"]))
                    tags_.save(f, v2_version=3)
                if make_sidecar:
                    f.with_suffix(".lrc").write_text("la", encoding="utf-8")
                recs.append(song(f.stem, artist=artist, path=str(f), genre=""))
            for rel, data in (prefill or {}).items():
                (music / rel).parent.mkdir(parents=True, exist_ok=True)
                (music / rel).write_bytes(data)
            state.mkdir()
            cache = {dl._dd_artist(a): {"tags": tags, "ts": time.time(), "src": "MusicBrainz"} for a in artists}
            (state / "genre-cache.json").write_text(json.dumps(cache), encoding="utf-8")
            cfg = {"music_dir": music, "state_dir": state, "gen_limit": 0, "lastfm_key": ""}
            saved = (dl._pl_library, dl._web_json, sys.stdout)

            def no_net(*a, **k):
                raise AssertionError("repair_genres went to the network")
            dl._pl_library = lambda m, s, mf: recs
            dl._web_json = no_net
            sys.stdout = io.StringIO()
            try:
                dl.repair_genres(cfg, dry_run=dry_run)
                out["stdout"] = sys.stdout.getvalue()
            finally:
                dl._pl_library, dl._web_json, sys.stdout = saved
            out["tree"] = sorted(str(p.relative_to(music)) for p in music.rglob("*") if p.is_file())
            out["read"] = {str(p.relative_to(music)): p.read_bytes() for p in music.rglob("*") if p.is_file()}
            log = state / "genre-repair-log.txt"
            out["log"] = log.read_text(encoding="utf-8") if log.exists() else ""
        return out

    @unittest.skipUnless(HAVE_MUTAGEN, "mutagen is not installed")
    def test_moves_song_out_of_other_into_genre_folder(self):
        out = self.run_repair(["Other/a.mp3"], ["Band"], ["indie rock"])
        self.assertIn("Alternative/a.mp3", out["tree"], out["stdout"])
        self.assertNotIn("Other/a.mp3", out["tree"])
        self.assertIn("a.mp3", out["log"])

    @unittest.skipUnless(HAVE_MUTAGEN, "mutagen is not installed")
    def test_name_collision_in_target_folder_keeps_both_files(self):
        out = self.run_repair(["Other/a.mp3"], ["Band"], ["pop"], prefill={"Pop/a.mp3": b"KEEP"})
        self.assertEqual(out["read"].get("Pop/a.mp3"), b"KEEP", out["stdout"])
        self.assertEqual(len(out["tree"]), 2, out["stdout"])
        self.assertIn("Pop/a (2).mp3", out["tree"], out["stdout"])

    @unittest.skipUnless(HAVE_MUTAGEN, "mutagen is not installed")
    def test_lyrics_sidecar_moves_with_the_song(self):
        out = self.run_repair(["Unknown/a.mp3"], ["Band"], ["pop"], make_sidecar=True)
        self.assertIn("Pop/a.lrc", out["tree"], out["stdout"])
        self.assertNotIn("Unknown/a.lrc", out["tree"])

    @unittest.skipUnless(HAVE_MUTAGEN, "mutagen is not installed")
    def test_dry_run_changes_nothing(self):
        out = self.run_repair(["Other/a.mp3"], ["Band"], ["pop"], dry_run=True)
        self.assertEqual(out["tree"], ["Other/a.mp3"])
        self.assertEqual(out["log"], "")

    @unittest.skipUnless(HAVE_MUTAGEN, "mutagen is not installed")
    def test_odd_unicode_and_quote_names_move(self):
        out = self.run_repair(['Other/Beyoncé - "Halo" ❤ 日本.mp3'], ['Beyoncé'], ["r&b"])
        self.assertEqual(out["tree"], ['R&B/Beyoncé - "Halo" ❤ 日本.mp3'], out["stdout"])

    @unittest.skipUnless(HAVE_MUTAGEN, "mutagen is not installed")
    def test_unmatched_tags_leave_song_in_place(self):
        out = self.run_repair(["Other/a.mp3"], ["Band"], ["seen live"])
        self.assertEqual(out["tree"], ["Other/a.mp3"])

    @unittest.skipUnless(HAVE_MUTAGEN, "mutagen is not installed")
    def test_song_already_in_a_genre_folder_is_not_moved(self):
        out = self.run_repair(["Jazz/a.mp3"], ["Band"], ["pop"])
        self.assertEqual(out["tree"], ["Jazz/a.mp3"])

    @unittest.skipUnless(HAVE_MUTAGEN, "mutagen is not installed")
    def test_empty_library_is_a_clean_no_op(self):
        out = self.run_repair([], [], [])
        self.assertIn("every song already has a genre", out["stdout"])

    @unittest.skipUnless(HAVE_MUTAGEN, "mutagen is not installed")
    def test_mp3_without_id3_header_is_still_moved(self):
        # A tagless MP3 has no ID3 header. If writing the genre tag fails, the song must not be stranded
        # in Other with the error swallowed: the genre is known, so the move should still happen.
        out = self.run_repair(["Other/a.mp3"], ["Band"], ["pop"], id3=False)
        self.assertEqual(out["tree"], ["Pop/a.mp3"], out["stdout"])

    @unittest.skipUnless(HAVE_MUTAGEN, "mutagen is not installed")
    def test_mp3_without_id3_header_never_loses_the_file(self):
        out = self.run_repair(["Other/a.mp3"], ["Band"], ["pop"], id3=False)
        self.assertEqual(len(out["tree"]), 1)                    # moved or kept, but never deleted


class StopIgnoresSigterm(unittest.TestCase):
    STUBBORN = ("import signal, sys, time\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "print('ready', flush=True)\n"
                "while True: time.sleep(0.2)\n")

    def setUp(self):
        if dl.IS_WIN:
            self.skipTest("process groups are POSIX only")

    def spawn(self, code):
        import subprocess, sys
        p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
                             start_new_session=True)
        self.addCleanup(p.stdout.close)
        self.addCleanup(lambda: p.poll() is None and (p.kill(), p.wait()))
        self.assertEqual(p.stdout.readline().strip(), "ready")
        return p

    def test_sigterm_alone_does_not_kill_it_but_force_does(self):
        import signal, subprocess
        p = self.spawn(self.STUBBORN)
        dl._stop(p)                                        # SIGTERM to the group: ignored
        with self.assertRaises(subprocess.TimeoutExpired):
            p.wait(timeout=0.7)
        dl._stop(p, force=True)
        self.assertEqual(p.wait(timeout=10), -signal.SIGKILL)

    def test_force_kill_reaches_grandchildren_in_the_group(self):
        import time
        code = ("import subprocess, sys\n"
                "c = subprocess.Popen([sys.executable, '-c', 'import signal,time\\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\\ntime.sleep(60)'])\n"
                "print('ready', flush=True)\n"
                "c.wait()\n")
        p = self.spawn(code)
        kids = Path(f"/proc/{p.pid}/task/{p.pid}/children")
        if not kids.exists():
            self.skipTest("needs /proc")
        deadline = time.time() + 5
        while not kids.read_text().split() and time.time() < deadline:
            time.sleep(0.05)
        child = int(kids.read_text().split()[0])
        dl._stop(p, force=True)
        p.wait(timeout=10)
        stat = Path(f"/proc/{child}/stat")
        deadline = time.time() + 5
        while time.time() < deadline and stat.exists() and stat.read_text().rsplit(")", 1)[1].split()[0] != "Z":
            time.sleep(0.05)
        state = stat.read_text().rsplit(")", 1)[1].split()[0] if stat.exists() else "gone"
        self.assertIn(state, ("gone", "Z"), "grandchild survived the group SIGKILL")

    def test_stop_on_an_already_dead_process_does_not_raise(self):
        import subprocess, sys
        p = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        p.wait()
        dl._stop(p)
        dl._stop(p, force=True)

    def test_run_playlist_force_kills_when_sigterm_is_ignored_after_errno_24(self):
        import io, subprocess, sys, time, types
        code = ("import signal, time\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "for _ in range(6): print('OSError: [Errno 24] Too many open files', flush=True)\n"
                "time.sleep(60)\n")
        log, old = io.StringIO(), sys.stdout
        class Quick(subprocess.Popen):                       # do not make the test wait the full 30 s
            def wait(self, timeout=None):
                return super().wait(timeout=None if timeout is None else min(timeout, 2))
        real = dl.subprocess
        dl.subprocess = types.SimpleNamespace(**{k: getattr(subprocess, k) for k in dir(subprocess)
                                                 if not k.startswith("__")}, )
        dl.subprocess.Popen = Quick
        sys.stdout = io.StringIO()
        t0 = time.time()
        try:
            with tempfile.TemporaryDirectory() as d:
                rc, hit, lines = dl.run_playlist([sys.executable, "-c", code], d, log, 8)
        finally:
            dl.subprocess = real
            sys.stdout = old
        self.assertTrue(hit)
        self.assertEqual(lines, [dl.NOFILE_MARK])
        self.assertEqual(rc, -9)                           # SIGKILL: the SIGTERM was ignored
        self.assertLess(time.time() - t0, 20)

    def test_fd_report_counts_a_pipe_and_a_file(self):
        import os
        if not Path("/proc/self/fd").exists():
            self.skipTest("needs /proc")
        r, w = os.pipe()
        try:
            with tempfile.TemporaryDirectory() as d, open(Path(d) / "x", "w"):
                rep = dl.fd_report(os.getpid())
        finally:
            os.close(r)
            os.close(w)
        self.assertRegex(rep, r"file=\d+")
        self.assertRegex(rep, r"pipe=\d+")

    def test_fd_report_total_matches_the_parts(self):
        import os
        if not Path("/proc/self/fd").exists():
            self.skipTest("needs /proc")
        m = re.match(r"FD-WATCH (\d+) (.*)", dl.fd_report(os.getpid()))
        self.assertTrue(m)
        self.assertEqual(int(m.group(1)), sum(int(x.split("=")[1]) for x in m.group(2).split()))

    def test_nofile_mark_is_not_a_youtube_block(self):
        self.assertFalse(dl.is_block(dl.NOFILE_MARK))


class LoadIdsFlags(unittest.TestCase):
    def read(self, text):
        import io, sys
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "playlists.txt"
            p.write_text(text, encoding="utf-8")
            flags = {}
            old, sys.stdout = sys.stdout, io.StringIO()
            try:
                ids = dl.load_ids(p, flags)
                return ids, flags, sys.stdout.getvalue()
            finally:
                sys.stdout = old

    def test_23_char_id_with_each_flag_letter(self):
        for letter in "PSX":
            ids, flags, _ = self.read(f"{A}{letter}\n")
            self.assertEqual(ids, [A])
            self.assertEqual(flags[A], (letter, ""))

    def test_23_char_lower_case_flag_letter(self):
        ids, flags, _ = self.read(f"{A}s\n")
        self.assertEqual(flags[A], ("S", ""))

    def test_23_char_with_a_bad_letter_is_skipped(self):
        ids, flags, out = self.read(f"{A}Z\n")
        self.assertEqual(ids, [])
        self.assertIn("SKIPPED", out)

    def test_24_chars_is_skipped(self):
        ids, _, out = self.read(f"{A}PP\n")
        self.assertEqual(ids, [])
        self.assertIn("SKIPPED", out)

    def test_21_chars_is_skipped(self):
        ids, _, out = self.read(A[:21] + "\n")
        self.assertEqual(ids, [])

    def test_23_char_id_glued_flag_and_group(self):
        ids, flags, _ = self.read(f"{A}P=Road Trip\n")
        self.assertEqual(flags[A], ("P", "Road Trip"))

    def test_group_only_keeps_group_with_empty_flag(self):
        ids, flags, _ = self.read(f"{A} =Gym\n")
        self.assertEqual(flags[A], ("", "Gym"))

    def test_group_name_with_unicode_and_inner_spaces(self):
        ids, flags, _ = self.read(f"{A} P = Café  del Mar 日本\n")
        self.assertEqual(flags[A], ("P", "Café  del Mar 日本"))

    def test_group_stops_at_comment(self):
        ids, flags, _ = self.read(f"{A} P=Gym # not part of the name\n")
        self.assertEqual(flags[A], ("P", "Gym"))

    def test_duplicate_s_then_p_gives_p(self):
        ids, flags, _ = self.read(f"{A} S\n{A} P=G\n")
        self.assertEqual(ids, [A])
        self.assertEqual(flags[A], ("P", "G"))

    def test_duplicate_keeps_first_group(self):
        ids, flags, _ = self.read(f"{A} P=First\n{A} P=Second\n")
        self.assertEqual(flags[A], ("P", "First"))

    def test_duplicate_x_then_s_gives_s(self):
        ids, flags, _ = self.read(f"{A} X\n{A} S\n")
        self.assertEqual(flags[A], ("S", ""))

    def test_quoted_and_comma_separated_with_flag(self):
        ids, flags, _ = self.read(f'"{A}", X\n')
        self.assertEqual(ids, [A])
        self.assertEqual(flags[A], ("X", ""))

    def test_quoted_id_with_trailing_comma(self):
        ids, flags, _ = self.read(f'"{A}",\n')
        self.assertEqual(ids, [A])

    def test_spotify_uri_with_flag(self):
        ids, flags, _ = self.read(f"spotify:playlist:{B} S\n")
        self.assertEqual(flags[B], ("S", ""))

    def test_empty_comment_only_and_bom_files(self):
        self.assertEqual(self.read("")[0], [])
        self.assertEqual(self.read("# nothing\n\n   \n")[0], [])
        ids, flags, _ = self.read("﻿" + A + " X\n")
        self.assertEqual(flags[A], ("X", ""))

    def test_flags_dict_is_optional(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "playlists.txt"
            p.write_text(f"{A}P\n", encoding="utf-8")
            self.assertEqual(dl.load_ids(p), [A])

    def test_order_follows_the_file(self):
        ids, _, _ = self.read(f"{C}\n{A}X\n{B}\n")
        self.assertEqual(ids, [C, A, B])


class SpotdlPatch(unittest.TestCase):
    "The code injected into spotDL cannot run here (no spotDL), so check it parses and keeps its key parts."

    def test_patch_and_runner_are_valid_python(self):
        import ast
        ast.parse(dl.SPOTDL_PATCH)
        ast.parse(dl.SPOTDL_RUNNER)

    def test_search_skips_results_that_want_a_login(self):
        # One age-restricted result among the ten used to fail the whole song.
        self.assertIn('"ignoreerrors": True', dl.SPOTDL_PATCH)
        self.assertIn("ytsearch10:", dl.SPOTDL_PATCH)

    def test_view_count_lookup_and_login_fallback_are_patched(self):
        self.assertIn("_AP.get_views = _views", dl.SPOTDL_PATCH)
        self.assertIn("_need_login", dl.SPOTDL_PATCH)


class WebJsonRetries(unittest.TestCase):
    """_web_json with urlopen replaced and sleep made instant."""

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": 1}'

    class TrackedError(urllib.error.HTTPError):
        closed = 0

        def close(self):
            type(self).closed += 1
            super().close()

    def run_web(self, script):
        "script: exceptions to raise or Resp objects to return, one per call."
        calls, sleeps = [], []
        items = list(script)

        def fake_open(req, timeout=None):
            calls.append(req)
            it = items.pop(0)
            if isinstance(it, BaseException):
                raise it
            return it
        with mock.patch.object(dl.urllib.request, "urlopen", fake_open), \
                mock.patch.object(dl.time, "sleep", sleeps.append):
            try:
                result, err = dl._web_json("http://x.invalid/a"), None
            except Exception as e:
                result, err = None, e
        return result, err, len(calls), sleeps

    def http_err(self, code, retry_after=None):
        h = email.message.Message()
        if retry_after is not None:
            h["Retry-After"] = retry_after
        return self.TrackedError("http://x.invalid/a", code, "msg", h, io.BytesIO(b""))

    def test_503_with_retry_after_waits_that_long_closes_socket_and_succeeds(self):
        self.TrackedError.closed = 0
        result, err, n, sleeps = self.run_web([self.http_err(503, "7"), self.Resp()])
        self.assertIsNone(err)
        self.assertEqual(result, {"ok": 1})
        self.assertEqual(n, 2)
        self.assertEqual(sleeps, [7])
        self.assertEqual(self.TrackedError.closed, 1, "HTTPError socket leaked")

    def test_503_without_retry_after_uses_default_backoff(self):
        result, err, n, sleeps = self.run_web([self.http_err(503), self.Resp()])
        self.assertEqual(result, {"ok": 1})
        self.assertEqual(sleeps, [4])

    def test_retry_after_is_capped_at_a_minute(self):
        _, _, _, sleeps = self.run_web([self.http_err(429, "9999"), self.Resp()])
        self.assertEqual(sleeps, [60])

    def test_404_raises_without_retry_and_closes_socket(self):
        self.TrackedError.closed = 0
        _, err, n, sleeps = self.run_web([self.http_err(404), self.Resp()])
        self.assertIsInstance(err, urllib.error.HTTPError)
        self.assertEqual(err.code, 404)
        self.assertEqual(n, 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(self.TrackedError.closed, 1)

    def test_503_forever_gives_up_after_five_tries_and_closes_every_socket(self):
        self.TrackedError.closed = 0
        _, err, n, _ = self.run_web([self.http_err(503, "1") for _ in range(5)])
        self.assertIsInstance(err, urllib.error.HTTPError)
        self.assertEqual(n, 5)
        self.assertEqual(self.TrackedError.closed, 5)

    def test_incomplete_read_becomes_oserror_after_retries(self):
        _, err, n, _ = self.run_web([http.client.IncompleteRead(b"ab") for _ in range(5)])
        self.assertIsInstance(err, OSError)
        self.assertNotIsInstance(err, urllib.error.HTTPError)
        self.assertEqual(n, 5)

    def test_bad_status_line_becomes_oserror_after_retries(self):
        _, err, n, _ = self.run_web([http.client.BadStatusLine("junk") for _ in range(5)])
        self.assertIsInstance(err, OSError)
        self.assertEqual(n, 5)

    def test_bad_reply_then_good_reply_succeeds(self):
        result, err, n, _ = self.run_web([http.client.IncompleteRead(b""), self.Resp()])
        self.assertIsNone(err)
        self.assertEqual(result, {"ok": 1})


@unittest.skipUnless(HAVE_MUTAGEN, "mutagen is not installed (repair_genres needs it)")
class RepairGenresWebErrors(unittest.TestCase):
    """repair_genres with a damaged cache entry and with the lookup refusing one artist."""

    def run_repair(self, artists, cache, lookup):
        out = {}
        with tempfile.TemporaryDirectory() as d:
            music, state = Path(d) / "Music", Path(d) / "state"
            recs = []
            for i, (artist, n) in enumerate(artists):
                for j in range(n):
                    f = music / "Other" / f"s{i}_{j}.mp3"
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_bytes(MPEG_FRAME * 5)
                    recs.append(song(f.stem, artist=artist, path=str(f), genre=""))
            state.mkdir()
            (state / "genre-cache.json").write_text(json.dumps(cache), encoding="utf-8")
            cfg = {"music_dir": music, "state_dir": state, "gen_limit": 10, "lastfm_key": ""}
            asked = []

            def fake_mb(name):
                asked.append(name)
                return lookup(name)
            with mock.patch.object(dl, "_pl_library", lambda m, s, mf: recs), \
                    mock.patch.object(dl, "_gr_musicbrainz", fake_mb), \
                    mock.patch.object(dl.time, "sleep", lambda s: None), \
                    contextlib.redirect_stdout(io.StringIO()) as buf:
                dl.repair_genres(cfg)
            out["asked"], out["stdout"] = asked, buf.getvalue()
            out["cache"] = json.loads((state / "genre-cache.json").read_text(encoding="utf-8"))
            out["tree"] = sorted(str(p.relative_to(music)) for p in music.rglob("*") if p.is_file())
        return out

    def test_cache_entry_without_tags_key_does_not_crash(self):
        cache = {"band": {"ts": time.time(), "src": "MusicBrainz"}}
        out = self.run_repair([("Band", 1)], cache, lambda n: self.fail("asked the web"))
        self.assertEqual(out["asked"], [])
        self.assertEqual(out["tree"], ["Other/s0_0.mp3"], "song moved on no evidence")

    def test_old_cache_entry_without_tags_key_is_asked_again(self):
        cache = {"band": {"ts": 1, "src": "MusicBrainz"}}
        out = self.run_repair([("Band", 1)], cache, lambda n: ["pop"])
        self.assertEqual(out["asked"], ["Band"])

    def refuse(self, code):
        err = urllib.error.HTTPError("http://x.invalid", code, "refused", {}, io.BytesIO(b""))
        self.addCleanup(err.close)
        return err

    def test_http_404_caches_empty_tags_and_continues_with_next_artist(self):
        def lookup(name):
            if name == "Bad":
                raise self.refuse(404)
            return ["pop"]
        out = self.run_repair([("Bad", 2), ("Good", 1)], {}, lookup)
        self.assertEqual(out["asked"], ["Bad", "Good"], out["stdout"])
        self.assertEqual(out["cache"][dl._dd_artist("Bad")]["tags"], [])
        self.assertEqual(out["cache"][dl._dd_artist("Good")]["tags"], ["pop"])

    def test_http_400_for_one_artist_continues_with_next_and_is_not_cached(self):
        def lookup(name):
            if name == "Bad":
                raise self.refuse(400)
            return ["pop"]
        out = self.run_repair([("Bad", 2), ("Good", 1)], {}, lookup)
        self.assertEqual(out["asked"], ["Bad", "Good"], out["stdout"])
        self.assertNotIn(dl._dd_artist("Bad"), out["cache"])
        self.assertEqual(out["cache"][dl._dd_artist("Good")]["tags"], ["pop"])

    def test_three_http_400_in_a_row_stop_the_run(self):
        def lookup(name):
            raise self.refuse(400)
        out = self.run_repair([("A1", 4), ("B2", 3), ("C3", 2), ("D4", 1)], {}, lookup)
        self.assertEqual(len(out["asked"]), 3, out["stdout"])
        self.assertEqual(out["cache"], {})

    def test_http_500_stops_batch_and_does_not_cache_the_artist(self):
        def lookup(name):
            raise self.refuse(500)
        out = self.run_repair([("Bad", 2), ("Good", 1)], {}, lookup)
        self.assertEqual(out["asked"], ["Bad"])
        self.assertNotIn(dl._dd_artist("Bad"), out["cache"])


class DedupeProbeFreshFiles(unittest.TestCase):
    def probe(self, age):
        def broken(path, easy=True):
            raise ValueError("cannot parse")
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "a.mp3"
            f.write_bytes(b"not audio")
            if age:
                t = time.time() - age
                os.utime(f, (t, t))
            return dl._dd_probe(broken, f)

    def test_unparseable_file_written_just_now_is_false_not_broken(self):
        self.assertIs(self.probe(0), False)

    def test_unparseable_file_older_than_five_minutes_is_none(self):
        self.assertIsNone(self.probe(3600))

    def test_missing_file_is_false(self):
        self.assertIs(dl._dd_probe(lambda p, easy=True: None, Path("/nonexistent-zz/a.mp3")), False)


@unittest.skipUnless(os.path.isdir("/proc/self/fd"), "/proc is not available")
class FdReportGroup(unittest.TestCase):
    def test_report_for_live_process_group(self):
        code = ("import sys,time\n"
                "fs=[open(sys.argv[1]) for _ in range(3)]\n"
                "print('ready',flush=True)\n"
                "time.sleep(30)\n")
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "f.txt"
            target.write_text("x")
            p = subprocess.Popen([sys.executable, "-c", code, str(target)], stdout=subprocess.PIPE,
                                 text=True, start_new_session=True)
            try:
                self.assertEqual(p.stdout.readline().strip(), "ready")
                rep = dl.fd_report_group(p.pid)
            finally:
                p.kill()
                p.wait()
                p.stdout.close()
        self.assertTrue(rep.startswith("FD-WATCH"), rep)
        self.assertIn("processes", rep)
        self.assertIn("file=", rep)

    def test_unused_group_id_gives_empty_string(self):
        self.assertEqual(dl.fd_report_group(2**22 + 7), "")


if __name__ == "__main__":
    unittest.main()
