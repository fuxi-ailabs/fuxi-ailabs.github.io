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

Each row's 功能清单 column is something a person uploads: a link, or a table file.
  --checklist-url URL   link to the checklist kept elsewhere (a shared doc, a repo file, ...)
  --checklist FILE      upload a table: it is copied to releases/checklists/<tag>/<app>.<ext> and linked from the page.
                        Recognised formats are also parsed so the page can show counts and an inline preview:
                        markdown table | ID | 功能 | 结果 | 说明 |  (结果: 通过/部分/不通过/测不了/未评测 or pass/partial/fail/untestable/untested),
                        JSON list [{id, feature, result, note}], a2h-replay-bench functional-*.json (names via --checklist-names FUNCTIONAL.md).
                        Other files (xlsx, pdf, html, ...) are uploaded and linked as-is.
A migration done through the hmigbot-plus toolkit (hmbot CLI, see the hmbot-replay-migrate skill) is recorded with
  --hmbot-run <examples/cli-migration-matrix/runs/<run>>: app, model, host, toolkit version (→ --plus-tag), wall time and
  the capability modules that ran (pipeline → core, ecat / dt / visual / hitest, recall-log → memory) come from manifest.yaml.
  Give --tag as well when the run used an HMBot release's skill set, so the row shows under both products.
能力组合 (which HMBot capability modules were on for this migration) is a list of module keys:
  core (0-1 迁移 Core), ota, telemetry (数据上报), memory, ecat, dt, visual, hitest, 1to1 (1:1 复制), arch (架构迁移).
  --cap KEY (repeatable) adds modules by hand; with --project the detectable ones are filled in automatically:
  core always; 1to1 / arch from .migbot/plugin/install.json profile; ota when the project carries .migbot/release.json
  (the a2h-init update check); telemetry when .migbot/config.json telemetry_consent is granted; memory when
  .claude/a2h-bench/ (replay-bench recall) or an mbm plugin is installed. ecat / dt / visual / hitest are hmbot CLI
  capabilities and are only recorded when you pass them. --no-auto-caps skips the detection.
The 评估方法 column (right of 功能清单) is uploaded the same way:
  --method URL|FILE|TEXT  a link, a file (copied to releases/methods/<tag>/<app>.<ext> and linked), or a short text shown in the cell.
When a parsed checklist has verdicts and --functional-completeness is not given, functional_completeness = 通过 ÷ 已评测 (测不了计失败).
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
import shutil
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


SITE = Path(__file__).resolve().parent.parent


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
    row["migbot_session_id"] = man.get("migbot_session_id")
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


def parse_manifest(path):
    """Minimal reader for hmigbot-plus run manifest.yaml: top-level `key: value` lines plus the indented exit_codes map."""
    out, block = {}, None
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  ") and block is not None:
            k, _, v = line.strip().partition(":")
            out[block][k.strip()] = v.strip()
            continue
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if v == "":
            out[k] = {}
            block = k
        else:
            out[k] = v
            block = None
    return out


