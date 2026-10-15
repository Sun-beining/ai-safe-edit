#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
safe_edit -- the only safe entry point for editing files from an AI agent.
            (中文说明见下方 "原项目注释" 一节。)


WHY THIS EXISTS
  AI agents edit files by writing text. When that text is assembled from shell
  heredocs, regex substitutions or generated code, three things go wrong often
  enough to matter:

    1. Shell escaping eats characters. A literal backslash-n inside a heredoc
       becomes a real newline and silently breaks the string it was part of.
    2. Regex substitutions match more than intended. A pattern meant to touch one
       line crosses a string boundary and corrupts neighbouring code.
    3. Nested quotes. Putting double quotes inside double quotes when generating
       source is a syntax error, and a model produces it surprisingly often.

  The usual response is to write the lesson down. That is not a constraint --
  the next session, or the next model, repeats the mistake. This tool turns the
  lesson into a gate: back up, validate, roll back on failure, and tell the
  caller through the exit code that nothing was changed.

WHAT IT ENFORCES
  1. Backup before touching anything  -> .safe_edit_backup/<timestamp>/
  2. Validate immediately after writing:
       .py   -> compile()       .json -> json.loads
       .yaml -> yaml.safe_load  .bat  -> ASCII-only check
  3. On validation failure: automatic rollback plus a report saying so.
     A half-broken file is never left behind.
  4. Non-zero exit code on failure, so a model or a script cannot miss it.

USAGE HIGHLIGHTS
    python safe_edit.py --file a.py --replace "old" "new"
    python safe_edit.py --files a.py b.py --replace "old" "new"
    python safe_edit.py --file a.py --replace-with-file old.txt new.txt
        (use this for large blocks with quotes or non-ASCII text, where shell
         argument escaping is unreliable)
    python safe_edit.py --check-only a.py b.py
    python safe_edit.py --rollback a.py
    python safe_edit.py --selftest        # positive + negative controls

HONEST LIMITS
  * It cannot judge whether an edit is CORRECT, only that the result still
    parses. Semantic correctness still needs tests, gates and a human.
  * It does not prevent a bypass. Pair it with check_edit_discipline.py so a
    bypass at least leaves a trace.


--------------------------------------------------------------------------------
原项目注释（中文原文，保留以便对照）

【为什么必须做成工具】（用户 2026-10-12 的要求）
  > 「**不要光记教训不落实成工具约束**，大模型的思考跟人的思考已经很接近，
  >   总会有**没考虑到的时候**，必须要**借助其它工具或人或流程来提醒和约束**。」

  ★ 直接触发它的现场事故：
    我用**正则批量改 5 个 .py 文件** ⇒ **改坏 2 个**：
      · 插入块落进了函数体内部
      · 正则贪婪匹配**把缩进吃掉了** ⇒ `IndentationError`
    事后我把"正则改代码危险"写进了 memory ——
    **但那是"记教训"，不是"约束"** ✗。下次我（或换个模型）还会犯。
  ⇒ 所以做成工具：**任何批量改动都必须走这里**，
    它**强制**备份 + **强制**语法校验 + **失败自动回滚**。

【它强制做什么】
  1. **改前备份** → `.safe_edit_backup/<时间戳>/`
  2. **改后立即校验**：`.py` → `compile()`；`.json` → `json.loads`；
     `.yaml` → `yaml.safe_load`；`.bat` → **ASCII-only 检查**
  3. ★ **校验失败 ⇒ 自动回滚 + 明确报告**（绝不留下半坏的文件）
  4. 退出码非 0 ⇒ 调用方（模型/脚本）**必须知道"这次改失败了"**

【用法】
    # 单文件替换
    python safe_edit.py --file a.py --replace "旧串" "新串"

    # 多文件同一替换（**正则批量改代码的正确打开方式**）
    python safe_edit.py --files a.py b.py c.py --replace "旧" "新"

    # 用正则（危险，但工具会兜住）
    python safe_edit.py --file a.py --regex "pattern" "repl"

    # 只验不写
    python safe_edit.py --check-only a.py b.py

    # 回滚到最近一次备份
    python safe_edit.py --rollback a.py

★ **限制（诚实边界）**：
  · 它**不能**判断"改得对不对"（语义）—— 只能保证"改完还能解析"
  · 所以**逻辑正确性**仍然要靠 gates / 判据 / 人
  · 但它把"**手滑改坏文件**"这类事故**彻底挡住**（这类占我实际错误的一大半）
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKUP_ROOT = HERE / ".safe_edit_backup"

