from datetime import timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch

from news.models import Anchor, Article, Category, Playout, PlayoutItem, Source
from prompts import build_headline_transition, country_ablative
from script import build_bulletin


class PromptHelperTests(TestCase):
    def test_headline_transition_uses_selected_count(self):
        self.assertEqual(
            build_headline_transition(2),
            "Gündemin başında, iki önemli gelişme.",
        )
        self.assertEqual(
            build_headline_transition(11),
            "Gündemin başında, 11 önemli gelişme.",
        )

    def test_headline_transition_rejects_invalid_count(self):
        with self.assertRaises(ValueError):
            build_headline_transition(0)

    def test_country_ablative_uses_vowel_harmony(self):
        self.assertEqual(country_ablative("Almanya"), "Almanya'dan")
        self.assertEqual(country_ablative("Hindistan"), "Hindistan'dan")
        self.assertEqual(country_ablative("İsrail"), "İsrail'den")
        self.assertEqual(country_ablative("Güney Kore"), "Güney Kore'den")


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class DashboardViewTests(TestCase):
    def test_dashboard_and_sources_render(self):
        self.assertEqual(self.client.get(reverse("studio:dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("studio:sources")).status_code, 200)

    def test_live_page_and_playlist_endpoint_render_without_ready_videos(self):
        page = self.client.get(reverse("studio:live"))
        playlist = self.client.get(reverse("studio:live_playlist"))

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Kesintisiz Canlı Yayın")
        self.assertEqual(playlist.status_code, 200)
        self.assertEqual(playlist.json(), {"items": []})

    def test_live_playlist_uses_article_video_with_local_voiceover_when_available(self):
        with TemporaryDirectory() as temp_dir:
            media_root = Path(temp_dir)
            category = Category.objects.create(name="Gündem", slug="gundem")
            source = Source.objects.create(
                slug="live-video-source",
                name="Live Video Source",
                url="https://example.com/rss",
            )
            article = Article.objects.create(
                title="Video haberi",
                url="https://example.com/video-story",
                source=source,
                category=category,
                video_url="https://cdn.example.com/story.mp4",
            )
            playout = Playout.objects.create(
                title="Canlı akış bölümü",
                status=Playout.READY,
                manifest_path="bultenler/playout_1/bulten.mp4",
            )
            item = PlayoutItem.objects.create(
                playout=playout,
                order=1,
                article=article,
                text="Video haberi",
                audio_path="bultenler/playout_1/ses/0001.mp3",
                title_text=article.title,
            )
            playout_dir = media_root / "bultenler" / f"playout_{playout.pk}"
            for relative in (
                "bulten.mp4",
                f"video/{item.pk:04d}.mp4",
                "ses/0001.mp3",
            ):
                path = playout_dir / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"media")

            with override_settings(MEDIA_ROOT=media_root):
                response = self.client.get(reverse("studio:live_playlist"))

        self.assertEqual(response.status_code, 200)
        stream_item = response.json()["items"][0]
        self.assertEqual(stream_item["video_url"], "https://cdn.example.com/story.mp4")
        self.assertEqual(
            stream_item["voiceover_url"],
            "/media/bultenler/playout_1/ses/0001.mp3",
        )
        self.assertTrue(stream_item["source_video"])
        self.assertEqual(
            stream_item["fallback_video_url"],
            f"/media/bultenler/playout_{playout.pk}/video/{item.pk:04d}.mp4",
        )

    def test_dashboard_categories_link_to_filtered_pages_and_hides_template_comments(self):
        category = Category.objects.create(name="Gündem", slug="gundem")

        response = self.client.get(reverse("studio:dashboard"))

        self.assertContains(
            response,
            reverse("studio:category", kwargs={"slug": category.slug}),
        )
        self.assertNotContains(response, "{#")
        self.assertNotContains(response, "Python tarafinda: on_air")

    def test_category_page_only_shows_its_articles_in_newest_first_order(self):
        current = Category.objects.create(name="Gündem", slug="gundem")
        other = Category.objects.create(name="Ekonomi", slug="ekonomi")
        source = Source.objects.create(
            slug="category-test-source",
            name="Category Test",
            url="https://example.com/rss",
        )
        newest = timezone.now()
        expected = Article.objects.create(
            title="Yeni gündem haberi",
            url="https://example.com/new",
            source=source,
            category=current,
            published_at=newest,
        )
        older = Article.objects.create(
            title="Eski gündem haberi",
            url="https://example.com/old",
            source=source,
            category=current,
            published_at=newest - timedelta(hours=2),
        )
        Article.objects.create(
            title="Ekonomi haberi",
            url="https://example.com/economy",
            source=source,
            category=other,
            published_at=newest + timedelta(minutes=1),
        )

        response = self.client.get(
            reverse("studio:category", kwargs={"slug": current.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["articles"]),
            [expected, older],
        )

    def test_detail_does_not_expose_paths_outside_media_root(self):
        playout = Playout.objects.create(
            title="test",
            manifest_path="../../settings.py",
        )
        response = self.client.get(
            reverse("studio:playout_detail", kwargs={"pk": playout.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_video"])

    def test_media_route_rejects_private_file_paths(self):
        self.assertEqual(self.client.get("/media/logs/yayin.log").status_code, 404)
        self.assertEqual(self.client.get("/media/../../settings.py").status_code, 404)

    def test_media_route_streams_files_from_allowed_media_folder(self):
        with TemporaryDirectory() as temp_dir:
            media_file = Path(temp_dir) / "spikerler" / "sample.jpg"
            media_file.parent.mkdir()
            media_file.write_bytes(b"test-image")

            with override_settings(MEDIA_ROOT=Path(temp_dir)):
                response = self.client.get("/media/spikerler/sample.jpg")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "image/jpeg")
                self.assertEqual(b"".join(response.streaming_content), b"test-image")
                response.close()

    def test_media_route_supports_byte_ranges_for_video_playback(self):
        with TemporaryDirectory() as temp_dir:
            media_file = Path(temp_dir) / "bultenler" / "sample.mp4"
            media_file.parent.mkdir()
            media_file.write_bytes(b"0123456789")

            with override_settings(MEDIA_ROOT=Path(temp_dir)):
                response = self.client.get(
                    "/media/bultenler/sample.mp4",
                    HTTP_RANGE="bytes=2-5",
                )
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response["Content-Range"], "bytes 2-5/10")
                self.assertEqual(response["Accept-Ranges"], "bytes")
                self.assertEqual(b"".join(response.streaming_content), b"2345")


class BulletinCompositionTests(TestCase):
    def test_duration_display_handles_fractional_audio_durations(self):
        self.assertEqual(Playout(total_seconds=44.7).duration_display, "00:45")

    def _create_sample_story(self):
        category = Category.objects.create(name="Gundem", slug="gundem")
        source = Source.objects.create(
            slug="test-source",
            name="Test Kaynak",
            url="https://example.com/rss",
            default_category=category,
        )
        Anchor.objects.create(name="Test Spiker", is_main=True)
        Article.objects.create(
            title="Test haberi",
            summary="Kısa haber özeti.",
            url="https://example.com/news",
            source=source,
            category=category,
            is_breaking=True,
        )

    def test_build_bulletin_uses_existing_model_fields_and_dynamic_count(self):
        self._create_sample_story()

        playout = build_bulletin()
        items = list(playout.items.order_by("order"))

        self.assertEqual(playout.item_count, len(items))
        self.assertEqual(items[1].article.title, "Test haberi")
        self.assertIn("kesintisiz devam ediyor", items[-1].text)
        self.assertFalse(any(item.kind == PlayoutItem.CLOSING for item in items))
        self.assertTrue(any("Kaynak: Test Kaynak" in item.text for item in items))

    def test_bulletin_includes_every_unplayed_active_story_without_old_limits(self):
        category = Category.objects.create(name="Gundem", slug="gundem")
        source = Source.objects.create(
            slug="all-news-source",
            name="All News",
            url="https://example.com/rss",
            default_category=category,
        )
        Anchor.objects.create(name="Test Spiker", is_main=True)
        published = timezone.now()
        articles = [
            Article.objects.create(
                title=f"Farklı haber {index}",
                url=f"https://example.com/news-{index}",
                source=source,
                category=category,
                published_at=published - timedelta(minutes=index),
            )
            for index in range(8)
        ]
        Article.objects.create(
            title="  Farklı   haber 0 ",
            url="https://example.com/duplicate",
            source=source,
            category=category,
            published_at=published,
        )
        Article.objects.create(
            title="Pasif haber",
            url="https://example.com/inactive",
            source=source,
            category=category,
            is_active=False,
        )

        playout = build_bulletin()
        story_items = list(
            playout.items.filter(kind__in=(PlayoutItem.STORY, PlayoutItem.BREAKING))
        )

        self.assertEqual(len(story_items), len(articles))
        self.assertEqual(
            {item.article.title_key for item in story_items},
            {article.title_key for article in articles},
        )
        self.assertEqual(playout.item_count, len(articles) + 2)

    def test_bulletin_assigns_a_country_specific_anchor_to_each_story(self):
        category = Category.objects.create(name="Dunya", slug="dunya")
        source = Source.objects.create(
            slug="country-speaker-source",
            name="Country Speaker",
            url="https://example.com/rss",
            default_category=category,
        )
        main = Anchor.objects.create(name="Ana Spiker", country_code="TR", is_main=True)
        article = Article.objects.create(
            title="United States news",
            url="https://example.com/us-news",
            source=source,
            category=category,
            country_code="US",
        )

        playout = build_bulletin()
        story = playout.items.get(article=article)
        story.anchor.refresh_from_db()

        self.assertNotEqual(story.anchor_id, main.pk)
        self.assertEqual(story.anchor.country_code, "US")
        self.assertEqual(story.anchor.name, "Amerika Spikeri")
        self.assertFalse(story.anchor.is_main)

    def test_dry_run_does_not_persist_new_bulletin(self):
        self._create_sample_story()
        call_command(
            "make_bulletin",
            "--dry-run",
            "--no-country-tour",
            stdout=StringIO(),
        )
        self.assertEqual(Playout.objects.count(), 0)

    def test_dry_run_reports_missing_main_anchor_cleanly(self):
        with self.assertRaisesMessage(CommandError, "Ana spiker yok"):
            call_command(
                "make_bulletin",
                "--dry-run",
                "--no-country-tour",
                stdout=StringIO(),
            )
        self.assertEqual(Playout.objects.count(), 0)


class GoogleNewsIngestionTests(TestCase):
    def test_rss_video_extraction_uses_direct_video_enclosures_only(self):
        from types import SimpleNamespace

        from services import _extract_video

        entry = SimpleNamespace(
            media_content=[
                {"url": "https://example.com/thumbnail.jpg", "medium": "image"},
                {"url": "https://cdn.example.com/report.mp4", "medium": "video"},
            ],
            enclosures=[],
            links=[],
        )

        self.assertEqual(
            _extract_video(entry),
            "https://cdn.example.com/report.mp4",
        )
        self.assertEqual(_extract_video(SimpleNamespace()), "")

    def test_article_page_video_parser_reads_public_video_metadata_and_rejects_local_hosts(self):
        from services import _is_public_http_url, _parse_article_video_metadata

        page = """
        <html><head>
          <meta property="og:video" content="/assets/report.mp4">
          <script type="application/ld+json">
            {"@type":"VideoObject","contentUrl":"https://cdn.example.com/story.webm"}
          </script>
        </head></html>
        """

        self.assertEqual(
            _parse_article_video_metadata(page, "https://news.example.com/story"),
            "https://news.example.com/assets/report.mp4",
        )
        self.assertFalse(_is_public_http_url("http://127.0.0.1/private.mp4"))
        self.assertFalse(_is_public_http_url("http://192.168.1.20/private.mp4"))

    def test_google_news_sources_are_stable_and_not_deleted_after_ingestion(self):
        from types import SimpleNamespace

        from services import _ingest_google_news

        with patch(
            "services.ingest_source",
            side_effect=lambda source, **kwargs: SimpleNamespace(
                source_id=source.pk,
                status="ok",
            ),
        ):
            first_run = _ingest_google_news(
                "GN-TR",
                "https://news.google.com/rss/search?q=Turkey",
                "TR",
                "Gundem",
                False,
                12,
            )
            source = Source.objects.get(slug="google-news-query-gn-tr")
            second_run = _ingest_google_news(
                "GN-TR",
                "https://news.google.com/rss/search?q=Turkey",
                "TR",
                "Gundem",
                False,
                12,
            )

        self.assertEqual(first_run.source_id, source.pk)
        self.assertEqual(second_run.source_id, source.pk)
        self.assertEqual(Source.objects.filter(slug="google-news-query-gn-tr").count(), 1)

    def test_duplicate_feed_article_is_updated_with_newly_available_video(self):
        from services import ingest_source

        category = Category.objects.create(name="Gündem", slug="gundem")
        source = Source.objects.create(
            slug="backfill-video-source",
            name="Backfill Video Source",
            url="https://example.com/rss",
            default_category=category,
        )
        Article.objects.create(
            title="Already ingested",
            url="https://example.com/already-ingested",
            source=source,
            category=category,
        )
        entry = {
            "title": "Already ingested",
            "url": "https://example.com/already-ingested",
            "summary": "",
            "published": timezone.now(),
            "image": "",
            "video": "https://cdn.example.com/already-ingested.mp4",
        }

        with patch("services.fetch_feed", return_value=[entry]):
            run = ingest_source(source)

        self.assertEqual(run.new_count, 0)
        self.assertEqual(
            Article.objects.get(url=entry["url"]).video_url,
            entry["video"],
        )

    def test_new_article_uses_public_page_video_when_rss_has_no_video(self):
        from services import ingest_source

        category = Category.objects.create(name="Gündem", slug="gundem")
        source = Source.objects.create(
            slug="page-video-source",
            name="Page Video Source",
            url="https://example.com/tr/rss",
            default_category=category,
        )
        entry = {
            "title": "Yeni video haberi",
            "url": "https://example.com/news/video",
            "summary": "",
            "published": timezone.now(),
            "image": "",
            "video": "",
        }

        with (
            patch("services.fetch_feed", return_value=[entry]),
            patch(
                "services.extract_article_video_url",
                return_value="https://cdn.example.com/report.mp4",
            ),
        ):
            run = ingest_source(source)

        article = Article.objects.get(url=entry["url"])
        self.assertEqual(run.new_count, 1)
        self.assertEqual(article.video_url, "https://cdn.example.com/report.mp4")
        self.assertIsNotNone(article.video_checked_at)


class ContinuousStudioTests(TestCase):
    def test_unplayed_archive_is_built_even_when_no_article_is_recent(self):
        from run_studio import Command

        category = Category.objects.create(name="Gündem", slug="gundem")
        source = Source.objects.create(
            slug="studio-old-news-source",
            name="Studio Old News",
            url="https://example.com/rss",
            default_category=category,
        )
        Article.objects.create(
            title="Arşivdeki haber",
            url="https://example.com/archive-story",
            source=source,
            category=category,
            published_at=timezone.now() - timedelta(days=5),
        )
        command = Command()

        def fake_command(name, *args, **kwargs):
            if name == "make_bulletin":
                Playout.objects.create(status=Playout.READY, item_count=1)

        with patch("run_studio.call_command", side_effect=fake_command) as call:
            ok, _ = command._cycle(
                {"max_age": 60, "headlines": 5, "build_videos": False}
            )

        self.assertTrue(ok)
        self.assertTrue(
            any(entry.args[0] == "make_bulletin" for entry in call.call_args_list)
        )


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class MediaRetentionTests(TestCase):
    def test_cleanup_removes_old_media_but_preserves_bulletin_record(self):
        with TemporaryDirectory() as temp_dir:
            media_dir = Path(temp_dir)
            old_playout = Playout.objects.create(
                title="Eski bülten",
                status=Playout.READY,
            )
            old_playout.manifest_path = (
                f"bultenler/playout_{old_playout.pk}/bulten.mp4"
            )
            old_playout.save(update_fields=["manifest_path"])
            Playout.objects.filter(pk=old_playout.pk).update(
                created_at=timezone.now() - timedelta(days=31)
            )
            item = old_playout.items.create(
                order=1,
                text="Örnek haber",
                audio_path=f"bultenler/playout_{old_playout.pk}/ses/0001.mp3",
                subtitle_path=f"bultenler/playout_{old_playout.pk}/ses/0001.srt",
            )
            bulletin_dir = media_dir / "bultenler" / f"playout_{old_playout.pk}"
            bulletin_dir.mkdir(parents=True)
            (bulletin_dir / "bulten.mp4").write_bytes(b"video")

            with override_settings(MEDIA_ROOT=media_dir):
                call_command("cleanup_media", "--days=30", stdout=StringIO())

            old_playout.refresh_from_db()
            item.refresh_from_db()
            self.assertTrue(Playout.objects.filter(pk=old_playout.pk).exists())
            self.assertFalse(bulletin_dir.exists())
            self.assertEqual(old_playout.manifest_path, "")
            self.assertEqual(item.audio_path, "")
            self.assertEqual(item.subtitle_path, "")

    def test_cleanup_preserves_bulletins_that_are_building_or_on_air(self):
        with TemporaryDirectory() as temp_dir:
            media_dir = Path(temp_dir)
            protected_dirs = []
            for status in (Playout.BUILDING, Playout.ON_AIR):
                playout = Playout.objects.create(
                    title=f"Korunan bülten {status}",
                    status=status,
                )
                Playout.objects.filter(pk=playout.pk).update(
                    created_at=timezone.now() - timedelta(days=31)
                )
                bulletin_dir = media_dir / "bultenler" / f"playout_{playout.pk}"
                bulletin_dir.mkdir(parents=True)
                (bulletin_dir / "bulten.mp4").write_bytes(b"video")
                protected_dirs.append(bulletin_dir)

            with override_settings(MEDIA_ROOT=media_dir):
                call_command("cleanup_media", "--days=30", stdout=StringIO())

            for bulletin_dir in protected_dirs:
                self.assertTrue(bulletin_dir.exists())

    def test_cleanup_rejects_bulletin_root_symlink(self):
        with TemporaryDirectory() as temp_dir:
            media_dir = Path(temp_dir) / "media"
            outside_dir = Path(temp_dir) / "outside"
            media_dir.mkdir()
            outside_dir.mkdir()
            try:
                (media_dir / "bultenler").symlink_to(
                    outside_dir, target_is_directory=True
                )
            except OSError as exc:
                self.skipTest(f"Directory symlinks are unavailable: {exc}")

            with override_settings(MEDIA_ROOT=media_dir):
                with self.assertRaisesMessage(CommandError, "sembolik bağlantı"):
                    call_command("cleanup_media", stdout=StringIO())
