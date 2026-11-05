#!/usr/bin/env node
// Turn Itinerary.json into the table and the move guide. It moves no file.
//
// Usage: node ACATE_Script_Workbook.mjs build <run_root>
//
// Needs the npm package jszip; ACATE_NODE_MODULES names the node_modules folder it is in.

import fs from "node:fs/promises";
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const TEMPLATE = path.resolve(SCRIPT_DIR, "..", "Template", "ACATE_Template_Timeline.xlsx");
const STATES = new Set(["停留", "移动"]);
const CATEGORY = { photo: "照片", video: "视频", lapse: "延时" };
const COLUMNS = "ABCDEFGH";

class BuildError extends Error {}

function loadJSZip() {
  const roots = [process.env.ACATE_NODE_MODULES, path.join(process.cwd(), "node_modules")].filter(Boolean);
  for (const root of roots) {
    try {
      return createRequire(path.join(root, "noop.js"))("jszip");
    } catch {
      // the next root
    }
  }
  throw new BuildError("未找到 jszip：请运行 npm install jszip，并将 node_modules 的路径写入 ACATE_NODE_MODULES");
}

function clockOf(text, what) {
  const match = /^(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d)(?::(\d\d))?/.exec(String(text));
  if (!match) throw new BuildError(`${what} 的时刻写法有误：${text}`);
  const [y, mo, d, h, mi, s] = match.slice(1).map((v) => Number(v ?? 0));
  return Date.UTC(y, mo - 1, d, h, mi, s);
}

// Wall-clock times are carried as UTC numbers, so that the arithmetic never meets a time zone.
function wall(ms) {
  return new Date(ms).toISOString();
}

function minuteText(ms) {
  const rounded = Math.floor((ms + 30_000) / 60_000) * 60_000;
  return `${wall(rounded).slice(0, 10)} ${wall(rounded).slice(11, 16)}`;
}

function checkSegments(segments) {
  if (!Array.isArray(segments) || segments.length === 0) throw new BuildError("Itinerary.json 不含行程段");
  const parsed = segments.map((s, i) => {
    const what = `第 ${i + 1} 段`;
    if (!STATES.has(s.state)) throw new BuildError(`${what} 的状态须为停留或移动`);
    if (s.state === "停留" && !s.place) throw new BuildError(`${what} 缺少地点`);
    if (s.state === "停留" && !(Array.isArray(s.coordinate) && s.coordinate.length === 2)) {
      throw new BuildError(`${what} 缺少坐标`);
    }
    if (s.state === "移动" && !(s.from && s.to)) throw new BuildError(`${what} 缺少起点或终点`);
    if (s.state === "移动" && !s.transport) throw new BuildError(`${what} 缺少交通方式`);
    const start = clockOf(s.start, what);
    const end = clockOf(s.end, what);
    if (minuteText(start) === minuteText(end)) throw new BuildError(`${what} 不足一分钟，须并入相邻的段`);
    return { ...s, startMs: start, endMs: end, number: i + 1 };
  });
  for (let i = 1; i < parsed.length; i += 1) {
    if (parsed[i - 1].end !== parsed[i].start) {
      throw new BuildError(`第 ${i} 段的结束与第 ${i + 1} 段的开始未首尾相接：${parsed[i - 1].end}，${parsed[i].start}`);
    }
  }
  return parsed;
}

// The start of a folder's name: month, day and time, so that folders sort by time.
function stampOf(ms) {
  const text = minuteText(ms);
  return `${text.slice(5, 10)} ${text.slice(11).replace(":", ".")}`;
}

