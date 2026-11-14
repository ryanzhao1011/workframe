#!/usr/bin/env python3
"""Narrow trust recovery using a measured, conditional user-layer write."""

import os
import sys

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["GIT_OPTIONAL_LOCKS"] = "0"

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

import _query_codex_trust as trust
import _codex_appserver as cas
from _harness import ROOT_MARKER
from workframe_door import _unc_strip
from workframe_query import QueryError, QueryParser, read_object, PLUGIN_ROOT
import workframe_doctor as doctor


def restore_observation(before, expected_snapshot, expected_source, *, adapter=None, server=None, project=None):
    """Compare the complete target, send one batch, then verify every key/hash.

    Authorization/revocation records belong to the calling desktop application;
    this entry point neither reads nor creates them. A lost write response is
    never evidence that the write did not happen.
    """
    from _query_codex_runtime import trust_storage_status, trust_config_readback
    keys = [hook["key"] for hook in before["hooks"]]
    remaining = [hook["key"] for hook in before["hooks"] if hook["status"] != "trusted"]
    value = {"result": "not_restored", "state_before": before["state"], "state_after": before["state"],
             "context_id": before["context"]["context_id"], "snapshot_before": before["snapshot_id"],
             "snapshot_after": before["snapshot_id"], "write_outcome": "none", "attempted_keys": [],
             "verified_keys": [], "remaining_keys": remaining, "error": None, "restart_required": False}
    def refused(code, message, exit_code=1):
        value["error"] = {"code": code, "message": message}
        return exit_code, value
    if before["snapshot_id"] is None:
        if before["state"] == "version_unsupported" or any(p["code"] == "side_effect_scope_unknown" for p in before["problems"]):
            return refused("protocol_unsupported", "Recovery runtime or protocol is unsupported", 2)
        return refused("unverified", "Current target could not be verified; no write was attempted")
    if before["snapshot_id"] != expected_snapshot:
        return refused("snapshot_mismatch", "Current target differs from --expect-snapshot")
    source = before["plugin"]["source"] if before["plugin"] else None
    if source != expected_source:
        return refused("source_mismatch", "Current source differs from --expect-source")
    if before["state"] == "ready":
        value.update(result="unchanged", verified_keys=keys, remaining_keys=[])
        return 0, value
    if before["state"] != "needs_trust" or before["effective"]["project_trusted"] is not True:
        return refused("not_eligible", "Project trust or effective hook enablement does not permit recovery")
    if adapter is None or server is None or trust.runtime_adapter(before["context"]) is None:
        return refused("protocol_unsupported", "This runtime has no verified conditional-write adapter", 2)
    target = adapter.write_target(before["context"])
    if target is None:
        return refused("protocol_unsupported", "Cannot verify one versioned user-layer configuration target", 2)
    if any(not isinstance(hook.get("key"), str) or not hook["key"]
           or not isinstance(hook.get("current_hash"), str) or not hook["current_hash"] for hook in before["hooks"]) or len(set(keys)) != len(keys):
        return refused("unverified", "Every hook must have a unique complete key and current hash")
    pending = [hook for hook in before["hooks"] if hook["status"] != "trusted"]
    if not pending:
        return refused("unverified", "A non-ready observation with no pending hooks cannot authorize a write")
    storage_target = adapter.storage_target(before["context"], before)
    if storage_target is None:
        return refused("protocol_unsupported", "Cannot bind one plugin-content baseline to the user configuration", 2)
    deadline = getattr(server, "deadline", None)
    if isinstance(deadline, (int, float)) and time.monotonic() >= deadline:
        return refused("timeout", "Recovery deadline expired before writing")
    edits = [{"keyPath": "hooks.state." + json.dumps(hook["key"]) + ".trusted_hash",
              "mergeStrategy": "upsert", "value": hook["current_hash"]} for hook in pending]
    value["attempted_keys"] = [hook["key"] for hook in pending]
    adapter.attempted = {hook["key"]: hook["current_hash"] for hook in pending}
    adapter.attempted_storage = (".hook-trust/" + storage_target[0].name, storage_target[1])
    value.update(write_outcome="unknown", restart_required=True)
    def unconfirmed():
        value.update(result="write_unconfirmed", state_after="unverified", snapshot_after=None)
        return refused("write_unconfirmed", "写入结果未确认；请只读复查，不要自动重试", 3)
    try:
        server.config_batch_write(edits, expected_version=target[1], file_path=target[0], reload_user_config=True)
    except cas.AppServerRpcError:
        adapter.attempted_storage = None
        value.update(write_outcome="none", restart_required=False)
        return refused("conditional_write_rejected", "Codex rejected the conditional user-layer batch")
    except (cas.AppServerError, OSError):
        return unconfirmed()
    try:
        after = adapter.observe(server, before["context"], project)
        storage_status = trust_storage_status(storage_target)
    except (cas.AppServerError, OSError):
        return unconfirmed()
    if after["snapshot_id"] is None:
        return unconfirmed()
    value.update(state_after=after["state"], snapshot_after=after["snapshot_id"])
    expected = {hook["key"]: hook["current_hash"] for hook in before["hooks"]}
    verified = {hook["key"] for hook in after["hooks"] if hook["status"] == "trusted"
                and hook["current_hash"] == expected.get(hook["key"])}
    config_verified = trust_config_readback((adapter.user_layer or {}).get("config"), adapter.attempted)
    if storage_status != "verified" or not config_verified:
        verified.clear()
    value.update(verified_keys=[key for key in keys if key in verified],
                 remaining_keys=[key for key in keys if key not in verified], write_outcome="partial")
    if storage_status != "verified":
        return refused("readback_mismatch", "Codex trust baseline readback: " + storage_status +
                       "; existing baselines are not overwritten")
    if not config_verified:
        return refused("readback_mismatch", "Codex user-layer readback does not confirm both trust hash fields")
    if (after["state"] != "ready" or after["snapshot_id"] != before["snapshot_id"] or
            set(expected) != {hook["key"] for hook in after["hooks"]} or value["remaining_keys"]):
        return refused("readback_mismatch", "Codex readback does not confirm every original key and hash")
    value.update(result="restored", write_outcome="confirmed", error=None)
    return 0, value


