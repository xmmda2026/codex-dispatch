#!/usr/bin/env python3
"""Write a tmux task's explicit completion marker to that task only."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def read_json(path: Path) -> dict:
    with path.open() as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def atomic_write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def snapshot(args: argparse.Namespace) -> str:
    if args.snapshot_file:
        return Path(args.snapshot_file).read_text()
    command = ["tmux", "-S", args.socket, "capture-pane", "-t", args.tmux_session, "-p", "-S", "-500"]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        return ""
    return result.stdout


def complete(args: argparse.Namespace) -> bool:
    meta_path = Path(args.session_dir) / "task-meta.json"
    task_path = Path(args.tasks_dir) / f"{args.task_id}.json"
    meta = read_json(meta_path)
    if meta.get("task_id") != args.task_id:
        raise ValueError("task id does not match session metadata")
    marker = meta.get("completion_marker")
    if not isinstance(marker, str) or not marker:
        raise ValueError("missing completion marker")
    if marker not in snapshot(args):
        return False

    timestamp = datetime.now(timezone.utc).isoformat()
    for path in (meta_path, task_path):
        value = read_json(path)
        if value.get("task_id") != args.task_id:
            raise ValueError(f"task id does not match: {path}")
        value.update({
            "status": "done",
            "completed_at": timestamp,
            "completion_source": "tmux_explicit_marker",
        })
        atomic_write(path, value)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--tasks-dir", required=True)
    parser.add_argument("--tmux-session")
    parser.add_argument("--socket", default="/root/clawdbot-tmux-sockets/codex-code.sock")
    parser.add_argument("--snapshot-file")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=43200)
    args = parser.parse_args()
    if not args.snapshot_file and not args.tmux_session:
        parser.error("--tmux-session is required without --snapshot-file")

    deadline = time.monotonic() + args.timeout_seconds
    while True:
        if complete(args):
            return 0
        if args.once or time.monotonic() >= deadline:
            return 1
        time.sleep(1)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"tmux completion watcher error: {error}", file=sys.stderr)
        raise SystemExit(2)
