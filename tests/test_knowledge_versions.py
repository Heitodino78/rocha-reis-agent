"""Aprovação, histórico imutável e separação de escopos com dados de teste."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from rocha_reis_agent.knowledge_catalog import (
    Approval, FactRevision, KnowledgeCatalog, KnowledgeScope, TextFact,
    FAQFact,
)
from rocha_reis_agent.knowledge import FAQ
from rocha_reis_agent.knowledge_projection import authorize_catalog


def revision(version=1, status=Approval.APPROVED, **changes):
    values = dict(
        id="hours", version=version, replaces=version-1 if version>1 else None,
        scope=KnowledgeScope.SYNTHETIC, status=status,
        source="Fonte fictícia de teste", reviewed_by="revisor fictício",
        updated_at=datetime(2026,11,19,version,tzinfo=timezone.utc),
        data=TextFact(kind="hours",text=f"Horário fictício versão {version}"),
    )
    values.update(changes)
    return FactRevision(**values)


def test_approved_revision_is_authorized_and_traceable():
    fact=revision()
    snapshot=authorize_catalog(KnowledgeCatalog(scope="SYNTHETIC",revisions=(fact,)))
    assert snapshot.base.opening_hours == "Horário fictício versão 1"
    assert snapshot.facts[0].source == "Fonte fictícia de teste"
    assert snapshot.facts[0].reference.key == "hours@1"


@pytest.mark.parametrize("status",[Approval.DRAFT,Approval.REJECTED,Approval.ARCHIVED])
def test_unapproved_latest_revision_does_not_resurrect_old_approval(status):
    catalog=KnowledgeCatalog(scope="SYNTHETIC",revisions=(revision(),))
    updated=catalog.append(revision(2,status))
    assert len(updated.revisions)==2
    assert catalog.active()==(revision(),)
    assert authorize_catalog(updated).base.opening_hours is None


def test_update_preserves_history_and_selects_only_latest_approved_revision():
    first=KnowledgeCatalog(scope="SYNTHETIC",revisions=(revision(),))
    second=first.append(revision(2))
    assert [r.version for r in second.revisions]==[1,2]
    assert [r.version for r in second.active()]==[2]
    assert authorize_catalog(second).base.opening_hours.endswith("2")


def test_conflicting_duplicate_version_and_version_gap_are_rejected():
    first=KnowledgeCatalog(scope="SYNTHETIC",revisions=(revision(),))
    with pytest.raises(ValidationError):
        first.append(revision(data=TextFact(kind="hours",text="OUTRO DADO FICTÍCIO")))
    with pytest.raises(ValidationError):
        first.append(revision(3))


def test_two_current_records_for_the_same_field_are_rejected():
    with pytest.raises(ValidationError):
        KnowledgeCatalog(scope="SYNTHETIC",revisions=(revision(),revision(id="another-hours")))


def test_synthetic_record_cannot_be_loaded_in_official_catalog():
    with pytest.raises(ValidationError):
        KnowledgeCatalog(scope="OFFICIAL",revisions=(revision(),))


def test_simulated_official_approval_is_distinct_from_fixture_scope():
    fact=revision(scope=KnowledgeScope.OFFICIAL)
    snapshot=authorize_catalog(KnowledgeCatalog(scope="OFFICIAL",revisions=(fact,)))
    assert not snapshot.base.synthetic
    assert snapshot.base.opening_hours == fact.data.text  # Valores fictícios só neste teste.


def test_approval_requires_reviewer_and_aware_update_date():
    with pytest.raises(ValidationError):
        revision(reviewed_by=None)
    with pytest.raises(ValidationError):
        revision(updated_at=datetime(2026,11,19))


def test_nonexistent_fact_stays_unknown():
    snapshot=authorize_catalog(KnowledgeCatalog(scope="OFFICIAL"))
    assert not snapshot.facts
    assert snapshot.base.name is None
    assert snapshot.base.booking_link() is None


def test_faqs_that_match_the_same_normalized_question_cannot_conflict():
    first=revision(id="faq-one",data=FAQFact(faq=FAQ(questions=("Vocês aceitam?",),answer="Resposta fictícia A.")))
    second=revision(id="faq-two",data=FAQFact(faq=FAQ(questions=("voces aceitam!",),answer="Resposta fictícia B.")))
    with pytest.raises(ValidationError):
        KnowledgeCatalog(scope="SYNTHETIC",revisions=(first,second))
