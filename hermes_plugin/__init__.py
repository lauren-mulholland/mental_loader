"""Native Hermes plugin. Standard library only; all household logic is in our service."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def register(ctx):
    # Capture configuration outside tool args. A model cannot select another household.
    origin = os.environ.get("ML_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
    token = os.environ.get("ML_HOUSEHOLD_ACCESS_CODE", "")
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("Invalid ML_BACKEND_URL")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Only loopback backends can use plain HTTP")
    if len(token) < 32:
        raise ValueError("Configure ML_HOUSEHOLD_ACCESS_CODE in the dedicated Hermes home")
    opener = build_opener(ProxyHandler({}), NoRedirect())

    def call(path, args=None, method=None):
        request = Request(
            origin + path,
            method=method,
            data=json.dumps(args).encode() if args is not None else None,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with opener.open(request, timeout=15) as response:
                return response.read().decode()
        except HTTPError as error:
            # Backend returns sanitised errors; never expose credentials or URLs.
            return json.dumps(
                {"error": "Household tool rejected the request", "status": error.code}
            )
        except (URLError, TimeoutError):
            return json.dumps(
                {
                    "error": "Household service unavailable; no success confirmed. "
                    "Check tasks before retrying and reuse the request_id."
                }
            )

    def remind(args, **kwargs):
        allowed = {
            key: args[key]
            for key in ("title", "due_at", "request_id", "conversation_id")
            if key in args
        }
        return call("/api/tool/reminders", allowed)

    schemas = [
        (
            "household_clock",
            "Read the current UTC time and household timezone.",
            {},
            [],
            lambda args, **kwargs: call("/api/tool/clock"),
        ),
        (
            "household_tasks",
            "Read this household's saved tasks and reminder delivery status.",
            {},
            [],
            lambda args, **kwargs: call("/api/tool/tasks"),
        ),
        (
            "household_remind",
            "Save a reminder in the household database. Confirm ambiguous "
            "dates first. Reuse request_id on retries; report success only if task_id is returned.",
            {
                "title": {"type": "string", "maxLength": 240},
                "due_at": {"type": "string", "description": "ISO 8601 date/time WITH UTC offset"},
                "request_id": {"type": "string", "format": "uuid"},
                "conversation_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "Application conversation ID, when provided",
                },
            },
            ["title", "due_at", "request_id"],
            remind,
        ),
    ]

    def save_birthday(args, **kwargs):
        from uuid import UUID

        birthday_id = str(UUID(args["birthday_id"]))
        body = {key: value for key, value in args.items() if key != "birthday_id"}
        return call("/api/tool/birthdays/" + birthday_id, body, method="PUT")

    schemas.extend(
        [
            (
                "household_birthdays",
                "Read saved dated birthdays, budgets and gift progress.",
                {},
                [],
                lambda args, **kwargs: call("/api/tool/birthdays"),
            ),
            (
                "household_save_birthday",
                "Save or update one dated birthday. Reuse birthday_id on retry. "
                "Read existing records first and preserve fields. "
                "Ready or cancelled stops reminders; purchased does not.",
                {
                    "birthday_id": {"type": "string", "format": "uuid"},
                    "conversation_id": {"type": "string", "format": "uuid"},
                    "recipient": {"type": "string", "maxLength": 100},
                    "occasion_date": {"type": "string", "format": "date"},
                    "preferences": {"type": "string", "maxLength": 1000},
                    "budget": {"type": ["number", "null"], "minimum": 0},
                    "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
                    "lead_days": {"type": "integer", "minimum": 0, "maximum": 365},
                    "gift_status": {
                        "type": "string",
                        "enum": ["planning", "purchased", "ready", "cancelled"],
                    },
                },
                [
                    "birthday_id",
                    "conversation_id",
                    "recipient",
                    "occasion_date",
                    "preferences",
                    "budget",
                    "currency",
                    "lead_days",
                    "gift_status",
                ],
                save_birthday,
            ),
        ]
    )
    for name, description, properties, required, handler in schemas:
        ctx.register_tool(
            name=name,
            toolset="household",
            handler=handler,
            schema={
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        )
