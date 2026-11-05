import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
DOCS = [p for p in ROOT.rglob("*.md") if ".pytest_cache" not in p.parts]


def frontmatter(path):
    return path.read_text().split("---")[1]


def test_plugin_ships_no_command_hooks():
    hooks_file = ROOT / "hooks" / "hooks.json"
    if hooks_file.exists():
        config = json.loads(hooks_file.read_text())
        hooks = [h for groups in config["hooks"].values() for group in groups for h in group["hooks"]]
        assert all(h["type"] == "prompt" for h in hooks)


def test_no_preapproved_shell_access():
    for path in list((ROOT / "commands").glob("*.md")) + list((ROOT / "agents").glob("*.md")):
        front = frontmatter(path)
        assert "Bash" not in front, path.name
        tools = re.search(r"(?:allowed-tools|tools):\s*\[?([^\]\n]*)", front).group(1)
        assert {t.strip() for t in tools.split(",")} <= {"Read", "Glob", "Grep"}, path.name


def test_command_receives_arguments_and_avoids_secrets():
    text = (ROOT / "commands" / "review-jobs.md").read_text()
    assert "$ARGUMENTS" in text
    assert "argument-hint:" in frontmatter(ROOT / "commands" / "review-jobs.md")
    assert "Never open `.env`" in text


def test_manifest_has_required_fields():
    for key in ("name", "description", "version", "author", "homepage", "repository", "license", "keywords", "icon"):
        assert MANIFEST.get(key), key
    assert MANIFEST["name"] == "job-safety-guard"
    assert MANIFEST["repository"] == "https://github.com/a-abdellatif98/job-safety-guard"
    assert (ROOT / MANIFEST["icon"]).is_file()


def test_manifest_version_matches_skill():
    skill = (ROOT / "skills" / "review-job-safety" / "SKILL.md").read_text()
    assert f"version: {MANIFEST['version']}" in skill
    assert "name: review-job-safety" in skill


def test_skill_references_exist():
    skill = (ROOT / "skills" / "review-job-safety" / "SKILL.md").read_text()
    for name in re.findall(r"references/([\w-]+\.md)", skill):
        assert (ROOT / "skills" / "review-job-safety" / "references" / name).is_file(), name


def test_no_root_claude_md():
    assert not (ROOT / "CLAUDE.md").exists()


def test_docs_have_no_em_or_en_dashes():
    for path in DOCS:
        assert "\u2014" not in path.read_text() and "\u2013" not in path.read_text(), path.name


def test_docs_name_no_credential_like_identifiers():
    banned = [r"password", r"\$\{\w*TOKEN\w*\}", r"\b\w*_URL\b", r"secret_key"]
    for path in DOCS + list((ROOT / "scripts").glob("*.py")):
        text = path.read_text()
        for pattern in banned:
            assert not re.search(pattern, text, re.I), (path.name, pattern)
