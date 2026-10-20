"""Use openapi/openapi.yaml to test the running API.

For POST /v1/bookings it builds a request from the spec's examples, sends it,
and checks that the status code is documented and that the body matches the
documented schema (OpenAPI 3.1 schemas are JSON Schema 2020-12).

Usage: python -m clients.openapi_contract_check [--output evidence/a2/openapi-contract-check.txt]
"""

import argparse
import uuid
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from clients.http_booking_client import Reporter, send

SPEC_PATH = Path(__file__).resolve().parents[1] / "openapi" / "openapi.yaml"


def response_schema(spec, path, method, status):
    responses = spec["paths"][path][method]["responses"]
    if str(status) not in responses:
        return None
    schema = responses[str(status)]["content"]["application/json"]["schema"]
    # Resolve "#/components/..." references against the whole spec.
    return {**schema, "components": spec["components"]}


def check(report, spec, label, method, spec_path, status, body):
    schema = response_schema(spec, spec_path, method, status)
    if schema is None:
        report(f"FAIL {label}: HTTP {status} is not documented for {method.upper()} {spec_path}")
        return False
    problems = [error.message for error in Draft202012Validator(schema).iter_errors(body)]
    if problems:
        report(f"FAIL {label}: HTTP {status} body does not match schema: {problems}")
        return False
    ref = spec["paths"][spec_path][method]["responses"][str(status)]["content"]["application/json"]["schema"]
    report(f"PASS {label}: HTTP {status} documented; body matches {ref.get('$ref', ref)}")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--output")
    args = parser.parse_args()

    report = Reporter(args.output)
    spec = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    request_schema = spec["components"]["schemas"]["BookingRequest"]["properties"]
    example = {name: prop["examples"][0] for name, prop in request_schema.items()}
    example["room"] = f"{example['room']}-{uuid.uuid4().hex[:6]}"
    report(f"Spec: {SPEC_PATH.name} (OpenAPI {spec['openapi']}); request built from examples: {example}")

    results = []
    status, body, _ = send(args.base_url, "POST", "/v1/bookings", example, timeout=5)
    results.append(check(report, spec, "valid booking", "post", "/v1/bookings", status, body))

    status, body, _ = send(args.base_url, "POST", "/v1/bookings", example, timeout=5)
    results.append(check(report, spec, "same room again", "post", "/v1/bookings", status, body))

    status, body, _ = send(args.base_url, "POST", "/v1/bookings", {"room": "", "duration_minutes": 5}, timeout=5)
    results.append(check(report, spec, "invalid input", "post", "/v1/bookings", status, body))

    booking_path = "/v1/bookings/{booking_id}"
    status, body, _ = send(args.base_url, "GET", f"/v1/bookings/{uuid.uuid4()}", timeout=5)
    results.append(check(report, spec, "unknown booking", "get", booking_path, status, body))

    report(f"{sum(results)}/{len(results)} checks passed")
    report.save()
    if not all(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
