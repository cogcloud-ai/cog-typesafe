# TypeSafe System One Cog

A provider Cog for the `system-one/decisions` capability backed by TypeSafe's
**Jev**, the first public System One model. Jev answers typed questions about
a state — Noul (probability a statement is true), Choice (one of your options,
with a distribution) and Score (a position on your rubric, with a
distribution) — and reports a calibrated confidence. It is fast (tens to
hundreds of milliseconds) and priced per input token. See
[TypeSafe's announcement](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
and [API reference](https://docs.typesafe.ai/api).

Decision Cogs (`smith new --class decision`) require this capability. A host
admits a binding from this package and composes it with them; a decision Cog
never selects its own provider. For an LLM-backed alternative with the same
shape, see cog-system-one-adapter.

## Install and inspect

```sh
pixi install
pixi run check          # package validity; says whether TYPESAFE_API_KEY is set
pixi run test           # model-free
pixi run card           # the provider declaration
```

## Connect

Create an API key in the [TypeSafe console](https://console.typesafe.ai/keys)
and put it in `TYPESAFE_API_KEY` in your local environment using your usual
secret management. It is the only credential reference this package accepts.
Keep it out of source control and request files.

```sh
pixi run models                       # key check, no inference charge (lists aliases)
pixi run probe -- --model jev-1.13.0  # one tiny paid call; confirms the model answers
```

## Bind explicitly

`examples/bind-request.json` pins `jev-1.13.0` and asks for all three question
types in cloud locality with declaration evidence. Bind checks the key; the
model listing names only aliases, so the versioned ID is first exercised by
`probe` or the first turn, which fails if TypeSafe reports any other model. Aliases are refused: TypeSafe
advises pinning a versioned ID once confidence thresholds are tuned, and a
binding must name what will answer.

With Workbench (the suite's host):

```sh
cd ../cog-workbench
pixi run suite -- bind --provider cog-typesafe --request ../cog-typesafe/examples/bind-request.json
pixi run suite -- activate-composition --context cog-brief-router --binding-id binding-jev --revision 1
```

`bind` alone returns only a candidate. The host re-inspects it, applies its own
admission policy and records the next binding revision. `turn` then answers
`harness_turn_request` documents whose `task` is a System One turn
(`{"state", "questions"}`) and returns
`{"model", "answer_source": "system-one-model", "answers", "usage"}`.

## What is checked

- the binding is admitted, for this provider, capability and composition;
- the task satisfies `openteams/system-one-turn [0.1-draft]`, and its question
  types are features of the binding;
- TypeSafe reports the bound model (`identity-mismatch` otherwise);
- every question is answered once, with its own type, over exactly its
  declared options or levels, with distributions summing to 1.

Answer fields beyond the contract are dropped rather than passed through.
Rate limits (429) and overload (529) are retried with bounded backoff; nothing
else is retried. Identity is provider-reported: weights are not attested.
