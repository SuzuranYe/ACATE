"""The reader on generated files: metadata, proxies, a read-only source, and the time-lapses it finds."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Script"))
import ACATE_Script_Read as Read  # noqa: E402


def media(time: str, name: str = "A.JPG", camera: str = "CAM", focal: int = 26) -> dict:
    return {"path": name, "kind": "photo", "time": time, "camera": camera, "serial": "", "focal_mm": focal, "proxy": None,
            "width": 100, "height": 50}


def series(start: datetime, count: int, seconds: float, stem: str = "T", **kw) -> list[dict]:
    return [media((start + timedelta(seconds=i * seconds)).isoformat(), f"{stem}_{i:04d}.JPG", **kw) for i in range(count)]


def lengths(items: list[dict]) -> list[int]:
    frames = next(iter(Read.frames_of(items).values()))
    return [len(run) for run in Read.merged(Read.runs_of(frames))]


class FindingTests(unittest.TestCase):
    start = datetime(2026, 4, 12, 17, 0, 0)

    def test_steady_interval_is_one_run(self) -> None:
        self.assertEqual([30], lengths(series(self.start, 30, 10)))

    def test_a_lone_photo_before_stays_out(self) -> None:
        before = [media((self.start - timedelta(hours=3)).isoformat(), "B.JPG")]
        self.assertEqual([1, 30], lengths(before + series(self.start, 30, 10)))

    def test_changing_and_alternating_intervals_are_regular(self) -> None:
        moments, t = [], self.start
        for i in range(40):
            moments.append(media(t.isoformat(), f"G_{i:04d}.JPG"))
            t += timedelta(seconds=5 if i % 2 else 9)
        self.assertEqual([40], lengths(moments))

    def test_a_short_break_joins_two_runs(self) -> None:
        first = series(self.start, 30, 10, "A")
        second = series(self.start + timedelta(minutes=8), 30, 10, "B")
        self.assertEqual([60], lengths(first + second))

    def test_a_long_break_or_a_new_focal_length_keeps_them_apart(self) -> None:
        first = series(self.start, 30, 10, "A")
        self.assertEqual([30, 30], lengths(first + series(self.start + timedelta(hours=1), 30, 10, "B")))
        self.assertEqual([30, 30], lengths(first + series(self.start + timedelta(minutes=8), 30, 10, "B", focal=70)))

    def test_raw_and_jpg_of_one_shot_are_one_frame(self) -> None:
        pair = [media("2026-04-12T17:00:00", "A_0001.CR3"), media("2026-04-12T17:00:00", "A_0001.JPG")]
        frames = next(iter(Read.frames_of(pair).values()))
        self.assertEqual(1, len(frames))
        self.assertEqual(2, len(frames[0]))


class ProxyTests(unittest.TestCase):
    def test_a_preview_without_orientation_is_turned_as_the_file_says(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "p.jpg"
            Read.save_proxy(Image.new("RGB", (400, 300)), target, 6)
            with Image.open(target) as image:
                self.assertEqual((300, 400), image.size)

    def test_the_size_of_a_fujifilm_raw_is_its_own(self) -> None:
        self.assertEqual((6240, 4160), Read.image_size({"ImageWidth": 4416, "ImageHeight": 2944, "RawImageCroppedSize": "6240 4160"}))
        self.assertEqual((6048, 4024), Read.image_size({"ImageWidth": 6048, "ImageHeight": 4024}))

    def test_hidden_files_stay_out(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".Trashes").mkdir()
            for name in ("A.JPG", "._A.JPG", ".Trashes/B.JPG"):
                Image.new("RGB", (8, 8)).save(root / name, format="JPEG")
            self.assertEqual(["A.JPG"], [p.name for p in Read.media_files(root)])

    def test_every_brand_counts_as_a_photo(self) -> None:
        for name in ("a.CR3", "a.NEF", "a.ARW", "a.RAF", "a.ORF", "a.RW2", "a.DNG", "a.PEF", "a.HEIC"):
            with self.subTest(name):
                self.assertEqual("photo", Read.kind_of(Path(name)))

    def test_raw_preview_keeps_the_largest_and_stops_at_a_good_one(self) -> None:
        sizes = {"-JpgFromRaw": 0, "-PreviewImage": 5_000, "-OtherImage": 300_000, "-ThumbnailImage": 9}
        asked = []

        def fake(command, **_):
            asked.append(command[-2])
            return subprocess.CompletedProcess(command, 0, stdout=b"x" * sizes[command[-2]])

        original, Read.subprocess.run = Read.subprocess.run, fake
        try:
            self.assertEqual(300_000, len(Read.raw_preview(Path("a.NEF"))))
        finally:
            Read.subprocess.run = original
        self.assertNotIn("-ThumbnailImage", asked)


@unittest.skipUnless(shutil.which("exiftool"), "ExifTool is not installed")
class ScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.source = Path(self.dir.name) / "Source"
        self.run_root = Path(self.dir.name) / "Run"
        self.source.mkdir()
        for i in range(30):
            path = self.source / f"T_{i:04d}.JPG"
            Image.new("RGB", (400, 300), (120, 130, 140)).save(path)
            stamp = (datetime(2026, 4, 12, 17, 0, 0) + timedelta(seconds=10 * i)).strftime("%Y:%m:%d %H:%M:%S")
            subprocess.run(["exiftool", "-q", "-overwrite_original", f"-DateTimeOriginal={stamp}", "-Model=TEST-CAM",
                            "-GPSLatitude=34.98585", "-GPSLatitudeRef=N", "-GPSLongitude=135.7588", "-GPSLongitudeRef=E",
                            str(path)], check=True)

    def test_scan_reads_time_and_gps_and_leaves_the_source_alone(self) -> None:
        before = {p.name: p.read_bytes() for p in self.source.iterdir()}
        result = Read.scan(self.source, self.run_root)
        self.assertEqual({p.name: p.read_bytes() for p in self.source.iterdir()}, before)
        self.assertEqual(30, result["media"])
        self.assertEqual(30, result["with_gps"])
        first = json.loads((self.run_root / "Media.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual("2026-04-12T17:00:00", first["time"])
        self.assertEqual([34.98585, 135.7588], first["gps"])
        self.assertTrue((self.run_root / first["proxy"]).exists())

    def test_find_writes_a_time_lapse_file(self) -> None:
        Read.scan(self.source, self.run_root)
        result = Read.find(self.run_root)
        self.assertEqual(["TL-20260412-01"], [t["sequence_id"] for t in result["time_lapses"]])
        data = json.loads((self.run_root / "Time_Lapse/TL-20260412-01.json").read_text(encoding="utf-8"))
        self.assertEqual(30, len(data["frames"]))
        self.assertEqual("10", data["interval"])
        self.assertFalse(data["confirmed"])

    def test_every_file_keeps_its_own_tags(self) -> None:
        Read.scan(self.source, self.run_root)
        lines = (self.run_root / "Media.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(30, len({json.loads(line)["time"] for line in lines}))

    def test_the_run_root_may_not_sit_inside_the_source(self) -> None:
        with self.assertRaises(Read.ReadError):
            Read.scan(self.source, self.source / "Run")


if __name__ == "__main__":
    unittest.main()
