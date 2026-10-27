#!/usr/bin/env python3
"""Batch-publish ARI skill variants to ClawHub via the HTTP API (no GitHub, no CLI).

API: POST https://clawhub.ai/api/v1/skills  (multipart)
  - payload: JSON part {slug, displayName, version, changelog, tags, acceptLicenseTerms}
  - files:   repeated parts, one per file, filename = path relative to skill dir

Token comes from env CLAWHUB_TOKEN (never echoed).
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

DIST = Path(r"E:\Code\ari\docs\dist")
API = "https://clawhub.ai/api/v1/skills"
SUFFIX = "1.4.1"
TOKEN = os.environ.get("CLAWHUB_TOKEN", "").strip()
DELAY = 3          # seconds between publishes
MAX_ATTEMPTS = 3
RESULTS = Path(r"C:\Users\Tiktok\WorkBuddy\2026-08-12-21-10-26\clawhub_results.json")

if not TOKEN:
    print("ERROR: CLAWHUB_TOKEN env var not set", file=sys.stderr)
    sys.exit(2)


def fm_value(skill_md: Path, key: str) -> str:
    """Naive frontmatter line parse (same approach the skillhub CLI uses)."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except Exception:
        return ""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return ""
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            break
        m = re.match(r"^%s:\s*(.+)$" % re.escape(key), lines[i])
        if m:
            return m.group(1).strip()
    return ""


# ---- load already-done (resume support) ----
done = {}
if RESULTS.is_file():
    try:
        for r in json.loads(RESULTS.read_text(encoding="utf-8"))["results"]:
            if r[1] in ("PASS", "EXISTS"):
                done[r[0]] = r[2]
    except Exception:
        pass

folders = sorted([p for p in DIST.iterdir() if p.is_dir() and p.name.endswith(SUFFIX)])
todo = [f for f in folders if f.name not in done]
print(f"=== clawhub publish: total={len(folders)} todo={len(todo)} skip={len(folders)-len(todo)} ===", flush=True)

results = list(done.items()) and []  # fresh list; we'll rewrite file at end
results = []
for name, out in done.items():
    results.append((name, "PASS", out))

for f in todo:
    meta_path = f / "_meta.json"
    if not meta_path.is_file():
        results.append((f.name, "FAIL", "no _meta.json"))
        continue
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    slug = meta.get("slug", "").strip() or f.name[: -len("-" + SUFFIX)]
    version = meta.get("version", "").strip() or SUFFIX
    display = fm_value(f / "SKILL.md", "displayName") or slug

    payload = {
        "slug": slug,
        "displayName": display,
        "version": version,
        "changelog": "v1.4.1 release",
        "tags": ["latest"],
        "acceptLicenseTerms": True,
    }

    cmd = ["curl", "-s", "-X", "POST", API,
           "-H", "Authorization: Bearer %s" % TOKEN,
           "-F", "payload=%s;type=application/json" % json.dumps(payload, ensure_ascii=False)]
    # cd into the skill dir so relative paths work (Windows curl rejects /e/... paths)
    files = sorted([p for p in f.rglob("*") if p.is_file()])
    for p in files:
        rel = p.relative_to(f).as_posix()
        cmd += ["-F", "files=@%s;filename=%s" % (rel, rel)]
    cmd += ["-w", "\nHTTP=%{http_code}"]

    last_out = ""
    ok = False
    recorded = False   # guard: never append the same skill twice
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            proc = subprocess.run(cmd, cwd=str(f), capture_output=True, text=True,
                                  timeout=180, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            last_out = "TIMEOUT"
            time.sleep(DELAY * attempt)
            continue
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        last_out = out.replace("\n", " | ")
        code = 0
        m = re.search(r"HTTP=(\d+)", last_out)
        if m:
            code = int(m.group(1))
        body = re.sub(r"\s*HTTP=\d+", "", last_out).strip()
        low = body.lower()
        if code == 200 and '"ok":true' in body.replace(" ", ""):
            ok = True
            results.append((f.name, "PASS", body[:300]))
            print(f"PASS  {f.name} ({slug}@{version}): {body[:160]}", flush=True)
            break
        if "already exists" in low or "已存在" in body or "unchanged" in low:
            ok = True
            results.append((f.name, "EXISTS", body[:300]))
            print(f"EXISTS {f.name} ({slug}): {body[:160]}", flush=True)
            break
        retryable = code in (429, 500, 502, 503, 504) or "429" in body or "rate" in low
        if retryable and attempt < MAX_ATTEMPTS:
            wait = DELAY * attempt * 3
            print(f"  retry {f.name} (http={code}); wait {wait}s", flush=True)
            time.sleep(wait)
            continue
        results.append((f.name, "FAIL", "http=%s %s" % (code, body[:300])))
        recorded = True
        print(f"FAIL  {f.name} ({slug}): http={code} {body[:200]}", flush=True)
        break
    if not ok and not recorded:
        results.append((f.name, "FAIL", last_out[:300]))
    time.sleep(DELAY)

passed = sum(1 for r in results if r[1] in ("PASS", "EXISTS"))
failed = [r for r in results if r[1] == "FAIL"]
print(f"\n=== SUMMARY: total={len(results)} ok={passed} fail={len(failed)} ===", flush=True)
for r in failed:
    print("  FAILED:", r[0], "->", r[2][:200], flush=True)
RESULTS.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
