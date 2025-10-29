from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Site",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("address", models.CharField(max_length=255)),
            ],
            options={
                "verbose_name": "Sede",
                "verbose_name_plural": "Sedes",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="AccessPoint",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                (
                    "site",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="access_points",
                        to="institutions.site",
                    ),
                ),
            ],
            options={
                "verbose_name": "Acceso",
                "verbose_name_plural": "Accesos",
                "ordering": ["site__name", "name"],
                "unique_together": {("site", "name")},
            },
        ),
        migrations.CreateModel(
            name="AccessDevice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                (
                    "device_type",
                    models.CharField(
                        choices=[("turnstile", "Molinetes"), ("door", "Puerta")],
                        max_length=16,
                    ),
                ),
                ("has_credential_reader", models.BooleanField(default=False)),
                ("has_qr_reader", models.BooleanField(default=False)),
                ("has_facial_reader", models.BooleanField(default=False)),
                (
                    "access_point",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="devices",
                        to="institutions.accesspoint",
                    ),
                ),
            ],
            options={
                "verbose_name": "Dispositivo de acceso",
                "verbose_name_plural": "Dispositivos de acceso",
                "ordering": ["access_point__name", "name"],
                "unique_together": {("access_point", "name")},
            },
        ),
        migrations.CreateModel(
            name="Event",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                ("start_date", models.DateField()),
                ("end_date", models.DateField()),
                ("start_time", models.TimeField()),
                ("end_time", models.TimeField()),
                ("allowed_person_types", models.JSONField(blank=True, default=list)),
                ("allowed_guest_types", models.JSONField(blank=True, default=list)),
                (
                    "site",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="events",
                        to="institutions.site",
                    ),
                ),
            ],
            options={
                "ordering": ["-start_date", "name"],
            },
        ),
    ]
