from .base import *

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env("SECRET_KEY", default="django-insecure-dev-only-change-me")

# SECURITY WARNING: define the correct hosts in production!
ALLOWED_HOSTS = ["*"]



try:
    from .local import *
except ImportError:
    pass
