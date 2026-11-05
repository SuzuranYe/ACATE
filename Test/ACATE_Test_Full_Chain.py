"""The whole chain on generated photos: read, find, confirm, build; every step as a command, as a user runs it."""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
READ = ROOT / "Script" / "ACATE_Script_Read.py"
WORKBOOK = ROOT / "Script" / "ACATE_Script_Workbook.mjs"


def ready() -> bool:
    return bool(shutil.which("exiftool") and shutil.which("node") and os.environ.get("ACATE_NODE_MODULES"))


@unittest.skipUnless(ready(), "needs ExifTool, Node.js and ACATE_NODE_MODULES")
class FullChainTests(unittest.TestCase):
    def test_from_a_folder_to_the_table_and_the_move_guide(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source, run = Path(folder) / "Source", Path(folder) / "Run"
            source.mkdir()
            shots = [(f"A_{i:04d}.JPG", datetime(2026, 4, 12, 8, 0, 0) + timedelta(minutes=i)) for i in range(3)]
            shots += [(f"T_{i:04d}.JPG", datetime(2026, 4, 12, 17, 0, 0) + timedelta(seconds=10 * i)) for i in range(30)]
            for name, when in shots:
                Image.new("RGB", (200, 150), (90, 100, 110)).save(source / name)
                subprocess.run(["exiftool", "-q", "-overwrite_original", f"-DateTimeOriginal={when:%Y:%m:%d %H:%M:%S}",
                                "-Model=TEST-CAM", "-FocalLengthIn35mmFormat=26", str(source / name)], check=True)
            for command in ("scan", "find"):
                args = [sys.executable, "-B", "-X", "utf8", str(READ), command] + ([str(source)] if command == "scan" else []) + [str(run)]
                subprocess.run(args, check=True, capture_output=True)
            path = run / "Time_Lapse" / "TL-20260412-01.json"
            lapse = json.loads(path.read_text(encoding="utf-8"))
            lapse.update(confirmed=True, exclude=[lapse["frames"][-1]["number"]])
            path.write_text(json.dumps(lapse, ensure_ascii=False), encoding="utf-8")
            itinerary = {"segments": [
                {"start": "2026-04-12 07:40", "end": "2026-04-12 09:15", "state": "停留", "place": "京都站", "coordinate": [34.98585, 135.7588]},
                {"start": "2026-04-12 09:15", "end": "2026-04-12 16:00", "state": "移动", "from": "京都站", "to": "清水寺", "transport": "巴士"},
                {"start": "2026-04-12 16:00", "end": "2026-04-12 18:00", "state": "停留", "place": "清水寺", "coordinate": [34.99486, 135.78505]}]}
            (run / "Itinerary.json").write_text(json.dumps(itinerary, ensure_ascii=False), encoding="utf-8")
            done = subprocess.run(["node", str(WORKBOOK), "build", str(run)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            self.assertTrue((run / "ACATE_Timeline.xlsx").exists())
            with (run / "ACATE_Move_Guide.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(33, len(rows))
            targets = {r["目标路径"].rsplit("/", 1)[0] for r in rows}
            self.assertEqual({"照片/04-12 07.40 京都站", "延时/04-12 17.00 清水寺 26mm 29张", "照片/04-12 16.00 清水寺"}, targets)


if __name__ == "__main__":
    unittest.main()
