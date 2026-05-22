from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("access_control", "0012_ansesverificationrecord_snapshot_fields_and_reset"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ansesverificationrecord",
            name="verification_status",
            field=models.CharField(
                choices=[
                    ("generated", "Constancia generada"),
                    ("office_required", "Validar identidad en oficina ANSES"),
                    ("deceased", "Fallecido"),
                    ("unknown", "Resultado no identificado"),
                ],
                default="unknown",
                max_length=32,
            ),
        ),
    ]
