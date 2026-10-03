# Contracts

Shared JSON for the read APIs, the gateway, the specialists, and the orchestrator. Changing a field here changes every process that imports `src/contracts`.

## Technical outcome and business conclusion

A specialist consultation and the business conclusion are different fields.

`TechnicalOutcome` is the result of one domain consultation:

| Value | Meaning |
| --- | --- |
| `completed` | The replica returned evidence for the requested customer and business date. |
| `refused` | The gateway or the runtime refused the call. |
| `unavailable` | The tool did not answer. |
| `invalid` | The evidence names another customer or another business date. |

`BusinessConclusion` is what those consultations support:

| Value | Meaning |
| --- | --- |
| `divergence` | Both consultations completed with valid evidence, and a deterministic rule found a difference. |
| `no_divergence` | Both consultations completed with valid evidence, and the payment and reconciliation records agree. |
| `inconclusive` | A consultation was refused, unavailable, or invalid, or the evidence is not enough to claim agreement. |

`inconclusive` is not `no_divergence`. A refused or missing tool call must not be reported as “the domains do not diverge”.

## Divergence codes

`AnomalyArtifact.codes` and `Parecer.codes` list the differences. They are computed from the records. A stored `anomaly_code` on the reconciliation replica is not copied into this list.

| Code | Rule |
| --- | --- |
| `PAYMENT_SUCCESS_RECONCILIATION_ERROR` | Payment status is `SUCCESS` and reconciliation status is `ERROR`. |
| `AMOUNT_MISMATCH` | Payment amount, expected amount, and settled amount disagree wherever both sides of a pair are present. |
| `PAYMENT_ID_MISMATCH` | Both records have a payment id and the ids differ. |
| `PAYMENT_MISSING` | Reconciliation evidence exists and the payments replica has no row. |
| `RECONCILIATION_MISSING` | Payments evidence exists and the reconciliation replica has no row. |

`no_divergence` requires both rows, the same payment id, and matching amounts. `SUCCESS` with `UPDATED` needs the payment amount, the expected amount, and the settled amount to be equal. `FAILED` with `PENDING` needs the payment amount and the expected amount to be equal; a settled amount, when present, must match them.

## Parecer

`Parecer` keeps the technical outcomes (`payments_outcome`, `reconciliation_outcome`), the `conclusion`, the `anomaly` flag, and `codes`. `anomaly` is true only when `conclusion` is `divergence`. The prose in `answer` may be rewritten by the model. The structured fields stay on the deterministic result. If the model provider fails, `answer` stays on that deterministic text.

## Gateway decisions

`GatewayDecision` adds:

| Value | When it is recorded |
| --- | --- |
| `downstream_failed` | The tool raised or failed downstream. The audit line does not include the exception text. |
| `denied_authentication` | The bearer token was missing or unknown. The audit line does not include the token. |

The rate limit clock uses `time.monotonic()` when the caller does not pass `now`. Capacity returns as the window elapses.
