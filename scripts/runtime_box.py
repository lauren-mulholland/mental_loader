"""Provision one restricted Hermes container/home per shared household or private chat.

Does not register routes or make model calls. Register the private manifest with the
app CLI after provisioning. Provider credentials, if supplied, stay in that home.
"""

import argparse
import json
import secrets
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from mental_loader.cli import write_private
from mental_loader.runtimes import inspect_sandbox, scope_id

ROOT = Path(__file__).resolve().parents[1]


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def provision(
    household_id,
    conversation_id,
    backend_url,
    output,
    image="mental-loader-hermes:phase2",
    provider_env=None,
    test_command=None,
    restore_home=None,
):
    # Validate identifiers before they are used in labels or secret scope names.
    UUID(household_id)
    if conversation_id:
        UUID(conversation_id)
    if not backend_url.startswith("https://"):
        raise ValueError("Container backends require trusted HTTPS")
    output = Path(output)
    if output.exists():
        raise ValueError("Manifest already exists; preserve existing runtime credentials")
    scope = scope_id(household_id, conversation_id)
    volume = "ml-runtime-" + uuid4().hex
    api_key, tool_code = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    container_id = None
    docker("volume", "create", "--label", f"mental-loader.scope={scope}", volume)
    try:
        with tempfile.TemporaryDirectory(prefix="ml-runtime-home-") as temporary:
            home = Path(temporary)
            plugin = home / "plugins/mental-loader"
            plugin.mkdir(parents=True)
            import shutil

            if restore_home:
                source = Path(restore_home)
                identity = json.loads((source / ".mental-loader-scope.json").read_text())
                if identity.get("scope") != scope:
                    raise ValueError("Restored native state belongs to a different runtime scope")
                if any(path.is_symlink() for path in source.rglob("*")):
                    raise ValueError("Restored homes must not contain symlinks")
                shutil.copytree(source, home, dirs_exist_ok=True)
            (home / ".mental-loader-scope.json").write_text(json.dumps({"scope": scope}))
            for name in ("__init__.py", "plugin.yaml"):
                shutil.copyfile(ROOT / "hermes_plugin" / name, plugin / name)
            (home / "skills").mkdir(exist_ok=True)
            for skill in (ROOT / "skills").iterdir():
                if skill.is_dir() and not (home / "skills" / skill.name).exists():
                    shutil.copytree(skill, home / "skills" / skill.name)
            scripts = home / "scripts"
            scripts.mkdir(exist_ok=True)
            shutil.copyfile(
                ROOT / "scripts/hermes_reminder_tick.py", scripts / "mental-loader-reminders.py"
            )
            shutil.copyfile(ROOT / "src/mental_loader/hermes_instructions.md", home / "SOUL.md")
            content = (
                "API_SERVER_ENABLED=true\nAPI_SERVER_HOST=0.0.0.0\nAPI_SERVER_PORT=8642\n"
                f"API_SERVER_KEY={api_key}\nML_BACKEND_URL={backend_url}\n"
                f"ML_HOUSEHOLD_ACCESS_CODE={tool_code}\n"
            )
            if provider_env:
                # Only provider-specific values can be imported, never gateway/tool credentials.
                for line in Path(provider_env).read_text().splitlines():
                    if line.startswith(
                        ("OPENROUTER_API_KEY=", "OPENAI_API_KEY=", "ANTHROPIC_API_KEY=")
                    ):
                        content += line + "\n"
            if (home / ".env").exists():
                (
                    home / ".env"
                ).unlink()  # New scope credentials always replace restored credentials.
            write_private(home / ".env", content)
            (home / "config.yaml").write_text(
                "plugins:\n  enabled: [mental-loader]\n"
                "agent:\n  max_turns: 8\n"
                "delegation:\n  max_iterations: 4\n  max_concurrent_children: 1\n"
                "  max_spawn_depth: 1\n  inherit_mcp_toolsets: false\n"
                "platform_toolsets:\n"
                "  api_server: [household, memory, session_search, skills, delegation]\n"
                "  cli: [household, memory, session_search, skills]\n"
                "  cron: [household]\n"
            )
            # Bootstrap is a short, trusted copy/chown operation; the actual model
            # container has only its own volume, no host mounts or Docker socket.
            bootstrap = (
                "import os,shutil\nshutil.copytree('/input','/home/hermes',dirs_exist_ok=True)\n"
            )
            if not conversation_id:
                bootstrap += (
                    "from cron.jobs import create_job,list_jobs\n"
                    "jobs=[j for j in list_jobs(include_disabled=True) "
                    "if j.get('name')=='mental-loader-reminders']\n"
                    "if not jobs:\n"
                    " create_job(prompt=None,schedule='every 1m',name='mental-loader-reminders',"
                    "script='mental-loader-reminders.py',no_agent=True,deliver='local')\n"
                    "else:\n"
                    " assert len(jobs)==1 and jobs[0].get('no_agent') "
                    "and jobs[0].get('script')=='mental-loader-reminders.py'\n"
                )
            bootstrap += (
                "for root,ds,fs in os.walk('/home/hermes'):\n"
                " for p in [root]+[os.path.join(root,n) for n in ds+fs]:\n"
                "  os.chown(p,65532,65532)\n"
                "  os.chmod(p,0o700 if os.path.isdir(p) or os.stat(p).st_mode & 0o100 else 0o600)\n"
            )
            docker(
                "run",
                "--rm",
                "--user",
                "0",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "CHOWN",
                "--cap-add",
                "FOWNER",
                "--cap-add",
                "DAC_OVERRIDE",
                "--security-opt",
                "no-new-privileges",
                "--mount",
                f"type=volume,src={volume},dst=/home/hermes",
                "--mount",
                f"type=bind,src={home},dst=/input,readonly",
                image,
                "python",
                "-c",
                bootstrap,
            )
        command = [
            "run",
            "-d",
            "--read-only",
            "--user",
            "65532:65532",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--memory",
            "768m",
            "--cpus",
            "1",
            "--tmpfs",
            "/tmp:rw,nosuid,noexec,size=64m",
            "--mount",
            f"type=volume,src={volume},dst=/home/hermes",
            "--label",
            f"mental-loader.scope={scope}",
            "-p",
            "127.0.0.1::8642",
        ]
        command += [image, *(test_command or [])]
        container_id = docker(*command)
        port = docker("port", container_id, "8642").rsplit(":", 1)[-1]
        endpoint = f"http://127.0.0.1:{port}"
        inspect_sandbox(container_id, scope, endpoint)
        result = {
            "household_id": household_id,
            "conversation_id": conversation_id,
            "container_id": container_id,
            "endpoint": endpoint,
            "api_key": api_key,
            "tool_code": tool_code,
            "volume": volume,
        }
        write_private(output, json.dumps(result, indent=2) + "\n")
        return result
    except BaseException:
        if container_id:
            subprocess.run(["docker", "rm", "-f", container_id], capture_output=True)
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--household-id", required=True)
    parser.add_argument("--conversation-id")
    parser.add_argument("--backend-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider-env", type=Path)
    parser.add_argument("--restore-home", type=Path)
    args = parser.parse_args()
    provision(
        args.household_id,
        args.conversation_id,
        args.backend_url,
        args.output,
        provider_env=args.provider_env,
        restore_home=args.restore_home,
    )
    print("Scoped container prepared. Register its private manifest with the app CLI.")


if __name__ == "__main__":
    main()
