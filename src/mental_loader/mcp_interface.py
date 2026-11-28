"""Stateless Streamable HTTP adapter for the application's bounded MCP tools."""

import json

from fastapi import Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from mental_loader.application import ApplicationError
from mental_loader.planning import BulkInput, IdeaInput, PreviewInput, Scope

VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}


def tools_for(planning):
    return {
        "list_people": (Scope, planning.read, "Read people, effective defaults and birthdays."),
        "get_gift_settings": (Scope, planning.read, "Read person defaults and birthday overrides."),
        "save_gift_idea": (
            IdeaInput,
            planning.save_idea,
            "Save an explicit gift idea for an accessible person. Reuse request_id.",
        ),
        "preview_bulk_gift_settings": (
            BulkInput,
            planning.preview,
            "Propose budget/card changes. Set the intended scope. Show the preview in the app; "
            "only a signed-in user can apply it.",
        ),
        "get_gift_edit_receipt": (
            PreviewInput,
            planning.receipt,
            "Read whether the signed-in user applied a reviewed bulk edit.",
        ),
    }


def dispatch(payload, tenant, planning):
    if not isinstance(payload, dict):
        return rpc_error(None, -32600, "Invalid request")
    method, request_id = payload.get("method"), payload.get("id")
    params = payload.get("params", {})
    if payload.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return rpc_error(request_id, -32600, "Invalid request")
    if method.startswith("notifications/") and "id" not in payload:
        return Response(status_code=202)
    if "id" not in payload or not isinstance(params, dict):
        return rpc_error(request_id, -32600, "Invalid request")
    tools = tools_for(planning)
    if method == "initialize":
        version = params.get("protocolVersion")
        result = {
            "protocolVersion": version
            if isinstance(version, str) and version in VERSIONS
            else "2025-11-25",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mental-loader", "version": "1.0.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": name,
                    "description": description,
                    "inputSchema": schema.model_json_schema(),
                }
                for name, (schema, _, description) in tools.items()
            ]
        }
    elif method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or name not in tools:
            return rpc_error(request_id, -32602, "Unknown tool")
        schema, operation, _ = tools[name]
        error = False
        try:
            body = schema.model_validate(params.get("arguments", {}))
            value = operation(tenant, body)
        except ValidationError:
            value, error = {"error": "Invalid tool arguments", "status": 422}, True
        except ApplicationError as exc:
            value, error = {"error": exc.detail, "status": exc.status_code}, True
        except IntegrityError:
            value, error = {"error": "Concurrent edit. Refresh and retry.", "status": 409}, True
        result = {
            "content": [{"type": "text", "text": json.dumps(value)}],
            "structuredContent": value,
            "isError": error,
        }
    else:
        return rpc_error(request_id, -32601, "Method not found")
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


def rpc_error(request_id, code, message):
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
    )
