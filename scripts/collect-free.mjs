// 토큰 없이: 틱톡 크리에이티브 센터(틱톡 공식 무료 트렌드 페이지)를 일반 방문자처럼 열고,
// 그 페이지가 화면에 표시하려고 받아온 데이터만 읽어서 저장한다. (직접 API를 호출하거나 인증값을 재사용하지 않음)
import { chromium } from 'playwright';
import { writeFile } from 'node:fs/promises';

const BASE = 'https://ads.tiktok.com/business/creativecenter';
const PAGES = [
  ['videos', `${BASE}/inspiration/popular/pc/en`],
  ['hashtags', `${BASE}/inspiration/popular/hashtag/pc/en`],
  ['products', `${BASE}/top-products/pc/en`],
];

const firstArray = (o, depth = 0) => {
  if (!o || typeof o !== 'object' || depth > 3) return null;
  if (Array.isArray(o)) return o.length && typeof o[0] === 'object' ? o : null;
  for (const k of ['list', 'videos', 'materials', 'data']) { const a = firstArray(o[k], depth + 1); if (a) return a; }
  for (const v of Object.values(o)) { const a = firstArray(v, depth + 1); if (a) return a; }
  return null;
};
const label = (v) => v == null ? '' : typeof v === 'object' ? (v.value || v.label || v.name || '') : String(v);
const num = (...v) => { for (const x of v) { const n = Number(x); if (x != null && x !== '' && !isNaN(n)) return n; } return null; };

const norm = {
  videos: (d, rank) => {
    const id = String(d.item_id || d.id || d.vid || '');
    return {
      id: 'cc:' + id, rank, platform: 'tiktok', country: d.country_code || d.region || '',
      url: d.item_url || (id ? `https://www.tiktok.com/@tiktok/video/${id}` : ''),
      caption: (d.title || d.desc || '').slice(0, 300), thumb: d.cover || d.cover_url || '',
      duration: num(d.duration), views: num(d.vv, d.play_count, d.video_views), likes: num(d.like, d.digg_count),
    };
  },
  hashtags: (d, rank) => ({
    id: 'ht:' + (d.hashtag_id || d.hashtag_name), rank: num(d.rank) || rank, country: d.country_info ? label(d.country_info) : '',
    name: d.hashtag_name || d.name || '', views: num(d.video_views, d.vv), posts: num(d.publish_cnt, d.post),
    industry: label(d.industry_info), isNew: !!(d.is_new || d.new_on_board), rankDiff: num(d.rank_diff),
  }),
  products: (d, rank) => ({
    id: 'pd:' + (d.id || d.url_title || label(d.third_ecom_category) || rank), rank,
    name: d.url_title || label(d.third_ecom_category) || label(d.second_ecom_category) || d.title || '',
    category: [label(d.first_ecom_category), label(d.second_ecom_category)].filter(Boolean).join(' › '),
    posts: num(d.post, d.post_count), postChange: num(d.post_change), likes: num(d.like), shares: num(d.share), comments: num(d.comment),
    impressions: num(d.impression), ctr: num(d.ctr), cvr: num(d.cvr), cost: num(d.cost),
  }),
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ locale: 'en-US', viewport: { width: 1366, height: 900 } });
const page = await ctx.newPage();
const out = { videos: [], hashtags: [], products: [] };
const debug = { at: new Date().toISOString(), pages: {} };

for (const [kind, url] of PAGES) {
  // 이 페이지가 스스로 받아온 응답 중 목록 데이터를 기록
  const got = [];
  const onRes = async (res) => {
    if (!res.url().includes('/creative_radar_api/')) return;
    try { const arr = firstArray((await res.json())?.data); if (arr) got.push({ url: res.url(), arr }); } catch {}
  };
  page.on('response', onRes);
  try {
    await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(4000);
  } catch (e) { debug.pages[kind] = { error: e.message }; }
  page.off('response', onRes);

  // 가장 큰 목록을 이 페이지의 순위 데이터로 사용
  const best = got.sort((a, b) => b.arr.length - a.arr.length)[0];
  debug.pages[kind] = { ...debug.pages[kind], responses: got.map(g => `${new URL(g.url).pathname} n=${g.arr.length}`), sample: best?.arr[0] };
  if (best) out[kind] = best.arr.map((d, i) => norm[kind](d, i + 1)).filter(x => x.id && !x.id.endsWith(':'));
  console.log(`${kind}: ${out[kind].length}개`);
}
await browser.close();

await writeFile(new URL('../data/free-debug.json', import.meta.url), JSON.stringify(debug, null, 1));
const total = out.videos.length + out.hashtags.length + out.products.length;
if (!total) { console.error('수집된 데이터가 없습니다. data/free-debug.json 확인'); process.exit(1); }
await writeFile(new URL('../data/free.json', import.meta.url), JSON.stringify({ updatedAt: new Date().toISOString(), ...out }));
