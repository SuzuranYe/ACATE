"""The image tool: every example draws as shipped, and the time-lapse measuring finds what is planted."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image, ImageChops, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "Template"
sys.path.insert(0, str(ROOT / "Script"))
import ACATE_Script_Review_Images as Images  # noqa: E402

KINDS = {"Place_Card": "place_card", "Day_Overview": "day_overview", "Boundary": "boundaries", "Time_Lapse": "time_lapse",
         "Time_Lapse_Judge": "time_lapse_judge"}


class ExampleTests(unittest.TestCase):
    def test_every_example_draws_as_shipped(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            for name, kind in KINDS.items():
                with self.subTest(name):
                    data = json.loads((TEMPLATE / f"ACATE_Template_{name}.json").read_text(encoding="utf-8"))
                    out = Images.render(kind, data, Path(folder) / f"{name}.png")
                    with Image.open(out) as new, Image.open(TEMPLATE / f"ACATE_Template_{name}.png") as old:
                        self.assertEqual(Images.OUTPUT_W, new.width)
                        if Images.pick_fonts()[0] == Images.FONTS[0][0]:     # the examples are drawn in the Windows font
                            self.assertEqual(old.size, new.size)
                            self.assertIsNone(ImageChops.difference(old.convert("RGB"), new.convert("RGB")).getbbox())

    def test_unknown_kind_is_refused(self) -> None:
        with self.assertRaises(Images.ImageInputError):
            Images.render("nothing", {}, Path("x.png"))


class FontTests(unittest.TestCase):
    def test_the_environment_names_the_font(self) -> None:
        with mock.patch.dict(os.environ, {"ACATE_FONT": "a.ttf", "ACATE_FONT_BOLD": "b.ttf"}):
            self.assertEqual(("a.ttf", 0, "b.ttf", 0), Images.pick_fonts())

    def test_the_first_system_with_its_fonts_is_used(self) -> None:
        mac = Images.FONTS[1]
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(Path, "exists", lambda p: p == Path(mac[0])):
            os.environ.pop("ACATE_FONT", None)
            self.assertEqual(mac, Images.pick_fonts())

    def test_no_font_is_reported(self) -> None:
        with mock.patch.object(Path, "exists", lambda p: False):
            os.environ.pop("ACATE_FONT", None)
            with self.assertRaises(Images.ImageInputError):
                Images.pick_fonts()


class MeasureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        rng = np.random.default_rng(3)
        noise = Image.fromarray((rng.random((1000, 1600)) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(4))
        self.base = np.array(noise, dtype=np.float32)

    def frames(self, shifts: dict[int, int], gains: dict[int, float], count: int = 40) -> list[dict]:
        out = []
        for i in range(count):
            a = np.roll(self.base, shifts.get(i, 0), axis=1) * gains.get(i, 1.0)
            path = Path(self.dir.name) / f"{i:04d}.jpg"
            Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(path, quality=95)
            time = (datetime(2026, 4, 12, 17, 0, 0) + timedelta(seconds=10 * i)).isoformat()
            out.append({"number": f"{i:04d}", "file": path.name, "proxy": str(path), "time": time})
        return out

    def test_a_jump_at_the_end_and_a_dark_first_frame_are_flagged(self) -> None:
        frames = self.frames({39: 9}, {0: 0.9})
        m = Images.measure_time_lapse(frames, 1620)
        self.assertEqual({"0000", "0039"}, {n for n, _ in m["flags"]})
        self.assertEqual(["0039"], [n for n, _ in m["single_warnings"]])
        self.assertAlmostEqual(9, m["peaks"][0][1], delta=0.5)

    def test_a_steady_run_has_no_flags(self) -> None:
        m = Images.measure_time_lapse(self.frames({}, {}), 1620)
        self.assertEqual([], m["flags"])
        self.assertEqual([], m["single_warnings"])

    def test_excluded_frames_change_the_first_and_last(self) -> None:
        frames = self.frames({39: 9}, {})
        m = Images.measure_time_lapse(frames, 1620)
        prepared = Images.prepare_frames({"frames": frames, "exclude": ["0039"]}, m)
        self.assertEqual("edge", prepared[39]["role"])
        self.assertEqual("last", prepared[38]["role"])
        self.assertFalse(prepared[38]["judge"])

    def test_a_flag_is_settled_by_the_review(self) -> None:
        frames = self.frames({39: 9}, {})
        m = Images.measure_time_lapse(frames, 1620)
        self.assertTrue(Images.prepare_frames({"frames": frames}, m)[39]["judge"])
        self.assertFalse(Images.prepare_frames({"frames": frames, "reviewed": ["0039"]}, m)[39]["judge"])


if __name__ == "__main__":
    unittest.main()