function clean(name) {
  return String(name).replace(/[<>:"/\\|?*]/g, "-").replace(/[. ]+$/, "");
}

function placeOf(segment) {
  return segment.state === "停留" ? segment.place : `${segment.from} - ${segment.to}`;
}

async function readMedia(runRoot, offsets) {
  const text = await fs.readFile(path.join(runRoot, "Media.jsonl"), "utf8").catch(() => {
    throw new BuildError("缺少 Media.jsonl，请先运行 ACATE_Script_Read.py scan");
  });
  return text.split("\n").filter(Boolean).map((line) => {
    const m = JSON.parse(line);
    const shift = offsets.find((o) => o.camera === m.camera && (!o.kind || o.kind === m.kind))?.seconds ?? 0;
    return { ...m, ms: m.time ? clockOf(m.time, m.path) + shift * 1000 : null };
  });
}

async function readLapses(runRoot) {
  const folder = path.join(runRoot, "Time_Lapse");
  if (!existsSync(folder)) return [];
  const names = (await fs.readdir(folder)).filter((n) => n.endsWith(".json")).sort();
  const items = await Promise.all(names.map(async (n) => JSON.parse(await fs.readFile(path.join(folder, n), "utf8"))));
  return items.filter((item) => item.confirmed);
}

function segmentAt(segments, ms) {
  return segments.find((s) => s.startMs <= ms && ms < s.endMs) ?? null;
}

function assign(segments, media, lapses, offsets, report) {
  const keptBy = new Map();
  const counted = new Map(segments.map((s) => [s.number, { files: 0, lapses: 0 }]));
  const lapseItems = [];
  for (const lapse of lapses) {
    const excluded = new Set((lapse.exclude ?? []).map(String));
    const kept = lapse.frames.filter((f) => !excluded.has(String(f.number)));
    const shift = offsets.find((o) => o.camera === lapse.camera && o.kind !== "video")?.seconds ?? 0;
    const startMs = clockOf(kept[0].time, lapse.sequence_id) + shift * 1000;
    const segment = segmentAt(segments, startMs);
    if (!segment) throw new BuildError(`${lapse.sequence_id} 未落入任何行程段`);
    counted.get(segment.number).lapses += 1;
    const item = { lapse, segment, startMs, count: kept.length };
    lapseItems.push(item);
    for (const f of kept) for (const p of f.paths) keptBy.set(p, item);
  }
  const groups = [];
  for (const m of media) {
    const item = keptBy.get(m.path);
    const segment = item?.segment ?? (m.ms === null ? null : segmentAt(segments, m.ms));
    if (segment) counted.get(segment.number).files += 1;
    else if (m.ms === null) report.noTime += 1;
    else report.outside += 1;
    groups.push({ media: m, segment, lapse: item ?? null });
  }
  return { groups, counted, lapseItems };
}

function folderNames(segments, counted) {
  const names = new Map();
  for (const s of segments) {
    if (counted.get(s.number).files > 0) names.set(s.number, `${stampOf(s.startMs)} ${clean(placeOf(s))}`);
  }
  return names;
}

function targetFolder(group, names) {
  const category = group.lapse ? CATEGORY.lapse : CATEGORY[group.media.kind];
  if (group.lapse) {
    const { lapse, segment, startMs, count } = group.lapse;
    const place = clean(lapse.place_name || placeOf(segment));
    return `${category}/${stampOf(startMs)} ${place} ${lapse.focal_mm}mm ${count}张`;
  }
  if (group.segment) return `${category}/${names.get(group.segment.number)}`;
  return `${category}/${group.media.ms === null ? "日期未知" : "行程之外"}`;
}

function moveGuide(groups, names, report) {
  const taken = new Set();
  const rows = [["类别", "原路径", "目标路径"]];
  for (const group of groups) {
    const folder = targetFolder(group, names);
    const { name, ext } = path.parse(group.media.path);
    let target = `${folder}/${name}${ext}`;
    for (let k = 2; taken.has(target.toLowerCase()); k += 1) {
      target = `${folder}/${name}_${k}${ext}`;
      if (k === 2) report.renamed += 1;
    }
    taken.add(target.toLowerCase());
    rows.push([folder.split("/")[0], group.media.path, target]);
  }
  return rows;
}

function csv(rows) {
  const cell = (v) => (/[",\n]/.test(v) ? `"${v.replaceAll('"', '""')}"` : v);
  return `﻿${rows.map((r) => r.map(cell).join(",")).join("\r\n")}\r\n`;
}

function xml(text) {
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function tableRows(segments, counted, names) {
  return segments.map((s) => {
    const c = counted.get(s.number);
    const material = c.files === 0 ? "0" : c.lapses ? `${c.files}，延时 ${c.lapses} 组` : String(c.files);
    return [minuteText(s.startMs), minuteText(s.endMs), s.state, s.state === "停留" ? s.place : `${s.from} → ${s.to}`,
      s.state === "停留" ? `${s.coordinate[0].toFixed(5)}, ${s.coordinate[1].toFixed(5)}` : "", s.transport ?? "", material,
      names.get(s.number) ?? ""];
  });
}

async function writeTable(JSZip, rows, out) {
  const zip = await JSZip.loadAsync(await fs.readFile(TEMPLATE));
  const sheetPath = "xl/worksheets/sheet1.xml";
  const sheet = await zip.file(sheetPath).async("string");
  const pattern = /<row r="2"[^>]*>.*?<\/row>/s;
  const sample = pattern.exec(sheet);
  if (!sample) throw new BuildError("模板缺少样式行");
  const rowTag = /<row r="2"([^>]*)>/.exec(sample[0])[1];
  const styles = [...sample[0].matchAll(/<c r="[A-H]2"[^>]*?s="(\d+)"/g)].map((m) => m[1]);
  const body = rows.map((row, i) => {
    const r = i + 2;
    const cells = row.map((v, k) => `<c r="${COLUMNS[k]}${r}" s="${styles[k]}" t="inlineStr"><is><t xml:space="preserve">${xml(v)}</t></is></c>`);
    return `<row r="${r}"${rowTag}>${cells.join("")}</row>`;
  }).join("");
  const filled = sheet.replace(pattern, body).replace(/<dimension ref="[^"]*"\/>/, `<dimension ref="A1:H${rows.length + 1}"/>`);
  zip.file(sheetPath, filled);
  await fs.writeFile(out, await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" }));
}

async function build(runRoot) {
  const itineraryPath = path.join(runRoot, "Itinerary.json");
  if (!existsSync(itineraryPath)) throw new BuildError("运行根目录内缺少 Itinerary.json");
  const itinerary = JSON.parse(await fs.readFile(itineraryPath, "utf8"));
  const segments = checkSegments(itinerary.segments);
  const offsets = itinerary.clock_offsets ?? [];
  const media = await readMedia(runRoot, offsets);
  const lapses = await readLapses(runRoot);
  const report = { noTime: 0, outside: 0, renamed: 0 };
  const { groups, counted, lapseItems } = assign(segments, media, lapses, offsets, report);
  const names = folderNames(segments, counted);
  const guide = moveGuide(groups, names, report);
  const table = path.join(runRoot, "ACATE_Timeline.xlsx");
  const moves = path.join(runRoot, "ACATE_Move_Guide.csv");
  await writeTable(loadJSZip(), tableRows(segments, counted, names), table);
  await fs.writeFile(moves, csv(guide), "utf8");
  return { ok: true, table, moves, segments: segments.length, files: guide.length - 1, time_lapses: lapseItems.length,
    no_time: report.noTime, outside: report.outside, renamed: report.renamed };
}

const [command, runRoot] = process.argv.slice(2);
if (command !== "build" || !runRoot) {
  console.error("Usage: node ACATE_Script_Workbook.mjs build <run_root>");
  process.exit(2);
}
try {
  console.log(JSON.stringify(await build(path.resolve(runRoot))));
} catch (error) {
  console.log(JSON.stringify({ ok: false, error: error.message }));
  process.exit(error instanceof BuildError || error instanceof SyntaxError ? 2 : 1);
}
