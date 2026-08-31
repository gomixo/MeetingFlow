---
name: meetingflow-agent
description: Use MeetingFlow's local JSON protocol when an Agent needs to transcribe a completed Windows meeting audio or video file, poll the task, or read its structured transcription result. Do not use for meeting summaries, live captions, recording control, or MeetingFlow internals.
---

# MeetingFlow Agent

Run every command from the repository root. If `config/meetingflow.toml` exists, include `--config config/meetingflow.toml`; otherwise omit `--config` and use MeetingFlow defaults.

Use only `uv run meetingflow ... agent` with one JSON request on stdin. Parse the single JSON object on stdout even when the process exits with code 2. Do not import MeetingFlow's Python modules or read SQLite, `Work/jobs`, `run.jsonl`, or native analysis artifacts.

The authoritative contracts are:

- `schemas/agent-request-v1.schema.json`
- `schemas/agent-response-v1.schema.json`
- `schemas/agent-result-v1.schema.json`

## Submit and poll

1. Resolve the completed audio or video file to an absolute path and submit it:

   ```json
   {"schema_version":1,"operation":"submit","source":"D:\\Meetings\\Inbox\\meeting.mp4"}
   ```

2. Require `ok=true` and save the complete 64-character `job.job_id`.
3. Poll every 5 seconds with no overall timeout:

   ```json
   {"schema_version":1,"operation":"status","job_id":"<64-character SHA-256>"}
   ```

4. Continue polling while `job.status` is `queued` or `running`. A `job.warning.code` of `WORKER_START_FAILED` is not task failure; keep the job ID and poll again after 5 seconds.
5. When `job.status` is `failed` and `job.error.retryable` is true, send one `retry` request and resume polling. Never retry the same task more than once. For a second failure, or a non-retryable failure, stop and report `job.error.code` and `job.error.message`.
6. When `job.status` is `succeeded`, require a non-empty `job.result_path` and read that UTF-8 JSON file.

## Protocol failures

If the response has `ok=false`, stop and report `error.code` and `error.message`. Do not guess corrected fields, inspect internal state, or retry a protocol/configuration error automatically.

Exit code 0 means MeetingFlow produced a valid protocol response, including a response whose task status is `failed`. Exit code 2 means the request, configuration, or protocol failed; still parse stdout first. Stop with a contract error if stdout is not valid JSON, `schema_version` is not 1, the response job ID differs from the submitted job ID, or a succeeded task lacks `result_path`.

## Read the result

Require `schema_version == 1` and a matching `job_id`. Use `turns` as the structured transcript. Surface `review_flags` as warnings that require human review. Treat `artifacts` as optional paths for human-readable Markdown or SRT output.

If `result.json` is missing or invalid, report a contract error. Never fall back to MeetingFlow's internal task directory or native model output.
