from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.records.models import SubscriptionRound
from integrations.wca.api_client import WCAAPIClient
from integrations.wca_live.api_client import WCALiveAPIClient
from integrations.wca_live.discovery import discover_weekend_rounds
from integrations.weekend_window import resolve_weekend_window


class Command(BaseCommand):
    help = "Refresh WCA Live round format and cutoff metadata without starting subscriptions"

    def add_arguments(self, parser):
        parser.add_argument("--start", default=settings.WCA_WEEKEND_START)
        parser.add_argument("--end", default=settings.WCA_WEEKEND_END)
        parser.add_argument("--api-endpoint", default=settings.WCA_LIVE_API_URL)
        parser.add_argument(
            "--lookback-days",
            type=int,
            default=settings.WCA_COMPETITION_LOOKBACK_DAYS,
        )

    def handle(self, *args, **options):
        try:
            weekend_start, weekend_end = resolve_weekend_window(
                options["start"],
                options["end"],
                timezone_name=settings.WCA_WEEKEND_TIME_ZONE,
            )
        except ValueError as exc:
            raise CommandError("--start and --end must be valid ISO dates") from exc

        with WCAAPIClient(settings.WCA_PUBLIC_BASE_URL) as wcif_client:
            targets, metadata = discover_weekend_rounds(
                WCALiveAPIClient(options["api_endpoint"]),
                weekend_start,
                weekend_end,
                options["lookback_days"],
                wcif_client,
            )
        SubscriptionRound.objects.bulk_create(
            [
                SubscriptionRound(
                    round_id=target.round_id,
                    wca_live_competition_id=target.wca_live_competition_id,
                    wca_competition_id=target.wca_competition_id,
                    competition_name=target.competition_name,
                    competition_country_code=target.competition_country_code,
                    competition_timezone=target.competition_timezone,
                    competition_start_date=target.competition_start_date,
                    competition_end_date=target.competition_end_date,
                    event_id=target.event_id,
                    event_name=target.event_name,
                    round_number=target.round_number,
                    round_name=target.round_name,
                    format_id=target.format_id,
                    format_sort_by=target.format_sort_by,
                    expected_attempts=target.expected_attempts,
                    cutoff_attempts=target.cutoff_attempts,
                    cutoff_value=target.cutoff_value,
                    active=True,
                )
                for target in targets
            ],
            update_conflicts=True,
            unique_fields=["round_id"],
            update_fields=[
                "wca_live_competition_id",
                "wca_competition_id",
                "competition_name",
                "competition_country_code",
                "competition_timezone",
                "competition_start_date",
                "competition_end_date",
                "event_id",
                "event_name",
                "round_number",
                "round_name",
                "format_id",
                "format_sort_by",
                "expected_attempts",
                "cutoff_attempts",
                "cutoff_value",
            ],
        )
        missing = SubscriptionRound.objects.filter(
            round_id__in=[target.round_id for target in targets],
            expected_attempts__isnull=True,
        ).count()
        if missing:
            raise CommandError(
                f"WCA Live returned {missing} round(s) without format attempt metadata"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Hydrated {len(targets)} WCA Live rounds "
                f"from {metadata['competitions_overlapping']} competitions"
            )
        )
