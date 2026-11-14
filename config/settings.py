"""Setări platformă Active & Investiții Carmistin.

Toate valorile sensibile vin din variabile de mediu (vezi .env.example).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _incarca_env(cale):
    """Citește fișierul .env (CHEIE=valoare) fără să suprascrie variabilele deja setate în sistem."""
    if not cale.exists():
        return
    for linie in cale.read_text(encoding="utf-8").splitlines():
        linie = linie.strip()
        if not linie or linie.startswith("#") or "=" not in linie:
            continue
        k, v = linie.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_incarca_env(BASE_DIR / ".env")


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return str(env(name, default)).lower() in ("1", "true", "yes", "da")


SECRET_KEY = env("SECRET_KEY", "dev-doar-local-schimbati-in-productie")
DEBUG = env_bool("DEBUG", True)
ALLOWED_HOSTS = [h.strip() for h in env("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "platforma",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "platforma.audit.UtilizatorCurentMiddleware",
    "platforma.permisiuni.DoarCitireMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "platforma.views.context_global",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Bază de date: SQLite pentru demo local, PostgreSQL pe server ---
if env("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB"),
            "USER": env("POSTGRES_USER", "carmistin"),
            "PASSWORD": env("POSTGRES_PASSWORD", ""),
            "HOST": env("POSTGRES_HOST", "db"),
            "PORT": env("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            # SQLITE_PATH permite rularea testelor pe o copie, fără să atingă datele introduse de utilizatori
            "NAME": Path(env("SQLITE_PATH", BASE_DIR / "date" / "db.sqlite3")),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

# --- Autentificare prin domain controller (Active Directory / LDAP) ---
# Se activează cu LDAP_SERVER_URI=ldap://dc.carmistin.local. Necesită pachetul django-auth-ldap.
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
if env("LDAP_SERVER_URI"):
    import ldap  # noqa: E402
    from django_auth_ldap.config import LDAPSearch, ActiveDirectoryGroupType  # noqa: E402

    AUTH_LDAP_SERVER_URI = env("LDAP_SERVER_URI")
    AUTH_LDAP_BIND_DN = env("LDAP_BIND_DN", "")
    AUTH_LDAP_BIND_PASSWORD = env("LDAP_BIND_PASSWORD", "")
    AUTH_LDAP_USER_SEARCH = LDAPSearch(
        env("LDAP_USER_BASE", "DC=carmistin,DC=local"), ldap.SCOPE_SUBTREE, "(sAMAccountName=%(user)s)"
    )
    AUTH_LDAP_GROUP_SEARCH = LDAPSearch(
        env("LDAP_GROUP_BASE", "DC=carmistin,DC=local"), ldap.SCOPE_SUBTREE, "(objectClass=group)"
    )
    AUTH_LDAP_GROUP_TYPE = ActiveDirectoryGroupType()
    AUTH_LDAP_MIRROR_GROUPS = True
    AUTH_LDAP_USER_ATTR_MAP = {"first_name": "givenName", "last_name": "sn", "email": "mail"}
    AUTH_LDAP_CONNECTION_OPTIONS = {ldap.OPT_REFERRALS: 0}
    AUTHENTICATION_BACKENDS.insert(0, "django_auth_ldap.backend.LDAPBackend")

# Grupuri AD -> roluri în platformă (se sincronizează la fiecare autentificare)
LDAP_GRUP_ROL = {
    env("AD_GRUP_ADMIN", "INV-Administratori"): "admin",
    env("AD_GRUP_MANAGEMENT", "INV-Management"): "management",
    env("AD_GRUP_COORDONATOR", "INV-Coordonatori"): "coordonator",
    env("AD_GRUP_PM", "INV-ProjectManageri"): "pm",
    env("AD_GRUP_FINANCIAR", "INV-Financiar"): "financiar",
}

# Nume de cookie configurabile (o instanță de test pe alt port nu deconectează utilizatorul din aplicația principală)
SESSION_COOKIE_NAME = env("SESSION_COOKIE_NAME", "sessionid")
CSRF_COOKIE_NAME = env("CSRF_COOKIE_NAME", "csrftoken")

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

LANGUAGE_CODE = "ro"
TIME_ZONE = "Europe/Bucharest"
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = False

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "date" / "static"
MEDIA_URL = "/fisiere/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", BASE_DIR / "date" / "fisiere"))
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 200 * 1024 * 1024

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- AI local (Ollama pe serverul propriu) ---
AI_PROVIDER = env("AI_PROVIDER", "ollama")  # "ollama" sau "dezactivat"
OLLAMA_URL = env("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = env("OLLAMA_MODEL", "qwen2.5:14b")
AI_TIMEOUT = int(env("AI_TIMEOUT", "600"))

# --- Curs valutar ---
CURS_EUR_IMPLICIT = float(env("CURS_EUR_IMPLICIT", "5.0"))


# ---------------------------------------------------------------- email (avizarea Rapoartelor Zero din email)
# Implicit Gmail (smtp.gmail.com) cu „parolă de aplicație”. Cât timp contul / parola lipsesc,
# emailurile se salvează ca fișiere în date/emailuri (nu pleacă nimic).
EMAIL_HOST = env("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = (env("EMAIL_HOST_PASSWORD", "") or "").replace(" ", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_TIMEOUT = 20
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", f"Carmistin Investiții <{EMAIL_HOST_USER}>" if EMAIL_HOST_USER else "investitii@localhost")
EMAIL_CONFIGURAT = bool(EMAIL_HOST and EMAIL_HOST_USER and EMAIL_HOST_PASSWORD)
EMAIL_BACKEND = ("django.core.mail.backends.smtp.EmailBackend" if EMAIL_CONFIGURAT
                 else "django.core.mail.backends.filebased.EmailBackend")
EMAIL_FILE_PATH = env("EMAIL_FILE_PATH", str(BASE_DIR / "date" / "emailuri"))
# adresa la care utilizatorii accesează aplicația (pentru linkurile din email)
SITE_URL = env("SITE_URL", "http://localhost:8000").rstrip("/")
# cine avizează Raportul Zero din email; linkul expiră după RZ_LINK_ZILE zile
RZ_APROBATOR_EMAIL = env("RZ_APROBATOR_EMAIL", "andrei.brumaru@carmistin.ro")
RZ_LINK_ZILE = int(env("RZ_LINK_ZILE", "7"))
