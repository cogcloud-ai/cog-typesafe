---
type: cog [0.1]
name: cog-typesafe
description: Configurable System One provider using TypeSafe's Jev, with explicit versioned model bindings and typed, calibrated answers.
version: "0.1.0"
license: Apache-2.0
publisher: OpenTeams
manifest: cog.yaml
manifest_schema: openteams/cog-manifest [0.1]
---

# TypeSafe System One (Jev)

An installable provider for the `system-one/decisions` capability. It sends
typed questions — Noul, Choice and Score — and a state to TypeSafe's Jev, a
System One model trained to return calibrated decisions, and returns typed
answers with probabilities and confidence. An independently admitted binding
is the concrete satisfier; decision Cogs are composed with it by a host.

Model and interaction are inseparable here (`model+harness`): TypeSafe serves
the model and the System One protocol together. The profile's `kind: model`
is a remote-access declaration, not a claim to carry weights.

Supports versioned Jev IDs only (e.g. `jev-1.13.0`), the three question types,
text or JSON state up to the System One contract's limits, and declaration
evidence. Every turn requires TypeSafe to report the bound model, and every
answer is checked against the questions asked: declared options and levels,
distributions that sum to 1, a modal choice.

Out of scope: aliases (`jev-latest` moves when TypeSafe ships), open-ended
generation, tools, memory, streaming, non-text input, weights verification,
and public multi-user hosting. State leaves the machine for TypeSafe's cloud;
TypeSafe's data policies apply. Missing credentials, a different answering
model, and answers that do not fit the questions fail explicitly. No
automatic fallback to another model or provider.

Lifecycle entry points and turns return envelope v1. See README.md.
