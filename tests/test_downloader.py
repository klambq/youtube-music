import unittest

from downloader import (
    _youtube_compatibility_options,
    format_duration,
    validate_youtube_url,
)


class ValidateYoutubeUrlTests(unittest.TestCase):
    def test_accepts_youtube_music_track(self) -> None:
        url = "https://music.youtube.com/watch?v=abc123"
        self.assertEqual(validate_youtube_url(url), url)

    def test_accepts_playlist(self) -> None:
        url = "https://www.youtube.com/playlist?list=PL123"
        self.assertEqual(validate_youtube_url(url), url)

    def test_accepts_short_url(self) -> None:
        url = "https://youtu.be/abc123"
        self.assertEqual(validate_youtube_url(url), url)

    def test_rejects_spoofed_domain(self) -> None:
        with self.assertRaises(ValueError):
            validate_youtube_url("https://youtube.com.example.org/watch?v=abc")

    def test_rejects_non_http_scheme(self) -> None:
        with self.assertRaises(ValueError):
            validate_youtube_url("file://youtube.com/watch?v=abc")


class FormatDurationTests(unittest.TestCase):
    def test_minutes(self) -> None:
        self.assertEqual(format_duration(185), "3:05")

    def test_hours(self) -> None:
        self.assertEqual(format_duration(3723), "1:02:03")

    def test_unknown(self) -> None:
        self.assertEqual(format_duration(None), "")


class YoutubeCompatibilityTests(unittest.TestCase):
    def test_cloud_fallback_clients_are_enabled(self) -> None:
        options = _youtube_compatibility_options()
        clients = options["extractor_args"]["youtube"]["player_client"]

        self.assertEqual(options["source_address"], "0.0.0.0")
        self.assertIn("default", clients)
        self.assertIn("web_music", clients)
        self.assertIn("web_safari", clients)
        self.assertTrue(options["js_runtimes"])


if __name__ == "__main__":
    unittest.main()
