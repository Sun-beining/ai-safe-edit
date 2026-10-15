# ai-safe-edit

**The only safe entry point for an AI agent to edit files.**
Back up, validate, roll back on failure — and never leave a half-broken file behind.

[中文说明](README.zh.md) · MIT · Python 3.8+ · zero dependencies

---

## The problem

When an agent edits a file, it writes text. When that text is assembled from
shell heredocs, regex substitutions, or model-generated code, three things go
wrong often enough to actually hurt:

| Failure | What happens |
|---|---|
| **Shell escaping eats characters** | A literal `\n` inside a heredoc becomes a real newline and silently breaks the string it was part of |
| **Regex over-matches** | A pattern meant for one line crosses a string boundary and corrupts neighbouring code |
| **Nested quotes** | Putting `"` inside `"` when generating source is a syntax error — and a model produces it *often* |

The usual response is to write the lesson down. **That is not a constraint.**
The next session, or the next model, repeats the mistake.

> Real numbers from the project this came out of: **10 nested-quote errors in a
> single day**, all of them landing in the actual product files, because the
> edits bypassed the tool that existed to prevent exactly that.

> And a small confession, because it is the clearest illustration there is:
> **while writing the commit message for *this repository*, the same class of
> failure hit again.** The message contained `` `--selftest` `` inside a
> double-quoted shell string. The shell executed the backticks and deleted the
> word. The commit went in with a sentence missing its subject — and would have
> shipped that way, because nothing checks commit messages.
>
> Knowing about a failure mode is not protection from it. Only a gate is.


## What this does

It turns the lesson into a **gate**:

1. **Back up** before touching anything → `.safe_edit_backup/<timestamp>/`
2. **Validate** immediately after writing:
   `.py` → `compile()` · `.json` → `json.loads` · `.yaml` → `yaml.safe_load` · `.bat` → ASCII-only check
3. **Roll back automatically** if validation fails, and say so
4. **Non-zero exit code** on failure — a model or a script cannot miss it

A half-broken file is never left behind.

## Quick start

```bash
# edit with the safety net
python safe_edit.py --file app.py --replace "old text" "new text"

# same replacement across several files
python safe_edit.py --files a.py b.py --replace "old" "new"

# large blocks with quotes or non-ASCII text: read both sides from files
# (this is the mode to use when shell argument escaping is unreliable)
python safe_edit.py --file app.py --replace-with-file old.txt new.txt

# validate without writing
python safe_edit.py --check-only a.py b.py

# roll back to the most recent backup
python safe_edit.py --rollback app.py

# self-test: positive and negative controls
python safe_edit.py --selftest
```

On failure you get an unambiguous report and a clean file:

```
x  app.py   改 1 处后**校验失败** ⇒ **已自动回滚**：语法错 line 207: invalid syntax
            （备份 .safe_edit_backup/20261015-213510/app.py）
==> 1 个失败（已回滚）—— 调用方必须知道这次没改成     (exit code 1)
```

## Use it from an AI agent (MCP)

`mcp_server.py` exposes it over MCP — stdio, JSON-RPC 2.0, **no dependencies to
install**. Tools: `safe_edit`, `edit_check`, `edit_rollback`.

```json
{
  "mcpServers": {
    "ai-safe-edit": {
      "command": "python",
      "args": ["/absolute/path/to/ai-safe-edit/mcp_server.py"]
    }
  }
}
```

Works with any MCP client — including **DeepSeek Harness**, Claude Code, Cursor
and others.

## Why an agent should be forced through this

An agent that can write files freely will, eventually, write a broken one. The
question is whether that failure surfaces **immediately, with a rollback**, or
**later, as a mysterious error in a file nobody remembers touching**.

This project's answer, learned the hard way:

- **A lesson written down is not a constraint.** A lesson turned into a gate is.
- **The caller must be told when it failed.** Silent success is worse than loud
  failure — an agent that thinks its edit landed will build on top of it.

## Honest limits

- It **cannot** judge whether an edit is *correct* — only that the result still
  parses. Semantic correctness still needs tests, gates, and a human.
- It does **not prevent** a bypass. If you want bypasses to at least leave a
  trace, see the companion idea below.
- `.py` / `.json` / `.yaml` / `.bat` are the validated formats today. Other
  extensions are backed up and written, but not checked.

## Related work

This tool was built independently inside a hardware-design agent project. After
it was working, we read **DeepSeek Harness**'s source and found it solves the
same class of problem from the other end: *hardening an experiment requires
explicit user approval* (`install_bundle` + `pendingBuilds`), so nothing the
model writes reaches persistent configuration without a human.

The two are complementary:

| | DeepSeek Harness | ai-safe-edit |
|---|---|---|
| Approach | **Prevent** — a change needs approval before it persists | **Contain** — a change is validated and rolled back if it breaks |
| Strength | Nothing unauthorized gets written | Nothing broken survives, even unauthorized edits |

A companion idea in the same spirit: after every successful edit, record a hash
of the file. A checker can then compare the current hash against the last record
and flag **"this file was changed without going through the tool"** — turning a
silent bypass into a visible one.

## License

MIT
