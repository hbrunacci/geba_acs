from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("access_control", "0009_parkingmovement_exit_at_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AnsesVerificationRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("id_cliente", models.BigIntegerField()),
                ("dni", models.BigIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "requested_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="anses_verification_records",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Consulta ANSES",
                "verbose_name_plural": "Consultas ANSES",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddIndex(
            model_name="ansesverificationrecord",
            index=models.Index(fields=["requested_by", "id_cliente"], name="access_contr_request_491766_idx"),
        ),
        migrations.AddIndex(
            model_name="ansesverificationrecord",
            index=models.Index(fields=["requested_by", "-created_at"], name="access_contr_request_44ecf5_idx"),
        ),
        migrations.AddConstraint(
            model_name="ansesverificationrecord",
            constraint=models.UniqueConstraint(fields=("requested_by", "id_cliente"), name="uniq_anses_record_user_client"),
        ),
    ]
