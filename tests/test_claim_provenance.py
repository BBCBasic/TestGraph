from app.schemas.common import CommonExperienceData


def test_roast_chicken_passage_preserves_evidence_without_inventing_outcome():
    extracted = CommonExperienceData.model_validate({
        "observations": [
            {
                "category": "experience_context",
                "statement": "This was the reviewer's first roast chicken.",
                "confidence": 1.0,
                "evidence_type": "asserted",
                "supporting_quote": "Yesterday, I made my first roast chicken of my entire life.",
                "source_reference": "paragraph:1",
            },
            {
                "category": "social_context",
                "statement": "The reviewer cooked it for friends who were between houses.",
                "confidence": 1.0,
                "evidence_type": "asserted",
                "supporting_quote": "But yesterday I cooked a roast chicken for friends",
                "source_reference": "paragraph:3",
            },
        ],
        "subjective_impressions": [
            {
                "category": "perceived_difficulty",
                "statement": "Roast chicken felt unusually intimidating despite the reviewer's confidence with complex cooking.",
                "sentiment": -0.8,
                "importance_to_reviewer": 0.9,
                "confidence": 0.95,
                "evidence_type": "inferred",
                "supporting_quote": "I just had it in my mind that roasting is hard work and I'd probably fuck it up. I mean, I'm happy to tackle a 20+ ingredient Goan curry, but not a roast chicken.",
                "source_reference": "paragraph:4",
            },
        ],
    })

    payload = extracted.model_dump(exclude_none=True)
    assert "would_repeat" not in payload
    assert "strengths" in payload and payload["strengths"] == []
    assert all("rating" not in item for item in payload["observations"])
    assert payload["observations"][0]["evidence_type"] == "asserted"
    assert payload["subjective_impressions"][0]["evidence_type"] == "inferred"
    assert "supporting_quote" in payload["subjective_impressions"][0]
