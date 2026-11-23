# 🔥 바이럴 쇼핑 아이템 검색기

틱톡 · 인스타그램에서 조회수가 높은 신기한 아이템을 찾아 유튜브 쇼핑 쇼츠 주제로 쓰기 위한 도구입니다.
설치 없이 `index.html` 파일을 브라우저로 열면 바로 쓸 수 있습니다.

## 🏆 인기 랭킹 (매일 자동 갱신)
사이트를 열면 틱톡·인스타 쇼핑 아이템 영상이 **전체 기간 조회수 / 급상승 / 팔로워 대비 / 공유** 순위로 바로 보입니다.
GitHub Actions가 매주 월요일 오전 `config.json`의 해시태그를 수집해 `data/ranking.json`을 갱신합니다.

**최초 1회 설정**
1. https://apify.com 가입 → Settings → API & Integrations → Personal API token 복사
2. 이 저장소 Settings → Secrets and variables → Actions → New repository secret
   - Name: `APIFY_TOKEN` / Secret: 복사한 토큰
3. Actions 탭 → "인기 아이템 자동 수집" → Run workflow (바로 1회 수집)

**유튜브 쇼츠 (무료):** 구글 YouTube Data API 키를 `YOUTUBE_API_KEY` 시크릿으로 등록하면 `config.json`의 `youtubeQueries` 검색어로 역대 조회수 순 쇼츠도 함께 수집합니다. (Apify 크레딧 사용 안 함)

수집할 해시태그·개수는 `config.json`에서 바꿀 수 있어요. (개수를 늘리면 Apify 사용료가 늘어납니다)

## 기능
- **⚡ 빠른 검색 (무료):** 인기 쇼핑 해시태그(#tiktokmademebuyit, #amazonfinds, #신기한물건, #다이소꿀템 등)와 직접 입력한 검색어의 틱톡/인스타 검색 페이지를 클릭 한 번으로 열기
- **🤖 자동 수집:** Apify API로 해시태그별 게시물을 모아서
  - 조회수 / **바이럴 배수(조회수÷팔로워)** / 참여율 / 공유수 / 최신순 정렬
  - 최소 조회수, 최근 N일, 캡션 키워드, 영상만 보기 필터
  - 🔥 바이럴 배지: 팔로워 대비 10배 이상 조회된 영상 (작은 계정에서 터진 아이템 = 아이템 자체의 힘)
  - 🛒 판매 신호 배지: 캡션에 "link in bio", "amazon", "쿠팡", "구매" 등 포함
- **⭐ 저장함:** 마음에 드는 아이템 저장 후 CSV(엑셀) 내보내기

## 자동 수집 설정
1. https://apify.com 무료 가입 (매달 무료 크레딧 제공, 이후 사용량만큼 과금)
2. Console → Settings → API & Integrations → Personal API token 복사
3. 검색기의 ⚙️ 설정 탭에 붙여넣고 저장

사용 Actor: `clockworks/tiktok-scraper`, `apify/instagram-hashtag-scraper` (설정에서 변경 가능)
토큰과 저장 목록은 내 브라우저(localStorage)에만 저장됩니다.

## 🎯 SNS 원본 영상 찾기 (`lens/`, 안드로이드용)
캡처한 사진을 구글 렌즈로 바로 넘겨, 그 장면이 나온 인스타·틱톡·유튜브 원본 영상을 찾습니다. 무료, 키 불필요.

- 스크린샷 → **공유 → SNS검색**, 또는 사진 선택 / 📋 붙여넣기
- **🔍 이 영상 원본 찾기** → Google 렌즈 선택 → 렌즈 검색창에 `instagram` / `tiktok` / `youtube` 추가

**🎬 쇼츠 영상으로 찾기 (v8):** 짜깁기 쇼츠 영상을 그대로 넣으면(선택 또는 공유 → SNS검색) 컷이 바뀌는 지점마다 장면을 나누고, 장면마다 가장 선명한 한 장을 뽑아 위아래 흐린 여백을 잘라 보여줍니다. 장면별로
- **🔍 렌즈**: 폰 공유창 → Google 렌즈
- **🌐 웹**: 구글 렌즈 웹 결과를 새 탭으로 바로 열기 (PC)
- **💾**: 장면 사진 저장

**설정:** 저장소 Settings → Pages → Branch 선택 → Save 후 `https://<아이디>.github.io/<저장소>/lens/` 를 안드로이드 크롬으로 열기 → ⋮ → 앱 설치

## 🔎 쇼츠 원본 자동 찾기 (`finder/`, PC/서버용)
짜깁기 쇼츠 영상 파일을 넣으면 장면을 나눠 **장면마다 이미지 검색**을 하고, 여러 장면에서 반복해서 걸린 유튜브·틱톡 영상을 **원본 후보 + 올린 사람**으로 정리해 줍니다. 키 불필요.

```bash
pip install numpy pillow requests   # ffmpeg 필요
python3 finder/find_original.py 영상1.mp4 영상2.mp4 --out 결과
```
결과 폴더에 장면 사진(`scene-01.jpg`…)과 `report.md`(원본 영상 후보 → 같은 장면이 나온 페이지 → 장면별 결과)가 생깁니다.
