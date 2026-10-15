#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_edit_discipline.py -- did changes actually go through safe_edit?

WHY
  safe_edit only helps if it is used. "Bypassing it costs nothing" is the biggest
  hole in the whole idea -- and it is not hypothetical: in the project this came
  from, the tool sat unused for three days while the same class of mistake kept
  landing in real files.

  So: every successful edit records a hash. This checker compares each protected
  file's current hash against the last record and reports

      "this file changed, but NOT through safe_edit"

  It does not PREVENT a bypass. It makes one VISIBLE.

HOW
  1. safe_edit writes .safe_edit_stamps.json on every successful edit.
  2. Point this checker at the files you care about -- guard-class files first
     (the tool itself, the checkers, the pipeline entry points), because
     silently loosening THOSE is the most dangerous kind of bypass: it would
     disable the gates themselves.

CONFIG
  Put the protected paths in .edit_discipline (one per line, relative to the
  directory containing this script). Lines starting with # are ignored.
  With no config file, it protects nothing and says so.

USAGE
    python check_edit_discipline.py              # check
    python check_edit_discipline.py --init       # record current state as baseline
    python check_edit_discipline.py --selftest   # positive + negative controls
Exit: 0 = clean, 1 = bypass detected, 2 = cannot run.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STAMP_FILE = HERE / ".safe_edit_stamps.json"
CONFIG = HERE / ".edit_discipline"


def protected() -> list:
    if not CONFIG.is_file():
        return []
    out = []
    for line in CONFIG.read_text(encoding="utf-8").split("\n"):
        s = line.split("#", 1)[0].strip()
        if s:
            out.append(s)
    return out


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def _load() -> dict:
    if not STAMP_FILE.is_file():
        return {}
    try:
        return json.loads(STAMP_FILE.read_text(encoding="utf-8")).get("files", {})
    except Exception:                                            # noqa: BLE001
        return {}


def check(quiet: bool = False) -> int:
    files, stamps = protected(), _load()
    ok, bad, warn = [], [], []
    for rel in files:
        f = HERE / rel
        if not f.is_file():
            warn.append("%-44s missing" % rel)
            continue
        cur = _sha(f)
        st = stamps.get(rel)
        if st is None:
            warn.append("%-44s never registered (run --init)" % rel)
        elif st.get("sha256") != cur:
            bad.append("%-44s CHANGED WITHOUT safe_edit (recorded %s / now %s)"
                       % (rel, st.get("sha256"), cur))
        else:
            ok.append("%-44s OK (last change went through safe_edit, %s)"
                      % (rel, st.get("ts", "?")))

    if not quiet:
        print("=" * 84)
        print("  edit discipline -- did changes go through safe_edit?")
        print("=" * 84)
        if not files:
            print("  No .edit_discipline file found -- nothing is protected.")
            print("  Create one with the files you want guarded, one per line.")
            print("=" * 84)
            return 2
        print("  protected %d | OK %d | BYPASSED %d | unregistered %d"
              % (len(files), len(ok), len(bad), len(warn)))
        print()
        for s in bad:
            print("  x", s)
        for s in warn:
            print("  !", s)
        if not bad and not warn:
            print("  OK  every protected file's last change went through safe_edit")
        print()
        print("  ==> %s" % ("FAILED: %d file(s) bypassed" % len(bad) if bad else "PASS"))
        print("=" * 84)
    return 1 if bad else 0


def init_baseline() -> int:
    files, stamps = protected(), _load()
    n = 0
    for rel in files:
        f = HERE / rel
        if not f.is_file():
            continue
        stamps[rel] = {"sha256": _sha(f),
                       "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "by": "baseline"}
        n += 1
    STAMP_FILE.write_text(json.dumps({"version": 1, "files": stamps},
                                     ensure_ascii=False, indent=2, sort_keys=True),
                          encoding="utf-8")
    print("registered %d baseline(s) -- from now on every change must go through safe_edit" % n)
    return 0


def selftest() -> int:
    import shutil
    import tempfile
    print("[selftest] bypass detection -- positive + negative controls")
    fails = []

    def chk(name, cond):
        print("  %s %s" % ("OK " if cond else "x  ", name))
        if not cond:
            fails.append(name)

    td = Path(tempfile.mkdtemp())
    victim = td / "victim.py"
    victim.write_text("x = 1\n", encoding="utf-8")
    saved = {k: globals()[k] for k in ("HERE", "STAMP_FILE", "CONFIG", "protected")}
    try:
        globals()["HERE"] = td
        globals()["STAMP_FILE"] = td / ".safe_edit_stamps.json"
        globals()["CONFIG"] = td / ".edit_discipline"
        globals()["CONFIG"].write_text("victim.py\n", encoding="utf-8")
        globals()["protected"] = lambda: ["victim.py"]

        init_baseline()
        chk("positive: unchanged after baseline passes", check(quiet=True) == 0)
        victim.write_text("x = 2\n", encoding="utf-8")
        chk("negative: bypass IS caught", check(quiet=True) == 1)
    finally:
        for k, v in saved.items():
            globals()[k] = v
        shutil.rmtree(td, ignore_errors=True)

    print("  ==> %s" % ("PASS" if not fails else "FAILED %d" % len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--init" in sys.argv:
        sys.exit(init_baseline())
    sys.exit(check())
