from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.v2 import (
    LocationAssertion,
    SubjectClassificationDecision,
    SubjectRelationship,
    SubjectType,
    SubjectTypeAlias,
    TypeRelationship,
    V2Experience,
    V2Subject,
)


DEFAULT_OUTPUT = Path("/tmp/TestGraph_Run_A_Claude_to_GPT_Full_Evidence.md")

GPT_ANSWERS = """1. Kia EV6.
2. LX24 KRD.
3. Approximately 18,400 miles.
4. Kia EV6 LX24 KRD.
5. Rear-left tyre.
6. The cabin air filter was also replaced.
7. Lotus Emira.
8. HPE ProLiant ML350 Gen10.
9. CZJ9402M8K.
10. Intel Xeon Silver 4210.
11. 64 GB RAM.
12. 8 TB storage.
13. Patagonia Black Hole 70L duffel bag.
14. Dark blue.
15. Cotswold Outdoor.
16. Green.
17. Utility room.
18. A stack of hay bales.
19. Hay.
20. “Stack” describes their arrangement, not the bale type.
21. A rack of bicycle tyres, including a gravel tyre.
22. A gravel tyre.
23. Makita impact driver.
24. 18V.
25. Juniper & Co.
26. Pastries.
27. The Overstory.
28. It is long.
29. Forests.
30. Beacon.
31. Beacon is a software project, not a physical device.
32. Canon EOS R8.
33. Mirrorless camera.
34. The study.
35. Brompton C Line.
36. Folding bicycle.
37. The garage.
38. Le Creuset casserole dish.
39. Orange.
40. Salomon X Ultra 4 walking shoes.
41. Hiking.
42. Stainless-steel screws stored in a box.
43. They are fundamentally screws; “box” is the container.
44. 50 mm.
45. Yamaha P-225 digital piano.
46. The sitting room.
47. 88 keys.
48. Weber Spirit gas barbecue.
49. The patio.
50. Cars in V2, including descendant types: Lotus Emira; Kia EV6 LX24 KRD appears as two distinct V2 subject records under `electric car`.
51. Associated with the garage: Brompton C Line; Makita impact driver; Gravel tyre (garage rack).
52. Subjects with maintenance or replacement events: Kia EV6 LX24 KRD.
53. Explicit associations: hiking — Salomon X Ultra 4; cooking — Le Creuset casserole dish via the `cookware → casserole dish` hierarchy; music — Yamaha P-225; photography — Canon EOS R8; cycling — Brompton C Line and gravel tyre; software — Beacon.
54. Answers requiring hierarchy traversal: 7, 21–23, 30–33, 35–36, 38, 40, 45, 48, 50, 53.
55. Answers requiring multiple V2 facts/relationships: 4–6, 18–20, 21–24, 25–26, 27–29, 30–31, 32–34, 35–37, 38–39, 40–41, 42–44, 45–47, 48–49, 50–53."""


