from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.notifications.models import NotificationDelivery
from apps.records.classification import reclassify_scope
from apps.records.models import (
    CanonicalResult,
    ClassificationScopeWork,
    CubingChinaDiffTable,
    IngestionWorkerStatus,
    RecentRecordObservation,
    ResultObservation,
    WCALiveDiffTable,
)
from apps.records.reconciliation import reconcile_result_observation
from integrations.cubingchina.live_ingestion import _stored_result as cubingchina_result
from integrations.cubingchina.observations import result_observations as cubingchina_observations
from integrations.wca.record_validation import validate_scope_against_latest_snapshot
from integrations.wca_live.observations import result_observations as wca_observations
from integrations.wca_live.subscription_ingestion import _stored_result as wca_result


class Command(BaseCommand):
    help = "Rebuild ResultObservation and CanonicalResult from finalized provider state"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the rebuild; without this flag the command is a read-only dry run",
        )

    @staticmethod
    def _raw_links():
        return {
            (row.source, row.ingestion_method, row.source_result_identity): row.raw_observation_id
            for row in ResultObservation.objects.exclude(raw_observation_id=None).order_by("pk")
        }

    @staticmethod
    def _planned_rows(raw_links):
        missing_metadata = 0
        rows = []
        wca_states = WCALiveDiffTable.objects.filter(active=True).select_related("round")
        for state in wca_states.iterator():
            if state.round.expected_attempts is None:
                missing_metadata += 1
                continue
            raw_id = raw_links.get(
                ("wca_live", "graphql_subscription", state.stable_result_identity)
            )
            rows.extend(
                wca_observations(
                    state.round,
                    wca_result(state),
                    state.last_observed_at,
                    raw_id,
                )
            )
        china_states = CubingChinaDiffTable.objects.filter(active=True).select_related(
            "round", "round__competition"
        )
        for state in china_states.iterator():
            raw_id = raw_links.get(
                ("cubingchina", "cubingchina_websocket", state.stable_result_identity)
            )
            rows.extend(
                cubingchina_observations(
                    state.round,
                    cubingchina_result(state),
                    state.last_observed_at,
                    raw_id,
                )
            )
        return rows, missing_metadata

    def handle(self, *args, **options):
        raw_links = self._raw_links()
        rows, missing_metadata = self._planned_rows(raw_links)
        counts = Counter((row.source, row.kind) for row in rows)
        self.stdout.write(
            "Planned finalized observations: "
            + ", ".join(
                f"{source}/{kind}={count}" for (source, kind), count in sorted(counts.items())
            )
        )
        if missing_metadata:
            raise CommandError(
                f"Cannot rebuild: {missing_metadata} active WCA Live state row(s) have "
                "no round format metadata. Run refresh_wca_live_round_targets first."
            )
        if not options["apply"]:
            self.stdout.write(self.style.WARNING("Dry run only; pass --apply to rebuild"))
            return

        running_workers = list(
            IngestionWorkerStatus.objects.filter(is_running=True).values_list(
                "ingestion_method", flat=True
            )
        )
        claimed_scopes = ClassificationScopeWork.objects.exclude(claimed_by="").count()
        if running_workers or claimed_scopes:
            details = []
            if running_workers:
                details.append("running ingestion workers: " + ", ".join(running_workers))
            if claimed_scopes:
                details.append(f"claimed classification scopes: {claimed_scopes}")
            raise CommandError(
                "Pause workers before applying the backfill ("
                + "; ".join(details)
                + ")"
            )

        with transaction.atomic():
            # No delivery that predates this recovery may be sent after derived
            # identities are rebuilt. Sent and terminal deliveries remain as
            # immutable audit history; every non-terminal delivery is cancelled.
            cancelled = NotificationDelivery.objects.filter(
                status__in=[
                    NotificationDelivery.Status.PENDING,
                    NotificationDelivery.Status.PROCESSING,
                    NotificationDelivery.Status.RETRY,
                ],
            ).update(
                status=NotificationDelivery.Status.CANCELLED,
                claimed_by="",
                next_attempt_at=None,
                last_error_code="canonical_backfill",
                last_error_message="Superseded by finalized-result backfill",
                updated_at=timezone.now(),
            )

            # These are derived projections. Raw observations, provider state,
            # benchmarks, sent notification events, and subscriptions are preserved.
            ResultObservation.objects.all().delete()
            CanonicalResult.objects.all().delete()

            scopes = set()
            rebuilt = []
            for row in rows:
                observation = reconcile_result_observation(
                    row,
                    defer_classification=True,
                )
                rebuilt.append(observation)
                scopes.add((row.event_id, row.kind))
                RecentRecordObservation.objects.filter(
                    stable_result_identity=row.source_result_identity,
                    ingestion_method=row.ingestion_method,
                    kind=row.kind,
                ).update(canonical_result=observation.canonical_result)

            for event_id, kind in sorted(scopes):
                validate_scope_against_latest_snapshot(event_id, kind)
                reclassify_scope(
                    event_id,
                    kind,
                    publish_notifications=False,
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Rebuilt {len(rebuilt)} finalized observations across "
                f"{len(scopes)} scopes; cancelled {cancelled} obsolete unsent deliveries. "
                "Existing sent notification events were preserved and will not be resent."
            )
        )