def from_hmbot_run(run_dir, man, row):
    """Fill the row from a hmigbot-plus CLI run; returns the capability modules that actually ran."""
    line = man.get("line") or ""
    row["model"] = row.get("model") or man.get("model")
    row["hosts"] = row.get("hosts") or ([{"cc": "claude", "codex": "codex", "icode": "icode", "deveco": "icode"}.get(line, line)] if line else None)
    if man.get("started_at") and man.get("finished_at") and not row.get("duration_seconds"):
        t0 = datetime.fromisoformat(man["started_at"].replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(man["finished_at"].replace("Z", "+00:00"))
        row["duration_seconds"] = int((t1 - t0).total_seconds())
    codes = man.get("exit_codes") or {}
    ran = {k for k, v in codes.items() if v != "not_run"}
    caps = {"core"} if "pipeline" in ran else set()
    caps |= {c for c in ("ecat", "dt", "visual", "hitest") if c in ran}
    if (run_dir / "recall-log.txt").is_file() or (run_dir / "memory").is_dir():
        caps.add("memory")
    row["hmbot_run"] = {"name": man.get("run_name") or run_dir.name, "toolkit": man.get("hmbot") or man.get("hmigbot"),
                        "skill_set": Path(man["skill_set"]).name if man.get("skill_set") else None,
                        "exit_codes": {k: (int(v) if v.isdigit() else v) for k, v in codes.items()}, "reviewer": man.get("reviewer")}
    row["note"] = row.get("note") or ("hmbot run " + row["hmbot_run"]["name"] + " · exit " + ", ".join(f"{k}={v}" for k, v in codes.items()))
    return caps


CAPS = ("core", "ota", "telemetry", "memory", "ecat", "dt", "visual", "hitest", "1to1", "arch")


def detect_caps(project):
    """Capability modules visible in the migrated project tree (see module docstring)."""
    p = Path(project).expanduser()
    caps = {"core"}
    try:
        inst = json.loads((p / ".migbot" / "plugin" / "install.json").read_text())
        prof = inst.get("profile") or ""
        if prof == "1to1":
            caps.add("1to1")
        elif prof in ("arch", "architecture"):
            caps.add("arch")
    except (OSError, ValueError):
        pass
    if (p / ".migbot" / "release.json").is_file():
        caps.add("ota")
    try:
        cfg = json.loads((p / ".migbot" / "config.json").read_text())
        if cfg.get("telemetry_consent") == "granted":
            caps.add("telemetry")
    except (OSError, ValueError):
        pass
    if (p / ".claude" / "a2h-bench").is_dir() or any(x.is_dir() and "mbm" in x.name for x in (p / ".claude" / "plugins").glob("*") if (p / ".claude" / "plugins").is_dir()):
        caps.add("memory")
    try:
        if "mbm" in (p / ".claude" / "settings.json").read_text():
            caps.add("memory")
    except OSError:
        pass
    return caps


RESULT_MAP = {"通过": "pass", "pass": "pass", "passed": "pass", "present": "pass", "✓": "pass", "covered-correct": "pass",
              "部分": "partial", "partial": "partial", "◐": "partial", "degraded": "partial", "covered-wrong": "partial",
              "不通过": "fail", "fail": "fail", "failed": "fail", "missing": "fail", "✗": "fail", "absent": "fail",
              "测不了": "untestable", "untestable": "untestable", "—": "untestable", "na": "untestable", "n/a": "untestable", "blocked": "untestable",
              "未评测": "untested", "untested": "untested", "": "untested"}


def norm_result(v):
    return RESULT_MAP.get(str(v or "").strip().lower(), RESULT_MAP.get(str(v or "").strip(), "untested"))


def md_table_rows(text):
    """Yield cell lists for every markdown table row that is not a header/separator."""
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells or set(cells[0]) <= set("-: ") or cells[0].lower() == "id":
            continue
        yield cells


def load_checklist(path, names_path=None):
    p = Path(path).expanduser()
    text = p.read_text()
    items = []
    if p.suffix == ".json":
        data = json.loads(text)
        if isinstance(data, list):
            items = [{"id": i.get("id"), "feature": i.get("feature") or i.get("name") or "", "result": norm_result(i.get("result")),
                      "note": i.get("note") or ""} for i in data]
        elif isinstance(data, dict) and isinstance(data.get("items"), dict):      # a2h-replay-bench functional-*.json
            names = {}
            if names_path:
                for cells in md_table_rows(Path(names_path).expanduser().read_text()):
                    if len(cells) >= 2:
                        names[cells[0]] = cells[1]
            for k, v in data["items"].items():
                items.append({"id": k, "feature": names.get(k, ""), "result": norm_result(v.get("result")), "note": v.get("note") or ""})
        else:
            sys.exit(f"unrecognised checklist JSON in {p}")
    else:
        for cells in md_table_rows(text):
            if len(cells) >= 2:
                items.append({"id": cells[0].strip("*` "), "feature": cells[1], "result": norm_result(cells[2] if len(cells) > 2 else ""),
                              "note": cells[3] if len(cells) > 3 else ""})
    return items


def _unused_load_feature_index(path):
    text = Path(path).expanduser().read_text()
    sec = text.split("## 功能清单", 1)[1] if "## 功能清单" in text else text
    sec = sec.split("\n## ", 1)[0]
    return [{"id": c[0].strip("*` "), "feature": c[1], "result": "untested", "note": ""} for c in md_table_rows(sec) if len(c) >= 2]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--tag", help="HMBot release tag the migration used, e.g. v1.8.1")
    ap.add_argument("--plus-tag", help="hmigbot-plus (toolkit) release tag, e.g. v1.7.0; filled from --hmbot-run when omitted")
    ap.add_argument("--hmbot-run", help="hmigbot-plus CLI run dir (examples/cli-migration-matrix/runs/<run>): app, model, host, "
                                        "toolkit version, time and the capability modules that ran come from its manifest.yaml")
    ap.add_argument("--app", help="App name (default: app_name from --hmbot-run)")
    ap.add_argument("--session-dir")
    ap.add_argument("--project", help="with --session-dir: project dir, to recompute time/tokens when the archive has no stats")
    ap.add_argument("--gt-eval")
    ap.add_argument("--functional")
    ap.add_argument("--checklist", help="upload a checklist table file (markdown table / JSON / bench functional json / any file)")
    ap.add_argument("--checklist-url", help="link to a checklist kept elsewhere")
    ap.add_argument("--method", help="评估方法: a URL, a file to upload, or a short text")
    ap.add_argument("--cap", action="append", default=[], choices=sorted(CAPS), help="capability module on for this migration (repeatable)")
    ap.add_argument("--no-auto-caps", action="store_true", help="do not detect modules from --project")
    ap.add_argument("--migbot-session-id", help="override / supply .migbot config.json migbot_session_id (normally read from the archive MANIFEST)")
    ap.add_argument("--checklist-names", help="FUNCTIONAL.md giving feature text for a bench functional json")
    ap.add_argument("--checklist-source", help="who/where the checklist comes from (shown under the link)")
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

    man = parse_manifest(Path(a.hmbot_run).expanduser() / "manifest.yaml") if a.hmbot_run else {}
    a.app = a.app or man.get("app_name")
    ver = re.search(r"(\d+\.\d+\.\d+)", man.get("hmbot") or man.get("hmigbot") or "")
    a.plus_tag = a.plus_tag or (f"v{ver.group(1)}" if ver else None)
    if not a.app:
        sys.exit("--app is required (or --hmbot-run with app_name in its manifest)")
    if not (a.tag or a.plus_tag):
        sys.exit("give --tag (HMBot release) and/or --plus-tag (hmigbot-plus release)")

    f = Path(a.site) / "releases" / "evals.json"
    rows = json.loads(f.read_text()) if f.is_file() else []
    same = lambda r: r.get("tag") == a.tag and r.get("plus_tag") == a.plus_tag and r["app"].lower() == a.app.lower()
    rows = [r for r in rows if not same(r)]
    if a.remove:
        f.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n")
        print(f"removed {a.tag or ''} {a.plus_tag or ''} {a.app}")
        return
    row = {"tag": a.tag, "plus_tag": a.plus_tag, "app": a.app, "date": a.date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
           "functional_completeness": None, "usability": None, "accuracy": None, "code_completeness": None,
           "duration_seconds": None, "tokens": None, "model": a.model, "hosts": a.host, "strategy": a.strategy,
           "evidence": [], "note": a.note}
    run_caps = set()
    if man:
        run_caps = from_hmbot_run(Path(a.hmbot_run).expanduser(), man, row)
    if a.session_dir:
        from_session_dir(a.session_dir, row, a.project)
    if a.gt_eval:
        from_gt_eval(a.gt_eval, row)
    if a.functional:
        from_functional(a.functional, row)
    if a.checklist_url:
        row["checklist_url"] = a.checklist_url
        row["checklist_source"] = a.checklist_source or row.get("checklist_source")
    if a.checklist:
        src = Path(a.checklist).expanduser()
        dest_dir = SITE / "releases" / "checklists" / a.tag
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / (re.sub(r"[^\w.-]+", "_", a.app) + src.suffix.lower())
        if src.resolve() != dest.resolve():
            shutil.copyfile(src, dest)
        row["checklist_file"] = str(dest.relative_to(SITE / "releases"))
        row["checklist_source"] = a.checklist_source or src.name
        try:
            row["checklist"] = load_checklist(src, a.checklist_names)
        except SystemExit:
            row.pop("checklist", None)      # not a table we can parse: linked as a plain file
        print(f"uploaded checklist file -> releases/{row['checklist_file']}")
    if a.migbot_session_id:
        row["migbot_session_id"] = a.migbot_session_id
    caps = set(a.cap) | run_caps
    if a.project and not a.no_auto_caps:
        caps |= detect_caps(a.project)
    elif not a.no_auto_caps:
        caps.add("core")
        if (a.strategy or "") == "1to1":
            caps.add("1to1")
        elif (a.strategy or "") in ("arch", "architecture"):
            caps.add("arch")
    row["caps"] = [c for c in CAPS if c in caps]
    if a.method:
        if re.match(r"https?://", a.method):
            row["method_url"] = a.method
        elif Path(a.method).expanduser().is_file():
            src = Path(a.method).expanduser()
            dest_dir = SITE / "releases" / "methods" / a.tag
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / (re.sub(r"[^\w.-]+", "_", a.app) + src.suffix.lower())
            if src.resolve() != dest.resolve():
                shutil.copyfile(src, dest)
            row["method_file"] = str(dest.relative_to(SITE / "releases"))
            print(f"uploaded method file -> releases/{row['method_file']}")
        else:
            row["method_text"] = a.method
    cl = row.get("checklist") or []
    if cl and any(i["result"] != "untested" for i in cl) and a.functional_completeness is None:
        scored = [i for i in cl if i["result"] != "untested"]
        row["functional_completeness"] = sum(1 for i in scored if i["result"] == "pass") / len(scored)
        row["functional_basis"] = f"功能清单 {sum(1 for i in scored if i['result'] == 'pass')}/{len(scored)} 通过(测不了计失败)"
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
    rows.sort(key=lambda r: (r.get("tag") or "", r.get("plus_tag") or "", r["app"]))
    f.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n")
    if cl:
        print(f"checklist: {len(cl)} items from {row.get('checklist_source')}")
    print("caps: " + " ".join(row["caps"]))
    print(f"recorded {a.tag} {a.app}: " + ", ".join(f"{k}={row[k]}" for k in ("functional_completeness", "usability", "accuracy", "code_completeness", "duration_seconds", "tokens")))
    print("now rebuild: python3 tools/build_releases.py && git add releases && git commit -m 'evals: ...' && git push")


if __name__ == "__main__":
    main()
