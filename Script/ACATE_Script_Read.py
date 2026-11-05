#!/usr/bin/env python3
"""Read a folder of photos and videos without changing it, and find the time-lapses in it.

scan reads the time, GPS and camera of every file and draws a small proxy of each into the run root. find looks for
the time-lapses among them. The source folder is only read.

Usage:
    python ACATE_Script_Read.py scan <source_folder> <run_root>
    python ACATE_Script_Read.py find <run_root>
"""

from __future__ import annotations

import argparse
import io
import json
import re
import statistics
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from PIL import Image, ImageOps

PHOTO = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".heif", ".webp", ".avif"}
RAW = {".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".sr2", ".srf", ".raf", ".orf", ".rw2", ".rwl", ".dng", ".pef",
       ".srw", ".x3f", ".3fr", ".iiq", ".erf", ".mrw"}
VIDEO = {".mp4", ".mov", ".avi", ".mts", ".m2ts", ".mkv", ".m4v", ".3gp", ".wmv", ".mpg", ".mpeg", ".webm"}
PROXY_SIDE = 1620
BATCH = 400
TAGS = ["DateTimeOriginal", "SubSecTimeOriginal", "OffsetTimeOriginal", "CreationDate", "CreateDate", "GPSLatitude",
        "GPSLongitude", "Make", "Model", "SerialNumber", "InternalSerialNumber", "FocalLength",
        "FocalLengthIn35mmFormat", "FocalLength35efl", "ImageWidth", "ImageHeight", "RawImageCroppedSize", "Duration",
        "Orientation"]
PREVIEW_TAGS = ("JpgFromRaw", "PreviewImage", "OtherImage", "ThumbnailImage")
GOOD_PREVIEW = 200_000           # bytes; a smaller preview is only kept when nothing better can be had
TURNS = {3: Image.Transpose.ROTATE_180, 6: Image.Transpose.ROTATE_270, 8: Image.Transpose.ROTATE_90}
MIN_FRAMES, MIN_INTERVAL, MERGE_GAP, RECENT = 24, 0.8, 300, 8


class ReadError(Exception):
    """A problem to report as JSON, without a traceback."""


def kind_of(path: Path) -> str | None:
    suffix = path.suffix.lower()
    return "photo" if suffix in PHOTO or suffix in RAW else "video" if suffix in VIDEO else None


def media_files(source: Path) -> list[Path]:
    """Hidden files stay out: macOS writes a "._" file beside each photo on a shared drive."""
    return sorted(p for p in source.rglob("*") if p.is_file() and kind_of(p)
                  and not any(part.startswith(".") for part in p.relative_to(source).parts))


def exif_batches(files: Sequence[Path]) -> Iterable[dict[str, Any]]:
    """The tags of every file, a batch at a time; names go through a UTF-8 argument file."""
    for start in range(0, len(files), BATCH):
        part = files[start:start + BATCH]
        with tempfile.NamedTemporaryFile("w", suffix=".args", encoding="utf-8", delete=False) as args:
            args.write("\n".join(str(p) for p in part) + "\n")
        command = ["exiftool", "-charset", "filename=utf8", "-j", "-n", "-q", "-q", "-api", "LargeFileSupport=1"]
        command += [f"-{tag}" for tag in TAGS] + ["-@", args.name]
        try:
            done = subprocess.run(command, capture_output=True, check=False)
        except FileNotFoundError as exc:
            raise ReadError("未找到 ExifTool，请先安装") from exc
        finally:
            Path(args.name).unlink(missing_ok=True)
        yield from json.loads(done.stdout.decode("utf-8") or "[]")


def capture_time(tags: dict[str, Any]) -> tuple[str | None, str | None]:
    """The capture time as written on the device, and its offset when the file holds one."""
    for name in ("DateTimeOriginal", "CreationDate", "CreateDate"):
        value = str(tags.get(name) or "")
        match = re.match(r"(\d{4}):(\d\d):(\d\d) (\d\d):(\d\d):(\d\d)(\.\d+)?([+-]\d\d:\d\d)?", value)
        if match and match.group(1) != "0000":
            y, mo, d, h, mi, s, fraction, offset = match.groups()
            sub = fraction[1:] if fraction else str(tags.get("SubSecTimeOriginal") or "")
            return f"{y}-{mo}-{d}T{h}:{mi}:{s}" + (f".{sub}" if sub else ""), offset or tags.get("OffsetTimeOriginal")
    return None, None


