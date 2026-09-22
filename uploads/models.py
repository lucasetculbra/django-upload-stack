from django.db import models


class UploadedFile(models.Model):
    """A single file uploaded through the web form.

    The file itself is written to MEDIA_ROOT, which the stack mounts as a named Docker
    volume so uploads survive `docker compose down` followed by `up`.
    """

    file = models.FileField(upload_to="uploads/%Y/%m/", verbose_name="arquivo")
    original_name = models.CharField(max_length=255, verbose_name="nome original")
    size = models.PositiveBigIntegerField(default=0, verbose_name="tamanho (bytes)")
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="enviado em")

    class Meta:
        ordering = ["-uploaded_at"]
        verbose_name = "arquivo enviado"
        verbose_name_plural = "arquivos enviados"

    def __str__(self) -> str:
        return self.original_name

    def save(self, *args, **kwargs):
        # Capture the browser-provided name and size before the storage backend renames
        # the file (Django appends a random suffix when the name already exists).
        if self.file and not self.original_name:
            self.original_name = self.file.name
        if self.file and not self.size:
            self.size = self.file.size
        super().save(*args, **kwargs)

    @property
    def human_size(self) -> str:
        size = float(self.size)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"
