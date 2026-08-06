from .base import *

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "backend"]
CORS_ALLOWED_ORIGINS = ["http://localhost:5173"]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = ["http://localhost:5173"]