def image_size(tags: dict[str, Any]) -> tuple[Any, Any]:
    """Width and height of the original; a Fujifilm RAW gives the size of its JPEG as the image size."""
    raw = str(tags.get("RawImageCroppedSize") or "").split()
    if len(raw) == 2 and all(v.isdigit() for v in raw):
        return int(raw[0]), int(raw[1])
    return tags.get("ImageWidth"), tags.get("ImageHeight")


def record(path: Path, source: Path, tags: dict[str, Any]) -> dict[str, Any]:
    time, offset = capture_time(tags)
    lat, lon = tags.get("GPSLatitude"), tags.get("GPSLongitude")
    has_gps = isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and not (lat == 0 and lon == 0)
    camera = " ".join(str(tags.get("Model") or tags.get("Make") or "未知设备").split())
    rel = path.relative_to(source).as_posix()
    width, height = image_size(tags)
    focal = tags.get("FocalLengthIn35mmFormat") or tags.get("FocalLength35efl") or tags.get("FocalLength")
    return {"path": rel, "kind": kind_of(path), "raw": path.suffix.lower() in RAW, "time": time, "offset": offset,
            "gps": [round(lat, 6), round(lon, 6)] if has_gps else None, "camera": camera,
            "serial": str(tags.get("SerialNumber") or tags.get("InternalSerialNumber") or ""),
            "focal_mm": round(focal) if isinstance(focal, (int, float)) else None,
            "width": width, "height": height,
            "duration": tags.get("Duration") if kind_of(path) == "video" else None,
            "proxy": f"Proxy/{rel}.jpg"}


def save_proxy(image: Image.Image, target: Path, orientation: Any = None) -> None:
    """A preview inside a RAW file seldom carries the turn of the shot; orientation is the one the file records."""
    turned = image.getexif().get(0x0112)
    image = ImageOps.exif_transpose(image).convert("RGB")
    if not turned and orientation in TURNS:
        image = image.transpose(TURNS[orientation])
    image.thumbnail((PROXY_SIDE, PROXY_SIDE), Image.LANCZOS)
    image.save(target, quality=88)


def raw_preview(path: Path) -> bytes | None:
    """The largest preview a RAW file holds, stopping at the first good one; brands keep it under different tags."""
    best = b""
    for tag in PREVIEW_TAGS:
        done = subprocess.run(["exiftool", "-charset", "filename=utf8", "-b", f"-{tag}", str(path)],
                              capture_output=True, check=False)
        if len(done.stdout) > len(best):
            best = done.stdout
        if len(best) >= GOOD_PREVIEW:
            break
    return best or None


def raw_decode(path: Path) -> Image.Image | None:
    """Develop a RAW file with no usable preview; needs rawpy, which is optional."""
    try:
        import rawpy
    except ImportError:
        return None
    try:
        with rawpy.imread(str(path)) as raw:
            return Image.fromarray(raw.postprocess(half_size=True, use_camera_wb=True, output_bps=8))
    except (rawpy.LibRawError, OSError, ValueError):
        return None


def ffmpeg_frame(path: Path, target: Path) -> bool:
    scale = f"scale='if(gt(iw,ih),{PROXY_SIDE},-2)':'if(gt(iw,ih),-2,{PROXY_SIDE})'"
    try:
        done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-frames:v", "1", "-vf", scale,
                               "-q:v", "3", str(target)], capture_output=True, check=False)
    except FileNotFoundError as exc:
        raise ReadError("未找到 FFmpeg，请先安装") from exc
    return done.returncode == 0 and target.exists()


