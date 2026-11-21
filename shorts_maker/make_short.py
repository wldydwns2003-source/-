# -*- coding: utf-8 -*-
"""
원본 영상 하나 → 나레이션 이야기 쇼츠 자동 제작기

사용법:
    python make_short.py 원본.mp4                  # 기본 설정으로 제작
    python make_short.py 원본.mp4 --preset 내설정  # presets/내설정.json 설정 사용
    python make_short.py 원본.mp4 --review         # 대본을 먼저 확인·수정한 뒤 제작
    python make_short.py --list-voices             # 타입캐스트 목소리 목록 보기
    python make_short.py 원본.mp4 --mock           # API 없이 편집 과정만 테스트

필요한 것: ffmpeg, ANTHROPIC_API_KEY, TYPECAST_API_KEY (.env 파일에 적어두면 됩니다)
"""
import argparse
import base64
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
W, H = 1080, 1920  # 쇼츠 해상도 (9:16)
FPS = 30

TYPECAST_TTS_URL = "https://api.typecast.ai/v1/text-to-speech"
TYPECAST_VOICES_URL = "https://api.typecast.ai/v2/voices"


# ───────────────────────── 공통 도구 ─────────────────────────

def log(msg):
    print(msg, flush=True)


def load_env():
    """shorts_maker/.env 의 KEY=VALUE 를 환경변수로 읽어온다."""
    env = HERE / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v and not os.environ.get(k):
            os.environ[k] = v


def run(cmd, cwd=None):
    """ffmpeg 등 외부 명령 실행. 실패하면 마지막 오류 줄을 보여준다."""
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        tail = "\n".join(p.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"명령 실패: {' '.join(map(str, cmd[:4]))} ...\n{tail}")
    return p.stdout


FFMPEG_DIR = HERE / "ffmpeg"
FFMPEG_ZIPS = [
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
]


def _add_to_path(d):
    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


def _find_local_ffmpeg():
    """프로그램 폴더 안이나 winget 설치 위치에서 ffmpeg.exe 를 찾는다."""
    places = [FFMPEG_DIR]
    la = os.environ.get("LOCALAPPDATA")
    if la:
        places += [Path(la) / "Microsoft" / "WinGet" / "Links",
                   Path(la) / "Microsoft" / "WinGet" / "Packages"]
    for pf in (os.environ.get("ProgramFiles"), "C:\\ffmpeg"):
        if pf:
            places.append(Path(pf))
    for base in places:
        if not base.exists():
            continue
        if (base / "ffmpeg.exe").exists() and (base / "ffprobe.exe").exists():
            return base
        for exe in base.glob("**/bin/ffmpeg.exe") if base != Path(os.environ.get("ProgramFiles", "-")) \
                else base.glob("ffmpeg*/bin/ffmpeg.exe"):
            if (exe.parent / "ffprobe.exe").exists():
                return exe.parent
    return None


def _download_ffmpeg():
    import urllib.request
    import zipfile
    FFMPEG_DIR.mkdir(exist_ok=True)
    zpath = FFMPEG_DIR / "ffmpeg.zip"
    for url in FFMPEG_ZIPS:
        try:
            log(f"  · ffmpeg 를 내려받는 중이에요 (처음 한 번만, 약 100MB)...\n    {url}")
            urllib.request.urlretrieve(url, zpath)
            with zipfile.ZipFile(zpath) as z:
                for name in z.namelist():
                    base = name.rsplit("/", 1)[-1].lower()
                    if base in ("ffmpeg.exe", "ffprobe.exe"):
                        (FFMPEG_DIR / base).write_bytes(z.read(name))
            zpath.unlink(missing_ok=True)
            if (FFMPEG_DIR / "ffmpeg.exe").exists() and (FFMPEG_DIR / "ffprobe.exe").exists():
                log("  · ffmpeg 준비 완료")
                return True
        except Exception as e:
            log(f"    내려받기 실패: {e}")
    return False


def check_ffmpeg():
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    found = _find_local_ffmpeg()
    if found:
        _add_to_path(found)
        return
    if os.name == "nt" and _download_ffmpeg():
        _add_to_path(FFMPEG_DIR)
        return
    sys.exit("ffmpeg 를 찾지도, 내려받지도 못했어요. 인터넷 연결을 확인하거나 컴퓨터를 다시 켠 뒤 다시 해 주세요.")


