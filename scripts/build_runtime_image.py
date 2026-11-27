"""Build the pinned, patched Hermes image without copying any private runtime state."""

import argparse
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=ROOT / ".local/hermes-runtime")
    parser.add_argument("--tag", default="mental-loader-hermes:phase2")
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    revision = subprocess.check_output(
        ["git", "-C", str(runtime), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != (ROOT / "hermes-runtime.ref").read_text().splitlines()[1]:
        raise SystemExit("Runtime revision differs from the pin")
    with tempfile.TemporaryDirectory(prefix="ml-runtime-build-") as temporary:
        root = Path(temporary)
        archive = root / "source.tar"
        subprocess.run(
            [
                "git",
                "-C",
                str(runtime),
                "archive",
                "--format=tar",
                "--output",
                str(archive),
                revision,
            ],
            check=True,
        )
        destination = root / "hermes"
        destination.mkdir()
        with tarfile.open(archive) as stream:
            stream.extractall(destination, filter="data")
        archive.unlink()
        for patch in sorted((ROOT / "patches").glob("*.patch")):
            subprocess.run(["git", "-C", str(destination), "apply", str(patch)], check=True)
        shutil.copyfile(ROOT / "deploy/hermes.Dockerfile", root / "Dockerfile")
        subprocess.run(["docker", "build", "-t", args.tag, str(root)], check=True)


if __name__ == "__main__":
    main()
