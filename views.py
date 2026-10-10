"""Ajans paneli gorunumleri (canli yayin odasi).

Bu modul 3 sey gosterir:
 1. `dashboard`      - ajansin ana ekrani: hangi bulten uretildi,
                        hangi spikerler var, kategoriler ne durumda
  2. `playout_detail` - tek bir bultenin detayi: VIDEO + RUNDOWN
                    (hangi haber, hangi spiker, hangi kategori,
                         hangi kaynak).
  3. `source_list`    - kaynak listesi (haberlerin nereden geldigini
                        gostermek; "kaynak gosterimi" istiyordu).
⚠️ 
    SABLON DEGISKENLERI KURALI (buradaki en onemli ders):
    `render()` cagrisindaki sozluk anahtarlari, sablonda kullanacagimiz
    ISIMLERDIR. Bu dosyada hepsi ASCII ve tek kelime:
    
 playouts, anchors, categories, latest, stats, playout, items

    NIYE ASCII? Cunku Turkce harf iceren bir sozluk anahtari calisir
    ama kafa karistirir. `render(request, "x.html", {"spiker": a})`
    yazip sablonda `{{ spikerler }}` ararsan BOS gelir ve saatlerce
    "sablon degiskenim neden bos?" diye debug edersin. Sozluk anahtari
    ile sablon ifadesi BIREBIR AYNI yazilmalidir; isim olarak da
    Turkce KISALTMA kullanma, okunur bir ASCII isim sec.

    (Ilk yazimda `{"stüdyo": ...}` gibi kisaltmalar kullanilmisti.
    Python 3'te Unicode normalize edilmis hali saklandigi icin
    sablonda ayni karakteri yazmak gerekiyordu. Simdi sorun yok.)
"""


from __future__ import annotations

from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, render

from news.models import Anchor, Article, Category, Playout, Source


def dashboard(request):
    """Ajans ana ekrani.

    Sorgu verimliligi notu:
      - `select_related` / `prefetch_related` kullanmazsak, 20 bulten
        x 16 kalem = 320 ayri SQL sorgusu cikar. Django "N+1"
        problemi denir. Asagida `prefetch_related` ile 2 sorguya
        indiriyoruz.
    """
    
    playouts = (
        Playout.objects.prefetch_related("items", "items__anchor")
        .order_by("-created_at")[:12]
    )

   
    anchors = Anchor.objects.filter(is_active=True).order_by("-is_main", "name")

   
    total_seconds = (
        Playout.objects.aggregate(total_seconds=Sum("total_seconds"))["total_seconds"]
        or 0
    )
    categories = Category.objects.annotate(
        article_total=Count("articles", distinct=True),
        used_total=Count(
            "articles",
            filter=Q(articles__is_active=True),
            distinct=True,
        ),
    ).order_by("sort_order", "name")

    latest = (
        Article.objects.select_related("source", "category")
        .order_by("-published_at")[:10]
    )

    stats = {
        "articles": Article.objects.count(),
        "sources": Source.objects.filter(is_active=True).count(),
        "playouts": Playout.objects.count(),
        "anchors": anchors.count(),
        "minutes": round(total_seconds / 60) if total_seconds else 0,
    }

    return render(
        request,
        "dashboard.html",
        {
            "playouts": playouts,
            "anchors": anchors,
            "categories": categories,
            "latest": latest,
            "stats": stats,
        },
    )


def playout_detail(request, pk: int):
    """Tek bir bulten: video + rundown.

    `pk` = primary key. URL'den gelir: /studio/playout/4/
    `get_object_or_404` = yoksa 404 dondurur (sunucu hata sayfasi)
    yerine. Elle yazarsan `Playout.objects.get(pk=pk)` "DoesNotExist"
    hatasi verir ve kullanici 500 sayfasi gorur.
    """
    playout = get_object_or_404(
        Playout.objects.prefetch_related("items", "items__anchor"),
        pk=pk,
    )

    return render(
        request,
        "playout_detail.html",
        {
            "playout": playout,
            "items": playout.items.all(),
            # Video hazir mi? Degilse oynatici yerine bilgi gosterelim.
            "has_video": bool(playout.manifest_path),
            "video_url": f"/media/{playout.manifest_path}" if playout.manifest_path else "",
        },
    )


def source_list(request):
    """Kaynak listesi: hangi siteden kac haber geldi."""
    sources = Source.objects.annotate(article_total=Count("articles")).order_by(
        "-article_total"
    )
    return render(request, "sources.html", {"sources": sources})