def probe(path):
    out = run(["ffprobe", "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", str(path)])
    info = json.loads(out)
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    if not v:
        sys.exit("영상 트랙이 없는 파일이에요.")
    has_audio = any(s.get("codec_type") == "audio" for s in info["streams"])
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = abs(int(float(sd["rotation"])))
    if rot in (90, 270):
        w, h = h, w
    dur = float(info["format"].get("duration") or v.get("duration") or 0)
    return {"w": w, "h": h, "dur": dur, "has_audio": has_audio}


def load_preset(name):
    base = json.loads((HERE / "presets" / "default.json").read_text(encoding="utf-8"))
    if name and name != "default":
        p = Path(name)
        if not p.exists():
            p = HERE / "presets" / f"{name}.json"
        if not p.exists():
            sys.exit(f"설정 파일을 찾을 수 없어요: {name}")
        base.update(json.loads(p.read_text(encoding="utf-8")))
    return {k: v for k, v in base.items() if not k.startswith("_")}


def safe_name(s, limit=40):
    s = re.sub(r'[\\/:*?"<>|\n\r\t]', "", s).strip()
    return (s[:limit] or "short").rstrip(". ")


# ───────────────────────── 1. 원본 분석 ─────────────────────────

def extract_frames(src, dur, n, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        f = outdir / f"f{i:03d}.jpg"
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src),
             "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "5", str(f)])
        if f.exists():
            frames.append((t, f))
    return frames


def transcribe_original(src, preset):
    """원본에 말소리가 있으면 받아쓰기 (faster-whisper 가 설치된 경우에만)."""
    if not preset.get("use_whisper", True):
        return ""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return ""
    log("  · 원본 음성 받아쓰는 중 (faster-whisper)...")
    try:
        model = WhisperModel(preset.get("whisper_model", "small"), device="auto", compute_type="int8")
        segs, _ = model.transcribe(str(src), vad_filter=True)
        lines = [f"[{s.start:.1f}~{s.end:.1f}초] {s.text.strip()}" for s in segs]
        return "\n".join(lines)
    except Exception as e:
        log(f"  · 받아쓰기는 건너뛸게요 (오류: {type(e).__name__}). 장면만 보고 대본을 써요.")
        return ""


