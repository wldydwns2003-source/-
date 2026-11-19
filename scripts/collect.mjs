// 매일 GitHub Actions에서 실행: 틱톡/인스타 해시태그 게시물을 수집해 data/ranking.json 갱신
import { readFile, writeFile } from 'node:fs/promises';

const token = process.env.APIFY_TOKEN;
if (!token) { console.error('APIFY_TOKEN 시크릿이 없습니다.'); process.exit(1); }
const cfg = JSON.parse(await readFile(new URL('../config.json', import.meta.url), 'utf8'));
const OUT = new URL('../data/ranking.json', import.meta.url);

// maxItems / maxTotalChargeUsd: 결과 개수·비용 상한 (무료 크레딧 초과 방지)
async function runActor(actor, input, maxItems, maxUsd) {
  let url = `https://api.apify.com/v2/acts/${actor.replace('/', '~')}/run-sync-get-dataset-items?token=${token}&timeout=290&maxItems=${maxItems}`;
  if (maxUsd) url += `&maxTotalChargeUsd=${maxUsd}`;
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) });
  if (!res.ok) throw new Error(`${actor}: ${res.status} ${(await res.text()).slice(0, 300)}`);
  return (await res.json()).filter(d => !d.error);
}
const num = (...v) => { for (const x of v) { const n = Number(x); if (x != null && !isNaN(n)) return n; } return 0; };
const shop = (t) => { t = (t || '').toLowerCase(); return cfg.shopWords.some(w => t.includes(w.toLowerCase())); };

const tt = (d) => {
  const a = d.authorMeta || {}, v = d.videoMeta || {};
  return {
    id: 'tt:' + d.id, platform: 'tiktok', tag: d.searchHashtag?.name || '',
    url: d.webVideoUrl || `https://www.tiktok.com/@${a.name}/video/${d.id}`,
    caption: (d.text || '').slice(0, 300), author: a.name || '', followers: num(a.fans),
    views: num(d.playCount), likes: num(d.diggCount), comments: num(d.commentCount), shares: num(d.shareCount), saves: num(d.collectCount),
    date: d.createTimeISO || (d.createTime ? new Date(d.createTime * 1000).toISOString() : ''),
    thumb: v.coverUrl || v.originalCoverUrl || '',
  };
};
const ig = (d) => ({
  id: 'ig:' + (d.shortCode || d.id), platform: 'instagram', tag: (d.inputUrl || '').split('/tags/')[1]?.replace(/\/.*/, '') || '',
  url: d.url || `https://www.instagram.com/p/${d.shortCode}/`,
  caption: (d.caption || '').slice(0, 300), author: d.ownerUsername || '', followers: num(d.ownerFollowersCount),
  views: num(d.videoPlayCount, d.videoViewCount), likes: Math.max(0, num(d.likesCount)), comments: num(d.commentsCount), shares: 0, saves: 0,
  date: d.timestamp || '', thumb: d.displayUrl || '',
});

const jobs = [];
if (cfg.tiktokHashtags?.length) jobs.push(runActor('clockworks/tiktok-scraper', {
  hashtags: cfg.tiktokHashtags, resultsPerPage: cfg.tiktokPerHashtag,
  shouldDownloadVideos: false, shouldDownloadCovers: false, shouldDownloadSubtitles: false, shouldDownloadSlideshowImages: false,
}, cfg.tiktokHashtags.length * cfg.tiktokPerHashtag, cfg.tiktokMaxUsd).then(r => r.map(tt)));
if (cfg.instagramHashtags?.length) jobs.push(runActor('apify/instagram-hashtag-scraper', {
  hashtags: cfg.instagramHashtags, resultsLimit: cfg.instagramPerHashtag,
}, cfg.instagramHashtags.length * cfg.instagramPerHashtag, cfg.instagramMaxUsd).then(r => r.filter(d => d.type === 'Video' || d.videoPlayCount != null || d.videoViewCount != null).map(ig)));

const settled = await Promise.allSettled(jobs);
const fresh = [];
for (const s of settled) s.status === 'fulfilled' ? fresh.push(...s.value) : console.error('수집 실패:', s.reason.message);
if (!fresh.length) { console.error('수집된 게시물이 없습니다.'); process.exit(1); }

let prev = { items: [] };
try { prev = JSON.parse(await readFile(OUT, 'utf8')); } catch {}
const map = new Map(prev.items.map(i => [i.id, i]));
const now = new Date().toISOString();
for (const n of fresh) {
  if (!n.views) continue;
  const old = map.get(n.id);
  // 급상승 계산용: 직전 수집 때 조회수를 보관
  n.prevViews = old ? old.views : null;
  n.prevAt = old ? old.updatedAt : null;
  n.firstSeen = old?.firstSeen || now;
  n.updatedAt = now;
  n.thumb = n.thumb || old?.thumb || '';
  n.shop = shop(n.caption);
  map.set(n.id, n);
}
const cutoff = cfg.keepDays > 0 ? Date.now() - cfg.keepDays * 864e5 : 0; // 0 = 기간 제한 없음 (예전 대박 영상도 보관)
const items = [...map.values()]
  .filter(i => !cutoff || !i.date || new Date(i.date) >= cutoff)
  .sort((a, b) => b.views - a.views)
  .slice(0, cfg.maxItems);
await writeFile(OUT, JSON.stringify({ updatedAt: now, count: items.length, items }, null, 0));
console.log(`수집 ${fresh.length}개 → 랭킹 데이터 ${items.length}개 저장`);
