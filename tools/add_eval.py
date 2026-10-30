#!/usr/bin/env python3
"""Add (or update) one migration evaluation row for a release in releases/evals.json.

A row = one app migrated with one HMBot release, scored on the six things the ledger cares about:
  functional_completeness  功能完备度  share of the app's functions that work on HarmonyOS (gt-eval 严格完备率 / bench 严格口径)
  usability                可用性      share usable the way a normal user would use it (no freeze / crash detours)
  accuracy                 准确率      share of checked behaviours that match the Android original (behaviour / business comparisons)
  code_completeness        代码完备度  share of source units with a migrated counterpart (no stubs / TODO / missing files)
  duration_seconds         时间        wall clock of the migration (first → last session record)
  tokens                   token       total tokens of the migration (input + cache read + cache write + output)

Values are ratios in [0,1] (pass "26/31" or "0.84" or "84%"); anything not measured stays null and renders as 未评测.
Sources that can fill fields automatically:
  --session-dir DIR   output dir of hmbot-session-archive (MANIFEST.json stats → duration, tokens, model/host, archive link)
  --gt-eval FILE      gt-eval Eval Result (yaml/json): executed_kits[].summary passed/total → functional_completeness
  --functional FILE   a2h-replay-bench functional-*.json (verdict per item) → functional_completeness

Usage:
  add_eval.py --tag v1.8.1 --app Jetsnack --session-dir ~/Downloads/jetsnack-complete-session-... \\
              --functional-completeness 26/31 --usability 0.6 --accuracy 90% --code-completeness 0.95 \\
              --model glm-5.3 --host claude --evidence "https://..." --note "..."
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def ratio(s):
    if s is None:
        return None
    s = str(s).strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", s)
    if m:
        return float(m.group(1)) / float(m.group(2)) if float(m.group(2)) else None
    if s.endswith("%"):
        return float(s[:-1]) / 100
    v = float(s)
    return v / 100 if v > 1 else v


def from_session_dir(d, row, project=None):
    d = Path(d).expanduser()
    man = json.loads((d / "MANIFEST.json").read_text())
    st = man.get("stats") or {}
    if not st and project:   # archive made before stats existed: recompute from the live session stores
        import os
        sys.path.insert(0, os.environ.get("HMBOT_SESSION_ARCHIVE_SCRIPTS") or str(Path.home() / ".claude/skills/hmbot-session-archive/scripts"))
        import archive_session as a  # type: ignore
        from session_stats import stats_for  # type: ignore
        p = Path(project).expanduser()
        roots = {p.resolve(), p.absolute()}
        items = a.claude_sessions(roots) + a.codex_sessions(roots) + a.icode_sessions(roots)
        anchor = p / ".migbot" if (p / ".migbot").is_dir() else p
        t = getattr(anchor.stat(), "st_birthtime", None) or anchor.stat().st_ctime
        items = [i for i in items if not (i["src"].is_file() and i["src"].stat().st_mtime < t)]
        st = stats_for(items)
    row["duration_seconds"] = st.get("wall_clock_seconds")
    row["active_seconds"] = st.get("active_seconds")
    row["tokens"] = (st.get("tokens") or {}).get("total")
    row["tokens_breakdown"] = st.get("tokens")
    row["hosts"] = sorted({s["host"] for s in man.get("sessions", [])}) or row.get("hosts")
    row["hmbot_version"] = man.get("hmbot_version")
    row["run_id"] = man.get("run_id")
    if (d / "ARCHIVE.json").is_file():
        arc = json.loads((d / "ARCHIVE.json").read_text())
        z = next((o for o in arc["objects"] if o["object"].endswith(".zip")), None)
        if z:
            bucket, key = z["object"][len("obs://"):].split("/", 1)
            row.setdefault("evidence", []).append({"title": "complete session archive (OBS, private)",
                                                   "url": f"https://{bucket}.obs.{arc.get('region', 'cn-east-3')}.myhuaweicloud.com/{key}"})
    return row


def from_gt_eval(path, row):
    text = Path(path).expanduser().read_text()
    try:
        data = json.loads(text)
    except Exception:
        try:
            import yaml  # type: ignore
            data = yaml.safe_load(text)
        except Exception:
            sys.exit("gt-eval result is YAML and PyYAML is not installed; pass the numbers explicitly")
    g = data.get("gt_eval", data)
    passed = total = 0
    for kit in g.get("executed_kits", []):
        s = kit.get("summary") or {}
        p = s.get("passed") or s.get("present") or 0
        t = s.get("total") or s.get("executed") or 0
        passed += p
        total += t
    if total:
        row["functional_completeness"] = passed / total
        row["functional_basis"] = f"gt-eval {g.get('tier', '')} {passed}/{total} 严格完备率".strip()
    row.setdefault("evidence", []).append({"title": "gt-eval Eval Result", "url": str(path)})
    return row


def from_functional(path, row):
    data = json.loads(Path(path).expanduser().read_text())
    items = data if isinstance(data, list) else list(data.values())
    verdicts = [str((i.get("verdict") or i.get("result") or i.get("status") or "")).lower() for i in items if isinstance(i, dict)]
    ok = sum(1 for v in verdicts if v in ("pass", "passed", "present", "✓", "通过"))
    if verdicts:
        row["functional_completeness"] = ok / len(verdicts)
        row["functional_basis"] = f"functional test {ok}/{len(verdicts)} 严格口径(测不了计失败)"
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--tag", required=True, help="HMBot release tag, e.g. v1.8.1")
    ap.add_argument("--app", required=True)
    ap.add_argument("--session-dir")
    ap.add_argument("--project", help="with --session-dir: project dir, to recompute time/tokens when the archive has no stats")
    ap.add_argument("--gt-eval")
    ap.add_argument("--functional")
    ap.add_argument("--functional-completeness")
    ap.add_argument("--usability")
    ap.add_argument("--accuracy")
    ap.add_argument("--code-completeness")
    ap.add_argument("--duration-seconds", type=int)
    ap.add_argument("--tokens", type=int)
    ap.add_argument("--model")
    ap.add_argument("--host", action="append", help="claude | codex | icode (repeatable)")
    ap.add_argument("--strategy", help="standard | 1to1 | arch")
    ap.add_argument("--evidence", action="append", default=[], help="URL or 'title=URL' (repeatable)")
    ap.add_argument("--note")
    ap.add_argument("--date", help="evaluation date YYYY-MM-DD (default today)")
    ap.add_argument("--remove", action="store_true", help="delete the row for --tag/--app")
    a = ap.parse_args()

    f = Path(a.site) / "releases" / "evals.json"
    rows = json.loads(f.read_text()) if f.is_file() else []
    rows = [r for r in rows if not (r["tag"] == a.tag and r["app"].lower() == a.app.lower())]
    if a.remove:
        f.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n")
        print(f"removed {a.tag} {a.app}")
        return
    row = {"tag": a.tag, "app": a.app, "date": a.date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
           "functional_completeness": None, "usability": None, "accuracy": None, "code_completeness": None,
           "duration_seconds": None, "tokens": None, "model": a.model, "hosts": a.host, "strategy": a.strategy,
           "evidence": [], "note": a.note}
    if a.session_dir:
        from_session_dir(a.session_dir, row, a.project)
    if a.gt_eval:
        from_gt_eval(a.gt_eval, row)
    if a.functional:
        from_functional(a.functional, row)
    for k, v in (("functional_completeness", a.functional_completeness), ("usability", a.usability),
                 ("accuracy", a.accuracy), ("code_completeness", a.code_completeness)):
        if v is not None:
            row[k] = ratio(v)
    if a.duration_seconds is not None:
        row["duration_seconds"] = a.duration_seconds
    if a.tokens is not None:
        row["tokens"] = a.tokens
    for e in a.evidence:
        t, _, u = e.partition("=") if "=" in e and not e.startswith("http") else ("evidence", "=", e)
        row["evidence"].append({"title": t, "url": u})
    rows.append(row)
    rows.sort(key=lambda r: (r["tag"], r["app"]))
    f.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n")
    print(f"recorded {a.tag} {a.app}: " + ", ".join(f"{k}={row[k]}" for k in ("functional_completeness", "usability", "accuracy", "code_completeness", "duration_seconds", "tokens")))
    print("now rebuild: python3 tools/build_releases.py && git add releases && git commit -m 'evals: ...' && git push")


if __name__ == "__main__":
    main()
