import pytest
from jsonschema import ValidationError


def test_valid_llm_call_event_passes(validate_event, valid_llm_call_event):
    validate_event(valid_llm_call_event)


def test_unknown_event_type_rejected(validate_event, valid_llm_call_event):
    valid_llm_call_event["event_type"] = "bogus"
    with pytest.raises(ValidationError):
        validate_event(valid_llm_call_event)


def test_llm_call_requires_provider(validate_event, valid_llm_call_event):
    del valid_llm_call_event["payload"]["provider"]
    with pytest.raises(ValidationError):
        validate_event(valid_llm_call_event)


def test_extra_envelope_key_rejected(validate_event, valid_llm_call_event):
    valid_llm_call_event["surprise"] = True
    with pytest.raises(ValidationError):
        validate_event(valid_llm_call_event)


def test_extra_payload_key_accepted(validate_event, valid_llm_call_event):
    valid_llm_call_event["payload"]["vendor_specific"] = {"anything": [1, 2, 3]}
    validate_event(valid_llm_call_event)


def test_llm_call_requires_call_id(validate_event, valid_llm_call_event):
    valid_llm_call_event["call_id"] = None
    with pytest.raises(ValidationError):
        validate_event(valid_llm_call_event)
