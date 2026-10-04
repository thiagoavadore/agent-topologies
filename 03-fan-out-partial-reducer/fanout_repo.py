"""A throwaway git repo with one worktree per worker, merged with real `git merge`."""

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fanout", "GIT_AUTHOR_EMAIL": "fanout@example.invalid",
    "GIT_COMMITTER_NAME": "fanout", "GIT_COMMITTER_EMAIL": "fanout@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
}
# CHECKER.md is the answer key: it must never be in a repo a worker can read.
ANSWER_KEY = ("CHECKER.md",)


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    completed = subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, capture_output=True, text=True)
    if check and completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {completed.stderr.strip() or completed.stdout.strip()}")
    return completed


@dataclass(frozen=True)
class TextualConflict:
    branch: str
    files: tuple[str, ...]


class ScratchRepo:
    """`<base>/main` holds a copy of `source` as a git repo; `<base>/<name>` is the worktree of branch `<name>`."""

    def __init__(self, base: Path, source: Path) -> None:
        self.base = base
        self.main = base / "main"
        shutil.copytree(source, self.main, ignore=shutil.ignore_patterns(*ANSWER_KEY, "__pycache__", ".git"))
        git(self.main, "init", "-q", "-b", "main")
        self.commit(self.main, "chore: Harbour Bikes as given")

    def write(self, worktree: Path, files: dict[str, str]) -> None:
        for name, text in files.items():
            path = worktree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def commit(self, worktree: Path, message: str) -> bool:
        """Commit every change in `worktree`; False when there was nothing to commit."""
        git(worktree, "add", "-A")
        if git(worktree, "diff", "--cached", "--quiet", check=False).returncode == 0:
            return False
        git(worktree, "commit", "-q", "-m", message)
        return True

    def add_worktree(self, name: str) -> Path:
        path = self.base / name
        git(self.main, "worktree", "add", "-q", "-b", name, str(path), "main")
        return path

    def show(self, branch: str, name: str) -> str:
        return git(self.main, "show", f"{branch}:{name}").stdout

    def diff(self, branch: str) -> str:
        """What `branch` changed against `main`, as a unified diff."""
        return git(self.main, "diff", "main", branch).stdout

    def merge_branches(self, branches: list[str]) -> list[TextualConflict]:
        """Merge `branches` in order into the `merged` worktree, keeping the earlier side of any conflicted file.

        Each service belongs to one worker, so only `platform.yaml` can conflict. The harness rewrites it
        afterwards from the arm's resolution, so the git-level pick here is never the final value.
        """
        merged = self.add_worktree("merged")
        conflicts = []
        for branch in branches:
            if git(merged, "merge", "--no-ff", "--no-commit", branch, check=False).returncode != 0:
                files = tuple(sorted(git(merged, "diff", "--name-only", "--diff-filter=U").stdout.split()))
                conflicts.append(TextualConflict(branch, files))
                for name in files:
                    git(merged, "checkout", "--ours", "--", name)
            git(merged, "add", "-A")
            git(merged, "commit", "-q", "--no-edit", "--allow-empty", "-m", f"merge: {branch}")
        return conflicts

    def merged_worktree(self) -> Path:
        return self.base / "merged"
