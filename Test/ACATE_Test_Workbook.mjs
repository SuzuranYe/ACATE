// The workbook tool: the checks on the itinerary, the folder names and the move guide.
// Run with: node --test Test/ACATE_Test_Workbook.mjs

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const TOOL = path.join(ROOT, "Script", "ACATE_Script_Workbook.mjs");
const stay = (start, end, place = "京都站") => ({ start, end, state: "停留", place, coordinate: [34.98585, 135.7588] });
const move = (start, end) => ({ start, end, state: "移动", from: "京都站", to: "清水寺", transport: "巴士" });
const photo = (name, time, extra = {}) => ({ path: name, kind: "photo", time, camera: "CAM", ...extra });

async function run(segments, media = [], more = {}) {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "acate-"));
  await fs.writeFile(path.join(folder, "Itinerary.json"), JSON.stringify({ segments, ...more }));
  await fs.writeFile(path.join(folder, "Media.jsonl"), media.map((m) => JSON.stringify(m)).join("\n"));
  const done = spawnSync(process.execPath, [TOOL, "build", folder], { encoding: "utf8" });
  const result = JSON.parse(done.stdout.trim().split("\n").pop());
  const guide = result.ok ? (await fs.readFile(result.moves, "utf8")).replace(/^﻿/, "").trim().split("\r\n").slice(1) : [];
  return { result, guide, folder };
}

test("a gap between two segments is refused", async () => {
  const { result } = await run([stay("2026-04-12 08:00", "2026-04-12 09:00"), move("2026-04-12 09:05", "2026-04-12 10:00")]);
  assert.equal(result.ok, false);
  assert.match(result.error, /首尾相接/);
});

test("a stay without a coordinate and a move without transport are refused", async () => {
  const noPoint = await run([{ start: "2026-04-12 08:00", end: "2026-04-12 09:00", state: "停留", place: "京都站" }]);
  assert.match(noPoint.result.error, /坐标/);
  const noMode = await run([{ start: "2026-04-12 08:00", end: "2026-04-12 09:00", state: "移动", from: "A", to: "B" }]);
  assert.match(noMode.result.error, /交通方式/);
});

test("a segment shorter than a minute after rounding is refused", async () => {
  const { result } = await run([stay("2026-04-12 08:00:40", "2026-04-12 08:01:10")]);
  assert.match(result.error, /不足一分钟/);
});

test("files go to the segment by time, named by its start; empty segments have no folder", async () => {
  const { guide } = await run(
    [stay("2026-04-12 08:00", "2026-04-12 09:00"), move("2026-04-12 09:00", "2026-04-12 10:00"), stay("2026-04-12 10:00", "2026-04-12 11:00", "清水寺")],
    [photo("A.JPG", "2026-04-12T08:30:00"), photo("B.JPG", "2026-04-12T10:30:00"), photo("C.JPG", "2026-04-12T10:00:00")],
  );
  assert.deepEqual(guide, [
    "照片,A.JPG,照片/04-12 08.00 京都站/A.JPG",
    "照片,B.JPG,照片/04-12 10.00 清水寺/B.JPG",
    "照片,C.JPG,照片/04-12 10.00 清水寺/C.JPG",
  ]);
});

test("a device's clock offset moves its files to the right segment", async () => {
  const { guide } = await run(
    [stay("2026-04-12 08:00", "2026-04-12 09:00"), stay("2026-04-12 09:00", "2026-04-12 10:00", "清水寺")],
    [photo("A.JPG", "2026-04-12T08:30:00")],
    { clock_offsets: [{ camera: "CAM", seconds: 3600 }] },
  );
  assert.equal(guide[0], "照片,A.JPG,照片/04-12 09.00 清水寺/A.JPG");
});

test("same names get a number; no time and outside the trip have their own folders", async () => {
  const { guide, result } = await run([stay("2026-04-12 08:00", "2026-04-12 09:00")], [
    photo("a/X.JPG", "2026-04-12T08:10:00"), photo("b/X.JPG", "2026-04-12T08:20:00"), photo("N.JPG", null), photo("O.JPG", "2026-04-13T08:00:00"),
  ]);
  assert.deepEqual(guide.map((row) => row.split(",")[2]), [
    "照片/04-12 08.00 京都站/X.JPG", "照片/04-12 08.00 京都站/X_2.JPG", "照片/日期未知/N.JPG", "照片/行程之外/O.JPG",
  ]);
  assert.equal(result.no_time, 1);
  assert.equal(result.outside, 1);
  assert.equal(result.renamed, 1);
});

test("a confirmed time-lapse gets its own folder; excluded frames stay photos", async () => {
  const frames = [1, 2, 3].map((n) => ({ number: String(n), time: `2026-04-12T08:10:0${n}`, paths: [`T${n}.JPG`] }));
  const media = frames.map((f) => photo(f.paths[0], f.time));
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "acate-"));
  await fs.mkdir(path.join(folder, "Time_Lapse"));
  await fs.writeFile(path.join(folder, "Time_Lapse", "TL-20260412-01.json"), JSON.stringify({
    sequence_id: "TL-20260412-01", confirmed: true, place_name: "", camera: "CAM", focal_mm: 26, exclude: ["3"], frames,
  }));
  await fs.writeFile(path.join(folder, "Itinerary.json"), JSON.stringify({ segments: [stay("2026-04-12 08:00", "2026-04-12 09:00")] }));
  await fs.writeFile(path.join(folder, "Media.jsonl"), media.map((m) => JSON.stringify(m)).join("\n"));
  const done = spawnSync(process.execPath, [TOOL, "build", folder], { encoding: "utf8" });
  const result = JSON.parse(done.stdout);
  const rows = (await fs.readFile(result.moves, "utf8")).split("\r\n");
  assert.ok(rows.some((r) => r.endsWith("延时/04-12 08.10 京都站 26mm 2张/T1.JPG")));
  assert.ok(rows.some((r) => r.endsWith("照片/04-12 08.00 京都站/T3.JPG")));
});

test("the table has one row a segment, rounded to the minute", async () => {
  const { result } = await run(
    [stay("2026-04-12 07:40:10", "2026-04-12 09:15:40"), move("2026-04-12 09:15:40", "2026-04-12 10:00:00")],
    [photo("A.JPG", "2026-04-12T08:30:00")],
  );
  const require = createRequire(path.join(process.env.ACATE_NODE_MODULES ?? ".", "noop.js"));
  const JSZip = require("jszip");
  const zip = await JSZip.loadAsync(await fs.readFile(result.table));
  const sheet = await zip.file("xl/worksheets/sheet1.xml").async("string");
  assert.match(sheet, /2026-04-12 07:40/);
  assert.match(sheet, /2026-04-12 09:16/);
  assert.match(sheet, /34\.98585, 135\.75880/);
  assert.match(sheet, /京都站 → 清水寺/);
  assert.match(sheet, /04-12 07\.40 京都站/);
});
