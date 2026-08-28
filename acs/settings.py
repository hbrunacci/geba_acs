import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

def _get_int_env(name: str, default: int | None = None) -> int | None:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_float_env(name: str, default: float | None = None) -> float | None:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-change-me")

DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"

ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "*").split(",")

# Orígenes confiables para CSRF (necesario detrás de proxy / con host explícito).
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "common.apps.CommonConfig",
    "people.apps.PeopleConfig",
    "institutions.apps.InstitutionsConfig",
    "access_control.apps.AccessControlConfig",
    "xsys.apps.XsysConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Sirve archivos estáticos en producción sin nginx (Docker).
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "acs.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "common" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "common.context_processors.nav_roles",
            ],
        },
    }
]

# Con DEBUG=0 Django cachea los templates en memoria (cached.Loader), así que un
# cambio de .html no se ve hasta reiniciar el worker. Con DJANGO_TEMPLATE_RELOAD=1
# usamos loaders sin caché: el template se relee del disco en cada render. Pensado
# para el deploy con el código montado como volumen (ver docker-compose.yml), donde
# se busca que un cambio de template se vea al refrescar, sin rebuild ni restart.
if os.getenv("DJANGO_TEMPLATE_RELOAD", "1" if DEBUG else "0") == "1":
    TEMPLATES[0]["APP_DIRS"] = False
    TEMPLATES[0]["OPTIONS"]["loaders"] = [
        "django.template.loaders.filesystem.Loader",
        "django.template.loaders.app_directories.Loader",
    ]

WSGI_APPLICATION = "acs.wsgi.application"

