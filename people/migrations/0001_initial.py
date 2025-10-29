from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("institutions", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Person",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("first_name", models.CharField(max_length=255)),
                ("last_name", models.CharField(max_length=255)),
                ("dni", models.CharField(max_length=32, unique=True)),
                ("address", models.CharField(max_length=255)),
                ("phone", models.CharField(max_length=32)),
                ("email", models.EmailField(max_length=254)),
                ("credential_code", models.CharField(blank=True, max_length=64, null=True, unique=True)),
                ("facial_enrolled", models.BooleanField(default=False)),
                (
                    "person_type",
                    models.CharField(
                        choices=[
                            ("member", "Socio"),
                            ("employee", "Empleado"),
                            ("provider", "Proveedor"),
                            ("guest", "Invitado"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "guest_type",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("member_guest", "Invitado Acompañante Socio"),
                            ("event_visitor", "Invitado Visitante Evento"),
                        ],
                        help_text="Requerido para personas de tipo invitado.",
                        max_length=32,
                        null=True,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["last_name", "first_name"],
            },
        ),
        migrations.CreateModel(
            name="GuestInvitation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("guest_type", models.CharField(choices=[("member_guest", "Invitado Acompañante Socio"), ("event_visitor", "Invitado Visitante Evento")], max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="invitations",
                        to="institutions.event",
                    ),
                ),
                (
                    "person",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="guest_invitations",
                        to="people.person",
                    ),
                ),
            ],
            options={
                "verbose_name": "Invitación",
                "verbose_name_plural": "Invitaciones",
            },
        ),
        migrations.AlterUniqueTogether(
            name="guestinvitation",
            unique_together={("person", "event")},
        ),
    ]