# ───────────────────────── 2. Claude 로 대본·편집 계획 ─────────────────────────

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "hook_line1": {"type": "string"},
        "hook_line2": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "narration": {"type": "string"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "zoom": {"type": "number"},
                    "focus_x": {"type": "number"},
                    "focus_y": {"type": "number"},
                    "speed": {"type": "number"},
                },
                "required": ["narration", "start", "end", "zoom", "focus_x", "focus_y", "speed"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "description", "hashtags", "hook_line1", "hook_line2", "segments"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """너는 해외 영상을 원본으로 한국어 '이야기 쇼츠'를 만드는 전문 쇼츠 작가 겸 편집자다.
원본 영상의 장면(타임스탬프가 붙은 캡처들)과 받아쓰기를 보고, 50~60초짜리 쇼츠의 대본과 컷 편집 계획을 만든다.

대본 원칙
- 시청자는 20~50대 성인이다. 조회수 수백만을 찍는 한국 정보·썰 쇼츠의 전문 나레이터처럼, 자신감 있고 리듬감 있게 쓴다.
- 화면을 그대로 읽어주지 말고, 상황의 맥락·감정·반전을 살려 '이야기'로 재구성한다.
- 구조: 후킹 → 궁금증 키우기 → 핵심 원리나 반전 → 구체적 디테일 → 여운이나 질문.
- 첫 문장은 구체적인 장면이나 의외의 사실로 궁금증을 만든다. 막연한 과장("대박", "엄청난")으로 낚지 않는다.
- 짧은 문장과 조금 긴 문장을 섞어 리듬을 만든다. 같은 어미를 세 문장 연속 쓰지 않는다 (합니다/해요/~죠 를 섞는다).
- '똑', '쫙' 같은 소리·움직임 표현과 구체적 동사를 쓰고, "~라는데요", "끝이에요", "~했어요" 같은 밋밋한 어미를 반복하지 않는다.
- 초등학생 작문이나 제품 설명서처럼 들리는 문장(나열식 "위생, 안전, 편리함까지." 등)은 쓰지 않는다.
- 숫자는 아라비아 숫자로 쓴다 (예: 99.9%, 3초).
- 영상에서 확인할 수 없는 사실(매출, 실명, 국적, 사건 결과 등)을 사실처럼 단정하지 않는다. 화면 속 자료를 인용할 때는 출처를 밝힌다 ("제조사 자료를 보면").
- 마지막은 여운이나 시청자에게 던지는 질문으로 끝내 댓글을 유도한다.
- 다 쓴 뒤 처음부터 소리 내어 읽는다고 생각하고, 어색하거나 유치한 문장을 고쳐서 최종본만 낸다.

나쁜 예: "면봉 하나로 수백억을 벌었다고요? 끝이 갈색인 이 소독 면봉이죠. 위생, 안전, 편리함까지."
좋은 예: "꺾기만 하면 소독이 끝나는 면봉, 보신 적 있으세요? 한쪽 끝을 똑 꺾는 순간, 반대편 솜으로 약이 쫙 스며듭니다."

컷 편집 원칙
- 원본을 통째로 쓰지 않는다. 이야기에 필요한 장면만 골라 구간(start~end, 초)으로 지정한다.
- 각 구간의 길이는 그 구간 나레이션을 읽는 시간과 비슷하게 잡는다 (한국어 TTS는 약 초당 {cps}글자).
- 구간은 대체로 시간 순서대로 배치하고, 같은 장면을 반복하지 않는다.
- zoom 은 1.0~1.5 (1.0=확대 없음). 주인공이 작게 보이면 확대한다. 구간마다 조금씩 다르게 준다.
- focus_x, focus_y 는 0~1 사이로, 확대할 때 화면의 어느 부분을 중심으로 볼지다 (0.5,0.5=정중앙).
- speed 는 0.8~1.3. 긴장되는 순간은 느리게, 지루한 이동 장면은 빠르게.

메타 정보
- hook_line1, hook_line2: 화면 위에 계속 떠 있을 두 줄 문구. 각 12자 이내, 궁금증을 자극할 것.
- title: 40자 이내 유튜브 쇼츠 제목. description: 2~3문장 설명. hashtags: 3~5개 (#포함).
"""


def plan_with_claude(src_info, frames, transcript, preset):
    import anthropic

    cps = preset["chars_per_second"]
    target = preset["target_seconds"]
    style = preset.get("script_style", "")
    intro = (
        f"원본 길이: {src_info['dur']:.1f}초, 해상도 {src_info['w']}x{src_info['h']}\n"
        f"목표 쇼츠 길이: 약 {target}초 → 나레이션 전체 약 {int(target * cps)}글자, "
        f"구간 {preset['min_segments']}~{preset['max_segments']}개\n"
        f"채널 주제/말투 요청: {style or '없음'}\n\n"
        f"원본 음성 받아쓰기:\n{transcript or '(말소리 없음 또는 받아쓰기 안 함)'}\n\n"
        "아래는 원본에서 고르게 뽑은 장면 캡처들이다. 각 캡처 앞의 숫자가 원본에서의 시간(초)이다."
    )
    content = [{"type": "text", "text": intro}]
    for t, f in frames:
        content.append({"type": "text", "text": f"[{t:.1f}초]"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.standard_b64encode(f.read_bytes()).decode()}})
    content.append({"type": "text", "text": "이 원본으로 쇼츠 대본과 컷 편집 계획을 만들어줘."})

    client = anthropic.Anthropic()
    resp = client.beta.messages.create(
        model=preset.get("claude_model", "claude-opus-5-5"),
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        thinking={"type": "adaptive"},
        output_config={"effort": preset.get("claude_effort", "high"),
                       "format": {"type": "json_schema", "schema": PLAN_SCHEMA}},
        system=SYSTEM_PROMPT.replace("{cps}", str(cps)),
        messages=[{"role": "user", "content": content}],
    )
    if resp.stop_reason == "refusal":
        sys.exit("Claude 가 이 영상으로는 대본 작성을 거절했어요. 다른 원본을 써 주세요.")
    if resp.stop_reason == "max_tokens":
        sys.exit("대본 생성이 중간에 끊겼어요. 다시 실행해 주세요.")
    text = "".join(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


def mock_plan(src_info, preset):
    dur = src_info["dur"]
    lines = [
        "이 영상, 처음엔 평범해 보이지만 끝까지 보셔야 해요.",
        "지금 화면 속 주인공은 아무것도 모른 채 하루를 시작하고 있어요.",
        "그런데 바로 그때, 예상하지 못한 일이 벌어집니다.",
        "모두가 숨죽이고 지켜보는 순간이었죠.",
        "여러분이라면 이 상황에서 어떻게 하셨을 것 같나요?",
    ]
    n = len(lines)
    segs = []
    for i, t in enumerate(lines):
        s = dur * i / n
        segs.append({"narration": t, "start": s, "end": s + dur / n,
                     "zoom": 1.0 + 0.1 * (i % 3), "focus_x": 0.5, "focus_y": 0.5, "speed": 1.0})
    return {"title": "테스트 쇼츠 제목", "description": "모의 실행으로 만든 테스트 영상입니다.",
            "hashtags": ["#쇼츠", "#테스트"], "hook_line1": "끝까지 보면", "hook_line2": "소름 돋는 영상",
            "segments": segs}


def sanitize_plan(plan, dur):
    def clamp(v, lo, hi, d):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return d
        return max(lo, min(hi, v))
    segs = []
    for s in plan.get("segments", []):
        text = (s.get("narration") or "").strip()
        if not text:
            continue
        st = clamp(s.get("start"), 0, max(0, dur - 0.5), 0)
        segs.append({
            "narration": text,
            "start": st,
            "end": clamp(s.get("end"), st + 0.3, dur, min(dur, st + 3)),
            "zoom": clamp(s.get("zoom"), 1.0, 1.6, 1.0),
            "focus_x": clamp(s.get("focus_x"), 0, 1, 0.5),
            "focus_y": clamp(s.get("focus_y"), 0, 1, 0.5),
            "speed": clamp(s.get("speed"), 0.75, 1.35, 1.0),
        })
    if not segs:
        sys.exit("대본이 비어 있어요. 다시 실행해 주세요.")
    plan["segments"] = segs
    return plan


def review_script(plan, workdir):
    """대본을 메모장으로 열어 수정할 수 있게 한다. 한 줄 = 한 구간."""
    f = workdir / "대본_수정하기.txt"
    lines = ["# 한 줄이 한 장면의 나레이션입니다. 문장만 고치고 저장한 뒤 창을 닫으세요.",
             "# 줄 맨 앞의 번호| 는 지우지 마세요. 줄을 통째로 지우면 그 장면이 빠집니다.",
             f"# 상단 문구1| {plan['hook_line1']}",
             f"# 상단 문구2| {plan['hook_line2']}", ""]
    lines += [f"{i + 1}| {s['narration']}" for i, s in enumerate(plan["segments"])]
    lines += ["", f"상단1| {plan['hook_line1']}", f"상단2| {plan['hook_line2']}"]
    f.write_text("\n".join(lines), encoding="utf-8-sig")
    log(f"\n대본 파일: {f}")
    if os.name == "nt":
        os.startfile(f)  # 메모장으로 열기
    input("대본을 확인·수정하고 저장했으면 여기서 Enter 를 누르세요...")
    keep = []
    for line in f.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("#") or "|" not in line:
            continue
        key, text = line.split("|", 1)
        key, text = key.strip(), text.strip()
        if key == "상단1":
            plan["hook_line1"] = text
        elif key == "상단2":
            plan["hook_line2"] = text
        elif key.isdigit() and 1 <= int(key) <= len(plan["segments"]) and text:
            seg = dict(plan["segments"][int(key) - 1])
            seg["narration"] = text
            keep.append(seg)
    if keep:
        plan["segments"] = keep
    return plan


# ───────────────────────── 3. 타입캐스트 나레이션 ─────────────────────────

def typecast_tts(text, out_path, preset):
    import requests
    key = os.environ.get("TYPECAST_API_KEY")
    if not key:
        sys.exit("TYPECAST_API_KEY 가 없어요. shorts_maker/.env 파일에 적어 주세요.")
    voice = preset.get("typecast_voice_id")
    if not voice:
        sys.exit("목소리가 지정되지 않았어요. 목소리목록.bat 으로 voice_id 를 찾아 "
                 "presets/default.json 의 typecast_voice_id 에 넣어 주세요.")
    body = {"voice_id": voice, "text": text, "model": preset.get("typecast_model", "ssfm-v30"),
            "language": "kor", "output": {"audio_format": "wav"}}
    if preset.get("typecast_emotion"):
        body["prompt"] = {"emotion_preset": preset["typecast_emotion"],
                          "emotion_intensity": preset.get("typecast_emotion_intensity", 1.0)}
    for attempt in range(4):
        r = requests.post(TYPECAST_TTS_URL, json=body, timeout=120,
                          headers={"X-API-KEY": key, "Content-Type": "application/json"})
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(3 * (attempt + 1))
            continue
        break
    if r.status_code != 200:
        sys.exit(f"타입캐스트 오류 ({r.status_code}): {r.text[:300]}")
    if "json" in r.headers.get("Content-Type", ""):
        sys.exit(f"타입캐스트가 음성 대신 다른 응답을 보냈어요: {r.text[:300]}")
    out_path.write_bytes(r.content)


def list_voices():
    import requests
    key = os.environ.get("TYPECAST_API_KEY")
    if not key:
        sys.exit("TYPECAST_API_KEY 가 없어요. shorts_maker/.env 파일에 적어 주세요.")
    r = requests.get(TYPECAST_VOICES_URL, headers={"X-API-KEY": key}, timeout=60)
    if r.status_code != 200:
        sys.exit(f"타입캐스트 오류 ({r.status_code}): {r.text[:300]}")
    data = r.json()
    if isinstance(data, dict):
        data = data.get("result") or data.get("voices") or data.get("items") or data.get("data") or []
    log(f"목소리 {len(data)}개\n")
    for v in data:
        if not isinstance(v, dict):
            continue
        vid = v.get("voice_id") or v.get("id")
        name = v.get("voice_name") or v.get("name")
        extra = [str(v.get(k)) for k in ("gender", "age") if v.get(k)]
        models = v.get("models")
        if isinstance(models, list):
            models = ",".join(m.get("version", str(m)) if isinstance(m, dict) else str(m) for m in models)
        log(f"{vid}  {name}  {' '.join(extra)}  {models or ''}")


def make_narration(text, raw_path, out_path, preset, mock=False):
    if mock:
        secs = max(1.0, len(text) / preset["chars_per_second"])
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
             f"sine=frequency=440:duration={secs:.2f}", "-af", "volume=0.05", str(raw_path)])
    else:
        typecast_tts(text, raw_path, preset)
    tempo = float(preset.get("voice_speed", 1.0))
    af = [f"silenceremove=start_periods=1:start_threshold=-50dB",
          "areverse", "silenceremove=start_periods=1:start_threshold=-50dB", "areverse"]
    if abs(tempo - 1.0) > 0.01:
        af.append(f"atempo={tempo}")
    af.append(f"volume={preset.get('voice_volume', 1.0)}")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(raw_path), "-af", ",".join(af),
         "-ar", "44100", "-ac", "2", str(out_path)])
    return float(json.loads(run(["ffprobe", "-v", "error", "-print_format", "json",
                                 "-show_format", str(out_path)]))["format"]["duration"])