if os.getenv("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB"),
            "USER": os.getenv("POSTGRES_USER", "postgres"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", "postgres"),
            "HOST": os.getenv("POSTGRES_HOST", "db"),
            "PORT": os.getenv("POSTGRES_PORT", "5434"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            # Modo nativo: varios escritores (poller + fetch de fotos + requests).
            # WAL mejora la concurrencia lectura/escritura; timeout espera el lock
            # en vez de tirar "database is locked".
            "OPTIONS": {
                "timeout": 30,
                "init_command": "PRAGMA journal_mode=WAL;",
            },
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "es-ar"

TIME_ZONE = "America/Argentina/Buenos_Aires"

USE_I18N = True

USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        # Sin precompresión: el código va montado como volumen (.:/app) y
        # STATIC_ROOT cae en el bind mount de Docker Desktop (Windows), cuyo
        # filesystem no permite os.utime -> CompressedStaticFilesStorage falla
        # al comprimir. WhiteNoise igual sirve los estáticos (sin gzip previo).
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": _get_int_env("DRF_PAGE_SIZE", 50),
}

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

MSSQL_ACCESS_LOG = {
    "ENABLED": os.getenv("MSSQL_ACCESS_LOG_ENABLED", "1") == "1",
    "HOST": os.getenv("MSSQL_ACCESS_LOG_HOST", "192.168.0.6"),
    "PORT": _get_int_env("MSSQL_ACCESS_LOG_PORT", 1433),
    "DATABASE": os.getenv("MSSQL_ACCESS_LOG_DATABASE", "xsys_geba"),
    "USER": os.getenv("MSSQL_ACCESS_LOG_USER", "sa"),
    "PASSWORD": os.getenv("MSSQL_ACCESS_LOG_PASSWORD", "kvy2012*."),
    "TABLE": os.getenv("MSSQL_ACCESS_LOG_TABLE", "CD_ES"),
    "DRIVER": os.getenv("MSSQL_ACCESS_LOG_DRIVER", "{ODBC Driver 18 for SQL Server}"),
    "DEFAULT_LIMIT": _get_int_env("MSSQL_ACCESS_LOG_DEFAULT_LIMIT", 10),
}

MSSQL_CLIENT_LOOKUP = {
    "ENABLED": os.getenv("MSSQL_CLIENT_LOOKUP_ENABLED", "1") == "1",
    "HOST": os.getenv("MSSQL_CLIENT_LOOKUP_HOST", MSSQL_ACCESS_LOG["HOST"]),
    "PORT": _get_int_env("MSSQL_CLIENT_LOOKUP_PORT", MSSQL_ACCESS_LOG["PORT"]),
    "DATABASE": os.getenv("MSSQL_CLIENT_LOOKUP_DATABASE", MSSQL_ACCESS_LOG["DATABASE"]),
    "USER": os.getenv("MSSQL_CLIENT_LOOKUP_USER", MSSQL_ACCESS_LOG["USER"]),
    "PASSWORD": os.getenv("MSSQL_CLIENT_LOOKUP_PASSWORD", MSSQL_ACCESS_LOG["PASSWORD"]),
    "TABLE": os.getenv("MSSQL_CLIENT_LOOKUP_TABLE", "clientes"),
    "DRIVER": os.getenv("MSSQL_CLIENT_LOOKUP_DRIVER", MSSQL_ACCESS_LOG["DRIVER"]),
}

MSSQL_ACCESS_CHECK = {
    "ENABLED": os.getenv("MSSQL_ACCESS_CHECK_ENABLED", "1") == "1",
    "HOST": os.getenv("MSSQL_ACCESS_CHECK_HOST", MSSQL_ACCESS_LOG["HOST"]),
    "PORT": _get_int_env("MSSQL_ACCESS_CHECK_PORT", MSSQL_ACCESS_LOG["PORT"]),
    "DATABASE": os.getenv("MSSQL_ACCESS_CHECK_DATABASE", MSSQL_ACCESS_LOG["DATABASE"]),
    "USER": os.getenv("MSSQL_ACCESS_CHECK_USER", MSSQL_ACCESS_LOG["USER"]),
    "PASSWORD": os.getenv("MSSQL_ACCESS_CHECK_PASSWORD", MSSQL_ACCESS_LOG["PASSWORD"]),
    "DRIVER": os.getenv("MSSQL_ACCESS_CHECK_DRIVER", MSSQL_ACCESS_LOG["DRIVER"]),
}

# Conexión dedicada del espejo local xSys (app `xsys`).
# OJO: el puerto real del MSSQL es 49331 (no 1433) y el handshake TLS falla
# salvo que se fuerce Encrypt=no + TrustServerCertificate=yes.
MSSQL_XSYS = {
    "ENABLED": os.getenv("MSSQL_XSYS_ENABLED", "1") == "1",
    "HOST": os.getenv("MSSQL_XSYS_HOST", MSSQL_ACCESS_LOG["HOST"]),
    "PORT": _get_int_env("MSSQL_XSYS_PORT", 49331),
    "DATABASE": os.getenv("MSSQL_XSYS_DATABASE", MSSQL_ACCESS_LOG["DATABASE"]),
    "USER": os.getenv("MSSQL_XSYS_USER", MSSQL_ACCESS_LOG["USER"]),
    "PASSWORD": os.getenv("MSSQL_XSYS_PASSWORD", MSSQL_ACCESS_LOG["PASSWORD"]),
    "DRIVER": os.getenv("MSSQL_XSYS_DRIVER", MSSQL_ACCESS_LOG["DRIVER"]),
    "ENCRYPT": os.getenv("MSSQL_XSYS_ENCRYPT", "no"),
    "TRUST_SERVER_CERTIFICATE": os.getenv("MSSQL_XSYS_TRUST_CERT", "yes"),
    "LOGIN_TIMEOUT": _get_int_env("MSSQL_XSYS_LOGIN_TIMEOUT", 15),
    "QUERY_TIMEOUT": _get_int_env("MSSQL_XSYS_QUERY_TIMEOUT", 60),
    "BATCH_SIZE": _get_int_env("MSSQL_XSYS_BATCH_SIZE", 1000),
    # Acceso/controlador usados para recalcular la lista blanca general
    # (default: Cuota Social = acceso 22).
    "WHITELIST_ACCESO": _get_int_env("MSSQL_XSYS_WHITELIST_ACCESO", 22),
    "WHITELIST_CONTROLADOR": _get_int_env("MSSQL_XSYS_WHITELIST_CONTROLADOR", 0) or None,
    # Ventana de retención local de CD_ES: solo se espejan (y se conservan) los
    # movimientos de los últimos N días. Default 7 (última semana). 0 = sin límite.
    "CD_ES_RETENTION_DAYS": _get_int_env("MSSQL_XSYS_CD_ES_RETENTION_DAYS", 7),
    # Recálculo de whitelist: reusa UNA conexión MSSQL para todos los socios y
    # procesa en lotes con una pausa breve entre lotes para no saturar la base.
    "WHITELIST_BATCH_SIZE": _get_int_env("MSSQL_XSYS_WHITELIST_BATCH_SIZE", 250),
    "WHITELIST_BATCH_PAUSE": _get_float_env("MSSQL_XSYS_WHITELIST_BATCH_PAUSE", 0.15),
}

# Gracia de la cuota para el visor. Tiene que coincidir con Dias_Gracia y
# Meses_Gracia de CD_Accesos en los accesos que exigen cuota, porque
# xsys/services/cuota.py replica la fórmula de CF_SCA_ValidarUltCuotaPaga: fin
# del mes pagado (+ meses) + días. Hoy los cuatro accesos de auto/Noble están en
# 0 meses y 40 días.
XSYS_CUOTA_DIAS_GRACIA = _get_int_env("XSYS_CUOTA_DIAS_GRACIA", 40)
XSYS_CUOTA_MESES_GRACIA = _get_int_env("XSYS_CUOTA_MESES_GRACIA", 0)
