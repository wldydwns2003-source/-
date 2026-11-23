#!/usr/bin/env python3
"""짜깁기 쇼츠 영상 → 장면별 원본 영상 찾기.

사용법:
    python3 finder/find_original.py 영상1.mp4 [영상2.mp4 ...] [--out 결과폴더] [--query 검색어 ...]
    python3 finder/find_original.py --get 틱톡주소 [--out 폴더]      ← 틱톡 영상만 받기
    (필요: ffmpeg, pip install numpy pillow requests opencv-python-headless)

1) 컷이 바뀌는 지점마다 장면을 나누고, 장면마다 선명한 한 장을 뽑아 위아래 흐린 여백을 자름
2) 장면 사진을 얀덱스 이미지 검색에 올림 — 좌우 반전한 사진으로도 검색
   (짜깁기 영상은 원본을 좌우 반전해서 올리는 경우가 많음)
3) 검색 결과 썸네일을 내 영상의 모든 프레임과 특징점 매칭으로 직접 비교해서, 진짜 같은 장면인 것만 남김
4) 확인된 결과의 제목으로 유튜브를 다시 검색해 후보 영상을 모으고, 그 영상의 프레임 썸네일도 같은 방식으로 대조
5) 장면별 출처와, 여러 장면을 가진 원본 후보(제목·올린 사람·길이)를 report.md 로 정리
6) 틱톡 원본은 워터마크 없는 영상 파일까지 받아 둠 (--no-download 로 끔)
"""
import argparse
import html
import json
import time
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import numpy as np
from PIL import Image

FPS = 4            # 초당 몇 장 볼지
CUT = 0.16         # 이 이상 화면이 바뀌면 컷
MIN_SCENE = 0.75   # 이보다 짧은 장면은 앞 장면에 합침 (초)
AW, AH = 32, 56    # 컷 비교용 작은 크기

SITES = {
    'youtube': ('youtube.com', 'youtu.be'),
    'tiktok': ('tiktok.com',),
    'instagram': ('instagram.com',),
    'facebook': ('facebook.com', 'fb.watch'),
    'x': ('twitter.com', 'x.com'),
    'reddit': ('reddit.com',),
    'douyin': ('douyin.com',),
    'bilibili': ('bilibili.com',),
}


def probe(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                          'stream=width,height:format=duration', '-of', 'json', str(path)],
                         capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    s = j['streams'][0]
    return int(s['width']), int(s['height']), float(j['format']['duration'])


def small_frames(path):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-vf',
                          f'fps={FPS},scale={AW}:{AH},format=rgb24', '-f', 'rawvideo', '-'],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, AH, AW, 3).astype(np.float32)


def split_scenes(frames):
    keep = np.ones(AH, bool)
    keep[int(AH * 0.62):int(AH * 0.78)] = False   # 자막 줄 제외
    f = frames[:, keep]
    diffs = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2, 3)) / 255
    cuts = []
    for i, d in enumerate(diffs):
        if d < CUT:
            continue
        near = sorted(diffs[j] for j in (i - 2, i - 1, i + 1, i + 2) if 0 <= j < len(diffs))
        base = near[len(near) // 2] if near else 0
        if d >= base * 1.8:
            cuts.append(i + 1)
    bounds = [0, *cuts, len(frames)]
    scenes = []
    for s, e in zip(bounds, bounds[1:]):
        if e <= s:
            continue
        if scenes and (e - s) / FPS < MIN_SCENE:
            scenes[-1][1] = e
            continue
        scenes.append([s, e])
    if len(scenes) > 1 and (scenes[-1][1] - scenes[-1][0]) / FPS < MIN_SCENE:
        scenes[-2][1] = scenes.pop()[1]

    sharp = np.abs(np.diff(frames.sum(axis=3), axis=2)).mean(axis=(1, 2))
    picks = []
    for s, e in scenes:
        idx = list(range(s, e))
        inner = idx[1:-1] if len(idx) > 2 else idx       # 컷 바로 옆(전환 효과) 제외
        best = max(inner, key=lambda k: sharp[k])
        if picks and np.abs(frames[picks[-1][0]] - frames[best]).mean() / 255 < 0.07:
            continue                                      # 앞 장면과 거의 같은 화면
        picks.append((best, s / FPS, e / FPS))
    return picks


def grab(path, t, dest):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{t:.3f}', '-i', str(path),
                    '-frames:v', '1', '-q:v', '2', str(dest)], check=True)
    img = Image.open(dest).convert('RGB')
    img = img.crop(content_box(img))
    img.save(dest, quality=92)


