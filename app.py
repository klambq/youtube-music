import io
import re
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

from downloader import (
    DownloadServiceError,
    PlaylistTooLargeError,
    download_audio,
    format_duration,
    inspect_media,
    validate_youtube_url,
)


MAX_PLAYLIST_ITEMS = 100
MAX_RESULT_BYTES = 512 * 1024 * 1024
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


st.set_page_config(
    page_title="YouTube Music do MP3",
    page_icon="🎧",
    layout="centered",
)


def safe_download_name(value: str, fallback: str = "youtube-mp3") -> str:
    """Return a portable filename without changing readable Unicode letters."""
    name = INVALID_FILENAME_CHARS.sub("_", value).strip(" .")
    return (name or fallback)[:120]


def build_result(files: list[Path], title: str, is_playlist: bool) -> dict[str, object]:
    total_size = sum(path.stat().st_size for path in files)
    if total_size > MAX_RESULT_BYTES:
        raise DownloadServiceError(
            "Gotowe pliki przekraczają limit 512 MB tej aplikacji. "
            "Spróbuj krótszej playlisty albo uruchom projekt lokalnie z wyższym limitem."
        )

    if len(files) == 1 and not is_playlist:
        file = files[0]
        return {
            "data": file.read_bytes(),
            "filename": file.name,
            "mime": "audio/mpeg",
            "count": 1,
            "size": file.stat().st_size,
        }

    archive = io.BytesIO()
    # MP3 is already compressed, so storing it is faster and barely changes ZIP size.
    with zipfile.ZipFile(archive, mode="w", compression=zipfile.ZIP_STORED) as bundle:
        for index, file in enumerate(files, start=1):
            bundle.write(file, arcname=f"{index:03d} - {file.name}")

    return {
        "data": archive.getvalue(),
        "filename": f"{safe_download_name(title)}.zip",
        "mime": "application/zip",
        "count": len(files),
        "size": archive.tell(),
    }


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def render_result() -> None:
    result = st.session_state.get("download_result")
    if not result:
        return

    st.success(
        f"Gotowe: {result['count']} "
        f"{'plik' if result['count'] == 1 else 'pliki'} · {human_size(result['size'])}"
    )
    st.download_button(
        "⬇️ Pobierz MP3" if result["mime"] == "audio/mpeg" else "⬇️ Pobierz ZIP z MP3",
        data=result["data"],
        file_name=result["filename"],
        mime=result["mime"],
        type="primary",
        use_container_width=True,
    )


st.title("🎧 YouTube Music → MP3")
st.write(
    "Wklej link do utworu albo playlisty z YouTube/YouTube Music. "
    "Pojedynczy utwór dostaniesz jako MP3, a playlistę jako ZIP."
)

with st.container(border=True):
    url = st.text_input(
        "Link do utworu lub playlisty",
        placeholder="https://music.youtube.com/watch?v=...",
    )
    quality = st.select_slider(
        "Jakość MP3",
        options=(128, 192, 256, 320),
        value=192,
        format_func=lambda value: f"{value} kb/s",
        help="Wyższy bitrate zwiększa rozmiar pliku",
    )
    submitted = st.button(
        "Pobierz i przekonwertuj",
        type="primary",
        use_container_width=True,
    )

st.caption(
    f"Obsługiwane: youtube.com, youtu.be i music.youtube.com · "
    f"maksymalnie {MAX_PLAYLIST_ITEMS} utworów w playliście."
)

if submitted:
    st.session_state.pop("download_result", None)
    candidate_url = url.strip()

    try:
        validate_youtube_url(candidate_url)
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

    status = st.status("Sprawdzam link…", expanded=True)
    progress = st.progress(0.0, text="Odczytywanie informacji z YouTube…")

    try:
        media = inspect_media(candidate_url, max_items=MAX_PLAYLIST_ITEMS)
        status.write(
            f"Znaleziono: **{media.title}** · "
            f"{media.item_count} {'utwór' if media.item_count == 1 else 'utworów'}"
        )
        progress.progress(0.01, text="Rozpoczynam pobieranie dostępnych utworów…")

        if media.items:
            preview = []
            for item in media.items[:5]:
                duration = format_duration(item.duration)
                preview.append(f"{item.title}{f' · {duration}' if duration else ''}")
            status.write("Podgląd: " + " • ".join(preview))

        def update_progress(value: float, message: str) -> None:
            progress.progress(max(0.0, min(value, 1.0)), text=message)

        with tempfile.TemporaryDirectory(prefix="youtube_mp3_") as temp_dir:
            files, warnings = download_audio(
                candidate_url,
                Path(temp_dir),
                quality_kbps=quality,
                total_items=media.item_count,
                progress_callback=update_progress,
            )
            st.session_state.download_result = build_result(
                files, media.title, media.is_playlist
            )

        progress.progress(1.0, text="Konwersja zakończona.")
        if warnings:
            status.write(
                f"Uwaga: pominięto lub zgłoszono problem dla części materiałów "
                f"({len(warnings)} komunikatów)."
            )
        status.update(label="MP3 jest gotowe", state="complete", expanded=False)
    except PlaylistTooLargeError as exc:
        status.update(label="Playlista jest za długa", state="error")
        st.error(str(exc))
    except DownloadServiceError as exc:
        status.update(label="Nie udało się pobrać materiału", state="error")
        st.error(str(exc))
    except Exception:
        status.update(label="Wystąpił nieoczekiwany błąd", state="error")
        st.error(
            "Nieoczekiwany błąd aplikacji. Sprawdź logi serwera i zaktualizuj yt-dlp."
        )

render_result()
