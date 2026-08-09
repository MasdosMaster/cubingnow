from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("records", "0009_classificationscopework")]

    operations = [
        migrations.AddField(
            model_name="cubingchinacompetitiontarget",
            name="websocket_diagnostics",
            field=models.JSONField(default=dict),
        ),
    ]