def content_box(img):
    """위아래·좌우의 흐린/검은 여백을 잘라낸 영역."""
    w = 90
    h = round(90 * img.height / img.width)
    a = np.asarray(img.resize((w, h)).convert('L'), np.float32)
    rows = np.abs(np.diff(a, axis=1)).mean(axis=1)
    cols = np.abs(np.diff(a, axis=0)).mean(axis=0)

    def trim(arr):
        ref = np.sort(arr)[int(len(arr) * 0.75)]
        low = arr < ref * 0.3
        mx = int(len(arr) * 0.3)
        lo, hi = 0, len(arr)
        while lo < mx and low[lo]:
            lo += 1
        while len(arr) - hi < mx and low[hi - 1]:
            hi -= 1
        return lo / len(arr), hi / len(arr)

    t, b = trim(rows)
    l, r = trim(cols)
    return (round(l * img.width), round(t * img.height), round(r * img.width), round(b * img.height))


# ---------- 링크 정리 ----------
def site_of(url):
    host = urlparse(url).netloc.lower()
    for name, hosts in SITES.items():
        if any(host == h or host.endswith('.' + h) for h in hosts):
            return name
    return None


VIDEO_RE = re.compile(r'youtube\.com/watch\?v=|tiktok\.com/@[^/]*/video/\d+|instagram\.com/(reels?|p|tv)/'
                      r'|facebook\.com/(watch|reel|.*/videos/)|fb\.watch/|vkvideo\.ru/video|vk\.(com|ru)/(video|clip)'
                      r'|bilibili\.com/video/|douyin\.com/video/|(twitter|x)\.com/.*/status/')


def is_video(url):
    return bool(VIDEO_RE.search(url))


def video_from_thumb(img_url):
    """매칭된 사진이 유튜브/틱톡 썸네일이면 그 영상 주소를 돌려줌."""
    m = re.search(r'(?:ytimg\.com|img\.youtube\.com)/(?:vi|vi_webp)/([\w-]{11})/', img_url or '')
    if m:
        return f'https://www.youtube.com/watch?v={m.group(1)}'
    m = re.search(r'tiktok\.com/api/img/\?itemId=(\d+)', img_url or '')
    if m:
        return f'https://www.tiktok.com/@/video/{m.group(1)}'
    return None


def clean(url):
    p = urlparse(url)
    if p.netloc.endswith('google.com') and p.path == '/url':
        url = unquote(parse_qs(p.query).get('q', parse_qs(p.query).get('url', [url]))[0])
        p = urlparse(url)
    if not p.scheme:
        return ''
    if site_of(url) == 'youtube':
        m = re.search(r'(?:v=|youtu\.be/|shorts/)([\w-]{11})', url)
        if m:
            return f'https://www.youtube.com/watch?v={m.group(1)}'
    if site_of(url) in ('tiktok', 'instagram', 'facebook', 'x', 'reddit'):
        return f'{p.scheme}://{p.netloc}{p.path}'.rstrip('/')
    q = '&'.join(kv for kv in p.query.split('&') if kv and not kv.startswith('utm_'))
    return f'{p.scheme}://{p.netloc}{p.path}' + (f'?{q}' if q else '')


UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36')


