"""Unit tests for naming, store retention, and Bing parsing (no network)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import bing, config, store  # noqa: E402


def sample_entry(**overrides):
    entry = {
        "startdate": "20261111",
        "urlbase": "/th?id=OHR.NorthWindow_ZH-CN6734579353",
        "url": "/th?id=OHR.NorthWindow_ZH-CN6734579353_1920x1080.jpg&rf=LaDigue_1920x1080.jpg&pid=hp",
        "copyright": "北窗，拱门国家公园，莫阿布，犹他州，美国 (© lightphoto/Getty Images)",
        "title": "没有建筑师的建筑杰作",
        "copyrightlink": "https://www.bing.com/search?q=x",
        "hsh": "4cd78b6a40ab0d146b30dc1d5e26cd6e",
    }
    entry.update(overrides)
    return entry


class NamingTests(unittest.TestCase):
    def test_id_is_stable_and_descriptive(self):
        record = bing.build_record(sample_entry())
        self.assertEqual(record["id"], "bing-20261111-northwindow-4cd78b6a")

    def test_file_layout_uses_year_month(self):
        record = bing.build_record(sample_entry())
        self.assertEqual(record["file"], "2026-11/bing-20261111-northwindow-4cd78b6a.jpg")
        self.assertEqual(record["thumb"], "thumbs/bing-20261111-northwindow-4cd78b6a.jpg")

    def test_udh_url(self):
        record = bing.build_record(sample_entry())
        self.assertEqual(
            record["url"],
            "https://www.bing.com/th?id=OHR.NorthWindow_ZH-CN6734579353_UHD.jpg&rf=LaDigue_UHD.jpg",
        )

    def test_missing_ohr_name_still_works(self):
        entry = sample_entry(urlbase="/th?id=ABC123_ZH-CN1")
        record = bing.build_record(entry)
        self.assertTrue(record["id"].startswith("bing-20261111-image-"))

    def test_bad_startdate_is_rejected(self):
        with self.assertRaises(bing.BingError):
            bing.build_record(sample_entry(startdate="2026-11-11"))

    def test_slug_strips_punctuation(self):
        self.assertEqual(bing.slug_for("OHR.Whatever-Name_ZH"), "whatevername")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dsbw-test-"))
        self._orig = (config.DATA_DIR, config.WALLPAPER_DIR, config.INDEX_PATH, config.STATE_PATH)
        config.DATA_DIR = self.tmp
        config.WALLPAPER_DIR = self.tmp / "wallpapers"
        config.INDEX_PATH = self.tmp / "index.json"
        config.STATE_PATH = self.tmp / "state.json"
        config.ensure_dirs()

    def tearDown(self):
        config.DATA_DIR, config.WALLPAPER_DIR, config.INDEX_PATH, config.STATE_PATH = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _add(self, image_id, startdate, favorite=False):
        record = {
            "id": image_id,
            "startdate": startdate,
            "file": f"{startdate[:4]}-{startdate[4:6]}/{image_id}.jpg",
            "thumb": f"thumbs/{image_id}.jpg",
            "favorite": favorite,
        }
        path = config.WALLPAPER_DIR / record["file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xd8\xff" + b"0" * 100)
        thumb = config.WALLPAPER_DIR / record["thumb"]
        thumb.parent.mkdir(parents=True, exist_ok=True)
        thumb.write_bytes(b"\xff\xd8\xff")
        store.upsert_image(record)
        return record

    def test_defaults_when_files_missing(self):
        state = store.load_state()
        self.assertTrue(state["settings"]["autoUpdate"])
        self.assertEqual(state["settings"]["retentionDays"], 10)
        self.assertIsNone(state["currentId"])

    def test_state_roundtrip_keeps_pin_fields(self):
        state = store.load_state()
        state["selectedId"] = "x"
        state["pausedReason"] = "user-picked"
        state["settings"]["autoUpdate"] = False
        store.save_state(state)
        loaded = store.load_state()
        self.assertEqual(loaded["selectedId"], "x")
        self.assertEqual(loaded["pausedReason"], "user-picked")
        self.assertFalse(loaded["settings"]["autoUpdate"])

    def test_upsert_preserves_local_fields(self):
        record = self._add("bing-1", "20261101")
        store.mark_favorite("bing-1", True)
        refreshed = dict(record, title="新标题")
        merged = store.upsert_image(refreshed)
        self.assertTrue(merged["favorite"])
        self.assertEqual(merged["title"], "新标题")

    def test_prune_removes_old_unfavorited(self):
        self._add("bing-old", "20200101")
        self._add("bing-new", "29991231")
        removed = store.prune(retention_days=10)
        self.assertEqual(removed, ["bing-old"])
        self.assertFalse((config.WALLPAPER_DIR / "2020-01/bing-old.jpg").exists())
        self.assertEqual([i["id"] for i in store.list_images()], ["bing-new"])

    def test_prune_keeps_favorites_and_current(self):
        self._add("bing-fav", "20200101", favorite=True)
        self._add("bing-cur", "20200102")
        self._add("bing-other", "20200103")
        removed = store.prune(keep_ids=["bing-cur"], retention_days=10)
        self.assertEqual(removed, ["bing-other"])
        remaining = {i["id"] for i in store.list_images()}
        self.assertEqual(remaining, {"bing-fav", "bing-cur"})

    def test_images_sorted_newest_first(self):
        self._add("bing-a", "20260101")
        self._add("bing-b", "20260301")
        self._add("bing-c", "20260201")
        self.assertEqual([i["id"] for i in store.list_images()], ["bing-b", "bing-c", "bing-a"])

    def test_corrupt_index_falls_back_to_empty(self):
        config.INDEX_PATH.write_text("{not json", encoding="utf-8")
        self.assertEqual(store.load_index()["images"], [])

    def test_atomic_write_leaves_no_temp_files(self):
        store.save_state(store.load_state())
        leftovers = [p.name for p in self.tmp.iterdir() if ".tmp" in p.name]
        self.assertEqual(leftovers, [])


class ArchiveParseTests(unittest.TestCase):
    def test_parse_minimal_payload(self):
        entries = [sample_entry()]
        self.assertEqual(bing.image_id(entries[0]), "bing-20261111-northwindow-4cd78b6a")

    def test_ohr_token_from_url_when_urlbase_missing(self):
        entry = sample_entry(urlbase="")
        self.assertEqual(bing.ohr_token(entry), "OHR.NorthWindow_ZH-CN6734579353")


if __name__ == "__main__":
    unittest.main()


class BingUrlTests(unittest.TestCase):
    """copyrightlink / quiz 渲染成链接前必须过清洗。"""

    def test_relative_quiz_becomes_absolute(self):
        got = bing.absolute_bing_url("/search?q=Bing+homepage+quiz&FORM=HPQUIZ")
        self.assertEqual(got, "https://www.bing.com/search?q=Bing+homepage+quiz&FORM=HPQUIZ")

    def test_protocol_relative_is_upgraded(self):
        self.assertEqual(bing.absolute_bing_url("//www.bing.com/x"), "https://www.bing.com/x")

    def test_absolute_bing_url_is_kept(self):
        url = "https://www.bing.com/search?q=%E5%B8%83%E8%8E%B1%E6%96%AF"
        self.assertEqual(bing.absolute_bing_url(url), url)

    def test_subdomain_is_allowed(self):
        self.assertEqual(bing.absolute_bing_url("https://cn.bing.com/x"), "https://cn.bing.com/x")

    def test_http_is_rejected(self):
        self.assertIsNone(bing.absolute_bing_url("http://www.bing.com/x"))

    def test_foreign_host_is_rejected(self):
        self.assertIsNone(bing.absolute_bing_url("https://evil.example.com/x"))
        self.assertIsNone(bing.absolute_bing_url("https://bing.com.evil.com/x"))

    def test_script_scheme_is_rejected(self):
        self.assertIsNone(bing.absolute_bing_url("javascript:alert(1)"))
        self.assertIsNone(bing.absolute_bing_url("data:text/html,<script>"))

    def test_empty_is_rejected(self):
        self.assertIsNone(bing.absolute_bing_url(""))
        self.assertIsNone(bing.absolute_bing_url(None))

    def test_record_carries_quiz(self):
        record = bing.build_record(sample_entry(quiz="/search?q=quiz&FORM=HPQUIZ"))
        self.assertEqual(record["quiz"], "https://www.bing.com/search?q=quiz&FORM=HPQUIZ")
        self.assertTrue(record["copyrightlink"].startswith("https://www.bing.com/"))
