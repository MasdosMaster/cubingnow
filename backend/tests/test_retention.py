from datetime import timedelta

import pytest
from django.utils import timezone

from apps.records.models import SourceObservation
from apps.records.retention import purge_expired_source_observations


def source_observation(*, payload_hash: str, observed_at):
    return SourceObservation.objects.create(
        source="wca_live",
        ingestion_method="api_polling",
        external_id=payload_hash,
        event_type="record",
        observed_at=observed_at,
        payload={"id": payload_hash},
        payload_hash=payload_hash,
    )


@pytest.mark.django_db
def test_source_observation_retention_deletes_only_expired_rows_in_batches():
    now = timezone.now()
    old = source_observation(payload_hash="old", observed_at=now - timedelta(days=31))
    recent = source_observation(payload_hash="recent", observed_at=now - timedelta(days=29))

    deleted = purge_expired_source_observations(retention_days=30, batch_size=1)

    assert deleted == 1
    assert not SourceObservation.objects.filter(pk=old.pk).exists()
    assert SourceObservation.objects.filter(pk=recent.pk).exists()


@pytest.mark.django_db
def test_source_observation_retention_can_be_disabled():
    old = source_observation(
        payload_hash="kept",
        observed_at=timezone.now() - timedelta(days=365),
    )

    assert purge_expired_source_observations(retention_days=0) == 0
    assert SourceObservation.objects.filter(pk=old.pk).exists()