# ───────────────────────── 4. 컷 편집 ─────────────────────────

def layout_for(src, preset):
    """'center' = 가운데 영상 + 위 문구/아래 자막, 'fill' = 화면 꽉 채우기."""
    layout = preset.get("layout", "center")
    if layout == "center" and src["h"] / src["w"] > 1.2:  # 세로 원본이면 꽉 채우기
        layout = "fill"
    if layout == "center":
        vh = int(W * src["h"] / src["w"]) // 2 * 2
        vy = int(preset.get("video_center_y", 0.47) * H - vh / 2) // 2 * 2
        vy = max(0, min(H - vh, vy))
        return {"mode": "center", "vh": vh, "vy": vy}
    return {"mode": "fill", "vh": H, "vy": 0}


def crop_box(src, seg, aspect):
    """확대(zoom)·초점(focus)·화면비를 반영한 잘라낼 영역 (원본 픽셀 단위)."""
    iw, ih = src["w"], src["h"]
    cw, ch = iw / seg["zoom"], ih / seg["zoom"]
    if cw / ch > aspect:
        cw = ch * aspect
    else:
        ch = cw / aspect
    cw, ch = int(cw) // 2 * 2, int(ch) // 2 * 2
    x = min(max(0, seg["focus_x"] * iw - cw / 2), iw - cw)
    y = min(max(0, seg["focus_y"] * ih - ch / 2), ih - ch)
    return cw, ch, int(x), int(y)


