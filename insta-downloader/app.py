"""원본 화질 다운로더

- 인스타(릴스·게시물·사진 여러 장), 틱톡, 유튜브, 페이스북, X 등 yt-dlp 지원 사이트 전부
- 영상: yt-dlp로 가장 높은 화질의 영상+음성을 받아 mp4로 합침 (재인코딩 없음 → 원본 화질 유지)
- 사진: instaloader로 원본 해상도(display_url) 그대로 저장
- 여러 장이면 zip 하나로 묶어서 내려줌

- 휴대폰용: 클라우드(Render 등)에 올려서 휴대폰 브라우저로 접속. 안드로이드는 '공유' 메뉴로 바로 다운로드.

로컬 실행: python app.py  →  브라우저에서 http://127.0.0.1:5000
환경변수 (클라우드용, 모두 선택)
  IG_SESSIONID 인스타 로그인 쿠키 중 sessionid 값 하나 (가장 간단. 무음/로그인 요구 해결)
  IG_COOKIES   cookies.txt 내용 전체 (IG_SESSIONID 대신 써도 됨)
  ACCESS_KEY   접속 비밀번호 (남이 내 서버/계정을 쓰지 못하게)
"""
from __future__ import annotations

import mimetypes
import os
import re
import shutil
import tempfile
import threading
import webbrowser
import zipfile
from pathlib import Path
from urllib.parse import quote

from flask import Flask, Response, jsonify, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
COOKIES_FILE = BASE_DIR / "cookies.txt"  # (선택) 비공개/로그인 필요 게시물용
ACCESS_KEY = os.environ.get("ACCESS_KEY", "").strip()

if os.environ.get("IG_COOKIES", "").strip():
    # 클라우드에서는 파일 대신 환경변수로 쿠키를 받음
    COOKIES_FILE = Path(tempfile.gettempdir()) / "ig_cookies.txt"
    COOKIES_FILE.write_text(os.environ["IG_COOKIES"].strip() + "\n", encoding="utf-8")
elif os.environ.get("IG_SESSIONID", "").strip():
    # sessionid 값 하나만으로 cookies.txt를 만들어 줌
    _sid = os.environ["IG_SESSIONID"].strip().strip('"').removeprefix("sessionid=")
    COOKIES_FILE = Path(tempfile.gettempdir()) / "ig_cookies.txt"
    COOKIES_FILE.write_text(
        "# Netscape HTTP Cookie File\n"
        f".instagram.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\t{_sid}\n",
        encoding="utf-8",
    )
SAVE_DIR = BASE_DIR / "downloads"         # 서버 쪽에도 사본을 남김

IG_RE = re.compile(
    r"https?://(?:www\.|m\.)?instagram\.com/(?:[\w.]+/)?(p|reel|reels|tv)/([\w-]+)",
    re.I,
)
ANY_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)

# 주소 → 파일 이름 앞부분
SITE_NAMES = {
    "youtube.com": "youtube", "youtu.be": "youtube", "tiktok.com": "tiktok",
    "facebook.com": "facebook", "fb.watch": "facebook", "fb.com": "facebook",
    "twitter.com": "x", "x.com": "x", "threads.net": "threads", "threads.com": "threads",
    "pinterest.com": "pinterest", "pin.it": "pinterest", "vimeo.com": "vimeo",
    "reddit.com": "reddit", "naver.com": "naver", "kakao.com": "kakao",
    "dailymotion.com": "dailymotion", "twitch.tv": "twitch", "bilibili.com": "bilibili",
    "douyin.com": "douyin", "xiaohongshu.com": "xiaohongshu", "soundcloud.com": "soundcloud",
}

app = Flask(__name__, static_folder=None)

# 첫 요청이 빠르도록 무거운 라이브러리를 서버 시작 때 미리 불러 둠
try:
    import instaloader  # noqa: F401
    import yt_dlp  # noqa: F401
except Exception:  # noqa: BLE001
    pass


def _keep_awake():
    """Render 무료 서버는 15분간 접속이 없으면 잠듦(깨우는 데 30~60초) → 10분마다 스스로 접속해 깨어 있게 함"""
    base = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not base:
        return
    import time
    import urllib.request

    while True:
        time.sleep(600)
        try:
            urllib.request.urlopen(base + "/api/config", timeout=30).read()
        except Exception:  # noqa: BLE001
            pass


threading.Thread(target=_keep_awake, daemon=True).start()


class DownloadError(Exception):
    pass


