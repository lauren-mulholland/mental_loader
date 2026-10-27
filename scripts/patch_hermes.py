"""Apply reviewed fixes to the pinned Hermes checkout; refuse conflicting source changes."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def apply_patches():
    runtime = ROOT / ".local/hermes-runtime"
    for patch in sorted((ROOT / "patches").glob("*.patch")):
        command = ["git", "-C", str(runtime), "apply"]
        if subprocess.run(
            command + ["--reverse", "--check", str(patch)], capture_output=True
        ).returncode == 0:
            continue
        check = subprocess.run(command + ["--check", str(patch)], capture_output=True)
        if check.returncode:
            raise SystemExit(
                f"Cannot safely apply {patch.name}; inspect the Hermes revision/local changes."
            )
        subprocess.run(command + [str(patch)], check=True)
        print(f"Applied Hermes compatibility fix: {patch.name}")


if __name__ == "__main__":
    apply_patches()
