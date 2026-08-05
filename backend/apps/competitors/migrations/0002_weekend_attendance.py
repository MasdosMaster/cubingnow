from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("competitions", "0002_competition_source_identity"),
        ("competitors", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="competitor",
            name="continent",
            field=models.CharField(
                blank=True,
                choices=[
                    ("Africa", "Africa"),
                    ("Asia", "Asia"),
                    ("Europe", "Europe"),
                    ("North America", "North America"),
                    ("South America", "South America"),
                    ("Oceania", "Oceania"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="attendance",
            name="sources",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.CreateModel(
            name="AttendanceSyncRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("window_start", models.DateField()),
                ("window_end", models.DateField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("running", "Running"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                        ],
                        default="running",
                        max_length=16,
                    ),
                ),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("error", models.TextField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
            ],
            options={"ordering": ["-started_at"]},
        ),
    ]