def parse_url(url: str) -> tuple[str, str]:
    """인스타 주소면 (kind, shortcode), 아니면 DownloadError"""
    m = IG_RE.search(url or "")
    if not m:
        raise DownloadError("인스타그램 주소가 아닙니다.")
    kind = m.group(1).lower()
    return ("reel" if kind == "reels" else kind), m.group(2)


def site_name(url: str) -> str:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    for dom, name in SITE_NAMES.items():
        if host == dom or host.endswith("." + dom):
            return name
    parts = host.split(".")
    return parts[-2] if len(parts) >= 2 else "video"


def safe(s: str) -> str:
    return re.sub(r"[^\w-]+", "_", s or "").strip("_")[:60] or "video"


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
def fetch_with_ytdlp(url: str, outdir: Path, allow_playlist: bool = True) -> list[Path]:
    import yt_dlp

    ff = ffmpeg_path()
    opts = {
        "outtmpl": str(outdir / "%(id)s_%(playlist_index|1)s.%(ext)s"),
        # 최고 화질 영상 + 최고 음질 → 합치기(재인코딩 없음). ffmpeg 없으면 단일 파일 중 최고.
        "format": "bv*+ba/b" if ff else "b",
        # 해상도가 가장 높은 것 우선. 같은 해상도면 H.264/AAC(휴대폰·캡컷 호환) 우선.
        "format_sort": ["res", "fps", "vcodec:h264", "acodec:aac"],
        "merge_output_format": "mp4",
        "noplaylist": not allow_playlist,  # 유튜브 재생목록 통째로 받는 것 방지
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "socket_timeout": 20,
        "concurrent_fragment_downloads": 8,  # 조각으로 나뉜 영상은 8개씩 동시에 받기
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
    sess = L.context._session
    for c in jar:
        if "instagram.com" in c.domain:
            # instaloader가 미리 넣어 둔 빈 쿠키(sessionid="" 등)와 겹치지 않게 먼저 지움
            for old in [o for o in sess.cookies if o.name == c.name]:
                sess.cookies.clear(old.domain, old.path, old.name)
            sess.cookies.set(c.name, c.value, domain=c.domain, path=c.path)
    if sess.cookies.get("sessionid"):
        L.context.username = "cookie-login"  # instaloader가 로그인 상태로 동작하게


# ---------------------------------------------------------------------- 공통
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


def has_audio(path: Path) -> bool:
    """영상 파일에 소리 트랙이 있는지 ffmpeg로 확인 (확인 불가면 True로 간주)"""
    if path.suffix.lower() not in VIDEO_EXTS:
        return True
    ff = ffmpeg_path()
    if not ff:
        return True
    import subprocess

    try:
        out = subprocess.run([ff, "-hide_banner", "-i", str(path)],
                             capture_output=True, text=True, timeout=30).stderr
    except Exception:
        return True
    return "Audio:" in out


def silent_files(files: list[Path]) -> list[Path]:
    return [f for f in files if not has_audio(f)]


SILENT_WARNING = (
    "⚠️ 받은 영상에 소리가 없어요. 인스타가 로그인 안 된 요청에는 소리를 빼고 주는 경우가 많습니다. "
    "Render 설정에 IG_SESSIONID(로그인 쿠키)를 넣으면 해결돼요. "
    "(원본 음악이 저작권 때문에 지역에서 막힌 경우일 수도 있어요)"
)


def fetch(url: str) -> tuple[list[Path], Path, str, str]:
    """반환: (파일들, 임시폴더, 파일이름 앞부분, 경고문)"""
    m = ANY_URL_RE.search(url or "")
    if not m:
        raise DownloadError("링크(https://...)를 넣어 주세요.")
    url = m.group(0).rstrip(".,)")
    workdir = Path(tempfile.mkdtemp(prefix="dl_"))
    errors = []
    silent_result: list[Path] | None = None

    if IG_RE.search(url):
        kind, shortcode = parse_url(url)
        clean_url = f"https://www.instagram.com/{kind}/{shortcode}/"
        prefix = f"instagram_{shortcode}"
        # 게시물(/p/)은 사진·혼합 캐러셀일 수 있으므로 instaloader 먼저
        order = ["instaloader", "ytdlp"] if kind == "p" else ["ytdlp", "instaloader"]
    else:
        clean_url, shortcode, order = url, "", ["ytdlp"]
        prefix = site_name(url)

    for method in order:
        sub = workdir / method
        sub.mkdir()
        try:
            files = (
                fetch_with_ytdlp(clean_url, sub, allow_playlist=bool(shortcode))
                if method == "ytdlp"
                else fetch_with_instaloader(shortcode, sub)
            )
            if files:
                if not shortcode:  # 다른 사이트: 영상 ID를 파일 이름에
                    prefix = f"{prefix}_{safe(files[0].stem.rsplit('_', 1)[0])}"
                if not silent_files(files):
                    return files, workdir, prefix, ""
                # 소리가 없음 → 다른 방법도 시도해 보고, 다 안 되면 이걸로
                errors.append(f"{method}: 소리 없는 영상")
                silent_result = silent_result or files
        except Exception as e:  # noqa: BLE001
            errors.append(f"{method}: {e}")

    if silent_result:
        return silent_result, workdir, prefix, SILENT_WARNING

    shutil.rmtree(workdir, ignore_errors=True)
    msg = " / ".join(errors) or "받을 수 있는 영상/사진이 없습니다."
    msg = re.sub(r"\x1b\[[0-9;]*m", "", msg)
    if "Unsupported URL" in msg:
        msg = "지원하지 않는 사이트이거나 영상이 없는 페이지입니다.\n\n상세: " + msg
    elif re.search(r"Sign in to confirm|not a bot", msg, re.I):
        msg = ("유튜브가 이 서버를 로봇으로 의심해 막았습니다. (클라우드 서버에서 자주 생겨요) "
               "잠시 후 다시 해 보거나 PC 버전을 써 주세요.\n\n상세: " + msg)
    elif re.search(r"login|401|403|private|rate|Please wait", msg, re.I):
        msg = ("사이트가 로그인을 요구하거나 요청을 막았습니다. 비공개 게시물이거나 요청이 너무 많을 수 있어요. "
               "인스타라면 Render 설정에 IG_SESSIONID(로그인 쿠키)를 넣어 주세요.\n\n상세: " + msg)
    raise DownloadError(msg)


@app.get("/")
@app.get("/share")  # 안드로이드 공유 메뉴(share_target) 진입점
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(BASE_DIR / "static", "manifest.webmanifest",
                               mimetype="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return send_from_directory(BASE_DIR / "static", "sw.js", mimetype="text/javascript")


@app.get("/icon-<int:size>.png")
def icon(size):
    return send_from_directory(BASE_DIR / "static", f"icon-{size}.png")


@app.get("/api/config")
def api_config():
    return jsonify(needKey=bool(ACCESS_KEY))


@app.get("/api/download")
def api_download():
    if ACCESS_KEY and request.headers.get("X-Key", "") != ACCESS_KEY:
        return jsonify(error="비밀번호가 틀렸습니다.", needKey=True), 401
    url = request.args.get("url", "").strip()
    try:
        files, workdir, prefix, warning = fetch(url)
    except DownloadError as e:
        return jsonify(error=str(e)), 400

    try:
        SAVE_DIR.mkdir(exist_ok=True)
        if len(files) == 1:
            f = files[0]
            name = f"{prefix}{f.suffix.lower()}"
            out = workdir / name
            f.rename(out)
        else:
            name = f"{prefix}.zip"
            out = workdir / name
            with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:  # 무압축 = 원본 그대로
                for i, f in enumerate(files, 1):
                    z.write(f, f"{prefix}_{i:02d}{f.suffix.lower()}")
        if not os.environ.get("RENDER"):  # PC에서 쓸 때만 사본 보관
            shutil.copy2(out, SAVE_DIR / name)
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise

    # 메모리에 올리지 않고 디스크에서 조금씩 전송 (긴 영상도 OK) → 끝나면 임시폴더 삭제
    def stream():
        try:
            with open(out, "rb") as fh:
                while chunk := fh.read(1024 * 1024):
                    yield chunk
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    resp = Response(stream(), mimetype=mimetypes.guess_type(name)[0] or "application/octet-stream")
    resp.headers["Content-Length"] = str(out.stat().st_size)
    resp.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
    resp.headers["X-File-Count"] = str(len(files))
    if warning:
        resp.headers["X-Warning"] = quote(warning)
    resp.headers["Access-Control-Expose-Headers"] = "Content-Disposition, X-File-Count, X-Warning"
    return resp


def main():
    port = int(os.environ.get("PORT", 5000))
    url = f"http://127.0.0.1:{port}"
    print(f"\n  인스타 다운로더 실행 중 → {url}")
    print("  같은 와이파이의 휴대폰에서는 http://<이 컴퓨터 IP>:%d 로 접속\n  (종료: Ctrl+C)\n" % port)
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    main()
