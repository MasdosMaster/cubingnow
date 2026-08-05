from django.db import migrations, models


def populate_source_keys(apps, schema_editor):
    Competition = apps.get_model("competitions", "Competition")
    for competition in Competition.objects.exclude(wca_id__isnull=True):
        competition.source_key = f"wca:{competition.wca_id}"
        competition.save(update_fields=["source_key"])


class Migration(migrations.Migration):
    dependencies = [("competitions", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="competition",
            name="wca_id",
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="competition",
            name="source_key",
            field=models.CharField(blank=True, max_length=180, null=True, unique=True),
        ),
        migrations.RunPython(populate_source_keys, migrations.RunPython.noop),
    ]