def render_segment(src_path, src, seg, narr_wav, narr_dur, lay, look, preset, out):
    gap = float(preset.get("gap_seconds", 0.15))
    out_len = narr_dur + gap
    speed = seg["speed"]
    src_len = out_len * speed
    start = seg["start"]
    if start + src_len > src["dur"]:
        start = max(0.0, src["dur"] - src_len)

    eq = (f"eq=brightness={look['brightness']:.3f}:contrast={look['contrast']:.3f}"
          f":saturation={look['saturation']:.3f}")
    flip = ",hflip" if look["mirror"] else ""
    pre = f"[0:v]setpts=(PTS-STARTPTS)/{speed:.3f},fps={FPS}{flip}"

    if lay["mode"] == "center":
        cw, ch, x, y = crop_box(src, seg, src["w"] / src["h"])
        fg = f"crop={cw}:{ch}:{x}:{y},scale={W}:{lay['vh']},{eq},setsar=1"
        if preset.get("background", "blur") == "blur":
            bw, bh, bx, by = crop_box(src, {**seg, "zoom": 1.0}, W / H)
            vf = (f"{pre},split[a][b];"
                  f"[a]crop={bw}:{bh}:{bx}:{by},scale={W // 4}:{H // 4},boxblur=10:2,"
                  f"scale={W}:{H},eq=brightness=-0.25,setsar=1[bg];"
                  f"[b]{fg}[fg];[bg][fg]overlay=0:{lay['vy']}")
        else:
            vf = (f"{pre},{fg}[fg];color=c=black:s={W}x{H}:r={FPS}[bg];"
                  f"[bg][fg]overlay=0:{lay['vy']}:shortest=1")
    else:
        cw, ch, x, y = crop_box(src, seg, W / H)
        vf = f"{pre},crop={cw}:{ch}:{x}:{y},scale={W}:{H},{eq},setsar=1"
    vf += f",tpad=stop_mode=clone:stop_duration={out_len:.2f},format=yuv420p[v]"

    ov = float(preset.get("original_volume", 0.15))
    if src["has_audio"] and ov > 0:
        tempo_chain = []
        t = speed
        while t > 2.0:
            tempo_chain.append("atempo=2.0"); t /= 2.0
        while t < 0.5:
            tempo_chain.append("atempo=0.5"); t /= 0.5
        tempo_chain.append(f"atempo={t:.4f}")
        af = (f"[0:a]asetpts=PTS-STARTPTS,{','.join(tempo_chain)},volume={ov},"
              f"aresample=44100,aformat=channel_layouts=stereo,apad[o];"
              f"[1:a]apad[n];[o][n]amix=inputs=2:duration=longest:normalize=0[a]")
    else:
        af = "[1:a]aresample=44100,apad[a]"

    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{src_len + 0.5:.3f}",
         "-i", str(src_path), "-i", str(narr_wav),
         "-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]",
         "-t", f"{out_len:.3f}", "-r", str(FPS),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", str(out)])
    return out_len


