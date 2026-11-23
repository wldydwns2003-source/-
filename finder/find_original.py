#!/usr/bin/env python3
"""짜깁기 쇼츠 영상 → 장면별 원본 영상 찾기.

사용법:
    python3 finder/find_original.py 영상1.mp4 [영상2.mp4 ...]
    (필요: ffmpeg, pip install numpy pillow requests) [--out 결과폴더] [--no-search]

1) 컷이 바뀌는 지점마다 장면을 나눔 (자막만 바뀌는 건 무시)
2) 장면마다 가장 선명한 프레임을 뽑고 위아래 흐린 여백을 잘라 JPG로 저장
3) 각 장면 사진을 얀덱스 이미지 검색에 올려 결과를 모으고, 유튜브·틱톡 영상은 제목·올린 사람까지 확인
4) 여러 장면에서 반복해서 나온 링크일수록 원본일 가능성이 높으므로 위로 정렬해 report.md 로 저장
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


# ---------- 구글 렌즈 검색 ----------
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

    def search(self, img_path):
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

        def add(url, title, rank, domain=''):
            if url and url not in found:
                found[url] = {'url': url, 'site': site_of(url) or domain, 'video': is_video(url),
                              'title': title, 'rank': rank}

        for rank, it in enumerate(sites):
            title = (it.get('title') or '').strip()
            add(clean(it.get('url', '')), title, rank, it.get('domain', ''))
            add(video_from_thumb((it.get('originalImage') or {}).get('url')), '', rank)
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


def main():
    ap = argparse.ArgumentParser(description='짜깁기 쇼츠의 장면별 원본 영상 찾기')
    ap.add_argument('videos', nargs='+')
    ap.add_argument('--out', default='finder-results')
    ap.add_argument('--no-search', action='store_true', help='장면 사진만 뽑고 검색은 안 함')
    args = ap.parse_args()

    out = Path(args.out)
    engine = None if args.no_search else Yandex()
    report = ['# 원본 영상 찾기 결과\n']
    try:
        for vid in map(Path, args.videos):
            _, _, dur = probe(vid)
            picks = split_scenes(small_frames(vid))
            vdir = out / vid.stem
            vdir.mkdir(parents=True, exist_ok=True)
            print(f'\n== {vid.name}: {dur:.0f}초, 장면 {len(picks)}개', flush=True)
            report.append(f'\n## {vid.name} ({dur:.0f}초, 장면 {len(picks)}개)\n')
            score = defaultdict(lambda: {'scenes': [], 'pts': 0.0})
            scene_lines = []
            for n, (k, start, end) in enumerate(picks, 1):
                img = vdir / f'scene-{n:02d}.jpg'
                grab(vid, k / FPS + 0.5 / FPS, img)
                line = f'- **장면 {n}** ({start:.1f}–{end:.1f}초) `{img.relative_to(out)}`'
                if engine:
                    try:
                        hits, page = engine.search(img)
                        line += f' — [검색 결과 전체]({page})'
                    except Exception as e:  # 한 장면 실패해도 계속
                        hits = []
                        line += f' — 검색 실패: {e}'
                    time.sleep(1.5)
                    vids = [h for h in hits if h['video']]
                    print(f'  장면 {n}: 결과 {len(hits)}개 (영상 {len(vids)}개)', flush=True)
                    for h in hits:
                        sc = score[h['url']]
                        sc.update(site=h['site'], video=h['video'])
                        sc['title'] = sc.get('title') or h['title']
                        sc['scenes'].append(n)
                        sc['pts'] += 1 / (1 + h['rank'] / 5)
                    for h in vids[:3] + [h for h in hits if not h['video']][:2]:
                        line += f"\n  - {'🎬 ' if h['video'] else ''}[{h['site']}] [{h['title'] or h['url']}]({h['url']})"
                scene_lines.append(line)
            if engine:
                # 여러 장면에서 반복해서 걸린 링크일수록 원본(또는 같은 원본을 쓴 영상)일 가능성이 큼
                key = lambda kv: (-len(set(kv[1]['scenes'])), -kv[1]['pts'])
                vids = sorted(((u, v) for u, v in score.items() if v['video']), key=key)[:15]
                pages = sorted(((u, v) for u, v in score.items() if not v['video']), key=key)[:10]
                report.append('\n### 🎬 원본 영상 후보 (여러 장면에서 걸린 순)\n')
                if not vids:
                    report.append('영상 링크를 못 찾았어요. 아래 관련 페이지나 장면 사진으로 직접 렌즈 검색을 해 보세요.\n')
                for url, sc in vids:
                    info = engine.oembed(url)
                    sn = sorted(set(sc['scenes']))
                    if info:
                        url = info.get('url', url)
                        who = f" — 올린 사람: [{info['author']}]({info['author_url']})" if info['author'] else ''
                        title = info['title'] or sc['title'] or url
                    else:
                        who, title = '', sc['title'] or url
                    report.append(f"- **장면 {len(sn)}개** ({', '.join(map(str, sn))}) [{sc['site']}] "
                                  f"[{title[:120]}]({url}){who}")
                report.append('\n### 📰 같은 장면이 나온 페이지 (재업로드·기사 등, 원본 단서)\n')
                for url, sc in pages:
                    sn = sorted(set(sc['scenes']))
                    report.append(f"- 장면 {len(sn)}개 ({', '.join(map(str, sn))}) [{sc['site']}] "
                                  f"[{(sc['title'] or url)[:120]}]({url})")
            report.append('\n### 장면별\n')
            report.extend(scene_lines)
    finally:
        if engine:
            engine.close()
    (out / 'report.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    print(f'\n결과: {out / "report.md"}')


if __name__ == '__main__':
    sys.exit(main())
