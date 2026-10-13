from django.urls import path

from . import views

app_name = "studio"

urlpatterns = [
    path("health/", views.healthcheck, name="health"),
    path("media/<path:path>", views.media_file, name="media"),
    path("studi/haber-cek/", views.start_fetch, name="start_fetch"),
    path("studi/bulten-uret/", views.start_build, name="start_build"),
    path("canli/", views.live, name="live"),
    path("canli/playlist/", views.live_playlist, name="live_playlist"),
    path("spiker/<int:pk>/", views.anchor_detail, name="anchor_detail"),
    path("", views.dashboard, name="dashboard"),
    path("playout/<int:pk>/", views.playout_detail, name="playout_detail"),
    path("kaynaklar/", views.source_list, name="sources"),
    path("kategori/<slug:slug>/", views.category_detail, name="category"),
]
