#!/usr/bin/env python3
"""Regression test for tmux completion state routing."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WATCHER = ROOT / "tmux_completion_watcher.py"


class TmuxCompletionWatcherTest(unittest.TestCase):
    def test_matching_marker_updates_only_the_own_task_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            session_dir = base / "session"
            tasks_dir = base / "tasks"
            session_dir.mkdir()
            tasks_dir.mkdir()
            task_id = "task-current"
            marker = f"TASK_COMPLETE:{task_id}"
            meta = {
                "task_id": task_id,
                "task_name": "current",
                "status": "running",
                "completion_marker": marker,
            }
            (session_dir / "task-meta.json").write_text(json.dumps(meta))
            (tasks_dir / f"{task_id}.json").write_text(json.dumps(meta))
            old = {"task_id": "task-old", "status": "running"}
            (tasks_dir / "task-old.json").write_text(json.dumps(old))
            snapshot = base / "pane.txt"
            snapshot.write_text(f"finished work\n{marker}\n")

            result = subprocess.run(
                [
                    "python3", str(WATCHER), "--task-id", task_id,
                    "--session-dir", str(session_dir), "--tasks-dir", str(tasks_dir),
                    "--snapshot-file", str(snapshot), "--once",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((session_dir / "task-meta.json").read_text())["status"], "done")
            self.assertEqual(json.loads((tasks_dir / f"{task_id}.json").read_text())["status"], "done")
            self.assertEqual(json.loads((tasks_dir / "task-old.json").read_text())["status"], "running")


if __name__ == "__main__":
    unittest.main()
