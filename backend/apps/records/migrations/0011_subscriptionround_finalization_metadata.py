from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("records", "0010_cubingchinacompetitiontarget_websocket_diagnostics")]

    operations = [
        migrations.AddField(
            model_name="subscriptionround",
            name="format_id",
            field=models.CharField(blank=True, max_length=8),
        ),
        migrations.AddField(
            model_name="subscriptionround",
            name="format_sort_by",
            field=models.CharField(blank=True, max_length=16),
        ),
        migrations.AddField(
            model_name="subscriptionround",
            name="expected_attempts",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="subscriptionround",
            name="cutoff_attempts",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="subscriptionround",
            name="cutoff_value",
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
