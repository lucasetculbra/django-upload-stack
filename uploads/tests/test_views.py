"""End-to-end tests for the upload flow: form, storage on disk and listing."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from uploads.models import UploadedFile


@pytest.fixture
def media_root(settings, tmp_path):
    """Point MEDIA_ROOT at a temp dir so tests never touch the real media volume."""
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.mark.django_db
def test_index_lists_nothing_when_empty(client):
    response = client.get(reverse("uploads:index"))

    assert response.status_code == 200
    assert "Nenhum arquivo enviado ainda." in response.content.decode()


@pytest.mark.django_db
def test_upload_creates_record_and_writes_file_to_media_root(client, media_root):
    upload = SimpleUploadedFile("relatorio.txt", b"conteudo de teste", content_type="text/plain")

    response = client.post(reverse("uploads:index"), {"file": upload})

    # Post/Redirect/Get: a successful upload redirects back to the listing.
    assert response.status_code == 302
    assert response["Location"] == reverse("uploads:index")

    record = UploadedFile.objects.get()
    assert record.original_name == "relatorio.txt"
    assert record.size == len(b"conteudo de teste")

    stored = media_root / record.file.name
    assert stored.exists(), f"file was not written to MEDIA_ROOT: {stored}"
    assert stored.read_bytes() == b"conteudo de teste"


@pytest.mark.django_db
def test_listing_shows_uploaded_file_with_media_link(client, media_root):
    client.post(
        reverse("uploads:index"),
        {"file": SimpleUploadedFile("planilha.csv", b"a,b,c", content_type="text/csv")},
    )

    body = client.get(reverse("uploads:index")).content.decode()
    record = UploadedFile.objects.get()

    assert "planilha.csv" in body
    assert record.file.url.startswith("/media/")
    assert f'href="{record.file.url}"' in body


@pytest.mark.django_db
def test_upload_without_file_is_rejected(client, media_root):
    response = client.post(reverse("uploads:index"), {})

    assert response.status_code == 200
    assert UploadedFile.objects.count() == 0
    assert response.context["form"].errors


@pytest.mark.django_db
def test_upload_larger_than_limit_is_rejected(client, media_root, settings):
    settings.MAX_UPLOAD_MB = 1
    settings.MAX_UPLOAD_BYTES = 1024 * 1024
    oversized = SimpleUploadedFile("grande.bin", b"x" * (1024 * 1024 + 1))

    response = client.post(reverse("uploads:index"), {"file": oversized})

    assert response.status_code == 200
    assert UploadedFile.objects.count() == 0
    assert "excede o limite" in response.content.decode()


@pytest.mark.django_db
def test_uploads_are_kept_in_subdirectories_by_date(client, media_root):
    client.post(
        reverse("uploads:index"),
        {"file": SimpleUploadedFile("nota.txt", b"ok")},
    )

    record = UploadedFile.objects.get()
    # upload_to="uploads/%Y/%m/" keeps the media volume tidy over time.
    assert record.file.name.startswith("uploads/")
    assert record.file.name.count("/") == 3