# ───────────────────────── 5. 자막·상단 문구 (ASS) ─────────────────────────

def ass_time(t):
    t = max(0.0, t)
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def ass_color(hex_rgb, alpha=0):
    hex_rgb = hex_rgb.lstrip("#")
    r, g, b = hex_rgb[0:2], hex_rgb[2:4], hex_rgb[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_escape(s):
    return s.replace("\\", "").replace("{", "(").replace("}", ")")


def split_chunks(text, max_chars):
    """자막을 한 화면에 보기 좋은 길이로 자른다 (띄어쓰기 기준)."""
    words, chunks, cur = text.split(), [], ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if len(cand) <= max_chars or not cur:
            cur = cand
        else:
            chunks.append(cur); cur = w
        if re.search(r"[.?!,…]$", cur) and len(cur) >= max_chars * 0.5:
            chunks.append(cur); cur = ""
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(plan, timings, lay, preset, total, path):
    font = preset.get("font_name", "Malgun Gothic")
    sub_size = int(preset.get("subtitle_size", 72))
    hook_size = int(preset.get("hook_size", 84))
    if lay["mode"] == "center":
        sub_y = lay["vy"] + lay["vh"] + int(preset.get("subtitle_gap", 60))
        hook_y = max(80, lay["vy"] - int(hook_size * 2.6) - 40)
    else:
        sub_y = int(H * preset.get("subtitle_y", 0.66))
        hook_y = int(H * preset.get("hook_y", 0.12))
    out_c = ass_color(preset.get("outline_color", "#000000"))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font},{sub_size},{ass_color(preset.get('subtitle_color', '#FFFFFF'))},&H000000FF,{out_c},&H80000000,-1,0,0,0,100,100,0,0,1,{preset.get('subtitle_outline', 7)},2,8,60,60,{sub_y},1
