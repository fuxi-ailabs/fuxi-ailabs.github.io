#!/usr/bin/env python3
"""Build the HMBot release ledger (releases/releases.json + releases/index.html) from fuxi-ailabs/HMBot.

For every GitHub Release it records: tag, date, kind (from the tag), the installable product archives with direct
download links, the HMBot source commit and a2h version from the release notes. Migration evaluations per App
come from releases/evals.json (written by tools/add_eval.py). No CI verification records are collected.
Needs `gh` logged in with read access to HMBot.

Usage: build_releases.py [--repo fuxi-ailabs/HMBot] [--site DIR] [--limit N]
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PACKAGE_RE = re.compile(r"^hmbot-(cc|codex|deveco)-.*\.(tar\.gz|zip)(\.sha256)?$")


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










def build_entry(repo, rel):
    body = rel.get("body") or ""
    tag = rel["tagName"]
    assets = rel.get("assets") or []
    packages = [a for a in assets if PACKAGE_RE.match(a["name"]) and not a["name"].endswith(".sha256")]
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
    return {
        "tag": tag, "name": rel.get("name") or tag, "url": rel["url"], "published_at": rel.get("publishedAt"),
        "prerelease": rel.get("isPrerelease"), "kind": classify(tag, packages)[0],
        "packages": [{"name": p["name"], "bytes": p["size"], "url": p["url"], "downloads": p.get("downloadCount"),
                      "sha256_url": next((a["url"] for a in assets if a["name"] == p["name"] + ".sha256"), None)} for p in packages],
        "source_commit": commit, "a2h_version": a2h,
    }


def classify(tag, packages):
    """Release kind from the tag itself, not from GitHub's pre-release checkbox (every HMBot release has it ticked).
    vX.Y.Z → 正式版; vX.Y.Z-rc.N / -beta / -alpha → 候选版; candidate-* → 预览版; anything without product archives → 构建输入."""
    if not packages:
        return "构建输入", "dim"
    if re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        return "正式版", "ok"
    if re.fullmatch(r"v\d+\.\d+\.\d+-(rc|beta|alpha)[.\-]?\d*", tag):
        return "候选版", "warn"
    if tag.startswith("candidate-"):
        return "预览版", "warn"
    return "其他", "dim"


# ── rendering ─────────────────────────────────────────────────────────────────
def chip(text, kind="ok"):
    return f'<span class="chip {kind}">{html.escape(text)}</span>'


HOST_LABEL = {"cc": "Claude Code", "codex": "Codex", "deveco": "DevEco"}
PLATFORM_LABEL = {"macos-arm64": "macOS arm64", "windows-x64": "Windows x64"}


def fmt_bytes(n):
    return f"{n / 1048576:.0f} MB" if n else ""


def packages_block(e):
    """Direct download links for the installable product archives, one row per host, one cell per platform."""
    pk = e.get("packages") or []
    if not pk:
        return ""
    grid = {}
    for p in pk:
        m = re.match(r"^hmbot-(cc|codex|deveco)-(.+?)-(macos-arm64|windows-x64)\.(tar\.gz|zip)$", p["name"])
        if not m:
            continue
        grid.setdefault(m.group(1), {})[m.group(3)] = p
    rows = []
    for host in ("cc", "codex", "deveco"):
        if host not in grid:
            continue
        cells = []
        for plat in ("macos-arm64", "windows-x64"):
            p = grid[host].get(plat)
            if not p:
                cells.append("<td class=dim>—</td>")
                continue
            sha = f' <a class=dim href="{html.escape(p["sha256_url"])}">sha256</a>' if p.get("sha256_url") else ""
            cells.append(f'<td><a href="{html.escape(p["url"])}">{html.escape(p["name"])}</a> <span class=dim>{fmt_bytes(p["bytes"])}</span>{sha}</td>')
        rows.append(f"<tr><th>{HOST_LABEL[host]}</th>{''.join(cells)}</tr>")
    return (f"<table class=pkgs><thead><tr><th>安装包</th><th>{PLATFORM_LABEL['macos-arm64']}</th><th>{PLATFORM_LABEL['windows-x64']}</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")




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


RESULT_LABEL = {"pass": ("✓ 通过", "ok"), "partial": ("◐ 部分", "warn"), "fail": ("✗ 不通过", "bad"),
                "untestable": ("— 测不了", "dim"), "untested": ("未评测", "dim")}


def checklist_block(r):
    cl = r.get("checklist") or []
    if not cl:
        return "<span class=dim>未提供</span>"
    counts = {}
    for i in cl:
        counts[i.get("result", "untested")] = counts.get(i.get("result", "untested"), 0) + 1
    summary = " · ".join(f"{RESULT_LABEL[k][0]} {counts[k]}" for k in ("pass", "partial", "fail", "untestable", "untested") if counts.get(k))
    src = f'<div class=sub>来源:{html.escape(r["checklist_source"])}</div>' if r.get("checklist_source") else ""
    rows = "".join(f"<tr><td class=cid>{html.escape(str(i.get('id') or ''))}</td><td>{html.escape(i.get('feature') or '')}</td>"
                   f"<td class=\"res {RESULT_LABEL.get(i.get('result', 'untested'), RESULT_LABEL['untested'])[1]}\">{RESULT_LABEL.get(i.get('result', 'untested'), RESULT_LABEL['untested'])[0]}</td>"
                   f"<td class=note>{html.escape(i.get('note') or '')}</td></tr>" for i in cl)
    return (f"<details class=cl><summary>{len(cl)} 项 · {summary}</summary>"
            f"<table class=checklist><thead><tr><th>ID</th><th>功能</th><th>结果</th><th>说明</th></tr></thead><tbody>{rows}</tbody></table></details>{src}")


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
                   f"<td class=ev>{ev}{('<div class=sub>' + html.escape(r['note']) + '</div>') if r.get('note') else ''}</td>"
                   f"<td class=clcell>{checklist_block(r)}</td></tr>")
    return ("<table class=evals><thead><tr><th>App</th><th>功能完备度</th><th>可用性</th><th>准确率</th><th>代码完备度</th>"
            "<th>时间</th><th>token</th><th>证据</th><th>功能清单</th></tr></thead><tbody>" + "".join(out) + "</tbody></table>")


def render(entries, repo, evals):
    cards = []
    for e in entries:
        day = (e.get("published_at") or "")[:10]
        tag = e["tag"]
        label, css = classify(tag, e.get("packages"))
        kind = f'<span class="chip {css}">{label}</span>'
        head = (f"<h2><a href=\"{html.escape(e['url'])}\">{html.escape(tag)}</a> <span class=dim>{html.escape(e['name'])}</span></h2>"
                f"<div class=meta>{day} {kind}"
                + (f" · source <a href=\"https://github.com/{repo}/commit/{e['source_commit']}\"><code>{e['source_commit'][:10]}</code></a>" if e.get("source_commit") else "")
                + (f" · a2h v{html.escape(e['a2h_version'])}" if e.get("a2h_version") else "")
                + (f" · {len(e['packages'])} product archives" if e.get("packages") else "") + "</div>")
        body = packages_block(e) + evals_table(evals.get(tag, []))
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
table.pkgs{{border-collapse:collapse;margin:0 0 12px;font-size:13px}} .pkgs th,.pkgs td{{text-align:left;padding:4px 14px 4px 0;border-bottom:1px solid var(--line);vertical-align:top}}
.pkgs thead th{{color:var(--dim);font-weight:600;font-size:11px;letter-spacing:.04em}} .pkgs tbody th{{font-weight:600;white-space:nowrap}} .pkgs a{{word-break:break-all}} .pkgs a.dim{{color:var(--dim)}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}} .chip{{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;background:var(--chip);color:var(--fg);text-decoration:none}}
.chip.ok{{background:var(--ok);color:var(--okfg)}} .chip.bad{{background:var(--bad);color:var(--badfg)}} .chip.warn{{background:var(--warn);color:var(--warnfg)}} .chip.dim{{color:var(--dim)}}
a{{color:var(--link)}} .dim{{color:var(--dim);font-weight:400;font-size:13px}}
tr.bad td:first-child{{color:var(--badfg)}} details{{margin-top:8px}} summary{{cursor:pointer;color:var(--dim)}} details ul{{margin:6px 0 0;padding-left:18px}} li{{margin:2px 0}}
.notes{{margin:8px 0 0;color:var(--dim)}}
table.evals{{border-collapse:collapse;width:100%;margin:6px 0 4px;font-size:13px}} .evals th,.evals td{{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
.evals th{{color:var(--dim);font-weight:600;font-size:11px;letter-spacing:.04em}}
td.clcell{{min-width:220px}} details.cl summary{{cursor:pointer;white-space:nowrap}} details.cl[open] summary{{color:var(--dim);font-size:12px}}
table.checklist{{border-collapse:collapse;width:100%;min-width:420px;margin:4px 0 0;font-size:12.5px}} .checklist th,.checklist td{{text-align:left;padding:4px 8px;border-top:1px solid var(--line);vertical-align:top}}
.checklist th{{color:var(--dim);font-weight:600;font-size:11px}} td.cid{{white-space:nowrap;color:var(--dim)}} td.res{{white-space:nowrap}} td.res.ok{{color:var(--okfg)}} td.res.bad{{color:var(--badfg)}} td.res.warn{{color:var(--warnfg)}} td.res.dim{{color:var(--dim)}} td.note{{color:var(--dim)}} .evals td.app{{font-weight:600}} .sub{{font-weight:400;color:var(--dim);font-size:12px}} .na{{color:var(--dim)}} td.ev{{font-size:12px;max-width:260px}}
footer{{margin-top:24px;color:var(--dim);font-size:12px}}
</style></head><body><main>
<h1>HMBot release test reports</h1>
<p class=lead>每个 <a href="https://github.com/{repo}/releases">{repo} release</a> 一张卡:用这个版本迁移过的 App 各一行,记录
<b>功能完备度</b>(功能在鸿蒙上可达的比例)、<b>可用性</b>(按普通用户正常使用可用的比例)、<b>准确率</b>(行为 / 业务对比与安卓一致的比例)、
<b>代码完备度</b>(源码单元有迁移对应物、无桩的比例)、<b>时间</b>(迁移首末记录的墙钟)、<b>token</b>(全部 API 调用之和)。
未测的格子显示"未评测"。每个 App 下面可展开它的<b>功能清单</b>:逐条功能的通过 / 部分 / 不通过 / 测不了。卡片顶部是该版本可直接安装的产品包(三种宿主 × 两个平台)。指向私有仓的链接(安装包、sha256 等)需要登录的组织成员。</p>
{''.join(cards)}
<footer>Updated {updated} by <a href="https://github.com/fuxi-ailabs/fuxi-ailabs.github.io/blob/main/tools/build_releases.py">tools/build_releases.py</a> · data in <a href="releases.json">releases.json</a> · evaluations in <a href="evals.json">evals.json</a> (add with <a href="https://github.com/fuxi-ailabs/fuxi-ailabs.github.io/blob/main/tools/add_eval.py">tools/add_eval.py</a>)</footer>
</main></body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="fuxi-ailabs/HMBot")
    ap.add_argument("--site", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()
    site = Path(args.site)
    out_dir = site / "releases"
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for rel in releases(args.repo, args.limit):
        if rel.get("isDraft"):
            continue
        log(rel["tagName"])
        entries.append(build_entry(args.repo, rel))
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
