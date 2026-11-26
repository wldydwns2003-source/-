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

**설정:** 저장소 Settings → Pages → Branch 선택 → Save 후 `https://<아이디>.github.io/<저장소>/lens/` 를 안드로이드 크롬으로 열기 → ⋮ → 앱 설치

## 🎬 쇼츠 대본 생성기 (`script/`)
제품 영상을 올리면 기준 쇼츠("다이소에서 꼭 챙겨야 하는 BEST")와 같은 구조·말투로 자막 대본을 만듭니다.

- 초반 훅 고정: `와~ 여러분 / 다이소 가시면 / 이건 꼭 챙기세요` (설정에서 변경 가능)
- 구조: 훅 → 문제·공감 → 권위자 전환 → 신뢰 근거 → 원리→결과 → 킬링 포인트 → 추가 장점 → 감각적 마무리 → 엔드카드
- 영상에서 장면을 뽑아 Claude가 보고 대본 작성 → 줄마다 장면 썸네일·시간 표시, 바로 수정 가능
- **📋 복사** 또는 **⬇️ SRT 다운로드** → 캡컷 텍스트 → 자막 가져오기
- 필요: Claude API 키 (https://console.anthropic.com/settings/keys) — 브라우저에만 저장됨
- 주소: `https://<아이디>.github.io/<저장소>/script/`
