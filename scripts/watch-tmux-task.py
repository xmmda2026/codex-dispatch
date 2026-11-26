#!/usr/bin/env python3
"""Task-scoped tmux completion watcher.

For interactive Codex runs this process is the only component allowed to
transition task JSON from running to done/failed.  It deliberately watches
one tmux session and one completion marker, so output from another task can
never release a Batch Dispatch waiter.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def atomic_json(path: Path, value: dict) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temp, path)


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def lock_session(meta_path: Path):
    """Serialize terminal writes with a same-session redispatch."""
    lock_handle = (meta_path.parent / "dispatch.lock").open("a", encoding="utf-8")
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
    return lock_handle


def append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{now()}] {message}\n")


def capture(socket: str, session: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["tmux", "-S", socket, "capture-pane", "-p", "-J", "-t", session, "-S", "-160"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode == 0, result.stdout


def finish(meta_path: Path, task_path: Path, task_id: str, status: str, reason: str, output: str, log_path: Path) -> None:
    timestamp = now()
    lock_handle = lock_session(meta_path)
    try:
        meta = read_json(meta_path)
        if meta.get("task_id") == task_id:
            meta.update(
                {
                    "status": status,
                    "completed_at": timestamp,
                    "status_writer": "tmux-watcher",
                    "watcher_log": str(log_path),
                    "completion_evidence": reason,
                }
            )
            if status == "failed":
                meta["failure_reason"] = reason
            atomic_json(meta_path, meta)
        else:
            append_log(log_path, f"skip session metadata for stale task_id={task_id}")

        # A task record is normally created before the watcher starts.  Do not
        # copy session metadata as a fallback: it may already belong to a newer
        # task after a same-session redispatch.
        task = read_json(task_path) if task_path.exists() else {"task_id": task_id}
        task.update(
            {
                "task_id": task_id,
                "status": status,
                "completed_at": timestamp,
                "timestamp": timestamp,
                "status_writer": "tmux-watcher",
                "watcher_log": str(log_path),
                "completion_evidence": reason,
                "output": output[-6000:],
            }
        )
        if status == "failed":
            task["failure_reason"] = reason
        atomic_json(task_path, task)
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()
    append_log(log_path, f"terminal status={status} reason={reason}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--tasks-dir", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--tmux-session", required=True)
    parser.add_argument("--tmux-socket", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--log-file", required=True)
    args = parser.parse_args()

    session_dir = Path(args.session_dir)
    meta_path = session_dir / "task-meta.json"
    task_path = Path(args.tasks_dir) / f"{args.task_id}.json"
    log_path = Path(args.log_file)
    if not meta_path.is_file() or not task_path.is_file():
        append_log(log_path, "cannot start: task metadata missing")
        return 2
    meta = read_json(meta_path)
    if meta.get("task_id") != args.task_id or meta.get("run_mode") != "tmux":
        append_log(log_path, "cannot start: task identity or mode mismatch")
        return 2
    marker = meta.get("completion_marker", "")
    if not marker:
        append_log(log_path, "cannot start: completion marker missing")
        return 2

    append_log(log_path, f"watching task_id={args.task_id} session={args.tmux_session}")
    started = time.monotonic()
    missing_since: float | None = None
    while time.monotonic() - started < args.timeout_seconds:
        ok, output = capture(args.tmux_socket, args.tmux_session)
        if not ok:
            missing_since = missing_since or time.monotonic()
            # Startup has a short race; a session that disappears afterwards is failure.
            if time.monotonic() - missing_since >= 15:
                finish(meta_path, task_path, args.task_id, "failed", "tmux_session_missing", "", log_path)
                return 1
        else:
            missing_since = None
            if marker in output:
                # Require the same marker one second later, avoiding a partial capture race.
                time.sleep(1)
                confirmed, confirmed_output = capture(args.tmux_socket, args.tmux_session)
                if confirmed and marker in confirmed_output:
                    finish(meta_path, task_path, args.task_id, "done", "completion_marker_confirmed", confirmed_output, log_path)
                    return 0
        elapsed = time.monotonic() - started
        time.sleep(2 if elapsed < 30 else 10)

    finish(meta_path, task_path, args.task_id, "failed", "watcher_timeout", "", log_path)
    return 1


if __name__ == "__main__":
    sys.exit(main())
