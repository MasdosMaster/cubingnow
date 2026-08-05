import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("records", "0002_record_ingestion_experiment")]

    operations = [
        migrations.AlterField(
            model_name="ingestionrun",
            name="mode",
            field=models.CharField(
                choices=[
                    ("api_polling", "API polling"),
                    ("graphql_subscription", "GraphQL subscription"),
                    ("cubingchina_websocket", "CubingChina WebSocket"),
                    ("subscription", "Subscription"),
                    ("reconciliation", "Reconciliation"),
                ],
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="recentrecordobservation",
            name="ingestion_method",
            field=models.CharField(
                choices=[
                    ("api_polling", "API polling"),
                    ("graphql_subscription", "GraphQL subscription"),
                    ("cubingchina_websocket", "CubingChina WebSocket"),
                ],
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="ingestionworkerstatus",
            name="ingestion_method",
            field=models.CharField(
                choices=[
                    ("api_polling", "API polling"),
                    ("graphql_subscription", "GraphQL subscription"),
                    ("cubingchina_websocket", "CubingChina WebSocket"),
                ],
                max_length=32,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name="recentrecordobservation",
            name="source",
            field=models.CharField(default="wca_live", max_length=32),
        ),
        migrations.AddField(
            model_name="recentrecordobservation",
            name="source_result_id",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="recentrecordobservation",
            name="source_competition_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="recentrecordobservation",
            name="source_competitor_id",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="recentrecordobservation",
            name="round_number",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ingestionworkerstatus",
            name="last_successful_snapshot_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="CubingChinaCompetitionTarget",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("slug", models.CharField(max_length=180, unique=True)),
                (
                    "cubingchina_id",
                    models.PositiveIntegerField(blank=True, null=True, unique=True),
                ),
                ("wca_competition_id", models.CharField(blank=True, max_length=64)),
                ("competition_name", models.CharField(max_length=255)),
                ("competition_start_date", models.DateField()),
                ("competition_end_date", models.DateField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("active", "Active"),
                            ("error", "Error"),
                            ("retired", "Retired"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("active", models.BooleanField(default=True)),
                ("connected", models.BooleanField(default=False)),
                ("last_discovered_at", models.DateTimeField(blank=True, null=True)),
                ("last_connected_at", models.DateTimeField(blank=True, null=True)),
                ("last_message_at", models.DateTimeField(blank=True, null=True)),
                ("last_snapshot_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["competition_start_date", "competition_name"]},
        ),
        migrations.CreateModel(
            name="CubingChinaRoundTarget",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("event_id", models.CharField(max_length=16)),
                ("event_name", models.CharField(max_length=128)),
                ("round_id", models.CharField(max_length=16)),
                ("round_number", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("round_name", models.CharField(blank=True, max_length=128)),
                ("format", models.CharField(blank=True, max_length=8)),
                ("cutoff", models.IntegerField(default=0)),
                ("time_limit", models.IntegerField(default=0)),
                ("source_status", models.IntegerField(default=0)),
                ("active", models.BooleanField(default=True)),
                ("last_snapshot_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "competition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rounds",
                        to="records.cubingchinacompetitiontarget",
                    ),
                ),
            ],
            options={
                "ordering": ["competition", "event_id", "round_number", "round_id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("competition", "event_id", "round_id"),
                        name="unique_cubingchina_round_target",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="CubingChinaResultState",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("result_id", models.CharField(max_length=64)),
                ("stable_result_identity", models.CharField(max_length=255)),
                ("competitor_number", models.PositiveIntegerField()),
                ("competitor_name", models.CharField(blank=True, max_length=255)),
                ("competitor_wca_id", models.CharField(blank=True, max_length=16)),
                ("region", models.CharField(blank=True, max_length=128)),
                ("country_code", models.CharField(blank=True, max_length=8)),
                ("attempts", models.JSONField(default=list)),
                ("best", models.IntegerField(blank=True, null=True)),
                ("average", models.IntegerField(blank=True, null=True)),
                ("single_record_tag", models.CharField(blank=True, max_length=8)),
                ("average_record_tag", models.CharField(blank=True, max_length=8)),
                ("meaningful_hash", models.CharField(max_length=64)),
                ("normalized_payload", models.JSONField(default=dict)),
                ("active", models.BooleanField(default=True)),
                ("first_observed_at", models.DateTimeField()),
                ("last_observed_at", models.DateTimeField()),
                ("processed_at", models.DateTimeField()),
                (
                    "round",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="result_states",
                        to="records.cubingchinaroundtarget",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("round", "result_id"),
                        name="unique_cubingchina_result_per_round",
                    )
                ]
            },
        ),
    ]
