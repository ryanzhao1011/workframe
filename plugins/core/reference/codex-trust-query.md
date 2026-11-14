# Codex trust queries and narrow recovery

`workframe-query codex-trust --project <project>` observes the configuration
Codex would use for that project. It does not start a conversation or model.
Use Python 3.11 or later for this topic. Other query topics can run on earlier
supported Python versions.

Pass the same frozen launch arguments to queries, recovery and the subsequent
Codex session: `--codex-exe`, `--codex-home`, `--codex-cwd`, optional
`--codex-profile`, ordered repeatable `--codex-config`, and `--timeout-ms`.
The home and cwd must already exist. Timeout defaults to and cannot exceed
30000 milliseconds. Configuration override values are not echoed.

The query returns the `workframe.query.v1` envelope and exits 0 for a completed
classification. Read `data.state`, rather than using exit 0 as permission:

| State | Meaning |
|---|---|
| `ready` | Complete nonempty hook list, enabled effective configuration, known source and all current hashes trusted |
| `needs_trust` | Verified hooks need authorization, or project trust is missing |
| `disabled_by_user` | Effective configuration disables the plugin or hooks; policy disables identify `disabled_origin: policy` |
| `not_installed` | Installation and registration observations establish that core is absent |
| `version_unsupported` | Python or the required Codex protocol is unsupported |
| `unverified` | Runtime, paths, source, configuration, hooks or side effects could not be verified |

`data` includes `context`, `plugin`, every current hook's complete key and
opaque `current_hash`, `effective`, `problems`, `snapshot_id`, `change_token`
and `recovery`. `plugin.source` binds the loaded installation to a registered
marketplace; a project declaration alone does not establish that binding.
`snapshot_id` also binds the frozen context. Neither digest includes current
trust status, and neither digest is an authorization credential.

The measured runtime adapter supports exactly codex-cli 0.167.2. Other versions
return `unverified` with `side_effect_scope_unknown` and do not start a server.
`--capabilities` lists supported recovery versions. Project/configuration parse
failures exit 1. An unsupported query schema exits 1 with `schema_unsupported`
and `detail.supported`; invalid arguments exit 2. An unsupported recovery schema exits 2.

## Recovery

```text
workframe-codex-trust restore --project <project> --codex-exe <native-entry> --codex-home <home> --codex-cwd <cwd> --expect-snapshot <snapshot_id> --expect-source <source-JSON> --schema workframe.codex-trust-restore.v1
```

Recovery creates its own server with the frozen arguments, compares the full
snapshot and source, checks effective enablement and project trust, then sends
one version-conditioned user-layer batch. Each pending hook's exact
`hooks.state.<complete key>.trusted_hash` RPC edit carries its full opaque
`current_hash`. In 0.167.2 Codex persists the part before `/` as `trusted_hash`
and the plugin-content hash after `/` as `trusted_content_hash` under that same
complete key. Only additions or updates to these exact target values are
permitted; other keys, plugins and fields remain outside the allowance. It never
changes project trust, enablement, marketplaces or authorization records.
Every original key/hash must be read back and trusted, both persisted fields
of every attempted key must match, and the bound plugin baseline must exist
with the exact content hash bytes before success. Persisted fields and baseline
bytes are checked again after server shutdown.
An already-ready target returns `unchanged` without writing.

The caller owns consent and revocation detection. Before invoking recovery,
it must compare prior successful key/hash records and `change_token`; if an
unchanged plugin loses trust, stop automatic recovery and prompt the user.
A source change requires fresh consent. This command does not read those
records and cannot decide whether a user revoked trust.

| Exit | Result |
|---|---|
| 0 | `restored` or `unchanged`, with the target confirmed ready |
| 1 | Definite refusal, conditional-write rejection, or readback mismatch |
| 2 | Invalid usage or unsupported recovery protocol |
| 3 | `write_unconfirmed`: the write was sent but its result could not be verified; the message includes `写入结果未确认` |

The recovery envelope uses `workframe.codex-trust-restore.v1`. Read
`write_outcome`, `attempted_keys`, `verified_keys`, `remaining_keys` and `error`
even on failure. A lost response is not proof of no write. Exit 3 or a killed
process without output requires a read-only recheck, without blind retries.
`restart_required` does not prove a running session or `resume` loaded new trust.

## File effects

`side_effects: ["codex_app_server_logs"]` declares Codex's own log/runtime
writes. For 0.167.2 the checked allowance includes `log/`, `logs_*.sqlite` and
its WAL/shared memory, temporary `tmp/arg0*/codex-arg0*` and `.tmp/git-*`
directories, and `.sandbox/sandbox.*.log`. Other `*.sqlite-shm` files are
allowed only while the corresponding database and WAL bytes remain unchanged.
Marketplace clones under `.tmp/marketplaces/` are not temporary git directories.

A Codex home that 0.167.2 has not initialised is refused before any process
is started, because its first start would create `installation_id`,
`skills/.system/`, `state_5.sqlite`, `goals_1.sqlite`, `memories_1.sqlite`,
`queue_1.sqlite` and, while the plugins feature is enabled,
`.tmp/plugins.sync.lock`, none of which is in the allowance. If any of them is
missing the query returns `unverified` with `side_effect_scope_unknown`; run
Codex once in that home and query again. The lock is waived only when the
effective plugins switch is known to be off: the user config says so, no
profile is selected, no other configuration layer mentions the switch, and the
ordered `--codex-config` overrides leave it off. Anything else counts as on.

Queries do not permit `config.toml`, authentication, plugin-cache, session or
history content changes. Recovery additionally permits only its exact trust
hash edits. In 0.167.2 recovery also allows Codex to add or update its one corresponding
`CODEX_HOME/.hook-trust/` file. The filename is the lowercase SHA-256 of two
UTF-8 byte strings: the actual native user-layer `config.toml` path returned by
the frozen server, then the plugin ID. Each string is preceded by its byte
length as a u64 in little-endian order. On Windows the native path uses long
names and backslashes, without an extended-length prefix; an 8.3 alias or
forward-slash spelling produces a different filename. The file bytes must
equal the plugin-content hash, the second component of that build's
`current_hash`, without a BOM or newline. The parent directory may be created
only along with that exact file.
Updates must produce the exact target content hash bytes. Removal, links,
other plugins' files and unrelated content changes are outside this allowance.
Queries still reject every `.hook-trust/` change and every `trusted_hash` or
`trusted_content_hash` change.

Codex preserves existing baseline files. An unchanged file is not attributed
to recovery; an existing different value fails content readback with
`readback_mismatch` / exit 1 and no verified keys. Missing baseline files and
linked storage paths also fail verification. The audit rejects deletion or
changes to any value other than the exact target content. It checks the baseline again
after server shutdown; an audit failure reports `write_unconfirmed` / exit 3
after a possible write and clears the verified keys. Do not retry automatically.
Real-path preflight rejects linked baseline files/directories and overlap with project, framework state
or executing plugin trees, including linked control/config paths. Unmeasured
custom log/database locations are refused. Post-run file comparisons can
detect a violation; they cannot undo a write made by the external program.
