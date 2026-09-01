from django.utils import timezone

from .models import Achievement, QualificationDecision, RecordValidation

DISPLAY_PRECEDENCE = {
    Achievement.Type.WORLD: 0,
    Achievement.Type.CONTINENTAL: 1,
    Achievement.Type.NATIONAL: 2,
    Achievement.Type.PERSONAL: 3,
}
NOTIFIABLE_TYPES = {
    Achievement.Type.WORLD,
    Achievement.Type.CONTINENTAL,
    Achievement.Type.NATIONAL,
}


def evaluate_result_qualifications(result) -> list[Achievement]:
    active = list(result.achievements.filter(status=Achievement.Status.ACTIVE).order_by("type"))
    trusted_result = (
        result.validation_status == result.ValidationStatus.VERIFIED
        and result.validation_reason == "trusted_source_observation"
    )
    independently_validated = set(
        result.record_validations.filter(
            validator=RecordValidation.Validator.WCA_RECORDS_API,
            result_value=result.value,
            status=RecordValidation.Status.VERIFIED,
        ).values_list("level", flat=True)
    )
    verified_active = [
        achievement
        for achievement in active
        if trusted_result or achievement.type in independently_validated
    ]
    visible = min(
        verified_active, key=lambda item: DISPLAY_PRECEDENCE[item.type], default=None
    )
    eligible: list[Achievement] = []
    now = timezone.now()
    for achievement in result.achievements.all():
        verified = trusted_result or achievement.type in independently_validated
        is_active = achievement.status == Achievement.Status.ACTIVE
        show = is_active and verified and achievement.pk == getattr(visible, "pk", None)
        notify = (
            is_active
            and verified
            and achievement.pk == getattr(visible, "pk", None)
            and achievement.type in NOTIFIABLE_TYPES
        )

        if not is_active:
            homepage_reason = notification_reason = "achievement_withdrawn"
        elif not verified:
            homepage_reason = notification_reason = (
                result.validation_reason or "result_not_verified"
            )
        elif not show:
            homepage_reason = "superseded_by_higher_achievement"
            notification_reason = "superseded_by_higher_achievement"
        else:
            homepage_reason = "eligible"
            notification_reason = "eligible" if notify else "notification_type_not_supported"

        QualificationDecision.objects.update_or_create(
            achievement=achievement,
            defaults={
                "show_on_homepage": show,
                "homepage_category": achievement.type if show else "",
                "notification_eligible": notify,
                "homepage_reason": homepage_reason,
                "notification_reason": notification_reason,
                "evaluated_at": now,
            },
        )
        if notify:
            eligible.append(achievement)
    return eligible
