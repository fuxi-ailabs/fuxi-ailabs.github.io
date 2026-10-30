#!/usr/bin/env python3
"""Build the release test-report ledger (releases/releases.json + releases/index.html) from fuxi-ailabs/HMBot.

For every GitHub Release it collects, in this order of authority:
  1. the release itself: tag, date, pre-release flag, product archives, report-type assets
     (acceptance-*.json, *-audit.json, *-official-upgrade.json, *-global-install.json, *-size.json, evidence zips);
  2. the release notes: acceptance Actions runs, PRs, source commit, a2h version, "N PASS, N FAIL, N SKIP";
  3. each acceptance run: conclusion, and its verification-<product>-<platform> artifacts, which hold the actual
     test reports (dist/*-verification.json per variant, SUMMARY.txt smoke totals, audit, official upgrade);
  4. releases/overrides.json: hand-written extra report links / notes per tag.

Verification artifacts (~9 MB each, six per run) are downloaded only for the newest --artifact-releases product
releases (default 1) and cached in --cache (default ~/.cache/hmbot-release-reports); older releases get links plus
whatever small JSON reports are attached to the release itself. Needs `gh` logged in with read access to HMBot.

Usage: build_releases.py [--repo fuxi-ailabs/HMBot] [--site DIR] [--cache DIR] [--artifact-releases N] [--limit N]
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PACKAGE_RE = re.compile(r"^hmbot-(cc|codex|deveco)-.*\.(tar\.gz|zip)(\.sha256)?$")
REPORT_ASSET_RE = re.compile(r"(acceptance|audit|official-upgrade|global-install|size|timings|verification|evidence|report|provenance|lock)", re.I)
PRODUCTS = {"claude-code": "CC", "codex": "Codex", "deveco": "DevEco"}
PLATFORMS = {"macos-arm64": "mac", "windows-x64": "win"}


def log(msg):
    print(f"[releases] {msg}", flush=True)


def gh(*args, json_out=True):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout) if json_out else r.stdout


def releases(repo, limit):
    tags = [r["tagName"] for r in gh("release", "list", "--repo", repo, "--limit", str(limit), "--json", "tagName")]
    out = []
    for t in tags:
        out.append(gh("release", "view", t, "--repo", repo, "--json",
                      "tagName,name,body,publishedAt,isPrerelease,isDraft,targetCommitish,url,assets"))
    return out


def run_info(repo, run_id, cache):
    f = cache / "runs" / f"{run_id}.json"
    if f.is_file():
        return json.loads(f.read_text())
    try:
        info = gh("run", "view", run_id, "--repo", repo, "--json", "conclusion,status,name,workflowName,createdAt,url,headSha")
        arts = gh("api", f"repos/{repo}/actions/runs/{run_id}/artifacts", "--paginate")["artifacts"]
        info["artifacts"] = [{"id": a["id"], "name": a["name"], "bytes": a["size_in_bytes"], "expired": a["expired"],
                              "url": f"https://github.com/{repo}/actions/runs/{run_id}/artifacts/{a['id']}"} for a in arts]
    except RuntimeError as e:
        log(f"run {run_id}: {e}")
        info = {"error": str(e), "artifacts": []}
    f.parent.mkdir(parents=True, exist_ok=True)
    if info.get("status") in (None, "completed"):
        f.write_text(json.dumps(info, indent=1))
    return info


def parse_verification_dir(d):
    """One verification-<product>-<platform> artifact → summary dict."""
    s = {"variants": {}, "smoke": None, "audit_errors": None, "upgrade": None, "size_mib": None}
    for vf in d.rglob("*-verification.json"):
        try:
            for e in json.loads(vf.read_text()):
                s["variants"][e.get("variant", "?")] = e.get("status")
        except Exception:
            pass
    totals = [0, 0, 0]
    seen = 0
    for sm in d.rglob("SUMMARY.txt"):
        if ".build" in sm.parts:
            continue
        m = re.search(r"PASS=(\d+)\s+FAIL=(\d+)\s+SKIP=(\d+)", sm.read_text(errors="replace"))
        if m:
            seen += 1
            for i in range(3):
                totals[i] += int(m.group(i + 1))
    if seen:
        s["smoke"] = {"pass": totals[0], "fail": totals[1], "skip": totals[2], "suites": seen}
    for af in d.rglob("*-audit.json"):
        try:
            s["audit_errors"] = len(json.loads(af.read_text()).get("errors", []))
        except Exception:
            pass
    for uf in d.rglob("*-official-upgrade.json"):
        try:
            u = json.loads(uf.read_text())
            s["upgrade"] = {"checks": len(u.get("checks", [])), "legacy_ota": (u.get("legacy_ota") or {}).get("status"),
                            "legacy": u.get("legacy"), "version": u.get("version")}
        except Exception:
            pass
    for zf in d.rglob("*-size.json"):
        try:
            s["size_mib"] = json.loads(zf.read_text()).get("MiB")
        except Exception:
            pass
    return s


def artifact_summary(repo, run_id, art, cache):
    d = cache / "artifacts" / str(art["id"])
    marker = d / ".done"
    if not marker.is_file():
        if art["expired"]:
            return None
        d.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["gh", "run", "download", str(run_id), "--repo", repo, "-n", art["name"], "-D", str(d)],
                           capture_output=True, text=True)
        if r.returncode:
            log(f"artifact {art['name']} of {run_id}: {r.stderr.strip()[:200]}")
            return None
        # keep only the report files; the logs/symbol dumps are large
        for p in list(d.rglob("*")):
            if p.is_file() and not (p.suffix == ".json" or p.name == "SUMMARY.txt"):
                p.unlink()
        marker.touch()
    return parse_verification_dir(d)


def asset_summary(repo, tag, asset, cache):
    d = cache / "assets" / tag
    f = d / asset["name"]
    if not f.is_file():
        d.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["gh", "release", "download", tag, "--repo", repo, "-p", asset["name"], "-D", str(d), "--clobber"],
                           capture_output=True, text=True)
        if r.returncode or not f.is_file():
            return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def build_entry(repo, rel, cache, overrides, with_artifacts):
    body = rel.get("body") or ""
    tag = rel["tagName"]
    assets = rel.get("assets") or []
    packages = [a for a in assets if PACKAGE_RE.match(a["name"]) and not a["name"].endswith(".sha256")]
    report_assets = [a for a in assets if not PACKAGE_RE.match(a["name"]) and REPORT_ASSET_RE.search(a["name"])]
    run_ids = sorted(set(re.findall(r"actions/runs/(\d+)", body)))
    prs = sorted(set(re.findall(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)", body)))
    commit = None
    best = -1
    for m in re.finditer(r"\b([0-9a-f]{40})\b", body):   # prefer the hash described as the HMBot source, not a2h's
        before = body[max(0, m.start() - 90):m.start()].lower()
        score = 0
        if re.search(r"source|源码|源提交|release-tag commit|tag", before):
            score += 2
        if re.search(r"a2h|runtime|migbot-runtime", before[-50:]):
            score -= 3
        if score > best:
            best, commit = score, m.group(1)
    a2h = None
    m = re.search(r"a2h[^\n]{0,20}?v?(1\.\d+\.\d+)", body)
    if m:
        a2h = m.group(1)
    business = None
    m = re.search(r"([\d,]+) PASS, ([\d,]+) FAIL, ([\d,]+) (?:existing )?SKIP", body)
    if m:
        business = {"pass": int(m.group(1).replace(",", "")), "fail": int(m.group(2).replace(",", "")), "skip": int(m.group(3).replace(",", ""))}

    runs = []
    matrix = {}   # "claude-code/macos-arm64" -> summary
    for rid in run_ids:
        info = run_info(repo, rid, cache)
        entry = {"id": rid, "url": f"https://github.com/{repo}/actions/runs/{rid}", "name": info.get("workflowName") or info.get("name"),
                 "conclusion": info.get("conclusion"), "created_at": info.get("createdAt"), "head_sha": info.get("headSha"),
                 "verification_artifacts": []}
        for art in info.get("artifacts", []):
            m = re.match(r"verification-(claude-code|codex|deveco)-(macos-arm64|windows-x64)$", art["name"])
            if not m:
                continue
            key = f"{m.group(1)}/{m.group(2)}"
            a = {"name": art["name"], "url": art["url"], "bytes": art["bytes"], "expired": art["expired"], "product": m.group(1), "platform": m.group(2)}
            if with_artifacts and key not in matrix:
                s = artifact_summary(repo, rid, art, cache)
                if s:
                    matrix[key] = s
                    a["summary"] = s
            entry["verification_artifacts"].append(a)
        runs.append(entry)
        if commit is None and info.get("headSha"):
            commit = info["headSha"]

    # release-attached reports (1.7.x releases carry them directly)
    attached = []
    for a in report_assets:
        item = {"name": a["name"], "url": a["url"], "bytes": a["size"]}
        if a["name"].endswith(".json") and a["size"] < 5 << 20:
            d = asset_summary(repo, tag, a, cache)
            if isinstance(d, dict):
                if a["name"].startswith("acceptance-"):
                    item["summary"] = {"source_commit": d.get("source_commit"), "packages": len(d.get("packages", [])),
                                       "a2h": (d.get("a2h") or {}).get("runtime_version")}
                    commit = commit or d.get("source_commit")
                    a2h = a2h or (d.get("a2h") or {}).get("runtime_version")
                    runs_in = [str(d[k]) for k in ("macos_run", "windows_run") if d.get(k)]
                    for rid in runs_in:
                        if rid not in run_ids:
                            run_ids.append(rid)
                m2 = re.match(r"(claude-code|codex|deveco)-(macos-arm64|windows-x64)-(audit|official-upgrade|global-install|size)\.json", a["name"])
                if m2:
                    key = f"{m2.group(1)}/{m2.group(2)}"
                    s = matrix.setdefault(key, {"variants": {}, "smoke": None, "audit_errors": None, "upgrade": None, "size_mib": None, "from": "release-assets"})
                    if m2.group(3) == "audit":
                        s["audit_errors"] = len(d.get("errors", []))
                    elif m2.group(3) == "official-upgrade":
                        s["upgrade"] = {"checks": len(d.get("checks", [])), "legacy_ota": (d.get("legacy_ota") or {}).get("status"),
                                        "legacy": d.get("legacy"), "version": d.get("version")}
                    elif m2.group(3) == "size":
                        s["size_mib"] = d.get("MiB")
        attached.append(item)

    ov = overrides.get(tag, {})
    return {
        "tag": tag, "name": rel.get("name") or tag, "url": rel["url"], "published_at": rel.get("publishedAt"),
        "prerelease": rel.get("isPrerelease"), "kind": "product" if packages else "inputs",
        "packages": [{"name": p["name"], "bytes": p["size"]} for p in packages],
        "source_commit": commit, "a2h_version": a2h, "business_comparisons": business,
        "runs": runs, "prs": [{"repo": r, "number": int(n), "url": f"https://github.com/{r}/pull/{n}"} for r, n in prs],
        "attached_reports": attached, "matrix": matrix,
        "extra_reports": ov.get("reports", []), "notes": ov.get("notes"),
    }


# ── rendering ─────────────────────────────────────────────────────────────────
def chip(text, kind="ok"):
    return f'<span class="chip {kind}">{html.escape(text)}</span>'


def matrix_table(matrix, repo):
    if not matrix:
        return ""
    rows = []
    for key in sorted(matrix):
        s = matrix[key]
        prod, plat = key.split("/")
        v = s.get("variants") or {}
        vtxt = f"{sum(1 for x in v.values() if x == 'passed')}/{len(v)} variants" if v else "–"
        sm = s.get("smoke")
        smtxt = f"{sm['pass']} pass · {sm['fail']} fail · {sm['skip']} skip" if sm else "–"
        au = s.get("audit_errors")
        autxt = "clean" if au == 0 else (f"{au} error(s)" if au is not None else "–")
        up = s.get("upgrade")
        uptxt = f"{up['checks']} checks" + (f", OTA {up['legacy_ota']}" if up.get("legacy_ota") else "") if up else "–"
        bad = (sm and sm["fail"]) or (au not in (None, 0)) or (v and any(x != "passed" for x in v.values()))
        rows.append(f"<tr class={'bad' if bad else 'good'}><td>{PRODUCTS.get(prod, prod)} · {PLATFORMS.get(plat, plat)}</td>"
                    f"<td>{vtxt}</td><td>{smtxt}</td><td>{autxt}</td><td>{uptxt}</td>"
                    f"<td>{s['size_mib']:.1f} MiB</td></tr>" if s.get("size_mib") else
                    f"<tr class={'bad' if bad else 'good'}><td>{PRODUCTS.get(prod, prod)} · {PLATFORMS.get(plat, plat)}</td>"
                    f"<td>{vtxt}</td><td>{smtxt}</td><td>{autxt}</td><td>{uptxt}</td><td>–</td></tr>")
    return ("<table class=matrix><thead><tr><th>Product</th><th>Verification</th><th>Smoke tests</th><th>Archive audit</th>"
            "<th>Official upgrade</th><th>Size</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def pct(v):
    return "<span class=na>未评测</span>" if v is None else f"{v * 100:.0f}%"


def fmt_dur(sec):
    if sec is None:
        return "<span class=na>–</span>"
    h, m = divmod(int(sec) // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def fmt_tok(n):
    if n is None:
        return "<span class=na>–</span>"
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k"


def evals_table(rows):
    if not rows:
        return "<p class=na>这个版本还没有迁移评测记录。</p>"
    out = []
    for r in sorted(rows, key=lambda r: r["app"].lower()):
        who = " · ".join(x for x in [r.get("model"), ", ".join(r.get("hosts") or []), r.get("strategy")] if x)
        ev = " ".join(f'<a href="{html.escape(e["url"])}" title="{html.escape(e.get("title", ""))}">{html.escape(e.get("title") or "link")}</a>' for e in r.get("evidence") or [])
        tip = r.get("functional_basis") or ""
        tok = r.get("tokens_breakdown") or {}
        tok_tip = (f"input {tok.get('input', 0):,} · cache read {tok.get('cache_read', 0):,} · cache write {tok.get('cache_creation', 0):,} · output {tok.get('output', 0):,}") if tok else ""
        out.append(f"<tr><td class=app>{html.escape(r['app'])}<div class=sub>{html.escape(who)}</div></td>"
                   f"<td title=\"{html.escape(tip)}\">{pct(r.get('functional_completeness'))}</td><td>{pct(r.get('usability'))}</td>"
                   f"<td>{pct(r.get('accuracy'))}</td><td>{pct(r.get('code_completeness'))}</td>"
                   f"<td title=\"active {fmt_dur(r.get('active_seconds'))}\">{fmt_dur(r.get('duration_seconds'))}</td>"
                   f"<td title=\"{html.escape(tok_tip)}\">{fmt_tok(r.get('tokens'))}</td>"
                   f"<td class=ev>{ev}{('<div class=sub>' + html.escape(r['note']) + '</div>') if r.get('note') else ''}</td></tr>")
    return ("<table class=evals><thead><tr><th>App</th><th>功能完备度</th><th>可用性</th><th>准确率</th><th>代码完备度</th>"
            "<th>时间</th><th>token</th><th>证据</th></tr></thead><tbody>" + "".join(out) + "</tbody></table>")


def render(entries, repo, evals):
    cards = []
    for e in entries:
        day = (e.get("published_at") or "")[:10]
        tag = e["tag"]
        kind = chip("pre-release", "warn") if e.get("prerelease") else chip("release", "ok")
        if e["kind"] == "inputs":
            kind += chip("build inputs", "dim")
        head = (f"<h2><a href=\"{html.escape(e['url'])}\">{html.escape(tag)}</a> <span class=dim>{html.escape(e['name'])}</span></h2>"
                f"<div class=meta>{day} {kind}"
                + (f" · source <a href=\"https://github.com/{repo}/commit/{e['source_commit']}\"><code>{e['source_commit'][:10]}</code></a>" if e.get("source_commit") else "")
                + (f" · a2h v{html.escape(e['a2h_version'])}" if e.get("a2h_version") else "")
                + (f" · {len(e['packages'])} product archives" if e.get("packages") else "") + "</div>")
        body = evals_table(evals.get(tag, []))
        cards.append(f"<section class=card id=\"{html.escape(tag)}\">{head}{body}</section>")
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HMBot release test reports</title>
<style>
:root{{color-scheme:light dark;--fg:#1a1a1a;--bg:#fff;--dim:#6b6b6b;--line:#e4e4e4;--card:#fafafa;--link:#0b57d0;--ok:#e6f4ea;--okfg:#137333;--bad:#fce8e6;--badfg:#c5221f;--warn:#fef7e0;--warnfg:#b06000;--chip:#eee}}
@media(prefers-color-scheme:dark){{:root{{--fg:#e6e6e6;--bg:#121212;--dim:#9a9a9a;--line:#2b2b2b;--card:#1a1a1a;--link:#8ab4f8;--ok:#1e3a2a;--okfg:#81c995;--bad:#3c1f1d;--badfg:#f28b82;--warn:#3a2e14;--warnfg:#fdd663;--chip:#2a2a2a}}}}
body{{margin:0;padding:32px 24px;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:var(--fg);background:var(--bg)}}
main{{max-width:1000px;margin:0 auto}} h1{{font-size:22px;margin:0 0 6px}} .lead{{color:var(--dim);margin:0 0 24px;max-width:80ch}}
.card{{border:1px solid var(--line);background:var(--card);border-radius:10px;padding:16px 18px;margin:0 0 16px}}
.card h2{{font-size:17px;margin:0 0 2px}} .card h2 a{{color:var(--fg);text-decoration:none}} .card h2 a:hover{{text-decoration:underline}}
.meta{{color:var(--dim);font-size:13px;margin-bottom:10px}} code{{font-size:12px}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}} .chip{{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;background:var(--chip);color:var(--fg);text-decoration:none}}
.chip.ok{{background:var(--ok);color:var(--okfg)}} .chip.bad{{background:var(--bad);color:var(--badfg)}} .chip.warn{{background:var(--warn);color:var(--warnfg)}} .chip.dim{{color:var(--dim)}}
a{{color:var(--link)}} .dim{{color:var(--dim);font-weight:400;font-size:13px}}
table.matrix{{border-collapse:collapse;width:100%;margin:10px 0;font-size:13px}} .matrix th,.matrix td{{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line)}} .matrix th{{color:var(--dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.04em}}
tr.bad td:first-child{{color:var(--badfg)}} details{{margin-top:8px}} summary{{cursor:pointer;color:var(--dim)}} details ul{{margin:6px 0 0;padding-left:18px}} li{{margin:2px 0}}
.notes{{margin:8px 0 0;color:var(--dim)}}
table.evals{{border-collapse:collapse;width:100%;margin:6px 0 4px;font-size:13px}} .evals th,.evals td{{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
.evals th{{color:var(--dim);font-weight:600;font-size:11px;letter-spacing:.04em}} .evals td.app{{font-weight:600}} .sub{{font-weight:400;color:var(--dim);font-size:12px}} .na{{color:var(--dim)}} td.ev{{font-size:12px;max-width:260px}}
footer{{margin-top:24px;color:var(--dim);font-size:12px}}
</style></head><body><main>
<h1>HMBot release test reports</h1>
<p class=lead>每个 <a href="https://github.com/{repo}/releases">{repo} release</a> 一张卡:用这个版本迁移过的 App 各一行,记录
<b>功能完备度</b>(功能在鸿蒙上可达的比例)、<b>可用性</b>(按普通用户正常使用可用的比例)、<b>准确率</b>(行为 / 业务对比与安卓一致的比例)、
<b>代码完备度</b>(源码单元有迁移对应物、无桩的比例)、<b>时间</b>(迁移首末记录的墙钟)、<b>token</b>(全部 API 调用之和)。
未测的格子显示"未评测"。指向私有仓的链接需要登录的组织成员。</p>
{''.join(cards)}
<footer>Updated {updated} by <a href="https://github.com/fuxi-ailabs/fuxi-ailabs.github.io/blob/main/tools/build_releases.py">tools/build_releases.py</a> · data in <a href="releases.json">releases.json</a> · evaluations in <a href="evals.json">evals.json</a> (add with <a href="https://github.com/fuxi-ailabs/fuxi-ailabs.github.io/blob/main/tools/add_eval.py">tools/add_eval.py</a>) · hand-added links in <a href="overrides.json">overrides.json</a></footer>
</main></body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="fuxi-ailabs/HMBot")
    ap.add_argument("--site", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--cache", default=os.environ.get("HMBOT_RELEASE_CACHE") or str(Path.home() / ".cache" / "hmbot-release-reports"))
    ap.add_argument("--artifact-releases", type=int, default=0,
                    help="download and parse verification artifacts for this many newest product releases (default 1; 0 = none)")
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()
    site, cache = Path(args.site), Path(args.cache)
    out_dir = site / "releases"
    out_dir.mkdir(parents=True, exist_ok=True)
    ov_file = out_dir / "overrides.json"
    overrides = json.loads(ov_file.read_text()) if ov_file.is_file() else {}
    entries = []
    parsed = 0
    for rel in releases(args.repo, args.limit):
        if rel.get("isDraft"):
            continue
        is_product = any(PACKAGE_RE.match(a["name"]) for a in rel.get("assets") or [])
        with_artifacts = is_product and parsed < args.artifact_releases
        log(f"{rel['tagName']}" + ("  (parsing verification artifacts)" if with_artifacts else ""))
        entries.append(build_entry(args.repo, rel, cache, overrides, with_artifacts))
        if with_artifacts:
            parsed += 1
    entries.sort(key=lambda e: e.get("published_at") or "", reverse=True)
    (out_dir / "releases.json").write_text(json.dumps({"repo": args.repo, "generated_utc": datetime.now(timezone.utc).isoformat(),
                                                       "releases": entries}, ensure_ascii=False, indent=1) + "\n")
    ev_file = out_dir / "evals.json"
    evals = {}
    for r in (json.loads(ev_file.read_text()) if ev_file.is_file() else []):
        evals.setdefault(r["tag"], []).append(r)
    (out_dir / "index.html").write_text(render(entries, args.repo, evals))
    log(f"wrote {out_dir / 'releases.json'} and index.html ({len(entries)} releases)")


if __name__ == "__main__":
    main()
