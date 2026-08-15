from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone


def retire_superseded_attempt_results(apps, schema_editor):
    Achievement = apps.get_model("records", "Achievement")
    CanonicalResult = apps.get_model("records", "CanonicalResult")
    NotificationDelivery = apps.get_model("notifications", "NotificationDelivery")
    QualificationDecision = apps.get_model("records", "QualificationDecision")
    RecentRecordObservation = apps.get_model("records", "RecentRecordObservation")
    ResultObservation = apps.get_model("records", "ResultObservation")

    now = timezone.now()

    # This is a recovery boundary. A delivery queued by the pre-cutover identity
    # generation must never be released after this migration is applied. Sent and
    # terminal rows remain untouched as audit history.
    NotificationDelivery.objects.filter(status__in=["pending", "processing", "retry"]).update(
        status="cancelled",
        claimed_by="",
        next_attempt_at=None,
        last_error_code="finalized_identity_cutover",
        last_error_message="Cancelled at finalized-result recovery boundary",
        updated_at=now,
    )

    legacy_identity = Q(identity_key__contains="|attempt:") | Q(identity_key__endswith="|aggregate")
    identity_fields = (
        "wca_competition_id",
        "competitor_wca_id",
        "event_id",
        "round_number",
        "kind",
    )
    finalized_identities = set(
        CanonicalResult.objects.filter(
            status__in=["active", "corrected"],
        )
        .exclude(legacy_identity)
        .exclude(wca_competition_id="")
        .exclude(competitor_wca_id="")
        .filter(round_number__isnull=False)
        .values_list(*identity_fields)
    )
    legacy_result_ids = [
        row[0]
        for row in (
            CanonicalResult.objects.filter(legacy_identity)
            .exclude(wca_competition_id="")
            .exclude(competitor_wca_id="")
            .filter(round_number__isnull=False)
            .values_list("pk", *identity_fields)
        )
        if row[1:] in finalized_identities
    ]
    if not legacy_result_ids:
        return

    achievement_ids = list(
        Achievement.objects.filter(result_id__in=legacy_result_ids).values_list("pk", flat=True)
    )
    QualificationDecision.objects.filter(achievement_id__in=achievement_ids).update(
        show_on_homepage=False,
        homepage_category="",
        notification_eligible=False,
        homepage_reason="superseded_by_finalized_result",
        notification_reason="superseded_by_finalized_result",
        evaluated_at=now,
        updated_at=now,
    )
    Achievement.objects.filter(pk__in=achievement_ids).update(
        status="withdrawn",
        invalidated_at=now,
        updated_at=now,
    )
    RecentRecordObservation.objects.filter(canonical_result_id__in=legacy_result_ids).update(
        canonical_result_id=None
    )
    ResultObservation.objects.filter(canonical_result_id__in=legacy_result_ids).update(
        status="retracted",
        updated_at=now,
    )
    CanonicalResult.objects.filter(pk__in=legacy_result_ids).update(
        status="retracted",
        validation_status="pending",
        validation_reason="superseded_by_finalized_result",
        updated_at=now,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0002_notificationevent_achievement"),
        ("records", "0011_subscriptionround_finalization_metadata"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="canonicalresult",
            index=models.Index(
                fields=[
                    "wca_competition_id",
                    "competitor_wca_id",
                    "event_id",
                    "round_number",
                    "kind",
                    "status",
                ],
                name="canonical_family_status_idx",
            ),
        ),
        migrations.RunPython(
            retire_superseded_attempt_results,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
