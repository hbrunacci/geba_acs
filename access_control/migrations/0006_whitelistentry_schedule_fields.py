from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("access_control", "0005_biostaruser_activity_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="whitelistentry",
            name="start_time",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="whitelistentry",
            name="end_time",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="whitelistentry",
            name="recurrence",
            field=models.CharField(
                choices=[("none", "Sin recurrencia"), ("daily", "Diaria"), ("weekly", "Semanal")],
                default="none",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="whitelistentry",
            name="recurrence_days",
            field=models.JSONField(
                blank=True,
                help_text="Días de la semana (0=Lunes ... 6=Domingo) para recurrencia semanal.",
                null=True,
            ),
        ),
    ]
