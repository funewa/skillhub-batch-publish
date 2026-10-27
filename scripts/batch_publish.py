#!/usr/bin/env python3
"""Batch-publish versioned SkillHub skills from a dist directory.

Reusable driver for the "publish a whole versioned skill set to SkillHub" workflow.
Handles the two Windows/Git-Bash gotchas and the server rate-limit automatically.

Steps per folder <dist>/*-<suffix>:
  1. Inject SkillHub frontmatter into SKILL.md (idempotent):
       slug, version        <- _meta.json
       displayName, summary <- 上架清单.md (matched by "<folder>.zip" basename)
       license              <- --license (default MIT)
     Existing name/description preserved; only the 5 publish keys are (re)written.
  2. (unless --dry-run) Publish via the skillhub CLI with spacing + rate-limit retry.

Usage:
  python3 batch_publish.py --dist "<DIR>" --suffix 1.4.1 [--dry-run] [--changelog "..."]

Writes results_<suffix>.json and publish_manifest_<suffix>.md into CWD.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# --- environment-specific (stable on this machine; override via env if needed) ---
PY = os.environ.get("SKILLHUB_PY", r"C:\Users\Tiktok\.workbuddy\binaries\python\versions\3.13.12\python.exe")
CLI = os.environ.get("SKILLHUB_CLI", r"C:\Users\Tiktok\.skillhub\skills_store_cli.py")

# --- args ---
args = sys.argv[1:]
DIST = None
SUFFIX = None
CHANGELOG = "v1.4.1 发布"
LICENSE = "MIT"
DRY_RUN = "--dry-run" in args
DELAY = 20
COOLDOWN = 0
MAX_ATTEMPTS = 5
ONLY = None          # restrict to these folder base names (comma-separated, no "-<suffix>")
# Persistent SkillHub-only rename map (2026-09-03). These 6 bare slugs are already
# registered by OTHER owners, so our @user_507a5056/<slug> copy is shadowed (bare-slug
# lookup hits theirs). We re-publish under a unique `ari-<slug>` on SkillHub only;
# ClawHub keeps the original slug (renaming there = new slug = template-spam block).
# Mirrors the launch-readiness -> amazon-launch-readiness precedent (SLUG_FALLBACK).
PERSISTENT_SLUG_MAP = {
    "brand-monitor": "ari-brand-monitor",
    "faq-generator": "ari-faq-generator",
    "listing-optimizer": "ari-listing-optimizer",
    "product-page-audit": "ari-product-page-audit",
    "product-positioning": "ari-product-positioning",
    "product-research": "ari-product-research",
}
SLUG_MAP = dict(PERSISTENT_SLUG_MAP)  # --slug-map JSON merges on top
NAME_MAP = {}        # --name-map JSON: {channel: displayName} overrides the checklist name
OUT = None           # results/manifest filename infix; defaults to SUFFIX
i = 0
while i < len(args):
    a = args[i]
    if a == "--dist":
        DIST = Path(args[i + 1]); i += 2; continue
    if a == "--suffix":
        SUFFIX = args[i + 1]; i += 2; continue
    if a == "--changelog":
        CHANGELOG = args[i + 1]; i += 2; continue
    if a == "--license":
        LICENSE = args[i + 1]; i += 2; continue
    if a == "--delay":
        DELAY = int(args[i + 1]); i += 2; continue
    if a == "--max-attempts":
        MAX_ATTEMPTS = int(args[i + 1]); i += 2; continue
    if a == "--cooldown":
        COOLDOWN = int(args[i + 1]); i += 2; continue
    if a == "--only":
        ONLY = [x.strip() for x in args[i + 1].split(",") if x.strip()]; i += 2; continue
    if a == "--slug-map":
        SLUG_MAP.update(json.loads(args[i + 1])); i += 2; continue
    if a == "--name-map":
        NAME_MAP.update(json.loads(Path(args[i + 1]).read_text(encoding="utf-8"))
                        if Path(args[i + 1]).is_file() else json.loads(args[i + 1]))
        i += 2; continue
    if a == "--out":
        OUT = args[i + 1]; i += 2; continue
    i += 1

if not DIST or not SUFFIX:
    print("ERROR: --dist and --suffix are required", file=sys.stderr)
    sys.exit(2)

TARGET_KEYS = ("slug", "displayName", "version", "summary", "license")
CHECKLIST = DIST / "上架清单.md"
MODE = "DRY-RUN" if DRY_RUN else "PUBLISH"


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
folders = sorted([p for p in DIST.iterdir() if p.is_dir() and p.name.endswith(SUFFIX)])
if ONLY:
    wanted = set()
    for o in ONLY:
        wanted.add(o)
        wanted.add(f"{o}-{SUFFIX}")
    tail = len("-" + SUFFIX)
    folders = [f for f in folders if f.name in wanted or f.name[:-tail] in wanted]
    missing = [o for o in ONLY
               if o not in {f.name[:-tail] for f in folders}]
    for m in missing:
        print(f"  ! --only entry not found in dist: {m}", flush=True)
print(f"=== {MODE} : {len(folders)} skills (*-{SUFFIX}) ===", flush=True)
if SLUG_MAP:
    print(f"    slug-map active: {SLUG_MAP}", flush=True)

problems = []
# --- 1. inject frontmatter ---
for folder in folders:
    skill_md = folder / "SKILL.md"
    if not skill_md.is_file():
        problems.append(f"{folder.name}: no SKILL.md")
        continue
    meta = {}
    meta_path = folder / "_meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as e:
            problems.append(f"{folder.name}: _meta.json parse error {e}")
    slug = (meta.get("slug") or "").strip()
    version = (meta.get("version") or "").strip()
    if not slug:
        slug = folder.name[: -len("-" + SUFFIX)]
        problems.append(f"{folder.name}: slug from folder name ({slug})")
    if not version:
        version = SUFFIX
    if slug in SLUG_MAP:
        print(f"  slug-map: {slug} -> {SLUG_MAP[slug]}", flush=True)
        slug = SLUG_MAP[slug]
    zip_base = folder.name + ".zip"
    disp, summ = checklist.get(zip_base, ("", ""))
    channel = folder.name[: -len("-" + SUFFIX)]
    if channel in NAME_MAP:
        print(f"  name-map: {channel} displayName -> {NAME_MAP[channel]}", flush=True)
        disp = NAME_MAP[channel]
    if not disp or not summ:
        problems.append(f"{folder.name}: checklist missing (zip={zip_base})")
    text = skill_md.read_text(encoding="utf-8")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        problems.append(f"{folder.name}: no frontmatter")
        continue
    end = -1
    for j in range(1, len(lines)):
        if lines[j].strip() == "---":
            end = j
            break
    if end < 0:
        problems.append(f"{folder.name}: frontmatter unterminated")
        continue
    body = lines[1:end]
    new_body = []
    for ln in body:
        m = re.match(r"^([A-Za-z0-9_\-]+)\s*:", ln)
        if m and m.group(1) in TARGET_KEYS:
            continue
        new_body.append(ln)
    new_body += [f"slug: {slug}", f"displayName: {disp}", f"version: {version}",
                 f"summary: {summ}", f"license: {LICENSE}"]
    skill_md.write_text("---\n" + "\n".join(new_body) + "\n---\n" + "\n".join(lines[end + 1:]), encoding="utf-8")

if problems:
    print("\n=== FRONTMATTER PROBLEMS ===", flush=True)
    for p in problems:
        print("  !", p, flush=True)
else:
    print(f"Frontmatter injected/verified for all {len(folders)} (no problems).", flush=True)

# --- 2. dry-run or publish ---
if COOLDOWN and not DRY_RUN:
    print(f"=== cooldown {COOLDOWN}s before publishing (rate-limit recovery) ===", flush=True)
    time.sleep(COOLDOWN)
results = []
for f in folders:
    win = str(f)
    last_out = ""
    ok = False
    if DRY_RUN:
        cmd = [PY, CLI, "publish", win, "--changelog", CHANGELOG, "--dry-run"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8")
        except subprocess.TimeoutExpired:
            results.append((f.name, "TIMEOUT", ""))
            print(f"TIMEOUT {f.name}", flush=True)
            continue
        out = (proc.stdout or "") + (proc.stderr or "")
        last_out = out.strip().replace("\n", " | ")
        ok = proc.returncode == 0
        results.append((f.name, "PASS" if ok else "FAIL", last_out))
        print(f"{'PASS' if ok else 'FAIL'} {f.name}: {last_out}", flush=True)
    else:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            cmd = [PY, CLI, "publish", win, "--changelog", CHANGELOG]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8")
            except subprocess.TimeoutExpired:
                last_out = "TIMEOUT"
                time.sleep(DELAY)
                continue
            out = (proc.stdout or "") + (proc.stderr or "")
            last_out = out.strip().replace("\n", " | ")
            if proc.returncode == 0:
                ok = True
                results.append((f.name, "PASS", last_out))
                print(f"PASS  {f.name} (att {attempt}): {last_out}", flush=True)
                break
            # "版本 X 已存在" = same-version re-publish is REJECTED by SkillHub.
            # It is only safe to treat as published when the local package has no
            # pending change; when a rename/version bump is the whole point of the
            # run, a conflict means the change did NOT land -> hard FAIL.
            if "已存在" in out:
                if "--allow-exists-pass" in args:
                    ok = True
                    results.append((f.name, "PASS", last_out + " (already-exists -> treated as published)"))
                    print(f"PASS  {f.name} (att {attempt}, already exists): {last_out}", flush=True)
                else:
                    results.append((f.name, "FAIL", last_out + " (version exists -> change NOT applied)"))
                    print(f"FAIL  {f.name} (version exists, change not applied): {last_out}", flush=True)
                break
            retryable = ("频率过高" in out or "过于频繁" in out or "服务端错误" in out
                         or "524" in out or "timeout" in out.lower() or "TIMEOUT" in out)
            if retryable:
                wait = DELAY * attempt * 4
                # 把 CLI 原始报错片段带上（脱敏后）：上层熔断/人工判断需要区分
                # 「良性 524」与「发布频率过高」——后者是账号级配额，必须立刻停手，
                # 再试只会把恢复时间往后推。此前只打印 transient 行，上层拿不到原因。
                snippet = last_out[:200].replace("\n", " ")
                for _k in ("token", "bearer", "authorization"):
                    snippet = re.sub(_k, _k[:2] + "***", snippet, flags=re.I)
                print(f"  transient {f.name} (att {attempt}); wait {wait}s... [{snippet}]", flush=True)
                time.sleep(wait)
                continue
            results.append((f.name, "FAIL", last_out))
            print(f"FAIL  {f.name}: {last_out}", flush=True)
            break
        if not ok and (f.name, "FAIL", last_out) not in results:
            results.append((f.name, "FAIL", last_out))
        time.sleep(DELAY)

# --- 3. retry slug-conflict skills with a fallback slug (launch-readiness -> amazon-launch-readiness) ---
SLUG_FALLBACK = {"launch-readiness": "amazon-launch-readiness"}
conflicts = [(n, st, msg) for (n, st, msg) in results if st == "FAIL"
             and ("slug 冲突" in msg or "skill already exists" in msg.lower()
                  or "slug conflict" in msg.lower())]
if conflicts:
    print("\n=== SLUG CONFLICT RETRY (%d) ===" % len(conflicts), flush=True)
    for n, st, msg in conflicts:
        base = n[: -len("-" + SUFFIX)]
        fb = SLUG_FALLBACK.get(base)
        if not fb:
            print("  skip (no fallback) %s" % n, flush=True)
            continue
        skill_md = DIST / n / "SKILL.md"
        txt = skill_md.read_text(encoding="utf-8")
        new = re.sub(r"(?m)^slug:\s*.+$", "slug: %s" % fb, txt, count=1)
        if new == txt:
            print("  skip (slug not rewritten) %s" % n, flush=True)
            continue
        skill_md.write_text(new, encoding="utf-8")
        win = str(DIST / n)
        cmd = [PY, CLI, "publish", win, "--changelog", CHANGELOG]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8")
        out = (proc.stdout or "") + (proc.stderr or "")
        # Same trap as the main loop: "已存在" here means the fallback slug ALREADY
        # carries this version, i.e. the re-slug did NOT land. Only lenient when
        # the caller explicitly opts in for a pure-524-compensation run.
        if "已存在" in out and "--allow-exists-pass" in args:
            ok2 = True
        else:
            ok2 = proc.returncode == 0
        # replace the prior FAIL entry
        results = [(x, y, z) for (x, y, z) in results if not (x == n and y == "FAIL")]
        results.append((n, "PASS" if ok2 else "FAIL", out.strip().replace("\n", " | ")[:300]))
        print("%-6s %s -> %s" % ("PASS" if ok2 else "FAIL", n, fb), flush=True)
        time.sleep(DELAY)

passed = sum(1 for r in results if r[1] == "PASS")
print(f"\n=== SUMMARY {MODE}: total={len(results)} pass={passed} fail={len(results)-passed} ===", flush=True)
out_name = OUT or SUFFIX
Path(f"results_{out_name}.json").write_text(
    json.dumps({"mode": MODE, "suffix": SUFFIX, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
sys.exit(0 if passed == len(results) else 2)
