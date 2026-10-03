"""A throwaway git repo with one worktree per worker, merged with real `git merge`."""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fanout", "GIT_AUTHOR_EMAIL": "fanout@example.invalid",
    "GIT_COMMITTER_NAME": "fanout", "GIT_COMMITTER_EMAIL": "fanout@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
}


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
    """`<base>/main` holds the repo; `<base>/<name>` is the worktree of branch `<name>`."""

    def __init__(self, base: Path, files: dict[str, str]) -> None:
        self.base = base
        self.main = base / "main"
        self.main.mkdir(parents=True)
        git(self.main, "init", "-q", "-b", "main")
        self.write(self.main, files)
        self.commit(self.main, "chore: cards and platform.yaml")

    def write(self, worktree: Path, files: dict[str, str]) -> None:
        for name, text in files.items():
            (worktree / name).write_text(text)

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

    def merge_branches(self, branches: list[str], hub_file: str = "platform.yaml") -> list[TextualConflict]:
        """Merge `branches` in order into the `merged` worktree; keep `main`'s hub file on a textual conflict.

        Only the hub file may conflict (every card belongs to one worker). The harness rewrites the hub
        file afterwards from the arm's resolution, so the git-level pick here is never the final value.
        """
        merged = self.add_worktree("merged")
        conflicts = []
        for branch in branches:
            if git(merged, "merge", "--no-ff", "--no-commit", branch, check=False).returncode != 0:
                files = tuple(sorted(git(merged, "diff", "--name-only", "--diff-filter=U").stdout.split()))
                if files != (hub_file,):
                    raise RuntimeError(f"merge of {branch} conflicted outside {hub_file}: {files}")
                conflicts.append(TextualConflict(branch, files))
                git(merged, "checkout", "--ours", "--", hub_file)
            git(merged, "add", "-A")
            git(merged, "commit", "-q", "--no-edit", "--allow-empty", "-m", f"merge: {branch}")
        return conflicts

    def merged_worktree(self) -> Path:
        return self.base / "merged"
