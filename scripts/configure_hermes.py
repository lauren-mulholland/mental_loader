"""Configure native features using Hermes' own interpreter, YAML parser and cron store API."""

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".local/hermes-runtime"))
os.environ.setdefault("HERMES_HOME", str(ROOT / ".local/hermes-home"))
os.environ.setdefault("HERMES_RUNTIME_DIR", str(ROOT / ".local/hermes-managed"))


def install_skills(root, home):
    """Refresh our skills without overwriting improvements Hermes learned in its home."""
    manifest_path = home / ".mental-loader-skill-hashes.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    saved = dict(previous)
    installed, preserved = [], []
    for source in sorted((root / "skills").iterdir()):
        if not (source / "SKILL.md").exists():
            continue
        files = [path for path in source.rglob("*") if path.is_file()]
        changed = False
        for file in files:
            relative = file.relative_to(root / "skills").as_posix()
            destination = home / "skills" / relative
            if destination.exists():
                current = hashlib.sha256(destination.read_bytes()).hexdigest()
                expected = previous.get(relative)
                if current != expected and destination.read_bytes() != file.read_bytes():
                    changed = True
        if changed:
            preserved.append(source.name)
            continue
        for file in files:
            relative = file.relative_to(root / "skills").as_posix()
            destination = home / "skills" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, destination)
            saved[relative] = hashlib.sha256(file.read_bytes()).hexdigest()
        installed.append(source.name)
    manifest_path.write_text(json.dumps(saved, indent=2) + "\n")
    return installed, preserved


def main():
    from cron.jobs import create_job, list_jobs, update_job
    from ruamel.yaml import YAML

    home = Path(os.environ["HERMES_HOME"])
    installed, preserved = install_skills(ROOT, home)
    yaml = YAML()
    path = home / "config.yaml"
    config = yaml.load(path.read_text()) or {}
    hosted_workdir = os.environ.get("ML_HOSTED_WORKDIR")
    if hosted_workdir:
        Path(hosted_workdir).mkdir(parents=True, exist_ok=True)
        config.setdefault("terminal", {})["cwd"] = hosted_workdir
    config.setdefault("timezone", "Australia/Sydney")
    platforms = config.setdefault("platform_toolsets", {})
    for platform in ("cli", "api_server", "cron"):
        native = ["household", "memory", "session_search", "skills"]
        if platform != "cron":
            native += ["delegation", "cronjob"]
        enabled = platforms.setdefault(platform, [])
        for toolset in native:
            if toolset not in enabled:
                enabled.append(toolset)
    memory = config.setdefault("memory", {})
    memory["memory_enabled"] = True
    memory["user_profile_enabled"] = True
    skills = config.setdefault("skills", {})
    if not skills.get("creation_nudge_interval"):
        skills["creation_nudge_interval"] = 15
    passthrough = config.setdefault("terminal", {}).setdefault("env_passthrough", [])
    for name in ("ML_BACKEND_URL", "ML_HOUSEHOLD_ACCESS_CODE"):
        if name not in passthrough:
            passthrough.append(name)
    # Single native dispatcher avoids concurrent pushes and duplicate household scans.
    config.setdefault("cron", {})["max_parallel_jobs"] = 1
    with path.open("w") as stream:
        yaml.dump(config, stream)
    scripts = home / "scripts"
    scripts.mkdir(exist_ok=True)
    shutil.copyfile(
        ROOT / "scripts/hermes_reminder_tick.py", scripts / "mental-loader-reminders.py"
    )
    jobs = [
        job
        for job in list_jobs(include_disabled=True)
        if job.get("name") == "mental-loader-reminders"
    ]
    if len(jobs) > 1:
        raise RuntimeError(
            "Multiple reminder dispatcher jobs; inspect Hermes cron before continuing"
        )
    if jobs:
        job = jobs[0]
        if not job.get("no_agent") or job.get("script") != "mental-loader-reminders.py":
            raise RuntimeError(
                "Existing reminder job differs; inspect it before changing its behaviour"
            )
        if hosted_workdir:
            update_job(job["id"], {"workdir": hosted_workdir})
    else:
        job = create_job(
            prompt=None,
            schedule="every 1m",
            name="mental-loader-reminders",
            script="mental-loader-reminders.py",
            no_agent=True,
            deliver="local",
            workdir=hosted_workdir,
        )
    print(f"Native Hermes features configured; reminder cron job: {job['id']} (no-agent).")
    print("Household skills installed: " + ", ".join(installed))
    if preserved:
        print("Preserved locally changed/learned skills for review: " + ", ".join(preserved))


if __name__ == "__main__":
    main()
