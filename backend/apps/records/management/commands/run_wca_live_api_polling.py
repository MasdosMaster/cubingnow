from .sync_recent_records import Command as PollCommand


class Command(PollCommand):
    help = "Run the independent WCA Live recent-record API polling worker"

    def handle(self, *args, **options):
        options["watch"] = True
        return super().handle(*args, **options)
