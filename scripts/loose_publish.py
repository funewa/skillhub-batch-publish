#!/usr/bin/env python3
"""Publish 1.4.7 updates for the loose (non-batch) SkillHub slugs.

Each loose slug is an early manual copy of a batch dist (name matches the
batch dist's frontmatter `name`). We stage a copy of the matching 1.4.7 dist,
rewrite frontmatter slug -> the loose slug, inject current displayName/summary
from 上架清单.md, and publish via the skillhub CLI.

Excluded: `voc-ai` (download 404 "Version not found"; alipay pay-per-use
product line, not an ARI-API skill) and the 8 ai-*/aitop skills (different
project) and the 6 frozen shadowed copies.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

PY = os.environ.get("SKILLHUB_PY", r"C:\Users\Tiktok\.workbuddy\binaries\python\versions\3.13.12\python.exe")
CLI = os.environ.get("SKILLHUB_CLI", r"C:\Users\Tiktok\.skillhub\skills_store_cli.py")

def _arg(name, default=None):
    args = sys.argv[1:]
    return args[args.index(name) + 1] if name in args else default

SUFFIX = _arg("--suffix", "1.4.8")
WT = _arg("--worktree", r"E:/Code/ari/.claude/worktrees/ux-visual-comparison-154705")
DIST = Path(WT) / "docs" / "dist"
MASTER = Path(WT) / "docs" / ("amazon-review-intelligence-%s" % SUFFIX)
CHECKLIST = DIST / "上架清单.md"
# unique stage dir per run: safe-delete policy blocks reusing an existing dir
STAGE = Path(r"C:/Users/Tiktok/WorkBuddy/2026-08-12-21-10-26/_loose_%s_%s" % (SUFFIX.replace(".", ""), time.strftime("%m%d%H%M%S")))
VER = SUFFIX
CHANGELOG = "v%s 发布" % VER
LICENSE = "MIT"
DELAY = 20
DRY_RUN = "--dry-run" in sys.argv

# loose slug -> (source dist channel or "master",)
PLAN = {
    "amazon-review-intelligence": "master",
    "amazon-review-intelligence-extractor": "master",
    "amazon-voc-us": "master",
    "ari-amazon-voc": "amazon-voc",
    "amz-voc": "amazon-voc",
    "review-analyzer-pro": "amazon-voc",
    "amazon-user-persona": "user-persona",
    "amazon-product-research": "product-research",
    "review-radar-amazon": "review-alerts",
    "voc-insights": "voc-insight",
}
TARGET_KEYS = ("slug", "displayName", "version", "summary", "license")

# --name-map JSON: {loose_slug: displayName} overrides the checklist name.
NAME_MAP = {}
_nm = _arg("--name-map")
if _nm:
    NAME_MAP = json.loads(Path(_nm).read_text(encoding="utf-8")) if Path(_nm).is_file() else json.loads(_nm)


def parse_checklist(path: Path):
    text = path.read_text(encoding="utf-8")
    mapping = {}
    cur = {}
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"- 上传包：`([^`]+)`", line)
        if m:
            cur["zip"] = Path(m.group(1)).name
            continue
        m = re.match(r"- 展示名：(.+)", line)
        if m:
            cur["displayName"] = m.group(1).strip().strip("`").strip()
            continue
        m = re.match(r"- 一句话简介：(.+)", line)
        if m:
            cur["summary"] = m.group(1).strip().strip("`").strip()
            continue
        if line.startswith("## ") and cur.get("zip"):
            mapping[cur["zip"]] = (cur.get("displayName", ""), cur.get("summary", ""))
            cur = {}
    if cur.get("zip"):
        mapping[cur["zip"]] = (cur.get("displayName", ""), cur.get("summary", ""))
    return mapping


checklist = parse_checklist(CHECKLIST)
MODE = "DRY-RUN" if DRY_RUN else "PUBLISH"
print("=== loose slugs %s : %d targets ===" % (MODE, len(PLAN)), flush=True)

# --- stage + inject frontmatter ---
results = []
problems = []
STAGE.mkdir(parents=True, exist_ok=False)  # fresh unique dir, nothing to clean

for slug, src in PLAN.items():
    src_dir = MASTER if src == "master" else DIST / ("%s-%s" % (src, VER))
    if not src_dir.is_dir():
        problems.append("%s: source missing %s" % (slug, src_dir))
        continue
    disp, summ = checklist.get(("%s-%s.zip" % (src, VER)) if src != "master" else "sellerari-%s.zip" % VER, ("", ""))
    if not disp or not summ:
        # master uses the main listing's info
        disp, summ = checklist.get("sellerari-%s.zip" % VER, ("", ""))
    if slug in NAME_MAP:
        print("  name-map: %s displayName -> %s" % (slug, NAME_MAP[slug]), flush=True)
        disp = NAME_MAP[slug]
    if not disp or not summ:
        problems.append("%s: checklist info missing (src=%s)" % (slug, src))
        continue
    stage = STAGE / ("%s-%s" % (slug, VER))
    shutil.copytree(src_dir, stage)
    # _meta.json
    meta_p = stage / "_meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.is_file() else {"ownerId": "583963"}
    meta["slug"] = slug
    meta["version"] = VER
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    # SKILL.md frontmatter
    md = stage / "SKILL.md"
    lines = md.read_text(encoding="utf-8").split("\n")
    if not lines or lines[0].strip() != "---":
        problems.append("%s: no frontmatter in source" % slug)
        continue
    end = next((j for j in range(1, len(lines)) if lines[j].strip() == "---"), -1)
    if end < 0:
        problems.append("%s: unterminated frontmatter" % slug)
        continue
    body = [ln for ln in lines[1:end]
            if not (re.match(r"^([A-Za-z0-9_\-]+)\s*:", ln) and re.match(r"^([A-Za-z0-9_\-]+)\s*:", ln).group(1) in TARGET_KEYS)]
    body += ["slug: %s" % slug, "displayName: %s" % disp, "version: %s" % VER,
             "summary: %s" % summ, "license: %s" % LICENSE]
    md.write_text("---\n" + "\n".join(body) + "\n---\n" + "\n".join(lines[end + 1:]), encoding="utf-8")
    print("staged %-38s <- %-24s disp=%s" % (slug, src, disp[:24]), flush=True)

if problems:
    print("\n=== PROBLEMS ===", flush=True)
    for p in problems:
        print("  !", p, flush=True)

# --- publish ---
print(flush=True)
for slug in PLAN:
    stage = STAGE / ("%s-%s" % (slug, VER))
    if not stage.is_dir():
        results.append((slug, "SKIP", "not staged"))
        continue
    last_out = ""
    ok = False
    if DRY_RUN:
        cmd = [PY, CLI, "publish", str(stage), "--changelog", CHANGELOG, "--dry-run"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8")
        except subprocess.TimeoutExpired:
            results.append((slug, "TIMEOUT", ""))
            print("TIMEOUT", slug, flush=True)
            continue
        out = ((proc.stdout or "") + (proc.stderr or "")).strip().replace("\n", " | ")
        ok = proc.returncode == 0
        results.append((slug, "PASS" if ok else "FAIL", out[:300]))
        print(("PASS " if ok else "FAIL "), slug, ":", out[:160], flush=True)
    else:
        for attempt in range(1, 6):
            cmd = [PY, CLI, "publish", str(stage), "--changelog", CHANGELOG]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8")
            except subprocess.TimeoutExpired:
                last_out = "TIMEOUT"
                time.sleep(DELAY * attempt)
                continue
            out = ((proc.stdout or "") + (proc.stderr or "")).strip().replace("\n", " | ")
            last_out = out
            if proc.returncode == 0 or "已存在" in out:
                ok = True
                results.append((slug, "PASS", out[:300]))
                print("PASS  %-38s (att %d): %s" % (slug, attempt, out[:160]), flush=True)
                break
            retryable = ("频率过高" in out or "过于频繁" in out or "服务端错误" in out
                         or "524" in out or "timeout" in out.lower() or "TIMEOUT" in out)
            if retryable and attempt < 5:
                wait = DELAY * attempt * 4
                print("  transient %s (att %d); wait %ss" % (slug, attempt, wait), flush=True)
                time.sleep(wait)
                continue
            results.append((slug, "FAIL", out[:300]))
            print("FAIL  %-38s: %s" % (slug, out[:200]), flush=True)
            break
        if not ok and not any(r[0] == slug for r in results):
            results.append((slug, "FAIL", last_out[:300]))
        time.sleep(DELAY)

passed = sum(1 for r in results if r[1] == "PASS")
print("\n=== SUMMARY %s: total=%d pass=%d fail=%d ===" % (MODE, len(results), passed, len(results) - passed), flush=True)
out_name = "loose_results_%s_dry.json" % SUFFIX if DRY_RUN else "loose_results_%s.json" % SUFFIX
Path(out_name).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print("wrote", out_name, flush=True)