def parse_source(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
        required = ("kind", "marketplace_id", "plugin_name", "plugin_subpath", "assurance")
        if not isinstance(value, dict) or any(not isinstance(value.get(key), str) or not value[key] for key in required):
            raise ValueError("missing source identity")
        if value["kind"] not in ("directory", "git"):
            raise ValueError("unknown source")
        location = "real_path" if value["kind"] == "directory" else "repository"
        if not isinstance(value.get(location), str) or not value[location]:
            raise ValueError("missing source location")
        return value
    except (ValueError, TypeError):
        raise QueryError("bad_usage", "--expect-source must be a complete source identity JSON object", exit_code=2)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    project, operation, metadata = None, "restore", {}
    result = {"result": "not_restored", "state_before": "unverified", "state_after": "unverified",
              "context_id": None, "snapshot_before": None, "snapshot_after": None,
              "write_outcome": "none", "attempted_keys": [], "verified_keys": [], "remaining_keys": [],
              "error": None, "restart_required": False}
    version = None
    try:
        version = read_object(PLUGIN_ROOT / ".claude-plugin/plugin.json", "executing plugin manifest").get("version")
        if not isinstance(version, str) or not version:
            raise QueryError("internal", "Executing plugin manifest has no version")
        parser = QueryParser(prog="workframe-codex-trust", add_help=False, allow_abbrev=False)
        parser.add_argument("operation", choices=("restore",))
        parser.add_argument("--project", required=True)
        parser.add_argument("--expect-snapshot", required=True)
        parser.add_argument("--expect-source", required=True)
        parser.add_argument("--schema", default=trust.RESTORE_SCHEMA)
        trust.add_context_arguments(parser)
        args, unknown = parser.parse_known_args(argv)
        if unknown:
            raise QueryError("bad_usage", "Unrecognized recovery arguments", exit_code=2)
        if args.schema != trust.RESTORE_SCHEMA:
            raise QueryError("schema_unsupported", "Unsupported recovery schema", {"supported": [trust.RESTORE_SCHEMA]}, 2)
        if any(getattr(args, key) is None for key in ("codex_exe", "codex_home", "codex_cwd")):
            raise QueryError("bad_usage", "Recovery requires explicit --codex-exe, --codex-home and --codex-cwd", exit_code=2)
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.expect_snapshot):
            raise QueryError("bad_usage", "--expect-snapshot must be sha256:<64 lowercase hexadecimal digits>", exit_code=2)
        source = parse_source(args.expect_source)
        project = Path(_unc_strip(args.project)).resolve()
        if not project.is_dir():
            raise QueryError("bad_usage", "Project must be an existing directory", exit_code=2)
        if not (project / ROOT_MARKER).is_file():
            raise QueryError("not_connected", "Directory is not connected to Workframe")
        read_object(project / ROOT_MARKER, ROOT_MARKER)
        if doctor._has_legacy(doctor.Paths(project)):
            raise QueryError("legacy_layout", "Runtime data needs migration")
        before, metadata = trust.query(project, vars(args), reader=read_object,
            operation=lambda observation, adapter, server: restore_observation(
                observation, args.expect_snapshot, source, adapter=adapter, server=server, project=project))
        if "_operation_result" in metadata:
            code, result = metadata["_operation_result"]
            if metadata.get("_audit_error"):
                possible_write = result["write_outcome"] != "none"
                result["remaining_keys"] = result["verified_keys"] + result["remaining_keys"]
                result["verified_keys"] = []
                result.update(result="write_unconfirmed" if possible_write else "not_restored",
                              state_after="unverified", snapshot_after=None,
                              write_outcome="unknown" if possible_write else "none")
                code = 3 if possible_write else 1
                result["error"] = {"code": "side_effect_scope_unknown", "message":
                    ("写入结果未确认；" if possible_write else "") + metadata["_audit_error"]}
        else:
            code, result = restore_observation(before, args.expect_snapshot, source)
    except Exception as exc:
        if isinstance(exc, trust.TrustUsageError):
            exc = QueryError("bad_usage", str(exc), exit_code=2)
        if not isinstance(exc, QueryError):
            exc = QueryError("internal", "Recovery could not complete its read-only preflight")
        result["error"] = {"code": exc.code, "message": str(exc)}
        if exc.detail is not None:
            result["error"]["detail"] = exc.detail
        code = exc.exit_code
    value = {"schema": trust.RESTORE_SCHEMA, "operation": operation,
             "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "framework_version": version, "project": project.as_posix() if project else None,
             "side_effects": metadata.get("side_effects", []), "data": result}
    print(json.dumps(value, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
