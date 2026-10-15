#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcp_server.py -- expose ai-safe-edit as an MCP server (stdio, no dependencies).

WHY
  safe_edit is useful on its own, but the point of this project is that an AI
  agent uses it. MCP (Model Context Protocol) is how agents pick up tools, and
  it is what DeepSeek Harness, Claude Code, Cursor and others speak.

  The server is deliberately tiny and dependency-free: JSON-RPC 2.0 over stdio,
  three methods (initialize / tools/list / tools/call). Nothing to pip install.

TOOLS EXPOSED
  safe_edit       edit files with backup + validation + automatic rollback
  edit_check      validate files without writing
  edit_rollback   restore the most recent backup

REGISTER (example)
  {
    "mcpServers": {
      "ai-safe-edit": {
        "command": "python",
        "args": ["/absolute/path/to/mcp_server.py"]
      }
    }
  }
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAFE_EDIT = HERE / "safe_edit.py"

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "safe_edit",
        "description": (
            "Edit a file with backup, validation and automatic rollback.\n\n"
            "Use this INSTEAD of writing files directly: shell heredocs eat "
            "escape characters (a literal \\n becomes a real newline and breaks "
            "the string), regex substitutions over-match across string "
            "boundaries, and nested quotes are a syntax error. This tool backs "
            "up first, validates the result (.py/.json/.yaml/.bat), and rolls "
            "back automatically if validation fails -- so a half-broken file is "
            "never left behind.\n\n"
            "Returns the tool's report. A non-zero exit code means NOTHING was "
            "changed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string",
                         "description": "file to edit (relative to cwd)"},
                "old": {"type": "string",
                        "description": "exact text to find"},
                "new": {"type": "string",
                        "description": "replacement text"},
                "regex": {"type": "boolean", "default": False,
                          "description": "treat `old` as a regular expression"},
            },
            "required": ["file", "old", "new"],
        },
    },
    {
        "name": "edit_check",
        "description": "Validate files (syntax only) without writing anything.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "files": {"type": "array", "items": {"type": "string"},
                          "description": "files to validate"},
            },
            "required": ["files"],
        },
    },
    {
        "name": "edit_rollback",
        "description": "Restore a file from its most recent backup.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "file to roll back"},
            },
            "required": ["file"],
        },
    },
]


def _run(args: list) -> tuple:
    r = subprocess.run([sys.executable, str(SAFE_EDIT)] + args,
                       capture_output=True, text=True, encoding="utf-8")
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def call_tool(name: str, a: dict) -> tuple:
    if name == "safe_edit":
        flag = "--regex" if a.get("regex") else "--replace"
        rc, out = _run(["--file", a["file"], flag, a["old"], a["new"]])
    elif name == "edit_check":
        rc, out = _run(["--check-only"] + list(a["files"]))
    elif name == "edit_rollback":
        rc, out = _run(["--rollback", a["file"]])
    else:
        return True, "unknown tool: %s" % name
    return (rc != 0), out


def reply(mid, result=None, error=None) -> None:
    msg = {"jsonrpc": "2.0", "id": mid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:                                        # noqa: BLE001
            continue
        mid, method = req.get("id"), req.get("method")

        if method == "initialize":
            reply(mid, {"protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "ai-safe-edit", "version": "1.0.0"}})
        elif method == "tools/list":
            reply(mid, {"tools": TOOLS})
        elif method == "tools/call":
            p = req.get("params") or {}
            is_err, out = call_tool(p.get("name", ""), p.get("arguments") or {})
            reply(mid, {"content": [{"type": "text", "text": out}],
                        "isError": is_err})
        elif method in ("notifications/initialized", "initialized"):
            pass                                     # notification, no reply
        else:
            if mid is not None:
                reply(mid, error={"code": -32601,
                                  "message": "method not found: %s" % method})
    return 0


if __name__ == "__main__":
    sys.exit(main())