class Yandex:
    """얀덱스 이미지 검색(사진으로 찾기). 키 필요 없음. 영상 장면 원본 찾기에 강함."""

    def __init__(self):
        import requests
        self.s = requests.Session()
        self.s.headers['User-Agent'] = UA

    def search(self, img_path, tries=3):
        for i in range(tries):   # 네트워크가 잠깐 끊기면 다시 시도
            try:
                return self._search(img_path)
            except RuntimeError:
                raise
            except Exception:
                if i == tries - 1:
                    raise
                time.sleep(3 * (i + 1))

    def _search(self, img_path):
        with open(img_path, 'rb') as f:
            r = self.s.post('https://yandex.com/images/search', timeout=40, params={
                'rpt': 'imageview', 'format': 'json',
                'request': json.dumps({'blocks': [{'block': 'b-page_type_search-by-image__link'}]})},
                files={'upfile': ('scene.jpg', f, 'image/jpeg')})
        r.raise_for_status()
        p = r.json()['blocks'][0]['params']
        page_url = 'https://yandex.com/images/search?' + urlencode(
            {'cbir_id': p['cbirId'], 'rpt': 'imageview', 'url': p['originalImageUrl'], 'cbir_page': 'sites'})
        page = html.unescape(self.s.get(page_url, timeout=40).text)
        if 'showcaptcha' in page or 'smart-captcha' in page:
            raise RuntimeError('얀덱스가 보안문자를 요구함 (잠시 후 다시 시도)')
        i = page.find('"sites":[')
        sites = json.JSONDecoder().raw_decode(page[i + 8:])[0] if i >= 0 else []
        found = {}

        def add(url, title, rank, thumb, domain=''):
            if url and url not in found:
                found[url] = {'url': url, 'site': site_of(url) or domain, 'video': is_video(url),
                              'title': title, 'rank': rank, 'thumb': thumb}

        for rank, it in enumerate(sites):
            title = (it.get('title') or '').strip()
            thumb = (it.get('thumb') or {}).get('url', '')
            thumb = 'https:' + thumb if thumb.startswith('//') else thumb
            add(clean(it.get('url', '')), title, rank, thumb, it.get('domain', ''))
            add(video_from_thumb((it.get('originalImage') or {}).get('url')), title, rank, thumb)
        return list(found.values()), page_url

    def oembed(self, url):
        """유튜브/틱톡 영상의 제목과 올린 사람."""
        if site_of(url) == 'tiktok':
            try:
                t = self.s.get(url, timeout=20).text
                vid = re.search(r'/video/(\d+)', url).group(1)
                m = re.search(r'"id":"%s".{0,4000}?"uniqueId":"([^"]+)"' % vid, t, re.S) or re.search(r'"uniqueId":"([^"]+)"', t)
                d = re.search(r'"desc":"((?:[^"\\]|\\.)*)"', t)
                if m:
                    user = m.group(1)
                    desc = json.loads(f'"{d.group(1)}"') if d else ''
                    return {'title': desc, 'author': '@' + user, 'author_url': f'https://www.tiktok.com/@{user}',
                            'url': f'https://www.tiktok.com/@{user}/video/{vid}'}
            except Exception:
                pass
        api = {'youtube': 'https://www.youtube.com/oembed', 'tiktok': 'https://www.tiktok.com/oembed'}.get(site_of(url))
        if not api:
            return None
        try:
            r = self.s.get(api, params={'url': url, 'format': 'json'}, timeout=20)
            if r.ok:
                j = r.json()
                return {'title': j.get('title', ''), 'author': j.get('author_name', ''),
                        'author_url': j.get('author_url', '')}
        except Exception:
            pass
        return None

    def close(self):
        self.s.close()


# ---------- 그림 대조 (특징점 매칭) ----------
MATCH = 25   # 이 이상 특징점이 기하학적으로 맞으면 같은 장면으로 봄 (다른 장면은 보통 10 이하)


