from pathlib import Path
from urllib.parse import quote
import mimetypes
import re

from django.conf import settings
from django.db import connection
from django.db.utils import DatabaseError
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.core.paginator import Paginator
from django.db.models.functions import Coalesce

from .models import Anchor, Article, Category, Playout, PlayoutItem, Source


def healthcheck(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


def media_file(request, path: str):
    media_root = Path(settings.MEDIA_ROOT).resolve()
    relative_path = Path(path)
    if (
        relative_path.is_absolute()
        or not relative_path.parts
        or relative_path.parts[0] not in {"arka_plan", "bultenler", "spikerler"}
        or relative_path.suffix.lower()
        not in {".jpg", ".jpeg", ".png", ".webp", ".mp3", ".mp4", ".srt"}
    ):
        raise Http404

    candidate = (media_root / relative_path).resolve()
    try:
        candidate.relative_to(media_root)
    except ValueError as exc:
        raise Http404 from exc
    if not candidate.is_file():
        raise Http404

    content_type, _ = mimetypes.guess_type(candidate.name)
    range_header = request.headers.get("Range")
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        file_size = candidate.stat().st_size
        if not match or (not match.group(1) and not match.group(2)):
            return HttpResponse(
                status=416,
                headers={"Content-Range": f"bytes */{file_size}", "Accept-Ranges": "bytes"},
            )

        if match.group(1):
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
        else:
            suffix_length = int(match.group(2))
            start = max(0, file_size - suffix_length)
            end = file_size - 1
        if start >= file_size or end < start:
            return HttpResponse(
                status=416,
                headers={"Content-Range": f"bytes */{file_size}", "Accept-Ranges": "bytes"},
            )
        end = min(end, file_size - 1)
        length = end - start + 1

        def stream_range():
            with candidate.open("rb") as media:
                media.seek(start)
                remaining = length
                while remaining:
                    chunk = media.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        response = StreamingHttpResponse(
            stream_range(),
            status=206,
            content_type=content_type or "application/octet-stream",
        )
        response["Accept-Ranges"] = "bytes"
        response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        response["Content-Length"] = str(length)
        return response

    response = FileResponse(
        candidate.open("rb"),
        content_type=content_type or "application/octet-stream",
        filename=candidate.name,
    )
    response["Accept-Ranges"] = "bytes"
    return response


def dashboard(request):
    duration = Playout.objects.aggregate(total=Sum("total_seconds"))["total"] or 0
    latest = (
        Article.objects.filter(is_active=True)
        .select_related("source", "category")
        .annotate(displayed_at=Coalesce("published_at", "ingested_at"))
        .order_by("-displayed_at", "-ingested_at", "-pk")
    )
    context = {
        "on_air": Playout.objects.filter(status=Playout.ON_AIR).first(),
        "stats": {
            "articles": Article.objects.count(),
            "sources": Source.objects.filter(is_active=True).count(),
            "playouts": Playout.objects.count(),
            "minutes": duration // 60,
            "anchors": Anchor.objects.count(),
        },
        "playouts": Playout.objects.all()[:10],
        "anchors": Anchor.objects.filter(is_active=True),
        "categories": Category.objects.filter(is_active=True)
        .annotate(
            article_total=Count(
                "articles",
                filter=Q(articles__is_active=True),
            )
        )
        .order_by("sort_order", "name"),
        "latest": latest[:10],
    }
    return render(request, "dashboard.html", context)


def live(request):
    return render(
        request,
        "live.html",
        {"on_air": Playout.objects.filter(status=Playout.ON_AIR).first()},
    )


def live_playlist(request):
    media_root = Path(settings.MEDIA_ROOT).resolve()
    items = []
    playouts = Playout.objects.filter(
        status=Playout.READY,
    ).exclude(manifest_path="").order_by("-created_at", "-pk")[:1]
    seen_story_titles = set()

    for playout in playouts:
        playout_dir = media_root / "bultenler" / f"playout_{playout.pk}"
        for item in playout.items.select_related("article", "anchor").order_by("order"):
            if item.article:
                if item.article.title_key in seen_story_titles:
                    continue
                seen_story_titles.add(item.article.title_key)
            generated_video = _media_url(media_root, playout_dir / "video" / f"{item.pk:04d}.mp4")
            audio_url = _media_url(media_root, media_root / item.audio_path) if item.audio_path else ""
            article_video_url = item.article.video_url if item.article else ""
            if not article_video_url.startswith(("https://", "http://")):
                article_video_url = ""

            if article_video_url and audio_url:
                video_url = article_video_url
                voiceover_url = audio_url
            else:
                video_url = generated_video
                voiceover_url = ""
            if not video_url:
                continue
            items.append(
                {
                    "id": f"{playout.pk}-{item.pk}",
                    "title": item.title_text
                    or (item.article.title if item.article else playout.title),
                    "video_url": video_url,
                    "fallback_video_url": generated_video if voiceover_url else "",
                    "voiceover_url": voiceover_url,
                    "speaker": item.anchor.name if item.anchor else "",
                    "source_video": bool(voiceover_url),
                }
            )
    return JsonResponse({"items": items})


def _media_url(media_root: Path, path: Path) -> str:
    candidate = path.resolve()
    try:
        relative_path = candidate.relative_to(media_root)
    except ValueError:
        return ""
    if not candidate.is_file() or candidate.suffix.lower() not in {
        ".jpg", ".jpeg", ".png", ".webp", ".mp3", ".mp4", ".srt"
    }:
        return ""
    url_path = "/".join(quote(part) for part in relative_path.parts)
    return f"{settings.MEDIA_URL.rstrip('/')}/{url_path}"


def playout_detail(request, pk: int):
    playout = get_object_or_404(Playout, pk=pk)
    items = playout.items.select_related("anchor", "article", "article__source")
    video_url = ""

    if playout.manifest_path:
        media_root = Path(settings.MEDIA_ROOT).resolve()
        candidate = (media_root / playout.manifest_path).resolve()
        try:
            relative_path = candidate.relative_to(media_root)
        except ValueError:
            relative_path = None
        if relative_path is not None and candidate.is_file():
            url_path = "/".join(quote(part) for part in relative_path.parts)
            video_url = f"{settings.MEDIA_URL.rstrip('/')}/{url_path}"

    return render(
        request,
        "playout_detail.html",
        {
            "on_air": Playout.objects.filter(status=Playout.ON_AIR).first(),
            "playout": playout,
            "items": items,
            "has_video": bool(video_url),
            "video_url": video_url,
        },
    )


def source_list(request):
    sources = Source.objects.annotate(article_total=Count("articles")).order_by("name")
    return render(
        request,
        "sources.html",
        {
            "on_air": Playout.objects.filter(status=Playout.ON_AIR).first(),
            "sources": sources,
        },
    )


def category_detail(request, slug: str):
    category = get_object_or_404(Category, slug=slug, is_active=True)
    articles = (
        Article.objects.filter(category=category, is_active=True)
        .select_related("source")
        .annotate(displayed_at=Coalesce("published_at", "ingested_at"))
        .order_by("-displayed_at", "-ingested_at", "-pk")
    )
    page_obj = Paginator(articles, 30).get_page(request.GET.get("page"))
    return render(
        request,
        "category_detail.html",
        {
            "on_air": Playout.objects.filter(status=Playout.ON_AIR).first(),
            "category": category,
            "categories": Category.objects.filter(is_active=True).order_by(
                "sort_order", "name"
            ),
            "page_obj": page_obj,
            "articles": page_obj.object_list,
        },
    )