Style: Hook,{font},{hook_size},{ass_color(preset.get('hook_color1', '#FFFFFF'))},&H000000FF,{out_c},&H80000000,-1,0,0,0,100,100,0,0,1,{preset.get('hook_outline', 8)},3,8,40,40,{hook_y},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    h1, h2 = ass_escape(plan["hook_line1"]), ass_escape(plan["hook_line2"])
    c2 = ass_color(preset.get("hook_color2", "#FFE100"))
    if h1 or h2:
        ev.append(f"Dialogue: 1,{ass_time(0)},{ass_time(total)},Hook,,0,0,0,,"
                  f"{h1}\\N{{\\c{c2}}}{h2}")
    pop = preset.get("subtitle_pop", True)
    hl = ass_color(preset.get("subtitle_highlight_color", "#FFE100"))
    for (seg_start, narr_dur), seg in zip(timings, plan["segments"]):
        chunks = split_chunks(seg["narration"], int(preset.get("subtitle_max_chars", 14)))
        total_chars = sum(len(c) for c in chunks) or 1
        t = seg_start
        for i, c in enumerate(chunks):
            d = narr_dur * len(c) / total_chars
            fx = "{\\fscx112\\fscy112\\t(0,120,\\fscx100\\fscy100)}" if pop else ""
            text = ass_escape(c)
            if preset.get("subtitle_highlight_first", True) and i == 0 and seg is plan["segments"][0]:
                text = f"{{\\c{hl}}}{text}"
            ev.append(f"Dialogue: 0,{ass_time(t)},{ass_time(t + d)},Sub,,0,0,0,,{fx}{text}")
            t += d
    path.write_text(header + "\n".join(ev) + "\n", encoding="utf-8")


# ───────────────────────── 6. 합치기·마무리 ─────────────────────────

def finalize(workdir, seg_files, timings, total, preset, final_path):
    lst = workdir / "concat.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in seg_files), encoding="utf-8")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "concat.txt",
         "-c", "copy", "joined.mp4"], cwd=workdir)

    inputs = ["-i", "joined.mp4"]
    vf = "[0:v]ass=subs.ass"
    font_file = preset.get("font_file")
    if font_file and Path(font_file).exists():
        (workdir / "fonts").mkdir(exist_ok=True)
        shutil.copy(font_file, workdir / "fonts")
        vf += ":fontsdir=fonts"
    vf += "[v]"
    mix = ["[0:a]"]
    chains = []
    idx = 1
    bgm = preset.get("bgm_file")
    if bgm and Path(bgm).exists():
        inputs += ["-stream_loop", "-1", "-i", str(Path(bgm).resolve())]
        chains.append(f"[{idx}:a]volume={preset.get('bgm_volume', 0.12)},"
                      f"afade=t=out:st={max(0, total - 1.5):.2f}:d=1.5[bgm]")
        mix.append("[bgm]"); idx += 1
    sfx = preset.get("transition_sfx_file")
    if sfx and Path(sfx).exists() and len(timings) > 1:
        starts = [s for s, _ in timings[1:]]
        inputs += ["-i", str(Path(sfx).resolve())]
        labels = "".join(f"[s{i}]" for i in range(len(starts)))
        chains.append(f"[{idx}:a]volume={preset.get('sfx_volume', 0.5)},asplit={len(starts)}{labels}")
        for i, st in enumerate(starts):
            ms = int(st * 1000)
            chains.append(f"[s{i}]adelay={ms}|{ms}[d{i}]")
            mix.append(f"[d{i}]")
        idx += 1
    if len(mix) > 1:
        chains.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0[a]")
        amap = "[a]"
    else:
        amap = "0:a"
    fc = ";".join([vf] + chains)
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", fc,
         "-map", "[v]", "-map", amap, "-t", f"{total:.3f}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "final.mp4"], cwd=workdir)
    shutil.copy(workdir / "final.mp4", final_path)


# ───────────────────────── 메인 ─────────────────────────

