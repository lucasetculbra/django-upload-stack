"""URL configuration for the django-upload-stack project."""

from django.contrib import admin
from django.urls import include, path

from uploads.views import healthz

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", healthz, name="healthz"),
    path("", include("uploads.urls")),
]
