from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("records", "0003_cubingchina_live_pipeline")]

    operations = [
        migrations.AddField(
            model_name="recentrecordobservation",
            name="competition_country_code",
            field=models.CharField(blank=True, max_length=8),
        ),
        migrations.AddField(
            model_name="subscriptionround",
            name="competition_country_code",
            field=models.CharField(blank=True, max_length=8),
        ),
    ]
