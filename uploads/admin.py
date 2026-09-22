from django.contrib import admin

from .models import UploadedFile


@admin.register(UploadedFile)
class UploadedFileAdmin(admin.ModelAdmin):
    list_display = ("original_name", "size", "uploaded_at")
    search_fields = ("original_name",)
    readonly_fields = ("original_name", "size", "uploaded_at")
