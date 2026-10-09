# Repository review

Reviewed source: `main` at `1e626e11b7a3681307da4342246e7ed4077af40f`.

## Scope and validation limits

This is a static review of the repository's 47 text files: application entry points, contracts, agent runtime, orchestration, MCP integration, gateway, loaders, commands, tests, configuration, and documentation. The repository tree also contains an architecture PNG, which was not visually inspected.

No executable workspace was available. Dependencies were not installed; tests, compilation, the nine-process stack, real MCP transport, and provider calls were not executed. Test presence is not evidence that tests pass. No GitHub Actions runs were returned for the reviewed main commit. This review is not a production certification.

The `reliable-parecer` tip at `10c49edd5fdafaf2d37050ce965b28f5c707243d` and the reviewed main commit have no file differences.

## Assessment

The project solves a narrow, useful problem: gathering payment and reconciliation evidence and reporting discrepancies or an inconclusive result. It is suitable as an educational architecture example. It does not establish root cause, search production logs, or perform remediation.

Its strongest decisions are deterministic financial comparisons, separation of technical outcomes from business conclusions, task-bound evidence validation, and a read-only investigation path. LLMs improve presentation; this is a fixed workflow, not autonomous planning or LLM-selected tool use.

Separate processes make the boundaries visible but introduce deployment, latency, availability, and observability costs. For this scope, a modular application with domain adapters is a valid production baseline. Keep independently deployed services where ownership, scaling, isolation, or release requirements justify them.

## Findings and acceptance criteria

Priorities below describe what matters before an enterprise rollout; they do not imply that the local synthetic-data example is exposed to production users.

### 1. High: tool budgets do not bound attempted execution

Evidence: [gateway/policy.py](../src/gateway/policy.py), `GatewayPolicy._decide`.

The task/agent counter increases on successful calls and cached replays. A downstream exception returns before incrementing it. Repeated failed requests can therefore exceed the configured task budget as rate-limit tokens become available. Concurrent requests can also pass the same budget check before either increments the counter.

Reserve a call atomically before downstream execution and explicitly decide whether cache hits and failures consume the budget. Add expiry and bounded storage for counters and cached results. Use shared atomic state if the limit must span workers or instances.

Acceptance: concurrent and failing calls cannot exceed the configured attempted-call budget; cache semantics are explicit; state expires; multi-instance behavior is tested.

### 2. High before deployment: service credentials are not operator authorization

Evidence: [gateway/app.py](../src/gateway/app.py), [settings.py](../src/settings.py), [orchestrator/app.py](../src/orchestrator/app.py), [agents/payments.py](../src/agents/payments.py), and [replica/api.py](../src/replica/api.py).

The gateway authenticates static agent bearer credentials. Other application endpoints have no corresponding identity enforcement. The customer check compares caller-supplied fields and the session catalog; it does not establish operator or tenant entitlement. Localhost binding is appropriate for the local example, not a substitute for a deployment security design.

Establish operator identity at ingress, propagate trusted authorization context, enforce customer/tenant entitlements, authenticate internal services, and rotate credentials. Prevent agents from bypassing the gateway to reach MCP servers or read APIs. Use database-enforced read-only credentials or connections for the investigation runtime.

Acceptance: unauthorized operators, cross-tenant requests, forged task scope, direct endpoint access, and unauthorized tools are denied and auditable.

### 3. High for sensitive data: redaction is deliberately narrow

Evidence: [gateway/redact.py](../src/gateway/redact.py).

The secret regex matches only alphanumeric characters after `sk_live_`. For example, applying the pattern to the fictional value `sk_live_demo_secret` leaves the suffix `_secret`. Dictionary keys are not classified: a field named `token` with an arbitrary value is not masked simply because of its name.

Prefer explicit permitted fields in LLM inputs and audit records. Add key-aware redaction for secrets, realistic synthetic regression cases, and data-classification rules. Decide which fields may leave the financial environment before enabling a model provider.

Acceptance: approved sensitive-field cases do not reach prompts or logs, including nested structures and alternate token formats. Do not describe this as universal prompt-injection prevention.

### 4. Medium: validated structured output does not guarantee faithful prose

Evidence: [model_client.py](../src/model_client.py), [agents/runtime.py](../src/agents/runtime.py), and [orchestrator/loop.py](../src/orchestrator/loop.py), especially `_compose_answer`.

Provider failures fall back to deterministic text. However, successful LLM text is accepted without checking every statement against the structured report. Reattaching the replica warning does not prevent a model from misstating a status or conclusion.

Keep the structured report authoritative. Consider deterministic rendering of identifiers, statuses, amounts, and conclusions, with LLM text confined to explanation. Build evaluations for contradictions, unsupported claims, malicious text, and appropriate inconclusive answers.

