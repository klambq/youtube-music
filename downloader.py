from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import yt_dlp


ProgressCallback = Callable[[float, str], None]
ALLOWED_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}


class DownloadServiceError(RuntimeError):
    """A download error that is safe to display in the interface."""


class PlaylistTooLargeError(DownloadServiceError):
    """Raised before download when the playlist exceeds the app limit."""


@dataclass(frozen=True)
class TrackPreview:
    title: str
    duration: float | None = None


@dataclass(frozen=True)
class MediaInspection:
    title: str
    item_count: int
    is_playlist: bool
    items: tuple[TrackPreview, ...]


class QuietLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        if message:
            self.warnings.append(message)

    def error(self, message: str) -> None:
        if message:
            self.warnings.append(message)


def _javascript_runtimes() -> dict[str, dict[str, str]]:
    """Prefer the bundled Deno binary and fall back to a system Node runtime."""
    try:
        import deno

        return {"deno": {"path": str(deno.find_deno_bin())}}
    except (ImportError, OSError, RuntimeError):
        return {"node": {}}


def validate_youtube_url(url: str) -> str:
    if not url:
        raise ValueError("Wklej link do utworu albo playlisty.")

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or host not in ALLOWED_YOUTUBE_HOSTS:
        raise ValueError(
            "Podaj pełny link z youtube.com, youtu.be albo music.youtube.com."
        )
    return url


def _as_duration(value: Any) -> float | None:
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    return duration if duration >= 0 else None


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def inspect_media(url: str, max_items: int = 100) -> MediaInspection:
    validate_youtube_url(url)
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlistend": max_items + 1,
        "noplaylist": False,
        "js_runtimes": _javascript_runtimes(),
    }

    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadServiceError(_friendly_error(exc)) from exc

    if not info:
        raise DownloadServiceError("YouTube nie zwrócił informacji o tym materiale.")

    raw_entries = info.get("entries")
    is_playlist = raw_entries is not None
    if is_playlist:
        entries = [entry for entry in raw_entries if entry]
        reported_count = (
            _positive_int(info.get("playlist_count"))
            or _positive_int(info.get("n_entries"))
            or len(entries)
        )
        item_count = max(reported_count, len(entries))
        if item_count > max_items or len(entries) > max_items:
            raise PlaylistTooLargeError(
                f"Playlista ma {item_count} utworów, a limit aplikacji wynosi "
                f"{max_items}. Podziel playlistę na mniejsze części."
            )
        if not entries:
            raise DownloadServiceError(
                "Playlista jest pusta albo żaden z jej utworów nie jest dostępny."
            )
        items = tuple(
            TrackPreview(
                title=str(entry.get("title") or "Bez tytułu"),
                duration=_as_duration(entry.get("duration")),
            )
            for entry in entries
        )
        return MediaInspection(
            title=str(info.get("title") or "playlista-youtube"),
            item_count=len(entries),
            is_playlist=True,
            items=items,
        )

    item = TrackPreview(
        title=str(info.get("title") or "Bez tytułu"),
        duration=_as_duration(info.get("duration")),
    )
    return MediaInspection(
        title=item.title,
        item_count=1,
        is_playlist=False,
        items=(item,),
    )


