from django.contrib import admin

from .models import Anchor, Article, Category, Playout, PlayoutItem, Source
from ingest.models import IngestRun, Translation


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "sort_order", "is_active", "in_playout")
    list_filter = ("is_active", "in_playout")
    search_fields = ("name", "slug")


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("name", "country_code", "kind", "is_active", "trust_score")
    list_filter = ("is_active", "kind", "country_code")
    search_fields = ("name", "slug", "url")


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ("title", "source", "category", "country_code", "is_active", "is_breaking")
    list_filter = ("is_active", "is_breaking", "country_code", "category")
    search_fields = ("title", "summary", "source__name")
    readonly_fields = ("created_at", "ingested_at")


@admin.register(Anchor)
class AnchorAdmin(admin.ModelAdmin):
    list_display = ("name", "country_code", "voice", "is_main", "is_active")
    list_filter = ("is_main", "is_active", "country_code")
    search_fields = ("name", "country_code")


class PlayoutItemInline(admin.TabularInline):
    model = PlayoutItem
    extra = 0
    readonly_fields = ("word_count",)


@admin.register(Playout)
class PlayoutAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "item_count", "total_seconds", "created_at")
    list_filter = ("status",)
    search_fields = ("title",)
    inlines = (PlayoutItemInline,)
    readonly_fields = ("created_at", "built_at")


@admin.register(IngestRun)
class IngestRunAdmin(admin.ModelAdmin):
    list_display = ("source", "status", "started_at", "new_count", "duration_ms")
    list_filter = ("status",)
    readonly_fields = ("started_at", "finished_at")


@admin.register(Translation)
class TranslationAdmin(admin.ModelAdmin):
    list_display = ("source_lang", "target_lang", "quality", "created_at")
    search_fields = ("source_text", "target_text", "source_hash")
    readonly_fields = ("created_at",)


__all__ = [
    "AnchorAdmin",
    "ArticleAdmin",
    "CategoryAdmin",
    "PlayoutAdmin",
    "SourceAdmin",
]
