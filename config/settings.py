"""
Django settings for the OGCR operator API.

Everything environment-specific comes from environment variables (see .env.example),
so the same image runs in dev, demo and production.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Local runs (PyCharm, manage.py) pick up .env; real environment variables win.
# In Docker the same file is injected via env_file, so this is a no-op there.
load_dotenv(BASE_DIR / ".env", override=False)


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

SECRET_KEY = env("DJANGO_SECRET_KEY", "insecure-dev-key-change-me")
APP_VERSION = env("APP_VERSION", "dev")
DEBUG = env_bool("DJANGO_DEBUG", False)
# localhost is always allowed so the in-container healthcheck works behind any hostname
ALLOWED_HOSTS = list(dict.fromkeys(env_list("DJANGO_ALLOWED_HOSTS") + ["localhost", "127.0.0.1"]))
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

BEHIND_PROXY = env_bool("DJANGO_BEHIND_PROXY", False)
if BEHIND_PROXY:
    # TLS is terminated by the reverse proxy / ingress; trust its forwarded headers
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env_int("DJANGO_HSTS_SECONDS", 31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

INSTALLED_APPS = [
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.inlines",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    # third party
    "rest_framework",
    "rest_framework_gis",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    "axes",
    # local
    "apps.core",
    "apps.accounts",
    "apps.dcr",
    "apps.operators",
    "apps.projects",
    "apps.parcels",
    "apps.documents",
    "apps.partners",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.RequestBodyLimitMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": env("POSTGRES_DB", "operator"),
        "USER": env("POSTGRES_USER", "operator"),
        "PASSWORD": env("POSTGRES_PASSWORD", "operator"),
        "HOST": env("DB_HOST", "db"),
        "PORT": env("DB_PORT", "5432"),
        "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
        "OPTIONS": {"connect_timeout": env_int("DB_CONNECT_TIMEOUT", 10)},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# GeoDjago native libraries. On Linux (Docker) they are found automatically (gdal-bin);
# on Windows point these at the QGIS/OSGeo4W DLLs, e.g.
#   GDAL_LIBRARY_PATH=C:/Program Files/QGIS 3.44.12/bin/gdal313.dll
#   GEOS_LIBRARY_PATH=C:/Program Files/QGIS 3.44.12/bin/geos_c.dll
if env("GDAL_LIBRARY_PATH"):
    GDAL_LIBRARY_PATH = env("GDAL_LIBRARY_PATH")
    if os.name == "nt":
        os.add_dll_directory(str(Path(GDAL_LIBRARY_PATH).parent))
        os.environ.setdefault(
            "PROJ_LIB", str(Path(GDAL_LIBRARY_PATH).parent.parent / "share" / "proj")
        )
        os.environ.setdefault(
            "GDAL_DATA", str(Path(GDAL_LIBRARY_PATH).parent.parent / "share" / "gdal")
        )
if env("GEOS_LIBRARY_PATH"):
    GEOS_LIBRARY_PATH = env("GEOS_LIBRARY_PATH")

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",  # must be first: blocks locked-out logins (admin)
    "django.contrib.auth.backends.ModelBackend",
]

# django-axes: lock the admin login after repeated failures (per username + IP)
AXES_FAILURE_LIMIT = env_int("AXES_FAILURE_LIMIT", 5)
AXES_COOLOFF_TIME = 1  # hours
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True
AXES_IPWARE_PROXY_COUNT = 1 if BEHIND_PROXY else 0
AXES_IPWARE_META_PRECEDENCE_ORDER = ["HTTP_X_FORWARDED_FOR", "REMOTE_ADDR"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Belgrade"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static / media
# ---------------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True

# ---------------------------------------------------------------------------
# REST framework
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.dcr.authentication.DCRDirectLoginAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "COERCE_DECIMAL_TO_STRING": False,  # tonnes as JSON numbers, not strings
    "EXCEPTION_HANDLER": "apps.dcr.exceptions.dcr_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "120/min",
        "user": "3000/hour",
        "login": "10/min",
        "register": "5/min",
        "password_reset": "5/min",
    },
    # number of reverse proxies in front of the app; needed to read the real client IP
    "NUM_PROXIES": 1 if BEHIND_PROXY else None,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        *(["rest_framework.renderers.BrowsableAPIRenderer"] if DEBUG else []),
    ],
}

SPECTACULAR_SETTINGS = {
    "TITLE": "OGCR Operator API",
    "DESCRIPTION": "Middleware between the DCR platform and the operator frontend.",
    "VERSION": APP_VERSION,
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

# ---------------------------------------------------------------------------
# Cache (used for DCR token validation and throttling).
# Swap to Redis when running more than one API replica.
# ---------------------------------------------------------------------------

# The database cache is shared by all gunicorn workers, which throttling and the DCR token
# cache rely on. The entrypoint runs `createcachetable`. Tests use in-memory cache.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "cache_table",
        "OPTIONS": {"MAX_ENTRIES": 10000},
    }
}
if "test" in sys.argv:
    CACHES["default"] = {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "operator-api-tests",
    }

# ---------------------------------------------------------------------------
# DCR platform (OBP API)
# ---------------------------------------------------------------------------

DCR = {
    "BASE_URL": (env("DCR_BASE_URL", "") or "").rstrip("/"),
    "API_VERSION": env("DCR_API_VERSION", "v6.0.0"),
    "CONSUMER_KEY": env("DCR_CONSUMER_KEY", ""),
    "CONSUMER_SECRET": env("DCR_CONSUMER_SECRET", ""),
    "TIMEOUT": env_int("DCR_TIMEOUT", 15),
    "AUTH_CACHE_SECONDS": env_int("DCR_AUTH_CACHE_SECONDS", 300),
    "REFERENCE_CACHE_SECONDS": env_int("DCR_REFERENCE_CACHE_SECONDS", 600),
    # serve built-in sample certification schemes while DCR cannot provide them (demo phase)
    "REFERENCE_SAMPLE_FALLBACK": env_bool("DCR_REFERENCE_SAMPLE_FALLBACK", True),
    # Optional service account (needs CanCreateResetPasswordUrl) used for password reset e-mails.
    "SERVICE_USERNAME": env("DCR_SERVICE_USERNAME", ""),
    "SERVICE_PASSWORD": env("DCR_SERVICE_PASSWORD", ""),
}

# ---------------------------------------------------------------------------
# Documents (supporting files for projects and parcels, stored under MEDIA_ROOT)
# ---------------------------------------------------------------------------

DOCUMENTS = {
    "MAX_SIZE_MB": env_int("DOCUMENT_MAX_SIZE_MB", 25),
    "ALLOWED_EXTENSIONS": set(
        env_list(
            "DOCUMENT_ALLOWED_EXTENSIONS",
            "pdf,png,jpg,jpeg,tif,tiff,doc,docx,xls,xlsx,csv,txt,zip,geojson,json,kml,gpkg",
        )
    ),
}
# uploads larger than this are streamed to a temp file instead of memory
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
# hard cap on any request body, enforced before the body is read (413)
MAX_REQUEST_BODY_BYTES = (DOCUMENTS["MAX_SIZE_MB"] + 2) * 1024 * 1024
# geometry payloads: cap the number of vertices to keep GEOS/PostGIS work bounded
MAX_GEOMETRY_VERTICES = env_int("MAX_GEOMETRY_VERTICES", 50000)

# ---------------------------------------------------------------------------
# Admin (django-unfold)
# ---------------------------------------------------------------------------

UNFOLD = {
    "SITE_TITLE": "OGCR Operator Platform",
    "SITE_HEADER": "OGCR Operator",
    "SITE_SUBHEADER": "operator platform administration",
    "SITE_URL": "/api/docs/",
    "SITE_SYMBOL": "eco",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "DASHBOARD_CALLBACK": "config.admin_dashboard.dashboard_callback",
    "ENVIRONMENT": "config.admin_dashboard.environment_callback",
    "COLORS": {
        "primary": {
            "50": "240 253 244",
            "100": "220 252 231",
            "200": "187 247 208",
            "300": "134 239 172",
            "400": "74 222 128",
            "500": "34 197 94",
            "600": "22 163 74",
            "700": "21 128 61",
            "800": "22 101 52",
            "900": "20 83 45",
            "950": "5 46 22",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": "config.admin_dashboard.navigation",
    },
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "default"},
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {"level": "WARNING"},
        "apps": {"level": env("LOG_LEVEL", "INFO")},
    },
}
