# Contracts

Shared JSON for the read APIs, the gateway, the specialists, and the orchestrator. Changing a field here changes every process that imports `src/contracts`.

## Technical outcome and business conclusion

A specialist consultation and the business conclusion are different fields.

`TechnicalOutcome` is the result of one domain consultation:

| Value | Meaning |
| --- | --- |
| `completed` | The replica returned a valid envelope for the requested customer and business date. |
| `refused` | The gateway or a dependency refused the call, including authorization and the rate limit. |
| `unavailable` | The dependency did not answer, timed out, or returned an HTTP 5xx. |
| `invalid` | The body is missing, contradicts the read contract, or names another customer or business date. |

`BusinessConclusion` is what those consultations support:

| Value | Meaning |
| --- | --- |
| `divergence` | Both consultations completed with valid evidence, and a deterministic rule found a difference. |
| `no_divergence` | Both consultations completed with valid evidence, and an accepted combination matches. |
| `inconclusive` | A consultation was refused, unavailable, or invalid, or the records are not enough to claim agreement. |

`inconclusive` is not `no_divergence`. A refused, unavailable, or invalid consultation is not a source of obtained evidence. Its status, identifiers, and summary stay out of the parecer and out of the model prompt. The other domain's valid summary is kept.

`SpecialistStatus` includes `invalid` for a consultation that stopped before a summary was built. `DomainTask` and `AnomalyTask` accept an optional `timeout_seconds`, the remaining budget for that call.

## Specialist envelopes

A specialist response is evidence only when the whole envelope belongs to the task that was sent. Before the artifact is shared, summarized, or sent to a model, it is compared with that task:

| Field | Must equal |
| --- | --- |
| `task_id` | The domain task id |
| `trace_id` | The investigation trace |
| `agent_id` | The specialist that was called |
| `customer_id` | The customer on the task |
| `business_date` | The business date on the task |

The read payload must also match that envelope: its `customer_id` and `business_date` are the envelope's, and it must satisfy the read contract below. A mismatch is `invalid`. The foreign data, summary, and source reference are discarded. The other domain's valid evidence stays. The response identifiers are not rewritten so the foreign body can pass as this task.

## Anomaly response

`AnomalyArtifact` must belong to the `AnomalyTask` that was sent: same `task_id`, `trace_id`, `agent_id` (`anomaly`), `customer_id`, and `business_date`. `payments_outcome` and `reconciliation_outcome` must be the outcomes of the evidence that was forwarded. `conclusion`, `anomaly`, and `codes` must agree: `divergence` carries `anomaly: true` and at least one code; `no_divergence` and `inconclusive` carry `anomaly: false` and no codes. `divergence` and `no_divergence` also require both forwarded consultations to be `completed`.

A response that fails this check is discarded. The investigation stays `inconclusive`. Valid domain summaries remain. A `no_divergence` from another investigation does not become this investigation's conclusion.

## Read envelopes

`PaymentReadResponse` and `ReconciliationReadResponse` are validated before any summary or model call. The payload must include `source`, `as_of`, `found`, `customer_id`, and `business_date`. Amounts are non-negative integers. Status values must be the known literals.

| Shape | Result |
| --- | --- |
| `found: true` and a valid record for the task customer and date | `completed` |
| `found: false` and no record | Proven absence. It can produce `PAYMENT_MISSING` or `RECONCILIATION_MISSING`. |
| `found` missing, `found: true` without a record, or `found: false` with a record | `invalid`. This is not absence. |
| `found` is not a JSON boolean, or an amount is not an integer | `invalid`. `0`, `1`, `"false"`, and numeric strings are not coerced into a financial fact. |
| Record for another customer, another date, or the other domain | `invalid`. The foreign payload is discarded. |

A validation failure does not stop the investigation. The parecer stays `inconclusive`, and the validation message is not copied into the answer or the logs.

## Divergence codes

`AnomalyArtifact.codes` and `Parecer.codes` list the differences. They are computed from the records. A stored `anomaly_code` on the reconciliation replica is not copied into this list.

| Code | Rule |
| --- | --- |
| `PAYMENT_SUCCESS_RECONCILIATION_ERROR` | Payment status is `SUCCESS` and reconciliation status is `ERROR`. |
| `AMOUNT_MISMATCH` | See the amount rules below. |
| `PAYMENT_ID_MISMATCH` | Both records have a payment id and the ids differ. |
| `PAYMENT_MISSING` | A valid reconciliation record exists and payments `found` is explicitly `false`. |
| `RECONCILIATION_MISSING` | A valid payments record exists and reconciliation `found` is explicitly `false`. |

