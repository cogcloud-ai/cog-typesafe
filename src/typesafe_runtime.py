"""TypeSafe System One provider: Jev decisions as an inseparable Model+Harness.

A provider, not a host. `bind` returns a candidate binding and never admits
it; Workbench (or another host) admits. `turn` answers one System One turn
(openteams/system-one-turn [0.1-draft]) for an admitted binding. The bound
model is a VERSIONED Jev ID: aliases such as jev-latest move when TypeSafe
ships, so they are refused, and the model that answered must be the model
that was bound. No automatic substitution, fallback or retry of a paid turn
beyond the API's own 429/529 guidance.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

sys.path.insert(0, str(Path(__file__).resolve().parent))
import system_one_contract as s1          # noqa: E402
from typesafe_http import Fault, TypeSafeHTTP  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CARD = json.loads((ROOT / "binding/provider.json").read_text())
SCHEMA = json.loads((ROOT / "contracts/satisfier-binding.schema.json").read_text())
CONTRACT = CARD["contract"]
IDENTITY = CARD["provider"]
COMPOSITION = "model+harness"
CREDENTIAL = "env:TYPESAFE_API_KEY"
HARNESS = {"id": "typesafe/system-one-api", "version": "v1"}
FEATURES = frozenset(s1.QUESTION_TYPES)
MAX_BYTES = 8 * 1024 * 1024
TURN_SECONDS = 120
MODEL_ID = re.compile(r"^jev-[0-9]+\.[0-9]+\.[0-9]+$")
PROBE = {"state": "Help! My payouts have been failing for 3 days.",
         "questions": {"is_urgent": {"type": "noul", "instructions": "Does this convey urgency?"}}}


def require(condition, code, detail):
    if not condition:
        raise Fault(code, detail)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate(value, definition=None, schema=None):
    if schema is None:
        schema = dict(SCHEMA, **{"$ref": "#/$defs/" + definition})
        schema.pop("oneOf", None)
    try:
        errors = list(Draft202012Validator(schema).iter_errors(value))
    except SchemaError:
        raise Fault("invalid-configuration", "Declared schema is not valid JSON Schema.") from None
    if errors:
        where = "/".join(str(p) for p in errors[0].absolute_path) or "$"
        raise Fault("invalid-configuration", f"Invalid {definition or 'document'} at {where}; rule {errors[0].validator}.")


def envelope(task, payload=None, fault=None, binding=None):
    return {"envelope": 1, "cog": dict(IDENTITY), "task": task, "ok": fault is None,
            "error": {"code": fault.code, "detail": str(fault)} if fault else None,
            "payload": payload, "raw": None,
            "problems": [{"check": fault.code, "detail": str(fault), "severity": "error"}] if fault else [],
            "binding": binding, "timing": {"latency_s": None}}


def api_key(reference):
    require(reference == CREDENTIAL, "missing-credential", "Only env:TYPESAFE_API_KEY is supported.")
    value = os.environ.get("TYPESAFE_API_KEY", "")
    require(bool(value) and not any(c in value for c in "\r\n"), "missing-credential",
            "TYPESAFE_API_KEY is not set in this environment.")
    return value


def http():
    return TypeSafeHTTP(version=IDENTITY["version"])


# ------------------------------------------------------------------ bind --

def candidate(request, api=None):
    """Bind request -> candidate binding (never admitted here). Declaration
    evidence only: the key is shown to work (GET /v1/models, no charge) and the
    versioned model is pinned; answers are checked against it on every turn."""
    require(isinstance(request, dict) and request.get("contract") == CONTRACT,
            "unsupported-contract", "Unsupported binding contract.")
    validate(request, "bind_request")
    require(request["provider"] == IDENTITY, "incompatible-requirement", "Provider identity mismatch.")
    validate(request["configuration"], schema=CARD["configuration_schema"])
    validate(request["credential_refs"], schema=CARD["credential_schema"])
    req, config = request["requirement"], request["configuration"]
    require(req["capability"] == CARD["capability"] and COMPOSITION in req["accepted_compositions"],
            "incompatible-requirement", "This provider supplies system-one/decisions as Model+Harness only.")
    require(set(req["features"]) <= FEATURES, "incompatible-requirement",
            f"Supported features are the question types {sorted(FEATURES)}.")
    require("cloud" in req["allowed_localities"], "incompatible-requirement",
            "TypeSafe answers in TypeSafe's cloud; the requirement must allow cloud locality.")
    require(not req["identity_verified"] and not req["revision_pinned"], "qualification-failed",
            "A hosted API cannot attest immutable weights or a pinned revision.")
    require(req["evidence_level"] == "declaration", "qualification-failed",
            "This provider offers declaration evidence; `pixi run probe` is a separate, paid check.")
    require(req["model_id"] in (None, config["model_id"]), "incompatible-requirement",
            "Required and configured model differ.")
    require(MODEL_ID.fullmatch(config["model_id"]) is not None, "unsupported-model",
            "Bind a versioned Jev ID such as jev-1.13.0; aliases move when TypeSafe ships.")
    key = api_key(request["credential_refs"]["api_key"])
    (api or http()).models(key)
    binding = {"document_kind": "binding", "contract": CONTRACT, "binding_id": request["binding_id"],
               "revision": request["revision"], "state": "candidate", "provider": dict(IDENTITY),
               "requirement_id": req["id"], "configuration": config,
               "credential_refs": request["credential_refs"], "capability": CARD["capability"],
               "features": req["features"], "composition": COMPOSITION,
               "model": {"id": config["model_id"], "revision": None, "digest": None},
               "harness": {"id": HARNESS["id"], "version": HARNESS["version"],
                           "configuration_digest": "sha256:" + digest(config)},
               "model_binding": None, "locality": "cloud",
               "invocation": {"protocol": "cog-harness-turn-command-v1", "address": "cog-command:turn"},
               "qualification": {"level": "declaration", "identity_verified": False, "revision_pinned": False,
                                 "evidence": [{"check": "api-credential",
                                               "observation": "TypeSafe accepted the configured API key "
                                                              "(GET /v1/models, no inference charge)."},
                                              {"check": "versioned-model",
                                               "observation": "A versioned Jev ID is configured but not probed at bind "
                                                              "(the model listing names aliases only); every turn requires "
                                                              "TypeSafe to report that same model. Weights are not attested."}]},
               "admission": None}
    validate(binding, "binding")
    result = {"document_kind": "bind_result", "contract": CONTRACT, "request_id": request["request_id"],
              "status": "candidate", "binding": binding, "problems": []}
    validate(result, "bind_result")
    return result


# ------------------------------------------------------------------ turn --

def check_turn(request, binding, model_binding=None):
    validate(binding, "binding")
    require(binding["state"] == "admitted" and binding["provider"] == IDENTITY
            and binding["composition"] == COMPOSITION and binding["capability"] == CARD["capability"],
            "unauthorized", "An admitted system-one/decisions binding for this provider is required.")
    require(model_binding is None, "incompatible-requirement", "Model+Harness turns take no separate model binding.")
    validate(request, "harness_turn_request")
    ref = {"binding_id": binding["binding_id"], "revision": binding["revision"]}
    require(request["binding"] == ref and request["model_binding"] is None,
            "incompatible-requirement", "Turn binding references do not match.")
    require(not request["tool_grant_refs"] and request["thread_ref"] is None,
            "incompatible-requirement", "System One turns take no tools and no remembered thread.")
    try:
        task = s1.check_task(request["task"])
    except ValueError as exc:
        raise Fault("invalid-configuration", str(exc)) from None
    used = {q["type"] for q in task["questions"].values()}
    require(used <= set(binding["features"]), "incompatible-requirement",
            f"The admitted binding does not include question types {sorted(used - set(binding['features']))}.")
    require(MODEL_ID.fullmatch(binding["configuration"]["model_id"]) is not None, "unsupported-model",
            "The binding does not pin a versioned Jev ID.")
    return task


def normalize(answers):
    """Keep exactly the contract fields of each answer; later API additions are
    not silently passed through as part of a checked result."""
    fields = {"noul": ("type", "noul"),
              "choice": ("type", "choice", "probabilities", "confidence"),
              "score": ("type", "score", "legend", "probabilities", "confidence")}
    out = {}
    for qid, answer in answers.items():
        kind = answer.get("type") if isinstance(answer, dict) else None
        require(kind in fields, "provider-unavailable", f"TypeSafe returned an unrecognised answer for {qid}.")
        out[qid] = {k: answer[k] for k in fields[kind] if k in answer}
        for key in ("probabilities", "legend"):
            if isinstance(out[qid].get(key), dict):
                out[qid][key] = {str(k): v for k, v in out[qid][key].items()}
    return out


def ask(task, model_id, key, api, deadline=None):
    """One System One call -> (contract result, observations)."""
    body = {"state": task["state"], "model": model_id, "questions": task["questions"]}
    started = time.monotonic()
    data, attempts = api.system_one(key, body, deadline=deadline)
    latency = round(time.monotonic() - started, 3)
    observed = data.get("model")
    require(observed == model_id, "identity-mismatch",
            f"TypeSafe answered as {str(observed)[:64]!r}; the binding pins {model_id!r}.")
    require(isinstance(data.get("answers"), dict), "provider-unavailable", "TypeSafe returned no answers map.")
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    tokens = {k: usage.get(k) if isinstance(usage.get(k), int) and usage.get(k) >= 0 else None
              for k in ("input_tokens", "output_tokens")}
    result = {"model": observed, "answer_source": "system-one-model",
              "answers": normalize(data["answers"]), "usage": tokens}
    problems = s1.result_problems(result, task["questions"])
    require(not problems, "qualification-failed", "TypeSafe answers do not fit the questions: " + "; ".join(problems[:3]))
    observations = {"requested_model": model_id, "observed_model": observed, "identity_verified": False,
                    "answer_source": "system-one-model", "latency_s": latency, "attempts": attempts,
                    "usage": tokens}
    return result, observations


def turn(request, binding, model_binding=None, api=None, timeout=TURN_SECONDS):
    deadline = time.monotonic() + timeout
    task = check_turn(request, binding, model_binding)
    key = api_key(binding["credential_refs"]["api_key"])
    result, observations = ask(task, binding["configuration"]["model_id"], key, api or http(), deadline)
    payload = {"document_kind": "harness_turn_result", "contract": CONTRACT, "request_id": request["request_id"],
               "binding": request["binding"], "model_binding": None, "result": result, "tool_uses": []}
    validate(payload, "harness_turn_result")
    response = envelope("turn", payload, binding=binding)
    response["provider_observations"] = observations
    return response


# ----------------------------------------------------------------- check --

def package_check():
    validate(CARD, "provider")
    for field in ("configuration_schema", "credential_schema"):
        Draft202012Validator.check_schema(CARD[field])
    import yaml
    manifest = yaml.safe_load((ROOT / "cog.yaml").read_text())
    require(manifest["id"] == IDENTITY["id"] and str(manifest["version"]) == IDENTITY["version"],
            "invalid-configuration", "Manifest and provider declaration identities differ.")
    require(CARD["capability"] == s1.CAPABILITY and CARD["compositions"] == [COMPOSITION],
            "invalid-configuration", "Declaration must offer system-one/decisions as Model+Harness.")
    example = json.loads((ROOT / "examples/bind-request.json").read_text())
    validate(example, "bind_request")
    return {"package": "valid", "availability": "not-probed",
            "credential_present": bool(os.environ.get("TYPESAFE_API_KEY"))}


def probe(model_id, api=None):
    """A live, paid (fractions of a cent) check that the key and versioned
    model answer a fixed question. Not part of admission."""
    require(MODEL_ID.fullmatch(model_id) is not None, "unsupported-model", "Probe a versioned Jev ID.")
    key = api_key(CREDENTIAL)
    result, observations = ask(s1.check_task(copy.deepcopy(PROBE)), model_id, key, api or http(),
                               time.monotonic() + 60)
    return {"model": result["model"], "answers": result["answers"], "observations": observations}


def read(path):
    p = Path(path)
    require(p.stat().st_size <= MAX_BYTES, "invalid-configuration", "Input document exceeds the size limit.")
    return json.loads(p.read_text())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["card", "check", "models", "bind", "turn", "probe", "validate"])
    parser.add_argument("--request")
    parser.add_argument("--binding")
    parser.add_argument("--model-binding")
    parser.add_argument("--definition")
    parser.add_argument("--model", default="jev-1.13.0")
    args = parser.parse_args(argv)
    try:
        if args.operation == "card":
            result = envelope("card", CARD)
        elif args.operation == "check":
            result = envelope("check", package_check())
        elif args.operation == "models":
            result = envelope("models", http().models(api_key(CREDENTIAL)))
        elif args.operation == "validate":
            validate(read(args.request), args.definition)
            result = envelope("validate", {"valid": True})
        elif args.operation == "bind":
            request = read(args.request)
            try:
                result = envelope("bind", candidate(request))
            except Fault as exc:
                rid = request.get("request_id") if isinstance(request, dict) else None
                # A failed bind is a produced failure payload; it is never admitted.
                result = envelope("bind", {"document_kind": "bind_result", "contract": CONTRACT,
                                           "request_id": rid if isinstance(rid, str) and rid else "invalid-request",
                                           "status": "failed", "binding": None,
                                           "problems": [{"code": exc.code, "detail": str(exc)}]})
                result["ok"] = False
                result["error"] = {"code": exc.code, "detail": str(exc)}
        elif args.operation == "probe":
            result = envelope("probe", probe(args.model))
        else:
            result = turn(read(args.request), read(args.binding),
                          read(args.model_binding) if args.model_binding else None)
    except Fault as exc:
        result = envelope(args.operation, fault=exc)
    except (OSError, ValueError, KeyError, TypeError):
        result = envelope(args.operation, fault=Fault("invalid-configuration",
                                                      "Invalid or unavailable local document."))
    try:
        text = json.dumps(result, indent=2, allow_nan=False)
    except ValueError:
        result = envelope(args.operation, fault=Fault("provider-unavailable", "Result contained non-finite numbers."))
        text = json.dumps(result, indent=2)
    print(text)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
