"""The committed contract matches the code (DD-99: the deployment checks the same with oss911-svc-openapi --check)."""

from pathlib import Path

from oss911_svc.openapi import document

COMMITTED = Path(__file__).resolve().parents[3] / "contracts" / "openapi" / "v1.json"


def test_committed_openapi_matches_the_code():
    assert COMMITTED.read_text(encoding="utf-8") == document(), (
        "contracts/openapi/v1.json is stale: regenerate it with oss911-svc-openapi > contracts/openapi/v1.json")