# ── ★★ 「盖章」机制（2026-10-15 新增）────────────────────────────────────
#
# 【为什么】用户 2026-10-15 问「safe_edit 是什么？」—— 我一查：
#   ★ 它**建好 3 天，一次都没被用过**（备份目录时间戳停在 10-12），
#     而我当天用 `python - <<EOF` 直接写文件十几次、犯了 10 次嵌套引号 ✗
#   ⇒ 根因**不是"没这个工具"，而是"它只是一个可用工具、不是必经关口"** ——
#     **绕过它零成本** ✗✗
#
# 【机制】safe_edit 每改成功一个文件，就把「改后的 sha256」记进 STAMP_FILE。
#   检查器（check_edit_discipline.py）逐条核对：
#     · 文件当前哈希 **≠** 最后记录的哈希 ⇒ **改过但没走 safe_edit** ⇒ 报红
#
# 【和 DSH 的关系】DSH 的做法是「固化必须走审批」（install_bundle 要用户显式批准）。
#   我们**没有审批链路**，所以退一步：**至少让"绕过"留下痕迹**。
#   ★ 这是**更低一级的保障**，不是等价物 —— 如实标注。
STAMP_FILE = HERE / ".safe_edit_stamps.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def stamp_record(path: Path, kind: str = "safe_edit") -> None:
    """记下「这个文件的当前内容是被谁改的」。kind='baseline' 表示首次登记。"""
    try:
        d = json.loads(STAMP_FILE.read_text(encoding="utf-8")) if STAMP_FILE.is_file() else {}
    except Exception:                                            # noqa: BLE001
        d = {}
    files = d.setdefault("files", {})
    try:
        key = str(path.resolve().relative_to(HERE)).replace("\\", "/")
    except Exception:                                            # noqa: BLE001
        key = str(path).replace("\\", "/")
    files[key] = {"sha256": _sha(path),
                  "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                  "by": kind}
    d["version"] = 1
    try:
        STAMP_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2,
                                         sort_keys=True), encoding="utf-8")
    except Exception:                                            # noqa: BLE001
        pass      # 盖章失败不该让改动失败（它是附加保障，不是主路径）


def _validate(path: Path) -> tuple:
    """校验文件是否还能被解析。返回 (ok, 说明)。"""
    s = path.suffix.lower()
    try:
        if s == ".py":
            compile(path.read_text(encoding="utf-8", errors="replace"), str(path), "exec")
            return True, ""
        if s == ".json":
            json.loads(path.read_text(encoding="utf-8", errors="replace"))
            return True, ""
        if s in (".yaml", ".yml"):
            try:
                import yaml                                # noqa: PLC0415
                yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
            except ImportError:
                return True, "（无 yaml 模块，跳过校验）"
            return True, ""
        if s == ".bat":
            raw = path.read_bytes()
            bad = [i for i, b in enumerate(raw) if b > 127]
            if bad:
                return False, ("含 %d 个非 ASCII 字节（首个偏移 %d）—— "
                               "Windows .bat 会『假成功』" % (len(bad), bad[0]))
            return True, ""
        return True, "（跳过：不校验的类型 %s）" % s
    except SyntaxError as ex:
        return False, "语法错 line %s: %s" % (ex.lineno, ex.msg)
    except Exception as ex:                                # noqa: BLE001
        return False, "%s: %s" % (type(ex).__name__, ex)


def _backup(path: Path, stamp: str) -> Path:
    d = BACKUP_ROOT / stamp
    d.mkdir(parents=True, exist_ok=True)
    dst = d / path.name
    shutil.copy2(path, dst)
    return dst


def edit_one(path: Path, old: str, new: str, use_regex: bool, stamp: str,
             dry: bool = False) -> tuple:
    """改一个文件：备份 → 改 → 校验 → (失败)回滚。返回 (ok, 说明)。"""
    if not path.is_file():
        return False, "文件不存在"
    src = path.read_text(encoding="utf-8", errors="replace")
    if use_regex:
        new_src, n = re.subn(old, new, src)
    else:
        n = src.count(old)
        new_src = src.replace(old, new)
    if n == 0:
        return True, "无需改动（没找到匹配）"
    if dry:
        return True, "dry-run：会替换 %d 处" % n

    bak = _backup(path, stamp)
    path.write_text(new_src, encoding="utf-8", newline="\n")
    ok, why = _validate(path)
    if ok:
        # ★★ 盖章：记下"这个文件的当前内容是被 safe_edit 改的"
        #   ⇒ 检查器靠它判断"有人绕过了 safe_edit"（见文件头 STAMP_FILE 的说明）
        stamp_record(path, "safe_edit")
        return True, "改 %d 处；校验通过（备份 %s）" % (n, bak)
    # ★★ 校验失败 ⇒ **自动回滚**
    shutil.copy2(bak, path)
    return False, ("改 %d 处后**校验失败** ⇒ **已自动回滚**：%s（备份 %s）"
                   % (n, why, bak))


