#!/usr/bin/env python3
"""Episode-local schema, source, fixed-view and saved-preview audit. No API calls."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
BASE = HERE.parent.parent
OUT = BASE / "output" / "lesson-17-gemini"
ERRORS: list[str] = []
WARNINGS: list[str] = []


def check(condition, message):
    if not condition:
        ERRORS.append(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


script_bytes = (HERE / "script.json").read_bytes()
script = json.loads(script_bytes)
turns = script["turns"]
chapters = {c["id"]: c for c in script["chapters"]}
views = defaultdict(list)
source_files = {}
for ref in script["sourceReferences"] + [r for c in chapters.values() for r in c.get("sourceRefs", [])]:
    file = (BASE / ref["file"]).resolve()
    check(file.is_file(), f"Missing source: {ref['file']}")
    if not file.is_file():
        continue
    source_files[ref["file"]] = sha(file.read_bytes())
    if ref.get("anchor"):
        check(bool(re.search(r'\bid=["\']' + re.escape(ref["anchor"]) + r'["\']', file.read_text())),
              f"Missing source anchor: {ref['file']}#{ref['anchor']}")

check(script["visualStyle"] == "teaching-settings-v2", "Unexpected visual style")
check(script["speed"] == 1, "Speech speed differs from 1.0")
check(len({t["id"] for t in turns}) == len(turns), "Duplicate turn IDs")
check(set(t["speaker"] for t in turns) == {"mako", "manabu", "ikumi"}, "Unexpected cast")
check(set(chapters) == set(t["chapterId"] for t in turns), "Unused/undefined chapter")
check(all(c.get("goal") and c.get("sourceRefs") for c in chapters.values()), "Chapter lacks goal/source references")
quiz_questions = []
body_turns = []
svg_files = {}
for turn in turns:
    tid = turn["id"]
    check(turn["chapterId"] in chapters, f"{tid}: unknown chapter")
    check(all(turn.get(k) for k in ("speech", "caption", "subtitleLines", "visual")), f"{tid}: missing required data")
    check("TODO" not in json.dumps(turn, ensure_ascii=False), f"{tid}: authoring placeholder")
    chunks = turn.get("subtitleChunks") or [{"caption": turn["caption"], "lines": turn["subtitleLines"]}]
    check("".join(c["caption"] for c in chunks) == turn["caption"], f"{tid}: chunk captions do not reconstruct caption")
    check(turn["subtitleLines"] == chunks[0]["lines"], f"{tid}: initial lines differ from first chunk")
    for ci, chunk in enumerate(chunks):
        check(1 <= len(chunk["lines"]) <= 2, f"{tid} chunk {ci}: subtitle line count")
        check("".join(chunk["lines"]) == chunk["caption"], f"{tid} chunk {ci}: lines omit/alter caption")
        check(all(isinstance(line, str) and line for line in chunk["lines"]), f"{tid} chunk {ci}: empty line")
        check(chunk.get("weight", 1) > 0, f"{tid} chunk {ci}: nonpositive timing weight")
        check(all(len(line) <= 40 for line in chunk["lines"]), f"{tid} chunk {ci}: excessive subtitle length")
    visual = turn["visual"]
    if turn["chapterId"] == "opening":
        check(visual["layout"] == "hologram", f"{tid}: opening layout")
        continue
    body_turns.append(turn)
    check(visual.get("viewId"), f"{tid}: missing stable viewId")
    check(visual["layout"] in {"settings", "svg"}, f"{tid}: unexpected body layout")
    check(not any(k in visual for k in ("zoom", "camera", "motion", "azureMotion", "pulse", "pointer")),
          f"{tid}: body motion/pointer directive")
    views[visual.get("viewId")].append(turn)
    if turn["speaker"] == "mako":
        check(not visual.get("highlightFields") and not visual.get("highlightRows"), f"{tid}: question reveals highlight")
        if turn["chapterId"] == "check":
            quiz_questions.append(tid)
    if visual["layout"] == "settings":
        check(bool(visual.get("table")) != bool(visual.get("fields")), f"{tid}: require table xor fields")
        check(len(visual.get("menu", [])) <= 8, f"{tid}: menu overflow")
        rows = visual.get("fields", visual.get("table", {}).get("rows", []))
        row_ids = [row["id"] for row in rows]
        check(1 <= len(rows) <= 6 and len(row_ids) == len(set(row_ids)), f"{tid}: invalid row set")
        for key in ("highlightFields", "highlightRows"):
            check(set(visual.get(key, [])) <= set(row_ids), f"{tid}: unknown highlight ID")
        if visual.get("table"):
            columns = visual["table"]["columns"]
            check(2 <= len(columns) <= 5, f"{tid}: invalid column count")
            check(all(len(row["cells"]) == len(columns) for row in rows), f"{tid}: column/cell mismatch")
            if all("width" in col for col in columns):
                check(sum(col["width"] for col in columns) == 972, f"{tid}: invalid table width")
    else:
        svg_path = (HERE / visual["svg"]).resolve()
        check(svg_path.is_relative_to(HERE) and svg_path.is_file(), f"{tid}: SVG outside episode/missing")
        if svg_path.is_file():
            content = svg_path.read_bytes()
            root = ET.fromstring(content)
            check(root.attrib.get("viewBox") == "0 0 1207 511", f"{tid}: SVG viewport")
            check(not re.search(rb"<script\b|<foreignObject\b|<!DOCTYPE|<!ENTITY|<animate\b", content, re.I), f"{tid}: unsafe/moving SVG")
            svg_files[visual["svg"]] = sha(content)

view_report = []
for view_id, group in views.items():
    neutral = [{k: v for k, v in t["visual"].items() if k not in ("highlightRows", "highlightFields")} for t in group]
    check(all(v == neutral[0] for v in neutral), f"{view_id}: content/geometry changes within view")
    view_report.append({"viewId": view_id, "turnIds": [t["id"] for t in group],
                        "canonicalVisualSha256": sha(json.dumps(neutral[0], ensure_ascii=False, sort_keys=True).encode()),
                        "contentAndGeometryHeld": all(v == neutral[0] for v in neutral)})
check(len(quiz_questions) == 3, "Expected three neutral quiz questions")

# Verify every saved 1600x900 preview against a fresh in-memory raster of this
# exact script. Separately neutralize only row/field highlights and compare the
# 1207x511 body crop per view; no screenshots/files/API requests are created.
NODE = r'''
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import sharp from 'sharp';
import {renderLessonRaster} from './lesson-visual.mjs';
const episode=process.argv[1],s=JSON.parse(await fs.readFile(path.join(episode,'script.json'),'utf8'));
const output=path.resolve('output/lesson-17-gemini/previews');
const digest=b=>crypto.createHash('sha256').update(b).digest('hex');
const crop=async b=>sharp(b).extract({left:65,top:204,width:1207,height:511}).ensureAlpha().raw().toBuffer();
const rows=[],views=new Map();
for(const t of s.turns){
 const file=path.join(output,t.id+'.png');
 const actual=await fs.readFile(file),meta=await sharp(actual).metadata();
 const visual={...t.visual};
 if(visual.layout==='svg'){
  const bytes=await fs.readFile(path.join(episode,visual.svg));
  visual.svgSha256=digest(bytes);visual.svgDataUri='data:image/svg+xml;base64,'+bytes.toString('base64');
 }
 const args={phase:t.chapterId==='opening'?'opening':'body',episodeNumber:s.episodeNumber,episodeLabel:s.episodeLabel??`第${s.episodeNumber}回`,seriesLabel:s.seriesLabel,title:s.topic,chapter:s.chapters.find(c=>c.id===t.chapterId),speaker:t.speaker,visual,subtitleLines:t.subtitleChunks?.[0]?.lines??t.subtitleLines??[t.caption],turnTime:2,time:6};
 const expected=await renderLessonRaster(args);
 const actualFull=await sharp(actual).ensureAlpha().raw().toBuffer();
 const expectedFull=await sharp(expected).ensureAlpha().raw().toBuffer();
 const row={id:t.id,width:meta.width,height:meta.height,pngSha256:digest(actual),fullFrameMatchesCurrentScript:actualFull.equals(expectedFull)};
 if(args.phase==='body'){
  row.bodyCropMatchesCurrentScript=(await crop(actual)).equals(await crop(expected));
  const neutral={...visual};delete neutral.highlightRows;delete neutral.highlightFields;
  const neutralBuffer=await renderLessonRaster({...args,visual:neutral});
  row.neutralBodyCropSha256=digest(await crop(neutralBuffer));
  const prev=views.get(visual.viewId);
  row.neutralGeometryMatchesView=prev===undefined||prev===row.neutralBodyCropSha256;
  views.set(visual.viewId,row.neutralBodyCropSha256);
 }
 rows.push(row);
}
const establish=await sharp(await fs.readFile(path.join(output,'cast-establish.png'))).metadata();
console.log(JSON.stringify({turnPreviews:rows,castEstablish:{width:establish.width,height:establish.height},neutralBodyViewport:{x:65,y:204,width:1207,height:511},networkRequestsMade:false}));
'''
try:
    proc = subprocess.run(["node", "--input-type=module", "-e", NODE, str(HERE)], cwd=BASE,
                          capture_output=True, text=True, timeout=150)
    check(proc.returncode == 0, "Preview raster audit failed: " + proc.stderr[-1500:])
    raster = json.loads(proc.stdout) if proc.returncode == 0 else {}
except (subprocess.TimeoutExpired, json.JSONDecodeError) as error:
    ERRORS.append(f"Preview raster audit failed: {type(error).__name__}")
    raster = {}
for row in raster.get("turnPreviews", []):
    check((row["width"], row["height"]) == (1600, 900), f"{row['id']}: preview resolution")
    if not row["fullFrameMatchesCurrentScript"]:
        WARNINGS.append(f"{row['id']}: saved full preview is stale relative to current script; regenerate previews")
    if "bodyCropMatchesCurrentScript" in row:
        check(row["bodyCropMatchesCurrentScript"], f"{row['id']}: saved body preview differs from current script")
        check(row["neutralGeometryMatchesView"], f"{row['id']}: neutral body raster changes within view")
check(len(raster.get("turnPreviews", [])) == len(turns), "Saved preview coverage incomplete")
check(script_bytes == (HERE / "script.json").read_bytes(), "Script changed during QA; rerun against stable input")

result = {
    "status": "passed" if not ERRORS else "failed",
    "checkedAt": datetime.now(timezone.utc).isoformat(), "episodeKey": "17",
    "scriptSha256": sha(script_bytes), "turnCount": len(turns),
    "bodyTurnCount": len(body_turns), "bodyViewCount": len(views),
    "captionCharacters": sum(len(t["caption"]) for t in turns),
    "speechCharacters": sum(len(t["speech"]) for t in turns),
    "subtitleChunkCount": sum(len(t.get("subtitleChunks", [])) or 1 for t in turns),
    "subtitleContentComplete": not any("caption" in e or "subtitle" in e for e in ERRORS),
    "bodyCamera": "fixed", "quizQuestionIds": quiz_questions,
    "allQuizQuestionsHaveNeutralHighlights": not any("question reveals" in e for e in ERRORS),
    "views": view_report, "sourceFileSha256": source_files, "svgSha256": svg_files,
    "previewRasterAudit": raster,
    "continuousPreviewCandidates": [
        {"fromTurn": "lesson17-turn-030-mako", "throughTurn": "lesson17-turn-034-manabu", "viewId": "lesson17-delete"},
        {"fromTurn": "lesson17-turn-021-mako", "throughTurn": "lesson17-turn-024-ikumi", "viewId": "lesson17-transfer"},
    ],
    "errors": ERRORS, "warnings": WARNINGS, "networkRequestsMade": False,
    "scope": "Source/schema/subtitle and saved-preview raster checks. Audio generation, final video, and listening are separate checks.",
}
(HERE / "qa-structure.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({k: result[k] for k in ("status", "turnCount", "bodyTurnCount", "bodyViewCount", "subtitleChunkCount", "errors", "warnings")}, ensure_ascii=False))
sys.exit(bool(ERRORS))
