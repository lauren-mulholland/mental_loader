"""Operator-managed, container-attested Hermes routes. Never accept browser-selected routes."""

import json
import subprocess
from urllib.parse import urlparse

from sqlalchemy import select

from mental_loader.permissions import can_access
from mental_loader.security import Vault
from mental_loader.storage import Conversation, Household, Membership, RuntimeBinding, digest


def scope_id(household_id, conversation_id=None):
    return f"conversation:{conversation_id}" if conversation_id else f"household:{household_id}"


def inspect_sandbox(container_id, scope, endpoint):
    result = subprocess.run(
        ["docker", "inspect", container_id], capture_output=True, text=True, timeout=15, check=True
    )
    items = json.loads(result.stdout)
    if len(items) != 1:
        raise ValueError("Expected exactly one container")
    item = items[0]
    config, host = item["Config"], item["HostConfig"]
    mounts = item.get("Mounts", [])
    url = urlparse(endpoint)
    ports = item.get("NetworkSettings", {}).get("Ports", {}).get("8642/tcp") or []
    if (
        item["Id"] != container_id
        or not item["State"]["Running"]
        or config.get("Labels", {}).get("mental-loader.scope") != scope
        or config.get("User") != "65532:65532"
        or not host.get("ReadonlyRootfs")
        or host.get("Privileged")
        or host.get("CapAdd")
        or host.get("Devices")
        or "ALL" not in host.get("CapDrop", [])
        or not any(v.startswith("no-new-privileges") for v in host.get("SecurityOpt", []))
        or host.get("PidMode") == "host"
        or host.get("IpcMode") == "host"
        or host.get("NetworkMode") == "host"
        or str(host.get("NetworkMode", "")).startswith("container:")
        or host.get("PidMode") not in (None, "", "private")
        or host.get("IpcMode") not in (None, "", "private")
        or host.get("UsernsMode") == "host"
        or any(not v.startswith("no-new-privileges") for v in host.get("SecurityOpt", []))
        or not (0 < (host.get("PidsLimit") or 0) <= 256)
        or not (0 < (host.get("Memory") or 0) <= 2147483648)
        or not (0 < (host.get("NanoCpus") or 0) <= 2000000000)
        or len(mounts) != 1
        or mounts[0]["Destination"] != "/home/hermes"
        or mounts[0]["Type"] != "volume"
        or not mounts[0].get("RW")
        or url.scheme != "http"
        or url.hostname != "127.0.0.1"
        or url.username
        or url.password
        or url.path
        or url.query
        or url.fragment
        or not any(
            p.get("HostIp") == "127.0.0.1" and p.get("HostPort") == str(url.port) for p in ports
        )
    ):
        raise ValueError("Runtime container does not meet the scoped sandbox policy")
    volume = subprocess.run(
        ["docker", "volume", "inspect", mounts[0]["Name"]],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    volume_scope = (json.loads(volume.stdout)[0].get("Labels") or {}).get("mental-loader.scope")
    if volume_scope != scope:
        raise ValueError("Runtime state volume belongs to a different scope")
    return item


def register(
    sessions, settings, household_id, conversation_id, container_id, endpoint, api_key, tool_code
):
    scope = scope_id(household_id, conversation_id)
    inspect_sandbox(container_id, scope, endpoint)
    vault = Vault(settings.secret_key_path)
    with sessions.begin() as db:
        if db.get(Household, household_id) is None:
            raise ValueError("Unknown household")
        if (
            db.scalar(
                select(Membership.user_id)
                .where(
                    Membership.household_id == household_id,
                    Membership.role == "owner",
                )
                .limit(1)
            )
            is None
        ):
            raise ValueError("Provision a personal household owner before registering runtimes")
        if conversation_id:
            conversation = db.get(Conversation, conversation_id)
            if (
                conversation is None
                or conversation.household_id != household_id
                or conversation.visibility != "private"
                or not can_access(db, household_id, conversation_id, conversation.owner_id)
            ):
                raise ValueError(
                    "Private runtime requires an active creator and matching conversation"
                )
        if db.get(RuntimeBinding, scope) is not None:
            raise ValueError("Revoke the existing runtime before registering a replacement")
        # A state volume must never be reused across scopes, even by a different container.
        current = inspect_sandbox(container_id, scope, endpoint)
        for existing in db.scalars(select(RuntimeBinding)):
            other = inspect_sandbox(existing.container_id, existing.scope, existing.endpoint)
            if current["Mounts"][0]["Name"] == other["Mounts"][0]["Name"]:
                raise ValueError("Runtime scopes must use distinct state volumes")
        db.add(
            RuntimeBinding(
                scope=scope,
                household_id=household_id,
                conversation_id=conversation_id,
                container_id=container_id,
                endpoint=endpoint,
                tool_hash=digest(tool_code),
                encrypted_api_key=vault.seal(api_key, f"runtime:{scope}"),
            )
        )


def resolve(db, settings, household_id, conversation):
    key = scope_id(household_id, conversation.id if conversation.visibility == "private" else None)
    binding = db.get(RuntimeBinding, key)
    if binding is None:
        return None
    if binding.household_id != household_id:
        raise ValueError("Runtime household mismatch")
    if conversation.visibility == "private" and not can_access(
        db, household_id, conversation.id, conversation.owner_id
    ):
        raise ValueError("Private runtime creator no longer has access")
    inspect_sandbox(binding.container_id, binding.scope, binding.endpoint)
    api_key = Vault(settings.secret_key_path).open(
        binding.encrypted_api_key, f"runtime:{binding.scope}"
    )
    return binding.endpoint, api_key, key
