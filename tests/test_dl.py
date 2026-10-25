"""Tests for dl.py. They check the pure logic (no network, no spotDL, no audio files).
Run:  python -m unittest discover -s tests -v
"""
import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

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

    def test_pretty_ascii_fallback(self):
        dl.COLOR_ON = False
        text = "\n".join(dl.ego_pretty(self.stats(), unicode_ok=False))
        self.assertTrue(all(ord(c) < 128 for c in text))
        self.assertIn("150 songs", text)
        self.assertIn("Genres", text)

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

    def test_clean_name(self):
        self.assertEqual(dl._clean_name("NUL"), "_NUL")
        self.assertEqual(dl._clean_name("a/b:c"), "abc")


class Eta(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