def sips_frame(path: Path, target: Path) -> bool:
    """macOS reads HEIC by itself; Pillow cannot and FFmpeg not always."""
    if sys.platform != "darwin":
        return False
    done = subprocess.run(["sips", "-s", "format", "jpeg", "-Z", str(PROXY_SIDE), str(path), "--out", str(target)],
                          capture_output=True, check=False)
    return done.returncode == 0 and target.exists()


def make_proxy(path: Path, target: Path, orientation: Any = None) -> bool:
    if target.exists():
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    try:
        if suffix in VIDEO:
            return ffmpeg_frame(path, target)
        if suffix in RAW:
            data = raw_preview(path)
            developed = raw_decode(path) if data is None or len(data) < GOOD_PREVIEW else None
            if developed is not None:       # the decoder has turned it already
                save_proxy(developed, target)
                return True
            if data is None:
                return False
            with Image.open(io.BytesIO(data)) as image:
                save_proxy(image, target, orientation)
            return True
        with Image.open(path) as image:
            save_proxy(image, target)
        return True
    except OSError:
        return sips_frame(path, target) or ffmpeg_frame(path, target)


def scan(source: Path, run_root: Path) -> dict[str, Any]:
    source, run_root = source.resolve(), run_root.resolve()
    if not source.is_dir():
        raise ReadError(f"源文件夹不存在：{source}")
    if run_root == source or source in run_root.parents:
        raise ReadError("运行根目录不能放在源文件夹内")
    files = media_files(source)
    if not files:
        raise ReadError("源文件夹内没有照片或视频")
    run_root.mkdir(parents=True, exist_ok=True)
    tags = {str(Path(t["SourceFile"])): t for t in exif_batches(files)}
    media = [record(p, source, tags.get(str(p), {})) for p in files]
    with ThreadPoolExecutor(max_workers=4) as pool:
        turns = [tags.get(str(p), {}).get("Orientation") for p in files]
        made = list(pool.map(make_proxy, files, [run_root / m["proxy"] for m in media], turns))
    for m, ok in zip(media, made):
        if not ok:
            m["proxy"] = None
    lines = [json.dumps(m, ensure_ascii=False) for m in media]
    (run_root / "Media.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (run_root / "Source.json").write_text(json.dumps({"source": str(source)}, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "media": len(media), "photos": sum(m["kind"] == "photo" for m in media),
            "videos": sum(m["kind"] == "video" for m in media), "with_gps": sum(bool(m["gps"]) for m in media),
            "no_time": [m["path"] for m in media if not m["time"]][:20],
            "no_proxy": [m["path"] for m, ok in zip(media, made) if not ok][:20]}


def seconds(time: str) -> float:
    return datetime.fromisoformat(time).timestamp()


def frames_of(media: Sequence[dict[str, Any]]) -> dict[tuple[str, str], list[list[dict[str, Any]]]]:
    """Photos by camera body in capture order; the files of one shot (a RAW and its JPG) count as one frame."""
    bodies: dict[tuple[str, str], dict[tuple[str, str], list[dict[str, Any]]]] = {}
    for m in media:
        if m["kind"] == "photo" and m["time"]:
            shot = (m["time"], Path(m["path"]).stem)
            bodies.setdefault((m["camera"], m["serial"]), {}).setdefault(shot, []).append(m)
    return {body: [shots[k] for k in sorted(shots)] for body, shots in bodies.items()}


def regular(gap: float, recent: Sequence[float]) -> bool:
    """Whether a gap keeps the rhythm of the gaps before it."""
    if not recent:
        return True
    if len(recent) == 1:
        return gap <= 1.5 * recent[0] + 2 and recent[0] <= 1.5 * gap + 2
    middle = statistics.median(recent)
    return middle / 3 - 1 <= gap <= 1.5 * middle + 2


def runs_of(frames: Sequence[list[dict[str, Any]]]) -> list[list[list[dict[str, Any]]]]:
    runs: list[list[list[dict[str, Any]]]] = []
    current = [frames[0]]
    gaps: list[float] = []
    for before, after in zip(frames, frames[1:]):
        gap = seconds(after[0]["time"]) - seconds(before[0]["time"])
        if regular(gap, gaps[-RECENT:]):
            current.append(after)
            gaps.append(gap)
        elif len(current) == 2:        # one gap sets no rhythm: the run starts again from the frame before
            runs.append(current[:1])
            current, gaps = [before, after], [gap]
        else:
            runs.append(current)
            current, gaps = [after], []
    runs.append(current)
    return runs