def make_short(src_path, preset, review=False, mock=False):
    src_path = Path(src_path).resolve()
    if not src_path.exists():
        sys.exit(f"파일이 없어요: {src_path}")
    check_ffmpeg()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    workdir = HERE / "작업" / f"{src_path.stem[:30]}_{stamp}"
    workdir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(stamp)

    log(f"\n[1/6] 원본 분석: {src_path.name}")
    src = probe(src_path)
    log(f"  · {src['dur']:.1f}초, {src['w']}x{src['h']}, 소리 {'있음' if src['has_audio'] else '없음'}")
    frames = extract_frames(src_path, src["dur"], int(preset.get("analysis_frames", 40)), workdir / "frames")
    transcript = "" if mock else transcribe_original(src_path, preset)

    log("[2/6] 대본·편집 계획 만드는 중 (Claude)...")
    plan = mock_plan(src, preset) if mock else plan_with_claude(src, frames, transcript, preset)
    plan = sanitize_plan(plan, src["dur"])
    if review or preset.get("always_review"):
        plan = review_script(plan, workdir)
    (workdir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    for i, s in enumerate(plan["segments"], 1):
        log(f"  {i:2d}. [{s['start']:.1f}초~] {s['narration']}")

    log(f"[3/6] 나레이션 녹음 중 ({'모의' if mock else '타입캐스트'})...")
    durs = []
    for i, s in enumerate(plan["segments"]):
        d = make_narration(s["narration"], workdir / f"tts_raw_{i:02d}.wav",
                           workdir / f"narr_{i:02d}.wav", preset, mock)
        durs.append(d)
    log(f"  · 나레이션 총 {sum(durs):.1f}초")

    log("[4/6] 장면 자르고 확대·색감 맞추는 중...")
    lay = layout_for(src, preset)
    jit = float(preset.get("color_variation", 0.04))
    look = {"brightness": preset.get("brightness", 0.02) + rng.uniform(-jit, jit) / 2,
            "contrast": preset.get("contrast", 1.06) + rng.uniform(-jit, jit),
            "saturation": preset.get("saturation", 1.12) + rng.uniform(-jit, jit) * 2,
            "mirror": bool(preset.get("mirror", False))}
    seg_files, timings, t = [], [], 0.0
    for i, s in enumerate(plan["segments"]):
        out = workdir / f"seg_{i:02d}.mp4"
        L = render_segment(src_path, src, s, workdir / f"narr_{i:02d}.wav", durs[i],
                           lay, look, preset, out)
        seg_files.append(out)
        timings.append((t, durs[i]))
        t += L
        log(f"  · 장면 {i + 1}/{len(plan['segments'])} 완료")
    total = t

    log("[5/6] 자막·상단 문구 만드는 중...")
    build_ass(plan, timings, lay, preset, total, workdir / "subs.ass")

    log("[6/6] 합치고 배경음·효과음 넣는 중...")
    outdir = HERE / "완성"
    outdir.mkdir(exist_ok=True)
    name = f"{stamp}_{safe_name(plan['title'])}"
    final = outdir / f"{name}.mp4"
    finalize(workdir, seg_files, timings, total, preset, final)

    meta = (f"[제목]\n{plan['title']}\n\n[설명]\n{plan['description']}\n\n"
            f"{' '.join(plan['hashtags'])}\n\n[대본]\n" +
            "\n".join(s["narration"] for s in plan["segments"]) + "\n")
    (outdir / f"{name}.txt").write_text(meta, encoding="utf-8-sig")
    log(f"\n완성! ({total:.1f}초)\n  영상: {final}\n  제목·설명: {outdir / (name + '.txt')}")
    if total > 60:
        log("  ※ 60초를 넘었어요. 설정에서 target_seconds 를 줄이거나 voice_speed 를 올려 보세요.")
    if not preset.get("keep_work_files", False):
        shutil.rmtree(workdir, ignore_errors=True)
    return final


def main():
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    load_env()
    ap = argparse.ArgumentParser(description="원본 영상 → 이야기 쇼츠 자동 제작")
    ap.add_argument("videos", nargs="*", help="원본 영상 파일 (여러 개 가능)")
    ap.add_argument("--preset", default="default", help="presets 폴더의 설정 이름")
    ap.add_argument("--review", action="store_true", help="대본을 먼저 확인·수정")
    ap.add_argument("--mock", action="store_true", help="API 없이 편집 과정만 테스트")
    ap.add_argument("--list-voices", action="store_true", help="타입캐스트 목소리 목록")
    a = ap.parse_args()

    if a.list_voices:
        list_voices(); return
    if not a.videos:
        ap.print_help(); return
    preset = load_preset(a.preset)
    if not a.mock and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY 가 없어요. shorts_maker/.env 파일에 적어 주세요.")
    for v in a.videos:
        try:
            make_short(v, preset, review=a.review, mock=a.mock)
        except RuntimeError as e:
            log(f"\n오류가 났어요 ({Path(v).name}):\n{e}")


if __name__ == "__main__":
    main()
