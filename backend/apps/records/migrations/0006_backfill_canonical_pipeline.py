from django.db import migrations


TRUSTED_METHODS = {"api_polling", "graphql_subscription"}
PRECEDENCE = {"WR": 0, "CR": 1, "NR": 2, "PR": 3}


def forwards(apps, schema_editor):
    RecentRecordObservation = apps.get_model("records", "RecentRecordObservation")
    CanonicalResult = apps.get_model("records", "CanonicalResult")
    ResultObservation = apps.get_model("records", "ResultObservation")
    Achievement = apps.get_model("records", "Achievement")
    QualificationDecision = apps.get_model("records", "QualificationDecision")

    for source_record in RecentRecordObservation.objects.order_by(
        "detected_at", "pk"
    ).iterator():
        canonical_key = source_record.canonical_key
        level_suffix = f"|{source_record.record_level}"
        if canonical_key.endswith(level_suffix):
            canonical_key = canonical_key[: -len(level_suffix)]
        result, created = CanonicalResult.objects.get_or_create(
            identity_key=canonical_key,
            defaults={
                "wca_competition_id": source_record.wca_competition_id,
                "competition_name": source_record.competition_name,
                "competition_country_code": source_record.competition_country_code,
                "competition_start_date": source_record.competition_start_date,
                "competition_end_date": source_record.competition_end_date,
                "round_id": source_record.round_id,
                "round_number": source_record.round_number,
                "round_name": source_record.round_name,
                "event_id": source_record.event_id,
                "event_name": source_record.event_name,
                "competitor_name": source_record.competitor_name,
                "competitor_wca_id": source_record.competitor_wca_id,
                "country_code": source_record.country_code,
                "kind": source_record.kind,
                "value": source_record.raw_result,
                "formatted_result": source_record.formatted_result,
                "entered_at": source_record.source_update_timestamp,
                "first_observed_at": source_record.first_observed_at,
                "last_observed_at": source_record.last_observed_at,
                "source_url": source_record.source_url,
                "status": (
                    "retracted"
                    if source_record.status == "withdrawn"
                    else "active"
                ),
                "validation_status": (
                    "verified"
                    if source_record.ingestion_method in TRUSTED_METHODS
                    else "pending"
                ),
                "validation_reason": (
                    "trusted_source_observation"
                    if source_record.ingestion_method in TRUSTED_METHODS
                    else "untrusted_source_only"
                ),
            },
        )
        observation_key = "|".join(
            [
                source_record.source,
                source_record.ingestion_method,
                source_record.stable_result_identity,
                source_record.kind,
                "aggregate",
            ]
        )
        observation, _ = ResultObservation.objects.update_or_create(
            observation_key=observation_key,
            defaults={
                "canonical_result": result,
                "source": source_record.source,
                "ingestion_method": source_record.ingestion_method,
                "source_result_identity": source_record.stable_result_identity,
                "source_competition_id": source_record.source_competition_id,
                "source_competitor_id": source_record.source_competitor_id,
                "kind": source_record.kind,
                "value": source_record.raw_result,
                "source_record_tag": source_record.record_level,
                "source_claim_trusted": (
                    source_record.ingestion_method in TRUSTED_METHODS
                ),
                "result_evidence_trusted": (
                    source_record.ingestion_method in TRUSTED_METHODS
                ),
                "entered_at": source_record.source_update_timestamp,
                "first_observed_at": source_record.first_observed_at,
                "last_observed_at": source_record.last_observed_at,
                "status": (
                    "retracted"
                    if source_record.status == "withdrawn"
                    else "active"
                ),
                "normalized_payload": source_record.source_payload,
            },
        )
        source_record.canonical_result_id = result.pk
        source_record.save(update_fields=["canonical_result"])
        if result.current_observation_id is None or (
            source_record.ingestion_method in TRUSTED_METHODS
        ):
            result.current_observation_id = observation.pk
            update_fields = ["current_observation"]
            if source_record.ingestion_method in TRUSTED_METHODS:
                result.value = source_record.raw_result
                result.formatted_result = source_record.formatted_result
                result.validation_status = "verified"
                result.validation_reason = "trusted_source_observation"
                update_fields.extend(
                    [
                        "value",
                        "formatted_result",
                        "validation_status",
                        "validation_reason",
                    ]
                )
            result.save(update_fields=update_fields)
        if (
            source_record.status == "active"
            and source_record.ingestion_method in TRUSTED_METHODS
        ):
            Achievement.objects.update_or_create(
                result=result,
                type=source_record.record_level,
                defaults={
                    "status": "active",
                    "classification_reason": "trusted_source_claim",
                    "source_claim_supported": True,
                    "classified_at": (
                        source_record.source_update_timestamp
                        or source_record.detected_at
                    ),
                },
            )

    for result in CanonicalResult.objects.iterator():
        achievements = list(
            Achievement.objects.filter(result=result, status="active")
        )
        visible = min(
            achievements,
            key=lambda achievement: PRECEDENCE[achievement.type],
            default=None,
        )
        for achievement in achievements:
            show = achievement.pk == getattr(visible, "pk", None)
            QualificationDecision.objects.update_or_create(
                achievement=achievement,
                defaults={
                    "show_on_homepage": show,
                    "homepage_category": achievement.type if show else "",
                    "notification_eligible": show,
                    "homepage_reason": (
                        "eligible" if show else "superseded_by_higher_achievement"
                    ),
                    "notification_reason": (
                        "eligible" if show else "superseded_by_higher_achievement"
                    ),
                    "evaluated_at": achievement.classified_at,
                },
            )


class Migration(migrations.Migration):
    dependencies = [("records", "0005_canonicalresult_achievement_and_more")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
