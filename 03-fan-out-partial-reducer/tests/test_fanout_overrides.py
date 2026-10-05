import fanout_helpers  # noqa: F401  (import-safe path setup)

from fanout_overrides import grant_override, override_edits, overrides_of, strip_overrides

BASE = "name: x\nbackup: null  # inherits\noverrides: {}\n"


def test_edits_are_the_overrides_added_or_changed():
    new = "name: x\nbackup: daily/30d\noverrides:\n  http.default_timeout: 15s\n"
    assert override_edits(BASE, new) == [{"key": "http.default_timeout", "value": "15s"}]
    assert override_edits(new, new) == []
    assert override_edits(BASE, BASE) == []


def test_unparseable_text_has_no_detectable_edits_and_no_overrides():
    assert override_edits(BASE, "name: [unclosed") == [] and overrides_of("name: [unclosed") is None


def test_strip_keeps_the_workers_other_edits():
    new = "name: x\nbackup: daily/30d\noverrides:\n  http.default_timeout: 15s\n"
    stripped = strip_overrides(BASE, new)
    assert overrides_of(stripped) == {} and "daily/30d" in stripped


def test_grant_adds_and_replaces():
    granted = grant_override(BASE, "http.default_timeout", "15s")
    assert overrides_of(granted) == {"http.default_timeout": "15s"}
    assert overrides_of(grant_override(granted, "http.default_timeout", "20s")) == {"http.default_timeout": "20s"}
