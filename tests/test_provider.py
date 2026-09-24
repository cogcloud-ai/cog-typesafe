"""Model-free tests: no network, no key, no charge. The TypeSafe API is a fake
that returns the documented response shapes (https://docs.typesafe.ai/api)."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
import urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import system_one_contract as s1   # noqa: E402
import typesafe_http as th         # noqa: E402
import typesafe_runtime as rt      # noqa: E402

KEY = {"TYPESAFE_API_KEY": "ts-test-key"}
DOCS_RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {
        "department": {"type": "choice", "choice": "technical", "confidence": 0.78,
                       "probabilities": {"technical": 0.85, "sales": 0.0, "billing": 0.15}},
        "frustration": {"type": "score", "score": 1.0, "confidence": 1.0,
                        "legend": {"0": "Calm, just stating facts", "1": "Frustrated but civil",
                                   "2": "Very angry, strong language"},
                        "probabilities": {"0": 0.0, "1": 1.0, "2": 0.0}},
        "is_urgent": {"type": "noul", "noul": 1.0}},
    "usage": {"input_tokens": 392, "output_tokens": 65}}


class FakeAPI:
    def __init__(self, response=None):
        self.response = copy.deepcopy(response or DOCS_RESPONSE)
        self.calls = []

    def models(self, key):
        self.calls.append(("models", key))
        return {"models": [{"name": "jev-latest"}]}

    def system_one(self, key, body, deadline=None):
        self.calls.append(("system_one", key, body))
        return copy.deepcopy(self.response), 1


def admitted(binding):
    binding = copy.deepcopy(binding)
    binding["state"] = "admitted"
    binding["admission"] = {"resolver_id": "test-host", "checks": [
        {"check": "test", "passed": True, "detail": "mock admission"}]}
    return binding


class BindTests(unittest.TestCase):
    def setUp(self):
        self.request = json.loads((ROOT / "examples/bind-request.json").read_text())

    def bind(self, request=None, api=None):
        with patch.dict(os.environ, KEY):
            return rt.candidate(request or self.request, api or FakeAPI())

    def test_candidate_is_never_admitted_and_validates(self):
        api = FakeAPI()
        result = self.bind(api=api)
        self.assertEqual(result["binding"]["state"], "candidate")
        self.assertIsNone(result["binding"]["admission"])
        self.assertEqual(result["binding"]["composition"], "model+harness")
        self.assertEqual(result["binding"]["invocation"],
                         {"protocol": "cog-harness-turn-command-v1", "address": "cog-command:turn"})
        self.assertEqual(api.calls, [("models", "ts-test-key")])
        rt.validate(result, "bind_result")

    def test_candidate_is_deterministic_for_host_reinspection(self):
        self.assertEqual(self.bind(), self.bind())

    def test_aliases_are_refused(self):
        for alias in ("jev-latest", "jev-preview", "jev-1.13"):
            request = copy.deepcopy(self.request)
            request["configuration"]["model_id"] = alias
            request["requirement"]["model_id"] = None
            with self.subTest(alias=alias), self.assertRaises(rt.Fault):
                self.bind(request)

    def test_missing_key_fails_before_any_call(self):
        api = FakeAPI()
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}), self.assertRaises(rt.Fault) as caught:
            rt.candidate(self.request, api)
        self.assertEqual(caught.exception.code, "missing-credential")
        self.assertEqual(api.calls, [])

    def test_boundaries(self):
        cases = [(("requirement", "accepted_compositions"), ["model"]),
                 (("requirement", "allowed_localities"), ["local"]),
                 (("requirement", "features"), ["json-output"]),
                 (("requirement", "evidence_level"), "probe"),
                 (("requirement", "identity_verified"), True),
                 (("requirement", "revision_pinned"), True),
                 (("requirement", "capability"), "agentic-harness/chat"),
                 (("requirement", "model_id"), "jev-1.12.0"),
                 (("credential_refs", "api_key"), "env:OTHER_KEY"),
                 (("configuration", "extra"), "never-accepted")]
        for (section, key), value in cases:
            request = copy.deepcopy(self.request)
            request[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(rt.Fault):
                self.bind(request)


class TurnTests(unittest.TestCase):
    def setUp(self):
        request = json.loads((ROOT / "examples/bind-request.json").read_text())
        with patch.dict(os.environ, KEY):
            self.binding = admitted(rt.candidate(request, FakeAPI())["binding"])
        self.turn_request = json.loads((ROOT / "examples/turn-request.json").read_text())

    def turn(self, api=None, request=None, binding=None):
        with patch.dict(os.environ, KEY):
            return rt.turn(request or self.turn_request, binding or self.binding, None, api or FakeAPI())

    def test_turn_returns_checked_system_one_answers(self):
        api = FakeAPI()
        response = self.turn(api)
        self.assertTrue(response["ok"])
        self.assertEqual(response["binding"], self.binding)
        result = response["payload"]["result"]
        self.assertEqual(result["answer_source"], "system-one-model")
        self.assertEqual(result["model"], "jev-1.13.0")
        self.assertEqual(s1.result_problems(result, self.turn_request["task"]["questions"]), [])
        _, _, body = api.calls[0]
        self.assertEqual(body["model"], "jev-1.13.0")
        self.assertEqual(set(body), {"state", "model", "questions"})
        self.assertEqual(response["provider_observations"]["observed_model"], "jev-1.13.0")
        rt.validate(response["payload"], "harness_turn_result")

    def test_a_different_answering_model_is_refused(self):
        response = copy.deepcopy(DOCS_RESPONSE)
        response["model"] = "jev-1.14.0"
        with self.assertRaises(rt.Fault) as caught:
            self.turn(FakeAPI(response))
        self.assertEqual(caught.exception.code, "identity-mismatch")

    def test_answers_that_do_not_fit_are_refused(self):
        response = copy.deepcopy(DOCS_RESPONSE)
        response["answers"]["department"]["choice"] = "legal"
        with self.assertRaises(rt.Fault) as caught:
            self.turn(FakeAPI(response))
        self.assertEqual(caught.exception.code, "qualification-failed")

    def test_non_finite_answers_are_refused(self):
        response = copy.deepcopy(DOCS_RESPONSE)
        response["answers"]["is_urgent"]["noul"] = float("nan")
        with self.assertRaises(rt.Fault) as caught:
            self.turn(FakeAPI(response))
        self.assertEqual(caught.exception.code, "qualification-failed")

    def test_rounded_distributions_over_many_options_are_accepted(self):
        request = copy.deepcopy(self.turn_request)
        options = {f"o{i}": None for i in range(6)}
        request["task"]["questions"] = {"pick": {"type": "choice", "instructions": "x", "criteria": options}}
        response = {"model": "jev-1.13.0", "usage": {"input_tokens": 1, "output_tokens": 1}, "answers": {
            "pick": {"type": "choice", "choice": "o0", "confidence": 0.0,
                     "probabilities": {k: 0.17 for k in options}}}}
        self.assertTrue(self.turn(FakeAPI(response), request)["ok"])

    def test_unknown_answer_fields_are_not_passed_through(self):
        response = copy.deepcopy(DOCS_RESPONSE)
        response["answers"]["is_urgent"]["explanation"] = "not part of the contract"
        result = self.turn(FakeAPI(response))["payload"]["result"]
        self.assertEqual(result["answers"]["is_urgent"], {"type": "noul", "noul": 1.0})

    def test_question_types_outside_the_binding_are_refused(self):
        binding = copy.deepcopy(self.binding)
        binding["features"] = ["choice", "noul"]
        with self.assertRaises(rt.Fault):
            self.turn(binding=binding)

    def test_unadmitted_binding_tools_and_threads_are_refused(self):
        candidate = copy.deepcopy(self.binding)
        candidate["state"] = "candidate"
        candidate["admission"] = None
        with self.assertRaises(rt.Fault):
            self.turn(binding=candidate)
        for key, value in (("tool_grant_refs", ["grant"]), ("thread_ref", "thread-1")):
            request = copy.deepcopy(self.turn_request)
            request[key] = value
            with self.subTest(key=key), self.assertRaises(rt.Fault):
                self.turn(request=request)

    def test_malformed_tasks_are_refused_before_a_call(self):
        api = FakeAPI()
        request = copy.deepcopy(self.turn_request)
        request["task"]["questions"]["frustration"]["criteria"] = ["only one level"]
        with self.assertRaises(rt.Fault):
            self.turn(api, request)
        self.assertEqual(api.calls, [])


class Response:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self, limit):
        return self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Opener:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return Response(outcome)


def http_error(status, body=b"", headers=None):
    from email.message import Message
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError("https://api.typesafe.ai/v1/systemone", status, "x", message, io.BytesIO(body))


class HTTPTests(unittest.TestCase):
    def test_rate_limits_are_retried_with_backoff(self):
        waits = []
        opener = Opener([http_error(429, headers={"retry-after": "3"}), http_error(529), DOCS_RESPONSE])
        client = th.TypeSafeHTTP(opener=opener, sleep=waits.append)
        value, attempts = client.system_one("k", {"state": "x"})
        self.assertEqual((value["model"], attempts), ("jev-1.13.0", 3))
        self.assertEqual(waits, [3.0, 2])
        self.assertEqual(opener.requests[0].get_header("Authorization"), "Bearer k")

    def test_retries_are_bounded(self):
        opener = Opener([http_error(429)] * (th.MAX_RETRIES + 1))
        with self.assertRaises(th.Fault) as caught:
            th.TypeSafeHTTP(opener=opener, sleep=lambda s: None).system_one("k", {})
        self.assertEqual(caught.exception.code, "quota-exceeded")

    def test_other_failures_are_not_retried(self):
        opener = Opener([http_error(500), DOCS_RESPONSE])
        with self.assertRaises(th.Fault):
            th.TypeSafeHTTP(opener=opener, sleep=lambda s: None).system_one("k", {})
        self.assertEqual(len(opener.requests), 1)

    def test_non_finite_json_and_broken_http_are_faults(self):
        class Raw(Response):
            def __init__(self, raw):
                self.body = raw
        class RawOpener(Opener):
            def open(self, request, timeout):
                outcome = self.outcomes.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return Raw(outcome)
        with self.assertRaises(th.Fault):
            th.TypeSafeHTTP(opener=RawOpener([b'{"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": NaN}}}'])).system_one("k", {})
        import http.client
        with self.assertRaises(th.Fault) as caught:
            th.TypeSafeHTTP(opener=Opener([http.client.BadStatusLine("x")])).system_one("k", {})
        self.assertEqual(caught.exception.code, "provider-unavailable")

    def test_error_bodies_are_never_echoed(self):
        body = json.dumps({"detail": [{"loc": ["body", "questions", "q"], "msg": "bad",
                                       "input": "SECRET STATE TEXT"}]}).encode()
        opener = Opener([http_error(422, body)])
        with self.assertRaises(th.Fault) as caught:
            th.TypeSafeHTTP(opener=opener).system_one("k", {})
        self.assertIn("body/questions/q", str(caught.exception))
        self.assertNotIn("SECRET", str(caught.exception))
        opener = Opener([http_error(401, b"key sk-live-SECRET")])
        with self.assertRaises(th.Fault) as caught:
            th.TypeSafeHTTP(opener=opener).models("k")
        self.assertEqual(caught.exception.code, "unauthorized")
        self.assertNotIn("SECRET", str(caught.exception))


class PackageTests(unittest.TestCase):
    def test_package_check(self):
        self.assertEqual(rt.package_check()["package"], "valid")

    def test_vendored_contracts_match_recorded_digests(self):
        readme = (ROOT / "contracts/README.md").read_text()
        for path in (ROOT / "contracts/satisfier-binding.schema.json", ROOT / "src/system_one_contract.py"):
            with self.subTest(path=path.name):
                self.assertIn(hashlib.sha256(path.read_bytes()).hexdigest(), readme)

    def test_vendored_system_one_contract_matches_smith_when_present(self):
        master = ROOT.parent / "cog-smith/templates/decision-cog/src/system_one_contract.py"
        if not master.exists():
            self.skipTest("cog-smith sibling not checked out")
        self.assertEqual((ROOT / "src/system_one_contract.py").read_bytes(), master.read_bytes())


if __name__ == "__main__":
    unittest.main()