`settled_amount_cents` is the amount actually settled, in cents. `null` means the replica does not know the settled amount. It is not zero. `0` means the replica reports that nothing was settled.

For `SUCCESS` with `UPDATED`, `no_divergence` needs the same payment id and the payment amount, the expected amount, and the settled amount all present and equal. Any pair that is present and different is `AMOUNT_MISMATCH`.

For `FAILED` with `PENDING`:

| Payment amount and expected amount | Settled amount | Conclusion |
| --- | --- | --- |
| Equal, same payment id | `0` | `no_divergence`. Nothing was settled, which agrees with the failure. |
| Equal, same payment id | `null` | `inconclusive`. The settled amount is unknown. |
| Equal, same payment id | Positive and equal to both amounts | `inconclusive`. A positive settlement is not treated as agreement. |
| Different, or a positive settled amount differs from the attempted or expected amount | | `divergence` with `AMOUNT_MISMATCH`. |

There is no code yet for “payment failed and a positive amount was still settled” when the three amounts match. That case stays `inconclusive` until the business rule exists.

## Parecer

`Parecer` keeps the technical outcomes (`payments_outcome`, `reconciliation_outcome`), the `conclusion`, the `anomaly` flag, and `codes`. `anomaly` is true only when `conclusion` is `divergence`. The prose in `answer` may be rewritten by the model. The structured fields stay on the deterministic result. If the model provider fails, or the remaining budget is too small for the call, `answer` stays on that deterministic text.

`sources` lists only consultations whose outcome is `completed`. A refused, unavailable, or invalid call is omitted.

If the anomaly specialist is unavailable, times out, or returns a body that is not the contract, the orchestrator still returns a parecer. Valid domain summaries stay. `conclusion` is `inconclusive` and `codes` is empty. The orchestrator does not compute a business conclusion in place of that specialist.

## Deadlines

Each investigation builds its own `Deadline`. The shared HTTP client does not store that deadline. Two investigations can use the same directory without changing each other's remaining time.

`INVESTIGATION_DEADLINE_SECONDS` (default 16) is the whole investigation. `PARECER_RESERVE_SECONDS` (default 1) stays unused until the parecer is composed. A specialist call starts only when the time left, after that reserve, covers the call floor (the gateway timeout plus a short margin, and never more than `AGENT_CALL_TIMEOUT_SECONDS`, default 5).

The call window is fixed when the call starts. It is the smaller of the specialist limit and the investigation time still left. Fetching the Agent Card spends that window. Before the task POST, the clock is read again. The POST is not started when the remaining time is below half a second. The `timeout_seconds` sent to the specialist is that remaining time minus a 0.25s margin, so the specialist can still return. The card request does not refresh the window.

httpx applies a float `timeout` separately to connect, read, write, and pool. That number is not a wall-clock cap on the whole request: each phase may use up to that long, and the read timeout is the wait for the next response chunk. The limits this service actually enforces are the deadline checks before a request is started. The httpx value only bounds one phase of that request. There is no automatic retry.

Inside the specialist, the tool call and the model call are split from `timeout_seconds`. `MODEL_TIMEOUT_SECONDS` (default 2) is only an upper bound. When the model fails or does not fit, the answer stays on the deterministic text.

## Gateway decisions

`GatewayDecision` adds:

| Value | When it is recorded |
| --- | --- |
| `downstream_failed` | The tool raised, timed out, or failed downstream. The audit line does not include the exception text. |
| `denied_authentication` | The bearer token was missing or unknown. The audit line does not include the token. |
| `invalid_payload` | The specialist client received a body that is not the gateway contract. The gateway audit does not emit this value. The raw body is not copied into the specialist summary. |

The rate limit clock uses `time.monotonic()` when the caller does not pass `now`. Capacity returns as the window elapses.

## Transport

| Failure | Technical result |
| --- | --- |
| Connection error or timeout, between the specialist and the gateway or between the orchestrator and a specialist | `unavailable` |
| HTTP 5xx from a dependency | `unavailable` |
| HTTP 401 or 403 | `refused` |
| Agent card missing, not JSON, or without the expected skill | `invalid` for a bad card, `unavailable` when the card request itself fails |
| Non-JSON, malformed, or contract-incompatible task response | `invalid` |

These failures are returned as a parecer. They do not raise at the orchestrator entry.
