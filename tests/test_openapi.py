from pathlib import Path

import yaml
from openapi_spec_validator import validate

from app.main import app

SPEC_PATH = Path(__file__).resolve().parents[1] / "openapi" / "openapi.yaml"


def load_spec():
    return yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))


def test_committed_spec_matches_the_running_app():
    assert load_spec() == app.openapi(), (
        "openapi/openapi.yaml is stale; run: python scripts/export_openapi.py"
    )


def test_spec_is_valid_openapi():
    validate(load_spec())


def test_spec_documents_create_booking_contract():
    create = load_spec()["paths"]["/v1/bookings"]["post"]
    assert set(create["responses"]) >= {"201", "409", "422", "429", "503"}
    assert create["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
    assert any(p["name"] == "Idempotency-Key" for p in create["parameters"])
