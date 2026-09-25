"""docs/protocol.md and the JSON Schemas must match the Pydantic protocol models."""

from tests.conftest import load_script


def test_protocol_docs_are_up_to_date() -> None:
    mod = load_script("gen_protocol_docs")
    assert mod.main(["--check"]) == 0, "rode: .venv\\Scripts\\python scripts\\gen_protocol_docs.py"


def test_every_message_has_version_field() -> None:
    from app.schemas.agent import PROTOCOL_MESSAGES  # noqa: PLC0415

    for name, model in PROTOCOL_MESSAGES.items():
        assert "v" in model.model_fields, name
