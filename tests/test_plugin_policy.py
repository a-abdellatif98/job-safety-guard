import ast
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
    assert "Never open dotfiles" in text


def test_manifest_has_required_fields():
    for key in ("name", "description", "version", "author", "homepage", "repository", "license", "keywords", "icon"):
        assert MANIFEST.get(key), key
    assert MANIFEST["name"] == "job-safety-guard"
    assert MANIFEST["repository"].endswith("/" + MANIFEST["name"])
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


def test_docs_and_scripts_have_no_interpolation_or_opaque_values():
    banned = [r"\$\{", r"\b[A-Za-z0-9+=]{32,}\b"]
    for path in DOCS + list((ROOT / "scripts").glob("*.py")):
        text = path.read_text()
        for pattern in banned:
            assert not re.search(pattern, text), (path.name, pattern)


SCANNER = ROOT / "scripts" / "job_safety_scan.py"
ALLOWED_IMPORTS = {"argparse", "json", "os", "re", "sys", "dataclasses"}
ALLOWED_ATTRS = {"sys": {"exit", "stderr"}, "os": {"path", "walk"}}
ALLOWED_PATH_ATTRS = {"basename", "exists", "getsize", "isfile", "islink", "join", "splitext"}


ALLOWED_FROM_IMPORTS = {"dataclasses": {"asdict", "dataclass", "field"}}


def test_scanner_imports_only_allowed_stdlib_modules():
    imported = set()
    for node in ast.walk(ast.parse(SCANNER.read_text())):
        if isinstance(node, ast.Import):
            assert all(a.asname is None for a in node.names)
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module in ALLOWED_FROM_IMPORTS, node.module
            assert {a.name for a in node.names} <= ALLOWED_FROM_IMPORTS[node.module]
            imported.add(node.module)
    assert imported <= ALLOWED_IMPORTS, imported - ALLOWED_IMPORTS


def test_scanner_uses_only_allowed_module_attributes():
    for node in ast.walk(ast.parse(SCANNER.read_text())):
        if not isinstance(node, ast.Attribute):
            continue
        if isinstance(node.value, ast.Name) and node.value.id in ALLOWED_ATTRS:
            assert node.attr in ALLOWED_ATTRS[node.value.id], node.attr
        inner = node.value
        if isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name):
            if (inner.value.id, inner.attr) == ("os", "path"):
                assert node.attr in ALLOWED_PATH_ATTRS, node.attr
