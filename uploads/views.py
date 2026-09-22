from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from .forms import UploadForm
from .models import UploadedFile


def index(request):
    """List uploaded files and accept new uploads (Post/Redirect/Get)."""
    if request.method == "POST":
        form = UploadForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect("uploads:index")
    else:
        form = UploadForm()

    return render(
        request,
        "uploads/index.html",
        {"form": form, "files": UploadedFile.objects.all()},
    )


@require_GET
def healthz(request):
    """Liveness/readiness probe used by the compose healthcheck and the smoke test."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # pragma: no cover - only hit when the DB is down
        return JsonResponse(
            {"status": "error", "database": "error", "detail": str(exc)}, status=503
        )

    return JsonResponse({"status": "ok", "database": "ok", "vendor": connection.vendor})
