from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("people", "0001_initial"),
        ("institutions", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="WhitelistEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_allowed", models.BooleanField(default=True)),
                ("valid_from", models.DateField(blank=True, null=True)),
                ("valid_until", models.DateField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "access_point",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="whitelist_entries",
                        to="institutions.accesspoint",
                    ),
                ),
                (
                    "event",
                    models.ForeignKey(
                        blank=True,
                        help_text="Evento asociado si la autorización es específica para un evento.",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="whitelist_entries",
                        to="institutions.event",
                    ),
                ),
                (
                    "person",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="whitelist_entries",
                        to="people.person",
                    ),
                ),
            ],
            options={
                "verbose_name": "Entrada en lista blanca",
                "verbose_name_plural": "Lista blanca",
                "unique_together": {("person", "access_point", "event")},
            },
        ),
    ]
