import pytest
from fanout_helpers import BASE_CARDS, BASE_HUB

from hubfile import parse_hub, set_hub_values
from scratch_repo import ScratchRepo

HUB_TEXT = (__import__("fanout").FIXTURES / "platform.yaml").read_text()


def test_parse_reads_five_keys_and_skips_comments():
    assert list(BASE_HUB) == ["http.default_timeout", "http.default_rate_limit", "backup.policy", "dependencies.pinning", "availability.min_instances"]


def test_set_changes_only_named_lines():
    updated = set_hub_values(HUB_TEXT, {"http.default_timeout": "5s"})
    assert parse_hub(updated) == {**BASE_HUB, "http.default_timeout": "5s"}
    assert [line for line in updated.splitlines() if line.startswith("#")] == [line for line in HUB_TEXT.splitlines() if line.startswith("#")]


def test_values_with_colon_or_hash_round_trip():
    updated = set_hub_values(HUB_TEXT, {"backup.policy": "daily: 30d # kept"})
    assert parse_hub(updated)["backup.policy"] == "daily: 30d # kept"


def test_unknown_key_and_multiline_value_are_rejected():
    with pytest.raises(ValueError):
        set_hub_values(HUB_TEXT, {"nope": "1"})
    with pytest.raises(ValueError):
        set_hub_values(HUB_TEXT, {"backup.policy": "a\nb"})


def make_repo(tmp_path):
    return ScratchRepo(tmp_path / "scratch", {**BASE_CARDS, "platform.yaml": HUB_TEXT})


def commit_hub(repo, name, key, value, card=None):
    worktree = repo.add_worktree(name)
    files = {"platform.yaml": set_hub_values(HUB_TEXT, {key: value})}
    if card:
        files[card] = BASE_CARDS[card] + "\nedited\n"
    repo.write(worktree, files)
    assert repo.commit(worktree, f"fix: {name}")


def test_same_hub_line_changed_twice_is_a_textual_conflict(tmp_path):
    repo = make_repo(tmp_path)
    commit_hub(repo, "w1", "http.default_timeout", "5s", "bookings-api.md")
    commit_hub(repo, "w2", "http.default_timeout", "10s", "notifications.md")
    conflicts = repo.merge_branches(["w1", "w2"])
    assert [(c.branch, c.files) for c in conflicts] == [("w2", ("platform.yaml",))]
    merged = repo.merged_worktree()
    assert (merged / "bookings-api.md").read_text().endswith("edited\n")
    assert (merged / "notifications.md").read_text().endswith("edited\n")


def test_different_hub_lines_merge_cleanly(tmp_path):
    repo = make_repo(tmp_path)
    commit_hub(repo, "w1", "http.default_timeout", "5s")
    commit_hub(repo, "w2", "backup.policy", "daily")
    assert repo.merge_branches(["w1", "w2"]) == []
    assert parse_hub((repo.merged_worktree() / "platform.yaml").read_text())["backup.policy"] == "daily"


def test_conflict_outside_the_hub_file_raises(tmp_path):
    repo = make_repo(tmp_path)
    for name in ("w1", "w2"):
        worktree = repo.add_worktree(name)
        repo.write(worktree, {"bookings-api.md": name})
        repo.commit(worktree, name)
    with pytest.raises(RuntimeError, match="outside platform.yaml"):
        repo.merge_branches(["w1", "w2"])