def download_audio(
    url: str,
    output_dir: Path,
    *,
    quality_kbps: int = 192,
    total_items: int = 1,
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[Path], list[str]]:
    validate_youtube_url(url)
    if quality_kbps not in {128, 192, 256, 320}:
        raise ValueError("Nieobsługiwana jakość MP3.")

    output_dir.mkdir(parents=True, exist_ok=True)
    logger = QuietLogger()
    progress_callback = progress_callback or (lambda _value, _message: None)
    total_items = max(total_items, 1)

    def progress_hook(data: dict[str, Any]) -> None:
        info = data.get("info_dict") or {}
        title = str(info.get("title") or "utwór")
        index = _positive_int(info.get("playlist_index")) or 1
        index = min(index, total_items)
        status = data.get("status")

        if status == "downloading":
            downloaded = float(data.get("downloaded_bytes") or 0)
            expected = float(
                data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            )
            part = downloaded / expected if expected > 0 else 0.05
            overall = ((index - 1) + min(part, 1.0)) / total_items
            progress_callback(
                min(overall, 0.96),
                f"Pobieranie {index}/{total_items}: {title}",
            )
        elif status == "finished":
            progress_callback(
                min(index / total_items, 0.97),
                f"Konwersja do MP3 {index}/{total_items}: {title}",
            )

    def postprocessor_hook(data: dict[str, Any]) -> None:
        if data.get("status") != "started":
            return
        info = data.get("info_dict") or {}
        title = str(info.get("title") or "utwór")
        index = _positive_int(info.get("playlist_index")) or 1
        progress_callback(
            min(index / total_items, 0.98),
            f"Przetwarzanie {min(index, total_items)}/{total_items}: {title}",
        )

    options = {
        "format": "bestaudio/best",
        "paths": {"home": str(output_dir)},
        "outtmpl": {"default": "%(title).150B [%(id)s].%(ext)s"},
        "windowsfilenames": True,
        "noplaylist": False,
        "ignoreerrors": True,
        "quiet": True,
        "no_warnings": False,
        "logger": logger,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
        "concurrent_fragment_downloads": 4,
        "retries": 3,
        "fragment_retries": 3,
        "js_runtimes": _javascript_runtimes(),
        "final_ext": "mp3",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": str(quality_kbps),
            },
            {"key": "FFmpegMetadata", "add_metadata": True},
        ],
    }

    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            downloader.download([url])
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadServiceError(_friendly_error(exc)) from exc
    except OSError as exc:
        if "ffmpeg" in str(exc).lower():
            raise DownloadServiceError(
                "Nie znaleziono FFmpeg. Zainstaluj go i upewnij się, że jest w PATH."
            ) from exc
        raise

    files = sorted(output_dir.rglob("*.mp3"))
    if not files:
        details = "\n".join(logger.warnings[-20:]) or "brak dostępnego audio"
        raise DownloadServiceError(_friendly_error(details))

    progress_callback(1.0, "Gotowe.")
    return files, logger.warnings


def format_duration(duration: float | None) -> str:
    if duration is None:
        return ""
    total_seconds = max(0, round(duration))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _friendly_error(error: object) -> str:
    message = str(error)
    lowered = message.lower()
    if "ffmpeg" in lowered or "ffprobe" in lowered:
        return "Brakuje FFmpeg/FFprobe albo program nie jest dostępny w PATH."
    if "no supported javascript runtime" in lowered or "javascript runtime" in lowered:
        return (
            "Na serwerze brakuje zgodnego środowiska JavaScript potrzebnego przez YouTube. "
            "Sprawdź instalację Deno i ponownie uruchom aplikację."
        )
    if (
        "confirm you're not a bot" in lowered
        or "confirm you’re not a bot" in lowered
        or "http error 403" in lowered
        or "http error 429" in lowered
        or "too many requests" in lowered
    ):
        return (
            "YouTube zablokował połączenie z serwera Streamlit (ochrona przed botami "
            "lub limit zapytań). Ten sam link może działać lokalnie, ale publiczne adresy "
            "IP Streamlit Cloud bywają blokowane przez YouTube."
        )
    if "confirm your age" in lowered or "age-restricted" in lowered:
        return (
            "Dostępne pozycje tej playlisty wymagają zalogowania i potwierdzenia wieku. "
            "Ta aplikacja pobiera tylko materiały dostępne bez logowania."
        )
    if "sign in" in lowered or "login" in lowered or "cookies" in lowered:
        return (
            "Ten materiał wymaga logowania albo potwierdzenia przez YouTube. "
            "Ta wersja aplikacji obsługuje wyłącznie publicznie dostępne materiały."
        )
    if "private video" in lowered or "video is private" in lowered:
        return "Ten materiał jest prywatny i nie można go pobrać bez uprawnień."
    if "not available" in lowered or "unavailable" in lowered:
        return "Materiał jest niedostępny, usunięty albo zablokowany w tym regionie."
    return (
        "Nie udało się pobrać materiału. Sprawdź link, dostępność filmu i spróbuj "
        "ponownie"
    )
