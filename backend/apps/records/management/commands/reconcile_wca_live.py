from .sync_recent_records import Command as SyncRecentRecordsCommand


class Command(SyncRecentRecordsCommand):
    help = "Alias for sync_recent_records"
