from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.competitors.weekend import sync_weekend_attendance


class Command(BaseCommand):
    help = "Synchronize accepted WCA and CubingChina competitors for the current Wed-Tue window"

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            help="Override the as-of date (YYYY-MM-DD); intended for controlled backfills",
        )

    def handle(self, *args, **options):
        try:
            as_of = date.fromisoformat(options["date"]) if options["date"] else None
            stats = sync_weekend_attendance(as_of=as_of)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            raise CommandError(f"Weekend attendance synchronization failed: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Synchronized {competitors} competitors across {competitions} competitions "
                "({attendance_rows} attendance rows) for {window_start} through {window_end}.".format(
                    **stats
                )
            )
        )