class Matcher:
    """내 영상의 모든 프레임(초당 2장)을 기억해 두고, 아무 사진이나 어느 시점과 같은지 찾음."""

    def __init__(self, video, scenes):
        import cv2
        self.cv2 = cv2
        self.orb = cv2.ORB_create(1500)
        self.bf = cv2.BFMatcher(cv2.NORM_HAMMING)
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(video), '-vf', 'fps=2,scale=-2:640',
                              '-f', 'image2pipe', '-vcodec', 'mjpeg', '-q:v', '3', '-'],
                             capture_output=True, check=True).stdout
        self.frames = []
        for i, chunk in enumerate(raw.split(b'\xff\xd9')[:-1]):
            import io
            im = Image.open(io.BytesIO(chunk + b'\xff\xd9')).convert('RGB')
            self.frames.append((i / 2, self.features(im.crop(content_box(im)))))
        self.scenes = scenes   # [(start, end), ...]
        # 모든 프레임의 특징점을 한 덩어리로 모아 두고, 질문 사진과 한 번에 비교해 후보 프레임만 추림
        rows, owner = [], []
        for i, (_, (kp, des)) in enumerate(self.frames):
            if des is not None:
                rows.append(des)
                owner.append(np.full(len(des), i))
        self.all_des = np.vstack(rows)
        self.owner = np.concatenate(owner)

    def features(self, im):
        a = np.asarray(im.convert('L'))
        h, w = a.shape
        k = 480 / max(h, w)
        return self.orb.detectAndCompute(self.cv2.resize(a, (max(1, int(w * k)), max(1, int(h * k)))), None)

    def score(self, A, B):
        (ka, da), (kb, db) = A, B
        if da is None or db is None or len(ka) < 10 or len(kb) < 10:
            return 0
        good = [p[0] for p in self.bf.knnMatch(da, db, k=2) if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
        if len(good) < 8:
            return 0
        src = np.float32([ka[m.queryIdx].pt for m in good])
        dst = np.float32([kb[m.trainIdx].pt for m in good])
        H, mask = self.cv2.findHomography(src, dst, self.cv2.RANSAC, 8.0)
        if H is None or mask is None:
            return 0
        # 점들이 한 줄·한 점으로 뭉개진 변환(타일·창살 같은 반복 무늬에서 생김)은 같은 장면이 아님
        det = float(np.linalg.det(H[:2, :2]))
        if not 0.1 < det < 10:
            return 0
        return int(mask.sum())

    def scene_at(self, t):
        for n, (s, e) in enumerate(self.scenes, 1):
            if s <= t < e:
                return n
        return len(self.scenes)

    def locate(self, im):
        """사진이 내 영상의 어느 장면과 같은지. 좌우 반전도 시도. → (점수, 장면번호, 시각, 반전여부) 또는 None"""
        from PIL import ImageOps
        w, h = im.size
        if w > h * 1.1:   # 가로 썸네일 안에 세로 영상이 있으면 가운데만
            cw = int(h * 9 / 16)
            narrow = im.crop(((w - cw) // 2, 0, (w + cw) // 2, h))
            cands = [im, narrow]
        else:
            cands = [im]
        best = None
        for c in cands:
            for flip in (False, True):
                F = self.features(ImageOps.mirror(c) if flip else c)
                if F[1] is None or len(F[0]) < 10:
                    continue
                ms = self.bf.match(F[1], self.all_des)
                votes = np.bincount([self.owner[m.trainIdx] for m in ms if m.distance < 40],
                                    minlength=len(self.frames))
                for i in np.argsort(votes)[::-1][:3]:
                    if votes[i] < 5:
                        break
                    t, A = self.frames[i]
                    sc = self.score(F, A)
                    if sc >= MATCH and (not best or sc > best[0]):
                        best = (sc, self.scene_at(t), t, flip)
        return best


def fetch_image(session, url):
    import io
    try:
        r = session.get(url, timeout=20)
        if r.ok and len(r.content) > 1500:
            return Image.open(io.BytesIO(r.content)).convert('RGB')
    except Exception:
        pass
    return None


# ---------- 틱톡 영상 받기 ----------
def download_tiktok(session, url, dest_dir):
    """틱톡 영상을 워터마크 없이 받음 (tikwm.com 경유). → 저장한 파일 경로, 실패하면 오류 문구"""
    m = re.search(r'/video/(\d+)', url)
    if not m:
        return None, '틱톡 영상 주소가 아니에요'
    vid = m.group(1)
    try:
        j = session.post('https://www.tikwm.com/api/', data={'url': url, 'hd': 1}, timeout=30).json()
    except Exception as e:
        return None, f'tikwm 접속 실패: {e}'
    d = j.get('data') or {}
    if j.get('code') != 0 or not d:
        return None, f"tikwm 오류: {j.get('msg')}"
    author = (d.get('author') or {}).get('unique_id', '') or 'tiktok'
    Path(dest_dir).mkdir(parents=True, exist_ok=True)
    errors = []
    for kind in ('hdplay', 'play'):
        dest = Path(dest_dir) / f'{author}_{vid}{"_HD" if kind == "hdplay" else ""}.mp4'
        try:
            r = session.get(f'https://www.tikwm.com/video/media/{kind}/{vid}.mp4', timeout=120)
            if r.ok and len(r.content) > 50000 and r.content[4:8] == b'ftyp':
                dest.write_bytes(r.content)
                note = '' if kind == 'hdplay' else f"HD는 실패해서 일반 화질로 받음 ({'; '.join(errors)})"
                return dest, note
            errors.append(f'{kind}: HTTP {r.status_code}')
        except Exception as e:
            host = re.search(r"host='([^']+)'", str(e))
            errors.append(f"{kind}: {'영상 서버 ' + host.group(1) + ' 접속이 막혀 있어요' if host else e}")
    return None, '; '.join(errors)


# ---------- 유튜브 ----------
def youtube_search(session, query, limit=15):
    body = {'query': query, 'context': {'client': {'clientName': 'WEB', 'clientVersion': '2.20250101.00.00',
                                                   'hl': 'en', 'gl': 'US'}}}
    try:
        j = session.post('https://www.youtube.com/youtubei/v1/search', json=body, timeout=30).json()
    except Exception:
        return []
    out = []

    def walk(o):
        if isinstance(o, dict):
            if 'videoRenderer' in o:
                v = o['videoRenderer']
                out.append({'id': v['videoId'], 'title': ''.join(r['text'] for r in v['title'].get('runs', [])),
                            'author': (v.get('ownerText', {}).get('runs') or [{}])[0].get('text', ''),
                            'length': v.get('lengthText', {}).get('simpleText', ''),
                            'views': v.get('viewCountText', {}).get('simpleText', '')})
                return
            if 'shortsLockupViewModel' in o:
                v = o['shortsLockupViewModel']
                m = re.search(r'"videoId": ?"([\w-]{11})"', json.dumps(v)) or \
                    re.search(r'shorts-shelf-item-([\w-]{11})', json.dumps(v))
                if m:
                    meta = v.get('overlayMetadata', {})
                    out.append({'id': m.group(1), 'title': meta.get('primaryText', {}).get('content', ''),
                                'author': '', 'length': '쇼츠', 'views': meta.get('secondaryText', {}).get('content', '')})
                return
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(j)
    return out[:limit]


def youtube_frames(session, vid):
    imgs = []
    for n in ('oardefault', 'hq1', 'hq2', 'hq3', 'hqdefault'):
        im = fetch_image(session, f'https://i.ytimg.com/vi/{vid}/{n}.jpg')
        if im is not None and im.size[0] > 130:
            imgs.append(im)
    return imgs


STOP = set('the a an of and or to in on for with is are be this that you your it its from by at as how why what '
           'video videos shorts short youtube tiktok instagram vk watch clip clips online free full hd new'.split())


def clean_title(t):
    t = re.sub(r'\s*[-|–]\s*(YouTube|TikTok|Instagram|VK.*|Pinterest|Facebook|Twitter.*|Sotwe.*)\s*$', '', t, flags=re.I)
    return t.replace('#', ' ').strip()


def keywords(titles, n=5):
    """확인된 결과 제목들에서 여러 번 나온 영어 핵심 단어 (예: fakir, hostile, architecture, bench)."""
    cnt = defaultdict(int)
    for t in titles:
        words = {re.sub(r"['’]s?$", '', w) for w in re.findall(r"[A-Za-z][a-z'’]{3,}", clean_title(t).lower())}
        for w in words:
            if w not in STOP and len(w) >= 4:
                cnt[w] += 1
    return [w for w, c in sorted(cnt.items(), key=lambda kv: -kv[1]) if c >= 2][:n]


def make_queries(titles, extra, limit=10):
    """유튜브 검색어: 직접 준 검색어 → 핵심 단어 조합 → 확인된 영상 제목."""
    qs, seen = [], set()

    def add(q):
        q = ' '.join(q.split()[:8])
        if len(q) >= 6 and q.lower() not in seen:
            seen.add(q.lower())
            qs.append(q)
    for q in extra:
        add(q)
    kw = keywords(titles, 4)
    if len(kw) >= 3:
        add(' '.join(kw[:3]))
    for i, j in ((0, 1), (0, 2), (1, 2)):
        if j < len(kw):
            add(f'{kw[i]} {kw[j]}')
    for t in titles:
        add(' '.join(w for w in re.findall(r"[\w'’]+", clean_title(t)) if w.lower() not in STOP))
    return qs[:limit]


def main():
    ap = argparse.ArgumentParser(description='짜깁기 쇼츠의 장면별 원본 영상 찾기')
    ap.add_argument('videos', nargs='+')
    ap.add_argument('--out', default='finder-results')
    ap.add_argument('--query', action='append', default=[], help='유튜브에서 추가로 찾아볼 검색어 (여러 번 가능)')
    ap.add_argument('--no-search', action='store_true', help='장면 사진만 뽑고 검색은 안 함')
    ap.add_argument('--no-download', action='store_true', help='찾은 틱톡 원본 영상을 받지 않음')
    ap.add_argument('--get', action='store_true', help='videos 자리에 틱톡 주소를 넣으면 영상만 받음')
    args = ap.parse_args()

    if args.get:   # 틱톡 주소 → 영상 파일만 받기
        import requests
        se = requests.Session()
        se.headers['User-Agent'] = UA
        for u in args.videos:
            path, err = download_tiktok(se, u, args.out)
            print(f'{u}\n  → {path}' + (f' ({err})' if err else '') if path else f'{u}\n  → 실패: {err}')
        return

    from concurrent.futures import ThreadPoolExecutor
    from PIL import ImageOps
    out = Path(args.out)
    engine = None if args.no_search else Yandex()
    report = ['# 원본 영상 찾기 결과\n']
    pool = ThreadPoolExecutor(8)
    try:
        for vid in map(Path, args.videos):
            _, _, dur = probe(vid)
            picks = split_scenes(small_frames(vid))
            vdir = out / vid.stem
            vdir.mkdir(parents=True, exist_ok=True)
            n_sc = len(picks)
            print(f'\n== {vid.name}: {dur:.0f}초, 장면 {n_sc}개', flush=True)
            report.append(f'\n## {vid.name} ({dur:.0f}초, 장면 {n_sc}개)\n')
            imgs = []
            for n, (k, start, end) in enumerate(picks, 1):
                img = vdir / f'scene-{n:02d}.jpg'
                grab(vid, k / FPS + 0.5 / FPS, img)
                imgs.append(img)
            if not engine:
                report += [f'- 장면 {n} ({s:.1f}–{e:.1f}초) `{i.relative_to(out)}`' for n, ((_, s, e), i) in
                           enumerate(zip(picks, imgs), 1)]
                continue

            matcher = Matcher(vid, [(s, e) for _, s, e in picks])
            sources = {}   # url → 확인된 출처 정보

            def verify(item, img):
                if img is None:
                    return None
                hit = matcher.locate(img)
                return (item, hit) if hit else None

            def record(item, hit):
                sc, scene, t, flip = hit
                src = sources.setdefault(item['url'], {**item, 'scenes': {}, 'flip': 0, 'n': 0})
                src['scenes'][scene] = max(src['scenes'].get(scene, 0), sc)
                src['flip'] += flip
                src['n'] += 1

            # 1) 얀덱스: 장면 사진 + 좌우 반전 사진으로 검색 → 썸네일을 직접 대조
            raw_titles = []
            for n, img in enumerate(imgs, 1):
                hits = []
                for flip in (False, True):
                    path = img
                    if flip:
                        path = vdir / f'.flip-{n:02d}.jpg'
                        ImageOps.mirror(Image.open(img)).save(path, quality=92)
                    try:
                        hits += engine.search(path)[0][:20]
                    except Exception as e:
                        print(f'  장면 {n}: 검색 실패 ({e})', flush=True)
                    if flip:
                        path.unlink(missing_ok=True)
                    time.sleep(1)
                thumbs = list(pool.map(lambda h: fetch_image(engine.s, h['thumb']) if h['thumb'] else None, hits))
                ok = [r for r in pool.map(lambda a: verify(*a), zip(hits, thumbs)) if r]
                for item, hit in ok:
                    record(item, hit)
                    raw_titles.append(item['title'])
                print(f'  장면 {n}: 검색 결과 {len(hits)}개 중 같은 장면 확인 {len(ok)}개', flush=True)

            # 2) 유튜브: 확인된 결과 제목 + 직접 준 검색어로 검색 → 후보 영상의 프레임 썸네일 대조
            ytc = {}
            for url, src in sources.items():
                m = re.search(r'watch\?v=([\w-]{11})', url)
                if m:
                    ytc.setdefault(m.group(1), {'title': src['title']})
            counts = defaultdict(int)
            for t in raw_titles:
                counts[t] += 1

            def check_yt(item):
                vid_id, info = item
                hits = [matcher.locate(im) for im in youtube_frames(engine.s, vid_id)]
                return vid_id, info, [h for h in hits if h]

            # 두 번 돌림: 1차에서 확인된 유튜브 영상 제목으로 한 번 더 검색하면 같은 원본을 쓴 영상이 더 나옴
            checked, used = set(), set()
            for rnd in (1, 2):
                titles = sorted(counts, key=lambda t: -counts[t])
                queries = [q for q in make_queries(titles, args.query if rnd == 1 else []) if q not in used]
                used.update(queries)
                for q in queries:
                    for r in youtube_search(engine.s, q):
                        ytc.setdefault(r['id'], r)
                todo = [(k, v) for k, v in ytc.items() if k not in checked][:120]
                checked.update(k for k, _ in todo)
                print(f'  유튜브 {rnd}차: 검색어 {len(queries)}개 ({", ".join(queries[:4])}…) → 후보 {len(todo)}개 대조',
                      flush=True)
                found = 0
                for vid_id, info, hits in pool.map(check_yt, todo):
                    url = f'https://www.youtube.com/watch?v={vid_id}'
                    if hits:
                        found += 1
                        counts[info.get('title', '')] += 2
                    for h in hits:
                        record({'url': url, 'site': 'youtube', 'video': True, 'title': info.get('title', ''),
                                'length': info.get('length', ''), 'views': info.get('views', ''),
                                'author': info.get('author', '')}, h)
                print(f'    같은 장면이 있는 영상 {found}개', flush=True)
                if not found:
                    break

            # 3) 정리
            if not sources:
                report.append('확인된 출처가 없어요. 장면 사진으로 직접 구글 렌즈 검색을 해 보세요.\n')
            flips = sum(s['flip'] for s in sources.values())
            total = sum(s['n'] for s in sources.values())
            if total and flips / total > 0.6:
                report.append('> ⚠️ 이 영상은 원본을 **좌우 반전**해서 만들었어요 (확인된 일치의 '
                              f'{flips * 100 // total}%가 반전 상태에서 맞음).\n')

            ranked = sorted(sources.values(), key=lambda s: (-len(s['scenes']), -s['video'], -sum(s['scenes'].values())))
            for s in ranked[:25]:
                if s['video'] and not s.get('author'):
                    info = engine.oembed(s['url'])
                    if info:
                        s['url'] = info.get('url', s['url'])
                        s['title'] = info['title'] or s['title']
                        s['author'], s['author_url'] = info['author'], info['author_url']

            def line(s):
                sn = ', '.join(map(str, sorted(s['scenes'])))
                who = f" — {s['author']}" if s.get('author') else ''
                extra = ' · '.join(x for x in (s.get('length'), s.get('views')) if x)
                return (f"- {'🎬 ' if s['video'] else ''}**장면 {len(s['scenes'])}개** ({sn}) [{s['site']}] "
                        f"[{(s['title'] or s['url'])[:110]}]({s['url']}){who}" + (f' ({extra})' if extra else ''))

            report.append('\n### ✅ 그림으로 확인된 출처 (같은 장면이 많은 순)\n')
            report.append('긴 영상(몇 분짜리)이 여러 장면을 가지면 그게 원본일 가능성이 높고, '
                          '쇼츠는 같은 원본을 가져다 쓴 재업로드일 수 있어요.\n')
            report += [line(s) for s in ranked[:25]]

            scene_lines, missing = [], []
            for n, ((_, st, en), img) in enumerate(zip(picks, imgs), 1):
                srcs = sorted((s for s in sources.values() if n in s['scenes']),
                              key=lambda s: (-len(s['scenes']), -s['scenes'][n]))
                if not srcs:
                    missing.append(n)
                    scene_lines.append(f'- **장면 {n}** ({st:.1f}–{en:.1f}초) `{img.relative_to(out)}` — 확인된 출처 없음')
                    continue
                scene_lines.append(f'- **장면 {n}** ({st:.1f}–{en:.1f}초) `{img.relative_to(out)}`')
                for s in srcs[:3]:
                    scene_lines.append(f"  - [{s['site']}] [{(s['title'] or s['url'])[:100]}]({s['url']})"
                                  + (f" — {s['author']}" if s.get('author') else ''))
            covered = n_sc - len(missing)
            summary = (f'**장면 {n_sc}개 중 {covered}개의 출처를 그림으로 확인했어요.**'
                       + (f" 못 찾은 장면: {', '.join(map(str, missing))}" if missing else ''))
            report.insert(report.index('\n### ✅ 그림으로 확인된 출처 (같은 장면이 많은 순)\n'), summary)
            # 틱톡 원본은 영상 파일까지 받아 둠 (같은 장면이 많은 순으로 최대 5개)
            if not args.no_download:
                tts = [s for s in ranked if s['site'] == 'tiktok' and '/video/' in s['url']][:5]
                if tts:
                    report.append('\n### 📥 받아 둔 틱톡 원본 영상\n')
                for s in tts:
                    path, err = download_tiktok(engine.s, s['url'], vdir / 'originals')
                    report.append(f"- [{s['url']}]({s['url']}) → " +
                                  (f'`{path.relative_to(out)}`' + (f' — {err}' if err else '') if path
                                   else f'받기 실패 ({err})'))
                    print(f"  틱톡 받기: {s['url']} → {path or '실패: ' + err}", flush=True)
            report.append('\n### 장면별 출처\n')
            report += scene_lines
            print(f'  출처 확인: 장면 {covered}/{n_sc}개', flush=True)
    finally:
        pool.shutdown()
        if engine:
            engine.close()
    (out / 'report.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    print(f'\n결과: {out / "report.md"}')


if __name__ == '__main__':
    sys.exit(main())