def rollback(path: Path) -> tuple:
    """回滚到**最近一次**备份。"""
    if not BACKUP_ROOT.is_dir():
        return False, "没有备份目录"
    cands = sorted([d for d in BACKUP_ROOT.iterdir() if d.is_dir()],
                   key=lambda d: d.name, reverse=True)
    for d in cands:
        f = d / path.name
        if f.is_file():
            shutil.copy2(f, path)
            return True, "已回滚到 %s" % f
    return False, "备份里没有 %s" % path.name


def selftest() -> int:
    print("【safe_edit.py 自检】阳阴双对照（离线）")
    import tempfile
    fails = []

    def chk(name, got, want):
        ok = got == want
        print("  %s %-48s %s" % ("OK " if ok else "x  ", name,
                                 "" if ok else "得 %r / 期望 %r" % (got, want)))
        if not ok:
            fails.append(name)

    d = Path(tempfile.mkdtemp())
    # ① ★ 核心：**改坏 .py ⇒ 必须自动回滚**（这是本工具存在的理由）
    f = d / "t.py"
    f.write_text('x = 1\n', encoding="utf-8")
    ok, why = edit_one(f, "x = 1", 'x = "中文"引号"嵌套"', False, "T")
    chk("改坏 ⇒ 报告失败", ok, False)
    chk("改坏 ⇒ 内容已回滚", f.read_text(encoding="utf-8"), 'x = 1\n')
    # ② 阴性：改对 ⇒ 保留
    ok2, _ = edit_one(f, "x = 1", "x = 2", False, "T")
    chk("改对 ⇒ 成功", ok2, True)
    chk("改对 ⇒ 内容保留", f.read_text(encoding="utf-8"), "x = 2\n")
    # ③ 没匹配 ⇒ 不算错（但也不改）
    ok3, why3 = edit_one(f, "不存在的串", "x", False, "T")
    chk("无匹配 ⇒ 不报错", ok3, True)
    # ④ .bat 非 ASCII ⇒ 必须被拦
    b = d / "t.bat"
    b.write_bytes(b"echo ok\n")
    ok4, _ = edit_one(b, "echo ok", "echo \u4e2d\u6587", False, "T")
    chk(".bat 加中文 ⇒ 被拦并回滚", ok4, False)
    # ⑤ 回滚功能可用（用刚才的备份）
    chk("回滚能找到备份", rollback(f)[0], True)

    print("  ==> %s" % ("通过" if not fails else "失败 %d 项" % len(fails)))
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="安全批量改文件（改→验→失败回滚）")
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--replace", nargs=2, metavar=("OLD", "NEW"))
    # ADDED 2026-10-15: inserting a large block (with CJK text and quotes)
    # through shell arguments is impossible to quote correctly -- and the
    # temptation is then to bypass this tool altogether, which is exactly the
    # failure this tool exists to prevent. So: read both sides from files.
    ap.add_argument("--replace-with-file", nargs=2, metavar=("OLD_FILE", "NEW_FILE"),
                    help="same as --replace, but OLD and NEW are read from files")
    ap.add_argument("--regex", nargs=2, metavar=("PAT", "REPL"))
    ap.add_argument("--check-only", nargs="*")
    ap.add_argument("--rollback", metavar="FILE")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()

    if a.rollback:
        ok, why = rollback(Path(a.rollback))
        print(("OK  " if ok else "x   ") + why)
        return 0 if ok else 1

    if a.check_only is not None:
        bad = 0
        for p in a.check_only:
            path = Path(p)
            ok, why = _validate(path)
            print("  %s %s %s" % ("OK " if ok else "x  ", path, why))
            bad += 0 if ok else 1
        return 1 if bad else 0

    targets = [Path(p) for p in (a.file + a.files)]
    if not targets or not (a.replace or a.regex or a.replace_with_file):
        ap.error("需要 --file/--files + --replace/--regex（或 --selftest/--check-only）")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    if a.replace_with_file:
        _of, _nf = a.replace_with_file
        old = Path(_of).read_text(encoding="utf-8")
        new = Path(_nf).read_text(encoding="utf-8")
    else:
        old, new = (a.regex if a.regex else a.replace)
    use_re = bool(a.regex)
    print("=" * 76)
    print("  safe_edit：%d 个文件，%s" % (len(targets), "正则" if use_re else "字面替换"))
    print("=" * 76)
    n_bad = 0
    for t in targets:
        ok, why = edit_one(t, old, new, use_re, stamp, a.dry_run)
        print("  %s %-52s %s" % ("OK " if ok else "x  ", t, why))
        n_bad += 0 if ok else 1
    print()
    print("  ==> %s" % ("全部成功" if not n_bad else
                       "**%d 个失败（已回滚）** —— 调用方必须知道这次没改成" % n_bad))
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())


# ★★ 工具自描述（tools_bridge 自动扫描，供大模型看见）
TOOL_SPEC = {
    "name": "safe_edit",
    "stage": "-",
    "needs_proj": False,
    "desc": "改文件的唯一安全入口：备份 → 改 → 校验 → 失败自动回滚。",
}