def merged(runs: Sequence[list[list[dict[str, Any]]]]) -> list[list[list[dict[str, Any]]]]:
    """Runs split by a short break with the same focal length are one time-lapse, as when a battery is changed."""
    out: list[list[list[dict[str, Any]]]] = []
    for run in runs:
        if out and len(run) >= 3 and len(out[-1]) >= 3 and run[0][0]["focal_mm"] == out[-1][-1][0]["focal_mm"] \
                and seconds(run[0][0]["time"]) - seconds(out[-1][-1][0]["time"]) <= MERGE_GAP:
            out[-1] = out[-1] + run
        else:
            out.append(list(run))
    return out


def interval_text(run: Sequence[list[dict[str, Any]]]) -> str:
    gaps = [round(seconds(b[0]["time"]) - seconds(a[0]["time"])) for a, b in zip(run, run[1:])]
    gaps = [g for g in gaps if g <= statistics.median(gaps) * 3 + 2]
    low, high = min(gaps), max(gaps)
    return str(low) if low == high else f"{low}～{high}"


def frame_entry(shot: Sequence[dict[str, Any]], index: int, run_root: Path) -> dict[str, Any]:
    first = shot[0]
    digits = re.search(r"(\d{3,})\D*$", Path(first["path"]).stem)
    proxy = next((m["proxy"] for m in shot if m["proxy"]), None)
    return {"number": digits.group(1) if digits else str(index + 1), "file": Path(first["path"]).name,
            "paths": [m["path"] for m in shot], "proxy": str(run_root / proxy) if proxy else None,
            "time": first["time"]}


def find(run_root: Path) -> dict[str, Any]:
    run_root = run_root.resolve()
    path = run_root / "Media.jsonl"
    if not path.exists():
        raise ReadError("缺少 Media.jsonl，请先运行 scan")
    media = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    found: list[tuple[str, tuple[str, str], list[list[dict[str, Any]]]]] = []
    for body, frames in frames_of(media).items():
        for run in merged(runs_of(frames)):
            if len(run) >= MIN_FRAMES:
                gaps = [seconds(b[0]["time"]) - seconds(a[0]["time"]) for a, b in zip(run, run[1:])]
                if statistics.median(gaps) >= MIN_INTERVAL:
                    found.append((run[0][0]["time"], body, run))
    folder = run_root / "Time_Lapse"
    folder.mkdir(exist_ok=True)
    counts: dict[str, int] = {}
    index = []
    for start, body, run in sorted(found, key=lambda item: item[0]):
        day = start[:10].replace("-", "")
        counts[day] = counts.get(day, 0) + 1
        sequence = f"TL-{day}-{counts[day]:02d}"
        first = run[0][0]
        entry = {"sequence_id": sequence, "confirmed": False, "place_name": "", "camera": body[0],
                 "focal_mm": first["focal_mm"], "interval": interval_text(run),
                 "original_long_side": max(first["width"] or 0, first["height"] or 0) or PROXY_SIDE,
                 "exclude": [], "reviewed": [], "conclusion": [],
                 "frames": [frame_entry(shot, i, run_root) for i, shot in enumerate(run)]}
        (folder / f"{sequence}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1), encoding="utf-8")
        index.append({"sequence_id": sequence, "start": start, "frames": len(run), "camera": body[0]})
    return {"ok": True, "time_lapses": index}


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("scan", help="read the metadata and draw the proxies")
    one.add_argument("source")
    one.add_argument("run_root")
    two = sub.add_parser("find", help="find the time-lapses")
    two.add_argument("run_root")
    args = parser.parse_args(argv)
    try:
        result = scan(Path(args.source), Path(args.run_root)) if args.command == "scan" else find(Path(args.run_root))
    except (ReadError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
