#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd), flush=True)
    completed = subprocess.run(cmd, text=True, capture_output=True)
    if completed.stdout:
        print(completed.stdout.rstrip(), flush=True)
    if completed.stderr:
        print(completed.stderr.rstrip(), file=sys.stderr, flush=True)
    if check and completed.returncode != 0:
        raise subprocess.CalledProcessError(completed.returncode, cmd, completed.stdout, completed.stderr)
    return completed


def existing_paths(paths: list[str]) -> list[Path]:
    output: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.exists() and path.is_file():
            output.append(path)
    return output


def snapshot_files(paths: list[Path], snapshot_dir: Path) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for path in paths:
        target = snapshot_dir / path.as_posix()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        pairs.append((path, target))
    return pairs


def restore_snapshot(pairs: list[tuple[Path, Path]]) -> None:
    for path, source in pairs:
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, path)


def branch_name(default: str = "main") -> str:
    raw = os.getenv("GITHUB_REF_NAME", "").strip()
    if raw:
        return raw
    completed = run(["git", "branch", "--show-current"])
    return completed.stdout.strip() or default


def configure_git() -> None:
    run(["git", "config", "user.name", os.getenv("GIT_COMMITTER_NAME", "ThieucutooBot")])
    run(["git", "config", "user.email", os.getenv("GIT_COMMITTER_EMAIL", "bot@thieucutoo.local")])
    run(["git", "rebase", "--abort"])
    run(["git", "merge", "--abort"])


def stage_paths(paths: list[Path]) -> None:
    for path in paths:
        run(["git", "add", "-f", "--", path.as_posix()])


def commit_and_push(message: str, branch: str) -> bool:
    if run(["git", "diff", "--staged", "--quiet"]).returncode == 0:
        print("No data changes to commit.", flush=True)
        return True
    if run(["git", "commit", "-m", message]).returncode != 0:
        print("::warning::Data commit failed; report was already sent.", flush=True)
        return False
    return run(["git", "push", "origin", f"HEAD:{branch}"]).returncode == 0


def safe_commit(message: str, paths: list[str], attempts: int) -> int:
    branch = branch_name()
    files = existing_paths(paths)
    if not files:
        print("No data files exist; nothing to commit.", flush=True)
        return 0
    configure_git()
    with tempfile.TemporaryDirectory(prefix="thieucutoo-data-") as tmp:
        pairs = snapshot_files(files, Path(tmp))
        for attempt in range(1, max(1, attempts) + 1):
            print(f"Data commit attempt {attempt}/{attempts} on branch {branch}", flush=True)
            run(["git", "fetch", "origin", branch])
            reset_target = f"origin/{branch}"
            run(["git", "reset", "--mixed", reset_target])
            restore_snapshot(pairs)
            stage_paths(files)
            if commit_and_push(message, branch):
                print("Data commit/push finished.", flush=True)
                return 0
            print("::warning::Data push failed; retrying with latest remote and local scanner output reapplied.", flush=True)
        print("::warning::Data push still failed after retries. Keeping workflow green; uploaded artifact contains this run's data.", flush=True)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Commit generated scanner data without letting git conflicts kill the workflow.")
    parser.add_argument("--message", required=True)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--paths", nargs="+", required=True)
    args = parser.parse_args()
    return safe_commit(args.message, args.paths, args.attempts)


if __name__ == "__main__":
    raise SystemExit(main())
