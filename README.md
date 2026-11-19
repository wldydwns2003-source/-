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
