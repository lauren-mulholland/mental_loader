"""Exercise deployment images with disposable data. No provider requests or personal state."""

import base64
import io
import json
import os
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], check=True, capture_output=True, **kwargs).stdout


def wait_for(command, attempts=45):
    for _ in range(attempts):
        try:
            return command()
        except (subprocess.CalledProcessError, AssertionError):
            time.sleep(1)
    raise RuntimeError("Disposable deployment did not become ready")


def main():
    prefix = "ml-deploy-check-" + uuid4().hex[:8]
    db, web, hermes = (prefix + suffix for suffix in ("-db", "-web", "-hermes"))
    containers = []
    docker("network", "create", prefix)
    docker("volume", "create", prefix + "-home")
    try:
        with tempfile.TemporaryDirectory(prefix="ml-deploy-check-") as temp:
            work = Path(temp)
            docker("run", "-d", "--name", db, "--network", prefix,
                   "-e", "POSTGRES_PASSWORD=fixture-only", "postgres:17-alpine")
            containers.append(db)
            wait_for(lambda: docker("exec", db, "pg_isready", "-U", "postgres"))
            for name in ("source", "target"):
                docker("exec", db, "createdb", "-U", "postgres", name)
            source_url = f"postgresql://postgres:fixture-only@{db}:5432/source"
            docker("run", "--rm", "--platform", "linux/amd64", "--network", prefix,
                   "-e", f"DATABASE_URL={source_url}", "mental-loader-web:render", "python", "-c",
                   "from mental_loader.storage import database, Household, User, Membership; "
                   "from mental_loader.storage import digest; "
                   "from mental_loader.config import Settings; "
                   "engine,sessions=database(Settings().database_url); "
                   "s=sessions(); s.add(Household(id='fixture-household',name='Fixture',"
                   "access_hash='fixture')); s.flush(); "
                   "s.add(User(id='fixture-user',name='Owner',access_hash=digest('p'*43))); "
                   "s.flush(); s.add(Membership(household_id='fixture-household',"
                   "user_id='fixture-user',role='owner')); s.commit(); engine.dispose()")
            dump = work / "database.dump"
            dump.write_bytes(docker("exec", db, "pg_dump", "-U", "postgres", "-d", "source",
                                    "--format=custom", "--no-owner", "--no-acl"))
            private = ec.generate_private_key(ec.SECP256R1())
            public = base64.urlsafe_b64encode(private.public_key().public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )).rstrip(b"=").decode()
            pem = private.private_bytes(serialization.Encoding.PEM,
                                        serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())
            env = {
                "DATABASE_URL": f"postgresql://postgres:fixture-only@{db}:5432/target",
                "RENDER_EXTERNAL_URL": "https://fixture.onrender.com",
                "HERMES_HOSTPORT": f"{hermes}:8642",
                "HERMES_API_KEY": "k" * 43,
                "HERMES_HOUSEHOLD_ID": "fixture-household",
                "HERMES_TOOL_TOKEN": "h" * 43,
                "APP_SECRET_KEY_B64": base64.urlsafe_b64encode(b"x" * 32).decode(),
                "VAPID_PRIVATE_KEY_B64": base64.b64encode(pem).decode(),
                "VAPID_PUBLIC_KEY": public,
                "VAPID_SUBJECT": "mailto:fixture@example.com",
                "AI_ENABLED": "false",
            }
            env_path = work / "web.env"
            env_path.write_text("".join(f"{key}={value}\n" for key, value in env.items()))
            os.chmod(env_path, 0o600)

            def run_web(ready):
                docker("run", "-d", "--platform", "linux/amd64", "--name", web,
                       "--network", prefix, "--env-file", str(env_path),
                       "-e", f"DEPLOYMENT_READY={str(ready).lower()}", "mental-loader-web:render")

            def web_health():
                return json.loads(docker("exec", web, "python", "-c",
                    "import urllib.request; print(urllib.request.urlopen("
                    "'http://localhost:10000/healthz').read().decode())"))

            run_web(False)
            containers.append(web)
            assert wait_for(web_health)["setup"] is True
            docker("cp", str(dump), f"{web}:/tmp/database.dump")
            docker("exec", web, "python", "deploy/transfer.py", "restore-db", "/tmp/database.dump")
            # A second restore must refuse to replace existing data.
            try:
                docker("exec", web, "python", "deploy/transfer.py", "restore-db",
                       "/tmp/database.dump")
            except subprocess.CalledProcessError:
                pass
            else:
                raise AssertionError("Second restore replaced populated database")
            docker("rm", "-f", web)
            run_web(True)
            assert wait_for(web_health) == {"ok": True}
            # Hosted mode accepts the personal account and rejects the legacy login.
            docker("exec", web, "python", "-c",
                   "import json,urllib.request; request=urllib.request.Request("
                   "'http://localhost:10000/api/login',data=json.dumps({'access_code':'p'*43})"
                   ".encode(),headers={'Origin':'https://fixture.onrender.com',"
                   "'Content-Type':'application/json'}); "
                   "assert urllib.request.urlopen(request).status==200")
            docker("restart", web)
            assert wait_for(web_health) == {"ok": True}
            print("PASS: web setup, PostgreSQL restore, overwrite guard and restart.", flush=True)

            config = (
                "model:\n  provider: openrouter\n  default: fixture-model\n"
                "plugins:\n  enabled: [mental-loader]\n"
                "agent:\n  max_turns: 8\n"
            )
            archive_path = work / "home.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                for name, value in (("config.yaml", config), ("MEMORY.md", "Fixture memory")):
                    member = tarfile.TarInfo(name)
                    data = value.encode()
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
            hermes_env = work / "hermes.env"
            hermes_env.write_text(
                "PORT=8642\nAPI_SERVER_KEY=" + "k" * 43 + "\n"
                "OPENROUTER_API_KEY=fixture-not-a-real-key\n"
                "ML_BACKEND_URL=https://fixture.invalid\n"
                "ML_HOUSEHOLD_ACCESS_CODE=" + "h" * 43 + "\n"
            )
            os.chmod(hermes_env, 0o600)
            # Fixture callback is loopback HTTPS and cannot reach an external backend.
            # No scheduled call or provider call can succeed against personal resources.
            with hermes_env.open("a") as stream:
                stream.write("ML_BACKEND_URL=https://127.0.0.1:1\n")
            def run_hermes(ready):
                docker("run", "-d", "--platform", "linux/amd64", "--name", hermes,
                       "--network", prefix, "--env-file", str(hermes_env),
                       "-v", prefix + "-home:/data",
                       "-e", f"DEPLOYMENT_READY={str(ready).lower()}",
                       "mental-loader-hermes:render")

            run_hermes(False)
            containers.append(hermes)
            wait_for(lambda: docker("exec", hermes, "python", "-c",
                     "import urllib.request; urllib.request.urlopen('http://localhost:8642/healthz')"))
            docker("cp", str(archive_path), f"{hermes}:/tmp/home.tar.gz")
            docker("exec", hermes, "python", "deploy/transfer.py", "restore-home",
                   "/tmp/home.tar.gz")
            docker("rm", "-f", hermes)
            run_hermes(True)

            def hermes_health():
                return docker("exec", hermes, "python", "-c",
                    "import urllib.request; request=urllib.request.Request("
                    "'http://localhost:8642/health',headers={'Authorization':'Bearer '+ 'k'*43}); "
                    "print(urllib.request.urlopen(request).status)")

            wait_for(hermes_health)
            docker("restart", hermes)
            wait_for(hermes_health)
            assert docker("exec", hermes, "cat", "/data/hermes-home/MEMORY.md") == b"Fixture memory"
            docker("exec", "-w", "/app/.local/hermes-runtime", hermes,
                   "/app/.local/hermes-runtime/.venv/bin/python", "-c",
                   "from cron.jobs import list_jobs; jobs=[j for j in list_jobs(True) "
                   "if j.get('name')=='mental-loader-reminders']; "
                   "assert len(jobs)==1 and jobs[0]['no_agent']; "
                   "assert jobs[0]['workdir']=='/data/workspace'")
            print("PASS: Hermes persistent restore, gateway restart, API and single no-agent job.")
    except Exception:
        # Synthetic logs only; avoid real household state or provider credentials.
        if hermes in containers:
            result = subprocess.run(["docker", "logs", "--tail", "35", hermes],
                                    capture_output=True, text=True)
            print(result.stdout + result.stderr)
        raise
    finally:
        for name in reversed(containers):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "volume", "rm", prefix + "-home"], capture_output=True)
        subprocess.run(["docker", "network", "rm", prefix], capture_output=True)


if __name__ == "__main__":
    main()
