import pytest

from dastyar.store.memory_store import MemoryStore, MemoryStoreError
from dastyar.store.skill_store import SkillError, SkillStore

GOOD_SKILL = "---\nname: deploy-site\ndescription: Deploy the personal site\n---\n\n1. Run build\n2. Upload\n"


# ---------------------------------------------------------------- memory
def test_memory_add_replace_remove(tmp_path):
    m = MemoryStore(tmp_path)
    assert "Added" in m.add("memory", "User's server runs Ubuntu 24.04")
    assert m.add("memory", "User's server runs Ubuntu 24.04") == "Already present."
    m.add("memory", "Prefers short answers")
    assert "Replaced" in m.replace("memory", "Ubuntu", "Server runs Debian 12")
    assert m.entries("memory") == ["Server runs Debian 12", "Prefers short answers"]
    m.remove("memory", "short")
    assert m.entries("memory") == ["Server runs Debian 12"]


def test_memory_limit_enforced(tmp_path):
    m = MemoryStore(tmp_path, limits={"user": 50})
    m.add("user", "a" * 40)
    with pytest.raises(MemoryStoreError, match="over the 50 char limit"):
        m.add("user", "b" * 40)
    assert m.entries("user") == ["a" * 40]


def test_memory_ambiguous_and_missing_match(tmp_path):
    m = MemoryStore(tmp_path)
    m.add("memory", "likes tea")
    m.add("memory", "likes coffee")
    with pytest.raises(MemoryStoreError, match="matches 2 entries"):
        m.remove("memory", "likes")
    with pytest.raises(MemoryStoreError, match="no entry contains"):
        m.remove("memory", "zzz")


def test_memory_rejects_empty_and_delimiter(tmp_path):
    m = MemoryStore(tmp_path)
    with pytest.raises(MemoryStoreError):
        m.add("memory", "   ")
    with pytest.raises(MemoryStoreError):
        m.add("memory", "bad § entry")
    with pytest.raises(MemoryStoreError):
        m.add("other", "x")  # type: ignore[arg-type]


def test_memory_persian_and_snapshot_and_raw_roundtrip(tmp_path):
    m = MemoryStore(tmp_path)
    m.add("user", "زبان کاربر فارسی است")
    assert "فارسی" in m.snapshot()
    m.set_raw("user", "یکی\n\nدو")
    assert m.entries("user") == ["یکی", "دو"]
    assert MemoryStore(tmp_path).entries("user") == ["یکی", "دو"]  # persisted


# ---------------------------------------------------------------- skills
def test_skill_create_view_list_delete(tmp_path):
    s = SkillStore(tmp_path)
    s.create("deploy-site", GOOD_SKILL)
    assert s.list() == [{"name": "deploy-site", "description": "Deploy the personal site"}]
    assert "Run build" in s.view("deploy-site")
    assert "deploy-site: Deploy" in s.index_text()
    s.delete("deploy-site")
    assert s.names() == []


@pytest.mark.parametrize(
    "content,msg",
    [
        ("no frontmatter", "frontmatter"),
        ("---\nname: deploy-site\n---\nbody", "description"),
        ("---\nname: other\ndescription: x\n---\nbody", "must equal"),
        ("---\nname: deploy-site\ndescription: x\n---\n", "body is empty"),
        ("---\nname: deploy-site\ndescription: x\n", "not closed"),
    ],
)
def test_skill_validation(tmp_path, content, msg):
    with pytest.raises(SkillError, match=msg):
        SkillStore(tmp_path).create("deploy-site", content)


def test_skill_bad_names_and_duplicates(tmp_path):
    s = SkillStore(tmp_path)
    for bad in ("../evil", "Has Space", "", "a/b", "UPPER"):
        with pytest.raises(SkillError):
            s.create(bad, GOOD_SKILL)
    s.create("deploy-site", GOOD_SKILL)
    with pytest.raises(SkillError, match="already exists"):
        s.create("deploy-site", GOOD_SKILL)


def test_skill_patch_and_files(tmp_path):
    s = SkillStore(tmp_path)
    s.create("deploy-site", GOOD_SKILL)
    s.patch("deploy-site", "2. Upload", "2. Upload via rsync")
    assert "rsync" in s.view("deploy-site")
    with pytest.raises(SkillError, match="not found"):
        s.patch("deploy-site", "nonexistent", "x")
    s.write_file("deploy-site", "references/notes.md", "extra")
    assert s.view("deploy-site", "references/notes.md") == "extra"
    assert "references/notes.md" in s.view("deploy-site")


def test_skill_patch_cannot_break_frontmatter(tmp_path):
    s = SkillStore(tmp_path)
    s.create("deploy-site", GOOD_SKILL)
    with pytest.raises(SkillError):
        s.patch("deploy-site", "name: deploy-site", "name: renamed")
    assert "name: deploy-site" in s.view("deploy-site")


@pytest.mark.parametrize("rel", ["../../outside.txt", "/etc/passwd", "C:\\Windows\\x", "SKILL.md"])
def test_skill_file_path_traversal_blocked(tmp_path, rel):
    s = SkillStore(tmp_path / "skills")
    s.create("deploy-site", GOOD_SKILL)
    with pytest.raises(SkillError):
        s.write_file("deploy-site", rel, "pwned")
    assert not (tmp_path / "outside.txt").exists()
