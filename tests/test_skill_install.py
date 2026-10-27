import importlib.util
from pathlib import Path


def installer():
    path = Path(__file__).parents[1] / "scripts/configure_hermes.py"
    spec = importlib.util.spec_from_file_location("configure_household_hermes", path)
    module = importlib.util.module_from_spec(spec)
    # This module selects HERMES_HOME for subprocess use. Restore it in the calling test.
    return spec, module


def test_skill_refresh_preserves_hermes_learned_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", "test-home")
    monkeypatch.setenv("HERMES_RUNTIME_DIR", "test-runtime")
    spec, module = installer()
    spec.loader.exec_module(module)
    root, home = tmp_path / "project", tmp_path / "home"
    home.mkdir()
    source = root / "skills/household-meals/SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text("original instructions")
    assert module.install_skills(root, home) == (["household-meals"], [])
    target = home / "skills/household-meals/SKILL.md"
    source.write_text("updated project instructions")
    module.install_skills(root, home)
    assert target.read_text() == "updated project instructions"
    target.write_text("Hermes learned a better procedure")
    source.write_text("new project instructions")
    assert module.install_skills(root, home) == ([], ["household-meals"])
    assert target.read_text() == "Hermes learned a better procedure"
