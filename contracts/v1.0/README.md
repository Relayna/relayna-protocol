# Relayna Protocol v1.0

This profile covers a single task on a trusted same-machine deployment. Gateway authorizes the caller and applies its existing forwarding policy. The SDK service validates and publishes work, and its worker runs the handler and reports the outcome. Spooltail owns durable work delivery, status history, and results. Studio reads that history through the SDK service. Optional Cell execution has its own isolation and audit records.

## Wire contracts

- [TaskEnvelope](schemas/task-envelope.schema.json) carries the task ID, object payload, creation time, optional routing and correlation labels, and `spec_version: "1.0"`.
- [StatusEventEnvelope](schemas/status-event.schema.json) carries the task ID, event ID, status, event time, optional metadata and object result, and `spec_version: "1.0"`. The schema includes examples with and without execution details.
- [SDK service OpenAPI](openapi.yaml) describes submission, cancellation, replay, and discovery. [HTTP schemas](schemas/sdk-http.schema.json) define response bodies and SSE frame data.
- [Gateway task usage OpenAPI](gateway.openapi.yaml) describes the authorized task-scoped usage projection. [Observation schemas](schemas/observations.schema.json) cover SDK broker health and Cell inference summaries.

Timestamps are RFC 3339 date-times with a time zone. Canonical producers omit unset optional fields. Readers tolerate additive fields in transport envelopes and status or history responses. v1.0 extensions must be optional and safe for older readers to ignore. A new required field, changed field meaning, or changed replay behavior requires a separately versioned contract. This profile leaves the existing RabbitMQ and Redis contracts, alias configuration, and query-based `GET /history` API in place. It uses the [SDK adapter from PR #146](https://github.com/sarattha/relayna/tree/b2962ad7febf63fc8d164cf2665d19a628dd47a6/src/relayna/spooltail) as the envelope baseline.

## Submission and deduplication

`POST /tasks` takes one object with `task_id` and object `payload`. A new ID matches `[A-Za-z0-9][A-Za-z0-9._~-]{0,255}`. Existing read routes continue accepting legacy IDs. The request can include `created_at`, `service`, `task_type`, `correlation_id`, and `spec_version`. The version defaults to `"1.0"`. Omission and null are equivalent for `service`, `task_type`, and `correlation_id`. `created_at` and `spec_version` have no null form. The request uses only these fields.

The SDK binds the endpoint to a configured service and topic pair. It fills in an omitted service with the configured value. A supplied service must match that value. Gateway may mount the SDK paths under its existing service registration prefix. SDK-local paths in OpenAPI are relative to the registered service.

The SDK publishes a canonical work record with `event_id: "task"`. Explicit creation times are normalized to UTC. When the caller omits `created_at`, the first committed work record establishes it. For an equivalent retry the SDK reuses that record, including its timestamp. A retry that explicitly supplies a time must match the committed time after normalization. Submission equality compares normalized field values and the JSON payload, ignoring the order of object keys while preserving array order and JSON scalar types.

Spooltail deduplicates work by topic, task ID, and work event ID. With concurrent submissions, the first durable append determines the canonical record. If competing writes produce a broker conflict, the SDK checks the committed work record to distinguish a compatible retry from different input. For as long as that identity is retained, a compatible retry returns `202 {"task_id": "..."}` with no additional work record. Different input for the same retained ID returns `409` with a FastAPI `detail` string. A `202` confirms publication. Worker execution can occur later. Use a new ID for each independent task.

## Lifecycle, results, and execution identity

Work publication and status publication are distinct. `GET /status/{task_id}` returns `{task_id, event}` with the latest retained status, including any result. The route returns `404` before the first status or if none is retained. The outer `task_id` equals the event's `task_id`.

The SDK worker may report progress, such as `processing`, and settles its active work lease atomically with the terminal status in Spooltail. Producers use lower-case `completed`, `failed`, and, on cancellation-enabled services, `cancelled` for terminal outcomes. Other status values represent nonterminal progress. Each task has a single terminal outcome and no subsequent progress. A retried handler can report progress under a new attempt. Handlers with external side effects still need their own idempotency strategy.

`task_id` stays stable over all work deliveries. `correlation_id` joins related work across boundaries and an SDK worker may default it to the task ID. Each status record has a unique, stable `event_id` within its task's status journal. The SDK assigns an opaque `attempt_id` per claim and includes it on that claim's status events. Several events can share one attempt ID. Historical or externally produced status events may omit it.

When a trusted Cell invocation returns a Cell identifier, the SDK can add `meta.cell.cell_id` and, when supplied, `meta.cell.execution_id` to the corresponding attempt's status. The SDK passes the task and attempt IDs to the configured Cell invoker and checks the echoed IDs before attaching metadata. These fields link the SDK task timeline to Cell's audit evidence. Cell owns the execution and cleanup outcome, and the SDK owns translating it into task status. A missing Cell reference is valid, including when no sandbox was created.

## Cancellation

`POST /tasks/{task_id}/cancel` takes no body. The SDK confirms a durable cancellation intent with `202 {"task_id":"..."}`. A retry uses the same intent identity and cannot enqueue additional cancellation work. An unknown task returns `404`. A terminal outcome already observed before acceptance returns `409`.

The worker checks the persistent intent before starting a handler and while it runs. If cancellation wins, the worker stops execution and settles its lease atomically with `cancelled`. A racing completion may win even after the caller receives `202`. Read latest status or replay to determine the outcome. Cancellation does not undo external side effects. Enable the route only with a broker that supports atomic `cancelled` completion. Older clients can keep using submission and reads without adopting cancellation.

## Discovery and observations

Read `GET /relayna/capabilities` to discover a service's SDK version, topology, route IDs, and feature flags. Gateway forwards discovery under the same authorized service registration prefix. `spooltail.task_history.v1` selects task-scoped broker replay. `spooltail.cancel.v1` advertises the cancellation route. `spooltail.broker.v1` advertises `GET /relayna/broker`, which returns nonnegative broker-wide counters and may include the configured broker version. Treat earlier profiles without these optional flags according to the capabilities they advertise.

Gateway owns `GET /services/{registration}/tasks/{task_id}/usage` and checks the caller's registration grant. The response gives the scoped usage totals and up to 100 latest observations. An empty ledger returns zero totals, an empty observations array, and null cost. An observation can carry the SDK attempt as `run_id` when the trusted execution path established that correlation. The request ID and the optional trace ID identify the observed HTTP request. Worker attribution comes from the trusted invocation path. Studio can separately read Gateway's operator usage API with a scoped `usage:read` credential. Gateway accounting and Cell evidence remain separate from broker task status.

When scoped inference is enabled in standalone Cell, SDK status may include `meta.cell.inference` with the first 64 task inference events in journal order and a `truncated` flag if further matching events exist. Each event carries its ID, timestamp, a decision (`allow`, `deny`, `result`, or `error`), and optionally an HTTP status or a fixed, payload-safe reason code. The summary comes from trusted Cell audit records correlated by Cell ID. Inference request bodies, responses, and credentials are outside this summary.

## History and event replay

`GET /history/{task_id}?after=<event_id>` returns `{task_id, count, events}` in broker append order, even if wall clocks disagree. Every event belongs to the named task. The page holds up to 64 events and `count` is the number returned. Omit `after` to start at the first retained event. Continue from the last returned `event_id` until the page is empty. An empty history is a successful response, including before the first status event.

`GET /events/{task_id}` uses UTF-8 server-sent events. Without `Last-Event-ID`, the stream starts from the earliest retained event. With it, the stream begins strictly after that event, replays retained records in broker order, and continues with live updates. Each `status` frame carries an envelope and an SSE `id` equal to its `event_id`. Control frames `ready`, `gap`, and `error` carry JSON and have no SSE `id`. Clients keep their cursor from the last processed status. Close the stream after a terminal status. Resuming from a retained terminal event returns no further status. Clients that already have a terminal status should skip reconnecting, and can use latest status to recover when an older broker leaves that stream open.

Cursors are opaque and scoped to the service and task's status journal. Compare them for identity, pass them back unchanged, and use broker order instead of trying to sort them. A cursor that cannot be resolved yields an SSE `gap` and closes the stream. History reports the same condition as `410 Gone` with `{"detail":{"earliest_event_id":"..."}}`. In both cases `earliest_event_id` can be null when no status remains. On a gap, discard the stale cursor, consult latest status if needed, and restart without a cursor to include the earliest retained event. Passing the earliest ID as `after` would skip that event. Retention limits historical replay and deduplication. Studio should report an incomplete timeline when retained evidence has a gap.

## Spooltail interoperability

Spooltail owns its native [`spooltail.v1` Protobuf service](https://github.com/sarattha/relayna-spooltail/blob/272f9c694b3513df1e1a028066af2ad650b87a35/proto/spooltail/v1/spooltail.proto). Integrations use that service by reference and use the native SDK for journal reads, claims, settlement, and subscriptions. The native transport stores JSON objects in its event bytes and uses task and event IDs for addressing. Its completion and subscription operations recognize terminal status markers. Relayna payloads, results, attempt IDs, and Cell metadata are SDK-level content. Broker offsets and lease generations stay internal to the native adapter. A change to the native Protobuf contract is governed by Spooltail.
