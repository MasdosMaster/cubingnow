from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("records", "0012_retire_superseded_attempt_results"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="SubscriptionResultState",
            new_name="WCALiveDiffTable",
        ),
        migrations.RenameModel(
            old_name="CubingChinaResultState",
            new_name="CubingChinaDiffTable",
        ),
    ]