Acceptance: benchmark reports preserve all critical facts and uncertainty; failures fall back predictably; model/prompt versions and evaluation results are tracked.

### 5. Medium: reproducibility and full transport validation remain unproven

Evidence: [pyproject.toml](../pyproject.toml), [tests/test_mcp.py](../tests/test_mcp.py), and the repository tree.

Dependencies have lower bounds but no committed lockfile. There is no CI workflow in the reviewed tree. MCP tests exercise supporting code and tool registration; they do not establish that the deployed gateway-to-server MCP round trip works with the installed dependency set.

Commit a reproducible dependency resolution, run tests in CI, and add a focused smoke test covering read APIs, real MCP transport, gateway, specialists, and the final report. Exercise one healthy path and meaningful failure paths without requiring a real LLM credential.

Acceptance: a clean checkout installs the pinned dependency set and passes CI and the transport smoke test. Separately verify optional provider behavior.

### 6. Medium: source freshness and end-to-end observability need contracts

Evidence: [contracts/models.py](../src/contracts/models.py), [orchestrator/loop.py](../src/orchestrator/loop.py), and [gateway/policy.py](../src/gateway/policy.py).

The report carries source timestamps and the gateway writes correlated local JSONL records. There is no enforced maximum source age, cross-domain snapshot tolerance, centralized audit retention, or tracing across every hop.

Define freshness thresholds and how stale or mismatched evidence changes the conclusion. Propagate distributed tracing, centralize audit events with access controls, and record operator identity, policy version, model/prompt version, latency, and cost where appropriate.

Acceptance: stale evidence produces the agreed outcome; an operator can trace a report to its evidence and policy decisions; audit retention and access requirements are demonstrated.

### 7. Medium: Clean Architecture and operational boundaries are partial

Evidence: [orchestrator/loop.py](../src/orchestrator/loop.py), [agents/runtime.py](../src/agents/runtime.py), and [contracts/models.py](../src/contracts/models.py).

The `AgentDirectory` abstraction and separate evidence helpers are useful seams. However, use-case coordination, HTTP handling, environment configuration, and model invocation still share modules. Shared contracts also couple services to a common package.

If independent evolution is required, keep business rules and investigation use cases behind explicit ports; place HTTP, MCP, persistence, and LLM clients in adapters. Separate domain models from external DTOs where they evolve independently. Avoid adding layers without an ownership or testing benefit.

Acceptance: financial rules and investigation decisions can be tested without HTTP, MCP, or provider dependencies; adapter failures translate into explicit application outcomes; contracts have a compatibility strategy.

### 8. Medium: request budgets are not hard cancellation

Evidence: [orchestrator/loop.py](../src/orchestrator/loop.py) and [agents/runtime.py](../src/agents/runtime.py).

The current code isolates each request's deadline and recalculates the remaining budget after Agent Card retrieval. Those are meaningful improvements. HTTP client phase/inactivity timeouts do not by themselves guarantee an end-to-end wall-clock deadline or cancel downstream work after the caller stops waiting.

Define the latency objective, propagate deadlines where supported, and verify cancellation and concurrency behavior under slow or stalled dependencies. Avoid automatic retries unless they fit the remaining budget and the operation's replay semantics.

Acceptance: controlled slow-dependency and concurrent-request tests demonstrate the agreed response-time bound and document any downstream work that can outlive it.

## Corrections already present in the reviewed source

The current code includes evidence envelope checks for task, trace, agent, customer, and date; found/record consistency checks; unknown settled-amount handling; anomaly-result consistency checks; request-local deadlines; recalculation after Agent Card lookup; provider fallback; and tests that remove inherited model credentials.

These address important failure modes. Their implementation and regression tests were inspected, but execution still needs to be confirmed.

## Documentation changes in this branch

- Translate the main article into natural English for software engineering, production support, and product readers.
- Preserve the original Portuguese article separately.
- Explain fixed orchestration, Agent Cards versus full A2A execution, and the actual MCP boundary.
- Keep application endpoint names, identifiers, and Portuguese runtime behavior unchanged.
- Correct the README's implication that copying `.env.example` automatically loads configuration.
- Describe prototype limitations without presenting them as enterprise guarantees.

The existing PNG is retained; its visual content and language were not verified.

## Recommended delivery order

1. Confirm reproducible installation and run the test suite plus a real MCP smoke test.
2. Fix attempted-call accounting, concurrency, bounded state, and sensitive-data filtering.
3. Establish operator/service identity, authorization, and protected deployment boundaries.
4. Validate deadline behavior, evidence freshness, centralized audit, and LLM factuality.
5. Measure business value against deterministic reporting before adding autonomous planning or a framework.

Product acceptance should cover investigation time, evidence accuracy, appropriate inconclusive outcomes, operator effort, and cost per investigation. The team should choose numerical targets from a baseline rather than assume that more agents or a more expensive LLM improves the result.
