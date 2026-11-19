"""인스타그램 원본 화질 다운로더 (로컬 실행용)

- 릴스 / 게시물(사진·영상·여러 장) / IGTV 지원
- 영상: yt-dlp로 가장 높은 화질의 영상+음성을 받아 mp4로 합침 (재인코딩 없음 → 원본 화질 유지)
- 사진: instaloader로 원본 해상도(display_url) 그대로 저장
- 여러 장이면 zip 하나로 묶어서 내려줌

실행: python app.py  →  브라우저에서 http://127.0.0.1:5000
"""
from __future__ import annotations

import io
import os
import re
import shutil
import tempfile
import threading
import webbrowser
import zipfile
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
COOKIES_FILE = BASE_DIR / "cookies.txt"  # (선택) 비공개/로그인 필요 게시물용
SAVE_DIR = BASE_DIR / "downloads"         # 서버 쪽에도 사본을 남김

URL_RE = re.compile(
    r"https?://(?:www\.|m\.)?instagram\.com/(?:[\w.]+/)?(p|reel|reels|tv)/([\w-]+)",
    re.I,
)

app = Flask(__name__, static_folder=None)


class DownloadError(Exception):
    pass


def parse_url(url: str) -> tuple[str, str]:
    m = URL_RE.search(url or "")
    if not m:
        raise DownloadError("인스타그램 게시물/릴스 주소가 아닙니다. (예: https://www.instagram.com/reel/XXXX/)")
    kind = m.group(1).lower()
    return ("reel" if kind == "reels" else kind), m.group(2)


def ffmpeg_path() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


# ---------------------------------------------------------------- yt-dlp (영상)
def fetch_with_ytdlp(url: str, outdir: Path) -> list[Path]:
    import yt_dlp

    ff = ffmpeg_path()
    opts = {
        "outtmpl": str(outdir / "%(id)s_%(playlist_index|1)s.%(ext)s"),
        # 최고 화질 영상 + 최고 음질 → 합치기. ffmpeg 없으면 단일 파일 중 최고.
        "format": "bv*+ba/b" if ff else "b",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
    }
    if ff:
        opts["ffmpeg_location"] = ff
    if COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    return sorted(p for p in outdir.iterdir() if p.is_file() and not p.name.endswith(".part"))


# ------------------------------------------------------------- instaloader (사진)
def fetch_with_instaloader(shortcode: str, outdir: Path) -> list[Path]:
    import instaloader

    L = instaloader.Instaloader(
        dirname_pattern=str(outdir),
        filename_pattern="{shortcode}",
        download_videos=True,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        post_metadata_txt_pattern="",
        quiet=True,
    )
    if COOKIES_FILE.exists():
        _load_cookies_into(L, COOKIES_FILE)

    post = instaloader.Post.from_shortcode(L.context, shortcode)
    L.download_post(post, target=str(outdir))
    exts = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".mp4"}
    return sorted(p for p in outdir.iterdir() if p.suffix.lower() in exts)


def _load_cookies_into(L, path: Path) -> None:
    """Netscape cookies.txt → instaloader 세션"""
    from http.cookiejar import MozillaCookieJar

    jar = MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    for c in jar:
        if "instagram.com" in c.domain:
            L.context._session.cookies.set(c.name, c.value, domain=c.domain, path=c.path)


# ---------------------------------------------------------------------- 공통
def fetch(url: str) -> tuple[list[Path], Path]:
    kind, shortcode = parse_url(url)
    clean_url = f"https://www.instagram.com/{kind}/{shortcode}/"
    workdir = Path(tempfile.mkdtemp(prefix="igdl_"))
    errors = []

    # 게시물(/p/)은 사진·혼합 캐러셀일 수 있으므로 instaloader 먼저
    order = ["instaloader", "ytdlp"] if kind == "p" else ["ytdlp", "instaloader"]
    for method in order:
        sub = workdir / method
        sub.mkdir()
        try:
            files = (
                fetch_with_ytdlp(clean_url, sub)
                if method == "ytdlp"
                else fetch_with_instaloader(shortcode, sub)
            )
            if files:
                return files, workdir
        except Exception as e:  # noqa: BLE001
            errors.append(f"{method}: {e}")

    shutil.rmtree(workdir, ignore_errors=True)
    msg = " / ".join(errors) or "받을 수 있는 미디어가 없습니다."
    if re.search(r"login|401|403|private|rate|Please wait", msg, re.I):
        msg = ("인스타그램이 로그인을 요구하거나 요청을 막았습니다. 비공개 계정이거나 요청이 너무 많을 수 있어요. "
               "README의 'cookies.txt' 설정을 해 주세요.\n\n상세: " + msg)
    raise DownloadError(msg)


@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/api/download")
def api_download():
    url = request.args.get("url", "").strip()
    try:
        files, workdir = fetch(url)
    except DownloadError as e:
        return jsonify(error=str(e)), 400

    try:
        SAVE_DIR.mkdir(exist_ok=True)
        _, shortcode = parse_url(url)
        if len(files) == 1:
            f = files[0]
            name = f"instagram_{shortcode}{f.suffix.lower()}"
            shutil.copy2(f, SAVE_DIR / name)
            data = io.BytesIO(f.read_bytes())
        else:
            name = f"instagram_{shortcode}.zip"
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w", zipfile.ZIP_STORED) as z:  # 무압축 = 원본 그대로
                for i, f in enumerate(files, 1):
                    member = f"instagram_{shortcode}_{i:02d}{f.suffix.lower()}"
                    z.write(f, member)
                    shutil.copy2(f, SAVE_DIR / member)
            data.seek(0)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    resp = send_file(data, as_attachment=True, download_name=name)
    resp.headers["X-File-Count"] = str(len(files))
    resp.headers["Access-Control-Expose-Headers"] = "Content-Disposition, X-File-Count"
    return resp


def main():
    port = int(os.environ.get("PORT", 5000))
    url = f"http://127.0.0.1:{port}"
    print(f"\n  인스타 다운로더 실행 중 → {url}\n  (종료: Ctrl+C)\n")
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, threaded=True)


if __name__ == "__main__":
    main()
