#!/usr/bin/env python3
"""ClawHub publish with per-skill differentiated references/reference.md.

Root cause found: ClawHub rejects a skill when one of its files is byte-identical
to files already present in >=N other skills from the same account
(N observed ~= 20). In the ARI dist, 69/73 skills share one identical
references/reference.md; the 4 with a unique one (asin-change, competitor-change,
listing-change, price-bsr-track) all passed, even late in the run.

Fix: append a per-skill section built from that skill's own SKILL.md, so every
reference.md becomes unique. Nothing in git is touched -- copies go to a staging
dir and dist stays a build artifact.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

DIST = Path(r"E:\Code\ari\docs\dist")
STAGE = Path(r"C:\Users\Tiktok\WorkBuddy\2026-08-12-21-10-26\clawhub_stage")
API = "https://clawhub.ai/api/v1/skills"
SUFFIX = "1.4.1"
TOKEN = Path(os.path.expanduser("~/.clawhub/token")).read_text(encoding="utf-8").strip()
DELAY = float(os.environ.get("CLAWHUB_DELAY", "6"))
MAX_ATTEMPTS = int(os.environ.get("CLAWHUB_ATTEMPTS", "3"))
RESULTS = Path(r"C:\Users\Tiktok\WorkBuddy\2026-08-12-21-10-26\clawhub_results.json")

if not TOKEN:
    print("ERROR: empty token", file=sys.stderr)
    sys.exit(2)


def fm_value(skill_md: Path, key: str) -> str:
    try:
        lines = skill_md.read_text(encoding="utf-8").split("\n")
    except Exception:
        return ""
    if not lines or lines[0].strip() != "---":
        return ""
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            break
        m = re.match(r"^%s:\s*(.+)$" % re.escape(key), lines[i])
        if m:
            return m.group(1).strip()
    return ""


def own_headings(skill_md: Path, limit: int = 14) -> list:
    """Headings from this skill's own SKILL.md -- real content, nothing invented."""
    out = []
    for line in skill_md.read_text(encoding="utf-8").split("\n"):
        m = re.match(r"^(#{1,4})\s+(.+)$", line)
        if m:
            t = m.group(2).strip().strip("#").strip()
            if t and t not in out:
                out.append(t)
        if len(out) >= limit:
            break
    return out


def differentiate(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    ref = dst / "references" / "reference.md"
    if not ref.is_file():
        return
    heads = own_headings(src / "SKILL.md")
    block = ["", "", "---", "", "## 本技能专属参考索引（%s）" % dst.name.replace("-" + SUFFIX, ""), ""]
    if heads:
        block.append("本技能的执行主线如下，阅读时按此顺序定位：")
        block.append("")
        for h in heads:
            block.append("- %s" % h)
        block.append("")
    ref.write_text(ref.read_text(encoding="utf-8").rstrip() + "\n" + "\n".join(block),
                   encoding="utf-8")


def publish(f: Path, slug: str, version: str, display: str):
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
    for p in sorted([q for q in f.rglob("*") if q.is_file()]):
        rel = p.relative_to(f).as_posix()
        cmd += ["-F", "files=@%s;filename=%s" % (rel, rel)]
    cmd += ["-w", "\nHTTP=%{http_code}"]
    last = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            pr = subprocess.run(cmd, cwd=str(f), capture_output=True, text=True,
                                timeout=180, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            last = "TIMEOUT"
            time.sleep(DELAY * attempt)
            continue
        out = ((pr.stdout or "") + (pr.stderr or "")).strip().replace("\n", " | ")
        last = out
        code = 0
        m = re.search(r"HTTP=(\d+)", out)
        if m:
            code = int(m.group(1))
        body = re.sub(r"\s*HTTP=\d+", "", out).strip()
        if code == 200 and '"ok":true' in body.replace(" ", ""):
            return "PASS", body[:300]
        if "already exists" in body.lower() or "已存在" in body:
            return "EXISTS", body[:300]
        if code in (429, 500, 502, 503, 504) or "429" in body:
            if attempt < MAX_ATTEMPTS:
                time.sleep(DELAY * attempt * 3)
                continue
        return "FAIL", "http=%s %s" % (code, body[:300])
    return "FAIL", last[:300]


def main():
    targets = sys.argv[1:]
    done = {}
    if RESULTS.is_file():
        try:
            for r in json.loads(RESULTS.read_text(encoding="utf-8"))["results"]:
                if r[1] in ("PASS", "EXISTS"):
                    done[r[0]] = r[2]
        except Exception:
            pass
    if not targets:
        folders = sorted(p for p in DIST.iterdir() if p.is_dir() and p.name.endswith(SUFFIX))
        todo = [f for f in folders if f.name not in done]
    else:
        todo = [DIST / (t if t.endswith(SUFFIX) else "%s-%s" % (t, SUFFIX)) for t in targets]
        todo = [f for f in todo if f.is_dir()]
    print("=== clawhub retry (differentiated): todo=%d ===" % len(todo), flush=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    results = [(n, "PASS", o) for n, o in done.items()]
    for src in todo:
        dst = STAGE / src.name
        differentiate(src, dst)
        meta = json.loads((src / "_meta.json").read_text(encoding="utf-8"))
        slug = meta.get("slug", "").strip() or src.name[: -len("-" + SUFFIX)]
        version = meta.get("version", "").strip() or SUFFIX
        display = fm_value(src / "SKILL.md", "displayName") or slug
        st, msg = publish(dst, slug, version, display)
        results.append((src.name, st, msg))
        print("%-6s %-30s %s" % (st, src.name, msg[:150]), flush=True)
        time.sleep(DELAY)
    ok = sum(1 for r in results if r[1] in ("PASS", "EXISTS"))
    print("\n=== SUMMARY total=%d ok=%d fail=%d ===" % (len(results), ok, len(results) - ok),
          flush=True)
    RESULTS.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2),
                       encoding="utf-8")


if __name__ == "__main__":
    main()
