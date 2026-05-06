"""URLs principais."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from accounts.sync_views import SyncView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/", include("accounts.urls")),
    path("api/", include("workouts.urls")),
    path("api/", include("training_sessions.urls")),
    # Sync all-in-one (cross-app: traz user + workouts + exercises)
    path("api/sync/", SyncView.as_view(), name="sync"),
]

# Em DEBUG, o Django serve os arquivos de MEDIA_ROOT.
# Em produção, esse roteamento NÃO é usado — quem serve é o nginx
# (configurado na fase 5).
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
