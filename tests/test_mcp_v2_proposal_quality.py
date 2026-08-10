from app.api.mcp_v2 import _proposal_quality_issues


def _field(name: str, schema: dict, **overrides):
    item = {
        "submitted_name": name,
        "canonical_name": name,
        "json_schema": schema,
        "description": "Reusable field for repeated experiences.",
        "aliases": [],
        "generality_reason": "This is expected to apply across many experiences in this concept.",
        "analytical_value": "Structured storage materially improves later comparison and search.",
        "existing_field_check": "Canonical fields, aliases, pending proposals and parent concepts were checked.",
        "why_not_raw_text": "Raw text alone would make this recurring value difficult to compare reliably.",
    }
    item.update(overrides)
    return item


def test_rejects_money_as_free_form_string():
    issues = _proposal_quality_issues(_field("total_cost", {"type": "string"}))
    assert any("money" in issue for issue in issues)


def test_rejects_distance_as_free_form_string():
    issues = _proposal_quality_issues(_field("distance", {"type": "string"}))
    assert any("measurement" in issue for issue in issues)


def test_rejects_unconstrained_qualitative_string():
    issues = _proposal_quality_issues(_field("enjoyment", {"type": "string"}))
    assert any("qualitative" in issue for issue in issues)


def test_allows_simple_identity_string():
    assert _proposal_quality_issues(_field("provider", {"type": "string"})) == []


def test_allows_structured_money():
    schema = {
        "type": "object",
        "properties": {
            "amount": {"type": "number"},
            "currency": {"type": "string", "minLength": 3, "maxLength": 3},
        },
        "required": ["amount", "currency"],
    }
    assert _proposal_quality_issues(_field("total_cost", schema)) == []


def test_requires_substantive_schema_reasoning():
    issues = _proposal_quality_issues(_field("city", {"type": "string"}, generality_reason="useful"))
    assert any("generality_reason" in issue for issue in issues)