def safe(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def export_snapshot(output: Path = DEFAULT_OUTPUT) -> Path:
    with SessionLocal() as db:
        types = list(db.scalars(select(SubjectType)).all())
        aliases = list(db.scalars(select(SubjectTypeAlias)).all())
        type_rels = list(db.scalars(select(TypeRelationship)).all())
        subjects = list(db.scalars(select(V2Subject)).all())
        subject_rels = list(db.scalars(select(SubjectRelationship)).all())
        locations = list(db.scalars(select(LocationAssertion)).all())
        decisions = list(db.scalars(select(SubjectClassificationDecision)).all())
        experiences = list(db.scalars(select(V2Experience)).all())

        type_by_id = {t.id: t for t in types}
        subject_by_id = {s.id: s for s in subjects}

        lines: list[str] = []
        add = lines.append

        add("# TestGraph Cross-Model Knowledge Handoff Benchmark")
        add("")
        add("## Run A")
        add("")
        add("**Direction:** Claude → TestGraph → GPT")
        add("")
        add("## GPT retrieval answers")
        add("")
        add(GPT_ANSWERS)
        add("")

        add("## Complete subject/type tree")
        add("")
        for rel in sorted(
            type_rels,
            key=lambda item: (
                type_by_id.get(item.target_type_id).canonical_name
                if type_by_id.get(item.target_type_id)
                else "",
                type_by_id.get(item.source_type_id).canonical_name
                if type_by_id.get(item.source_type_id)
                else "",
            ),
        ):
            source = type_by_id.get(rel.source_type_id)
            target = type_by_id.get(rel.target_type_id)
            add(
                f"- {source.canonical_name if source else rel.source_type_id} "
                f"--{rel.relationship}--> "
                f"{target.canonical_name if target else rel.target_type_id} "
                f"[status={rel.status}]"
            )
        add("")

        add("## All subjects")
        add("")
        for subject in sorted(subjects, key=lambda item: (item.name or "").lower()):
            subject_type = type_by_id.get(subject.subject_type_id)
            add(f"### {subject.name}")
            add(f"- id: `{subject.id}`")
            add(f"- type: **{subject_type.canonical_name if subject_type else subject.subject_type_id}**")
            add(f"- canonical_key: `{subject.canonical_key}`")
            add(f"- classification_status: `{subject.classification_status}`")
            add(f"- classification_version: `{subject.classification_version}`")
            add(f"- identifiers: `{subject.identifiers_json}`")
            add(f"- attributes: `{subject.attributes_json}`")
            add(f"- provenance: `{subject.provenance_json}`")
            add("")

        add("## Subject relationships")
        add("")
        if subject_rels:
            for rel in subject_rels:
                source = subject_by_id.get(rel.source_subject_id)
                target = subject_by_id.get(rel.target_subject_id)
                add(
                    f"- **{source.name if source else rel.source_subject_id}** "
                    f"--{rel.relationship}--> "
                    f"**{target.name if target else rel.target_subject_id}** "
                    f"| status={rel.status} | provenance={rel.provenance_json}"
                )
        else:
            add("- None")
        add("")

        add("## Location assertions")
        add("")
        if locations:
            for assertion in locations:
                subject = subject_by_id.get(assertion.subject_id)
                obj = subject_by_id.get(assertion.object_subject_id) if assertion.object_subject_id else None
                add(
                    f"- subject=**{subject.name if subject else assertion.subject_id}** "
                    f"| predicate={assertion.predicate} "
                    f"| object={obj.name if obj else assertion.object_subject_id} "
                    f"| value={assertion.value_json} "
                    f"| qualifiers={assertion.qualifiers_json} "
                    f"| conflict={assertion.conflict_state}"
                )
        else:
            add("- None")
        add("")

        add("## Classification decisions")
        add("")
        if decisions:
            for decision in sorted(decisions, key=lambda item: safe(item.created_at)):
                subject = subject_by_id.get(decision.subject_id)
                source_type = type_by_id.get(decision.from_type_id)
                target_type = type_by_id.get(decision.target_type_id)
                add(
                    f"- subject=**{subject.name if subject else decision.subject_id}** "
                    f"| version={decision.classification_version} "
                    f"| from={source_type.canonical_name if source_type else decision.from_type_id} "
                    f"| target={target_type.canonical_name if target_type else decision.target_type_id} "
                    f"| model={decision.source_model} "
                    f"| outcome={decision.outcome} "
                    f"| reason={decision.reason!r}"
                )
        else:
            add("- None")
        add("")

        add("## Experiences")
        add("")
        if experiences:
            for experience in sorted(experiences, key=lambda item: safe(item.created_at)):
                subject = subject_by_id.get(experience.subject_id)
                add(f"### {experience.headline}")
                add(f"- subject: **{subject.name if subject else experience.subject_id}**")
                add(f"- record_type: `{experience.record_type}`")
                add(f"- summary: {experience.summary}")
                add(f"- raw_text: {experience.raw_text}")
                add(f"- structured_data: `{experience.structured_data}`")
                add(f"- submitted_data: `{experience.submitted_data}`")
                add(f"- provenance: `{experience.provenance}`")
                add("")
        else:
            add("- None")
        add("")

        add("## Duplicate identity check")
        add("")
        by_name: dict[str, list[V2Subject]] = {}
        for subject in subjects:
            by_name.setdefault((subject.name or "").strip().lower(), []).append(subject)
        duplicates = {name: items for name, items in by_name.items() if len(items) > 1}
        if duplicates:
            for name, items in sorted(duplicates.items()):
                add(f"### Duplicate name: {name}")
                for subject in items:
                    subject_type = type_by_id.get(subject.subject_type_id)
                    add(
                        f"- id={subject.id} | "
                        f"type={subject_type.canonical_name if subject_type else subject.subject_type_id} | "
                        f"canonical_key={subject.canonical_key} | "
                        f"identifiers={subject.identifiers_json} | "
                        f"status={subject.classification_status}"
                    )
                add("")
        else:
            add("- No exact duplicate subject names found.")
            add("")

        add("## Counts")
        add("")
        add(f"- subject types: {len(types)}")
        add(f"- type aliases: {len(aliases)}")
        add(f"- type relationships: {len(type_rels)}")
        add(f"- subjects: {len(subjects)}")
        add(f"- subject relationships: {len(subject_rels)}")
        add(f"- location assertions: {len(locations)}")
        add(f"- classification decisions: {len(decisions)}")
        add(f"- experiences: {len(experiences)}")

    output.write_text("\n".join(lines), encoding="utf-8")
    return output


if __name__ == "__main__":
    path = export_snapshot()
    print(path)
