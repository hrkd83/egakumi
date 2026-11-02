import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const sharp = require('sharp');
const W = 1600;
const H = 900;
const PEOPLE = [
  { id: 'mako', name: 'MAKO', role: 'Moderator', detail: '気になることを、一緒に。', cx: 302, skew: -2.1, color: '#79f4f0', depth: 0.96 },
  { id: 'manabu', name: 'MANABU', role: 'Product Manager', detail: '目的・価値', cx: 800, skew: 0, color: '#80f6c4', depth: 1.02 },
  { id: 'ikumi', name: 'IKUMI', role: 'Solution Architect', detail: '仕組みの解説', cx: 1298, skew: 2.1, color: '#8bcaff', depth: 0.96 },
];
const esc = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&apos;');
const clamp = (v, min = 0, max = 1) => Math.min(max, Math.max(min, v));
const ease = value => 1 - Math.pow(1 - clamp(value), 3);
const n = value => Number(value.toFixed(3));
const text = (x, y, value, size, fill = '#e5fbff', weight = 500, attrs = '') => `<text x="${n(x)}" y="${n(y)}" font-size="${n(size)}" fill="${fill}" font-weight="${weight}" ${attrs}>${esc(value)}</text>`;

// Decode once. The original artwork and full-color logos are preserved; PNG
// avoids librsvg's unsupported WebP image URIs. These bounded rasters also keep
// frame-by-frame encoding inexpensive. No per-frame blur/image filter is used.
const images = (async () => {
  const png = async (relative, width) => `data:image/png;base64,${(await sharp(await fs.readFile(path.join(HERE, relative))).resize({ width, withoutEnlargement: true }).png({ compressionLevel: 6 }).toBuffer()).toString('base64')}`;
  return {
    people: await Promise.all(PEOPLE.map(async p => ({ ...p, image: await png(`assets/portraits/${p.id}.webp`, 440) }))),
    symbol: await png('assets/brand/egakumi-symbol.webp', 540),
    wordmark: await png('assets/brand/egakumi-wordmark.webp', 850),
  };
})();

const floor = (time, logo = false) => {
  const horizon = logo ? 576 : 572;
  let out = `<g opacity="${logo ? '.52' : '.44'}"><path d="M0 ${horizon}H1600" stroke="#22687c" stroke-opacity=".28"/>`;
  for (let i = -8; i <= 8; i++) {
    const endX = 800 + i * 190;
    out += `<path d="M800 ${horizon - 34}L${endX} 900" stroke="#2b7188" stroke-width="1" stroke-opacity=".32"/>`;
  }
  for (let i = 0; i < 9; i++) {
    const p = ((i + time * .12) % 9) / 9;
    const y = horizon + 328 * p * p;
    out += `<path d="M0 ${n(y)}H1600" stroke="#4497ad" stroke-opacity="${n(.09 + p * .14)}"/>`;
  }
  return `${out}</g>`;
};

const background = time => {
  let out = `<rect width="1600" height="900" fill="#030710"/>
<rect width="1600" height="900" fill="url(#ambient)"/>
<ellipse cx="800" cy="546" rx="690" ry="290" fill="url(#mist)"/>
<path d="M78 198H1522M78 711H1522" stroke="#80e8ff" stroke-opacity=".075"/>
<path d="M78 198V220M1522 198V220M78 689V711M1522 689V711" stroke="#62d8e8" stroke-opacity=".28"/>`;
  for (let i = 0; i < 38; i++) {
    const x = 55 + ((i * 379 + Math.sin(time * .14 + i) * 15) % 1490);
    const y = 138 + ((i * 131 - time * (5 + i % 4) + 5000) % 510);
    out += `<circle cx="${n(x)}" cy="${n(y)}" r="${i % 3 === 0 ? 1.3 : .7}" fill="#b5faff" opacity="${n(.12 + (Math.sin(time * .55 + i) + 1) * .09)}"/>`;
  }
  return out;
};

/**
 * Code-native 2.5D holographic stage. This animates layers, projected light,
 * depth, and camera-like parallax; it deliberately does not simulate lip sync.
 *
 * Use phase="logo" for the common brand intro, then phase="dialogue" for the
 * episode-specific opening. time is absolute movie seconds. sceneTime can be
 * supplied for the local segment clock; otherwise it is time-logoDuration.
 * The exported SVG is self-contained and can be rendered directly by sharp.
 */
export async function renderHologramFrame({
  time = 0,
  duration = 40,
  speaker = null,
  phase = 'dialogue',
  subtitleLines = [],
  topic = 'Portal・SQL・APIMの役割',
  logoDuration = 4,
  sceneTime,
  outputWidth = W,
  outputHeight = H,
  rasterBase = false,
} = {}) {
  if (!Array.isArray(subtitleLines) || subtitleLines.length > 2) throw new TypeError('subtitleLines must contain at most two lines.');
  if (![outputWidth, outputHeight].every(v => Number.isInteger(v) && v > 0)) throw new TypeError('Output dimensions must be positive integers.');
  const t = Number.isFinite(time) ? Math.max(0, time) : 0;
  const local = Number.isFinite(sceneTime) ? Math.max(0, sceneTime) : Math.max(0, t - logoDuration);
  const assets = await images;
  const activeId = typeof speaker === 'string' ? speaker.toLowerCase() : null;
  const active = PEOPLE.find(p => p.id === activeId);
  const lines = subtitleLines.map(String).map(v => v.trim()).filter(Boolean);
  const isLogo = phase === 'logo';
  let out = `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${outputWidth}" height="${outputHeight}" viewBox="0 0 1600 900" role="img" aria-labelledby="title description">
<title id="title">egakumi 勉強会${isLogo ? ' ロゴオープニング' : `：${esc(topic)}`}</title>
<desc id="description">${isLogo ? 'egakumiの公式ロゴが光とともに現れる。' : 'MAKO、MANABU、IKUMIが暗い空間にホログラムのように投影される。3人の顔、名前、役割と字幕を表示する。'}</desc>
<defs>
<style>text{font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,sans-serif}.latin{font-family:-apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif}</style>
<radialGradient id="ambient" cx="50%" cy="38%" r="70%"><stop stop-color="#102b3c" stop-opacity=".82"/><stop offset=".58" stop-color="#061422" stop-opacity=".65"/><stop offset="1" stop-color="#030710" stop-opacity="0"/></radialGradient>
<radialGradient id="mist"><stop stop-color="#125b71" stop-opacity=".22"/><stop offset="1" stop-color="#083546" stop-opacity="0"/></radialGradient>
<linearGradient id="beam" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#7beeff" stop-opacity="0"/><stop offset=".75" stop-color="#4bdcf4" stop-opacity=".025"/><stop offset="1" stop-color="#8beeff" stop-opacity=".17"/></linearGradient>
<linearGradient id="glass" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#81e8ff" stop-opacity=".055"/><stop offset=".46" stop-color="#0b2335" stop-opacity=".14"/><stop offset="1" stop-color="#6acbd9" stop-opacity=".035"/></linearGradient>
<linearGradient id="caption" x1="0" y1="0" x2="1" y2="0"><stop stop-color="#09202f"/><stop offset=".5" stop-color="#0a1c2b"/><stop offset="1" stop-color="#081725"/></linearGradient>
<linearGradient id="scan" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#b0fbff" stop-opacity="0"/><stop offset=".6" stop-color="#b0fbff" stop-opacity=".12"/><stop offset="1" stop-color="#b0fbff" stop-opacity="0"/></linearGradient>
<pattern id="scanlines" x="0" y="${n((t * 2) % 5)}" width="5" height="5" patternUnits="userSpaceOnUse"><path d="M0 0H5" stroke="#b8faff" stroke-opacity=".055"/></pattern>
</defs>${background(t)}${floor(t, isLogo)}`;

  if (isLogo) {
    const lt = Number.isFinite(sceneTime) ? Math.max(0, sceneTime) : t;
    const reveal = ease((lt - .18) / 1.25);
    const settle = ease(lt / 1.8);
    const logoScale = .89 + settle * .11;
    const size = 344;
    const lx = 800 - size / 2;
    const ly = 190;
    const centerY = ly + size / 2;
    const ringFade = clamp((lt - .1) / .6) * .56;
    out += `<g opacity="${n(ringFade)}"><ellipse cx="800" cy="612" rx="292" ry="49" fill="none" stroke="#6beaff" stroke-opacity=".22" stroke-width="1"/><ellipse cx="800" cy="612" rx="242" ry="39" fill="none" stroke="#71ebff" stroke-opacity=".5"/><path d="M535 612H1065M800 573V651" stroke="#7ce6f4" stroke-opacity=".18"/></g>
<path d="M563 612L670 230H930L1037 612Z" fill="url(#beam)" opacity="${n(reveal)}"/>
<g transform="translate(800 ${centerY}) scale(${n(logoScale)}) translate(-800 -${centerY})">`;
    // Staggered horizontal slices settle into the unmodified official symbol.
    // Draw one image after settling to avoid subpixel seams between slice clips.
    if (lt > 1.3) out += `<image x="${lx}" y="${ly}" width="${size}" height="${size}" xlink:href="${assets.symbol}"/>`;
    else for (let i = 0; i < 12; i++) {
      const p = ease((lt - .12 - i * .038) / .72);
      const y = ly + i * size / 12;
      const dx = (i % 2 ? 1 : -1) * (1 - p) * 82;
      out += `<clipPath id="logo-slice-${i}"><rect x="${lx - 100}" y="${n(y)}" width="${size + 200}" height="${n(size / 12 + .3)}"/></clipPath><g clip-path="url(#logo-slice-${i})" opacity="${n(p)}"><image x="${n(lx + dx)}" y="${ly}" width="${size}" height="${size}" xlink:href="${assets.symbol}"/></g>`;
    }
    out += `</g>`;
    const wordOpacity = ease((lt - .85) / .8);
    out += `<g opacity="${n(wordOpacity)}" transform="translate(0 ${n((1 - wordOpacity) * 13)})"><image x="540" y="525" width="520" height="170" preserveAspectRatio="xMidYMid meet" xlink:href="${assets.wordmark}"/>${text(800, 725, '基盤管理者向け勉強会', 25, '#c2e8ee', 500, 'text-anchor="middle" letter-spacing="3"')}</g>`;
    const sweep = clamp((lt - .22) / 1.65);
    if (sweep < 1 && lt > .22) out += `<rect x="560" y="${n(170 + sweep * 420)}" width="480" height="2" fill="#bcfcff" opacity="${n(.22 * Math.sin(sweep * Math.PI))}"/>`;
    out += `<path d="M${n(640 - reveal * 135)} 777H${n(960 + reveal * 135)}" stroke="#66def2" stroke-opacity="${n(wordOpacity * .28)}"/>`;
    if (lt < .32) out += `<rect width="1600" height="900" fill="#030710" opacity="${n(1 - clamp(lt / .32))}"/>`;
    return `${out}</svg>`;
  }

  // Quiet brand/episode header; the episode topic changes while its type stays.
  out += `<image x="70" y="36" width="58" height="58" xlink:href="${assets.symbol}"/>
<image x="134" y="39" width="188" height="62" preserveAspectRatio="xMidYMid meet" xlink:href="${assets.wordmark}"/>
${text(1528, 70, '基盤管理者向け勉強会', 21, '#8dafbe', 500, 'text-anchor="end"')}
${text(78, 150, topic, 42, '#e8fcff', 700)}
${text(1524, 148, '目的から、仕組みへ。', 22, '#8eb9c8', 500, 'text-anchor="end"')}
<path d="M78 176H1524" stroke="#73d9e8" stroke-opacity=".2"/>`;

  for (let i = 0; i < assets.people.length; i++) {
    const person = assets.people[i];
    const talking = person.id === activeId;
    const hover = Math.sin(local * .73 + i * 1.42) * 3.3;
    const drift = Math.sin(local * .21) * (i - 1) * 3;
    const cx = person.cx + drift;
    const top = 232 + hover + (i === 1 ? -9 : 6);
    const base = 654;
    const opacity = talking ? 1 : .83;
    const pulse = .65 + Math.sin(t * 2.6) * .15;
    const panel = `M${cx - 184} ${top + 10}L${cx + 180} ${top - 3}L${cx + 191} ${base - 23}L${cx - 189} ${base - 12}Z`;
    out += `<g aria-label="${person.name}：${person.role}">
<path d="M${cx - 183} ${base}L${cx - 135} ${top}H${cx + 135}L${cx + 183} ${base}Z" fill="url(#beam)"/>
<ellipse cx="${n(cx)}" cy="${base + 3}" rx="193" ry="30" fill="#0a2b3b" fill-opacity=".42" stroke="${person.color}" stroke-opacity="${talking ? '.58' : '.22'}"/>
<ellipse cx="${n(cx)}" cy="${base + 3}" rx="164" ry="23" fill="none" stroke="${person.color}" stroke-opacity="${talking ? '.7' : '.3'}" stroke-width="${talking ? '1.7' : '1'}"/>
<ellipse cx="${n(cx)}" cy="${base + 3}" rx="140" ry="18" fill="none" stroke="${person.color}" stroke-opacity=".12"/>
<path d="M${cx - 213} ${base + 3}h24m378 0h24" stroke="${person.color}" stroke-opacity=".45"/>
<g transform="translate(${n(cx)} 460) skewY(${person.skew}) scale(${person.depth}) translate(-${n(cx)} -460)" opacity="${opacity}">
<path d="${panel}" fill="url(#glass)" stroke="${person.color}" stroke-opacity="${talking ? '.48' : '.19'}" stroke-width="${talking ? '1.6' : '.9'}"/>
<path d="M${cx - 184} ${top + 38}v-28l31-1M${cx + 149} ${top - 2}l31-1v29M${cx - 188} ${base - 40}v28l31-1M${cx + 190} ${base - 51}v28l-31 1" fill="none" stroke="${person.color}" stroke-width="2" stroke-opacity="${talking ? '.8' : '.42'}"/>
<image x="${n(cx - 167)}" y="${n(top - 10)}" width="334" height="365" preserveAspectRatio="xMidYMid meet" xlink:href="${person.image}"/>
<clipPath id="pane-${person.id}"><path d="${panel}"/></clipPath>
<g clip-path="url(#pane-${person.id})"><rect x="${n(cx - 189)}" y="${n(top)}" width="380" height="410" fill="url(#scanlines)"/>
${rasterBase ? '' : `<rect x="${n(cx - 190)}" y="${n(top + (local * 16 + i * 135) % 380)}" width="380" height="42" fill="url(#scan)"/>`}</g>
</g>
<path d="M${n(cx - 142)} 600H${n(cx + 142)}" stroke="${person.color}" stroke-opacity="${talking ? '.75' : '.28'}"/>
${text(cx, 638, person.name, 34, talking ? '#efffff' : '#c9ecf4', 750, 'class="latin" text-anchor="middle" letter-spacing="3"')}
<rect x="${n(cx - 170)}" y="676" width="340" height="56" rx="7" fill="#071522" fill-opacity=".94" stroke="${person.color}" stroke-opacity=".16"/>
${text(cx, 700, person.role, 20, '#c4eaf1', 600, 'text-anchor="middle"')}
${text(cx, 722, person.detail, 15, '#7ea6b7', 500, 'text-anchor="middle"')}`;
    if (talking && !rasterBase) {
      out += `<circle cx="${n(cx - 165)}" cy="628" r="4" fill="${person.color}" opacity="${n(pulse)}"/>`;
      for (let j = 0; j < 7; j++) {
        const bar = 3 + (Math.sin(t * 5.4 + j * 1.6) + 1) * 4;
        out += `<path d="M${n(cx + 137 + j * 4)} ${n(631 - bar / 2)}v${n(bar)}" stroke="${person.color}" stroke-width="2" stroke-linecap="round" opacity=".72"/>`;
      }
    }
    out += '</g>';
  }

  const longest = Math.max(1, ...lines.map(v => [...v].length));
  const size = Math.min(32, Math.max(24, 1300 / longest));
  const label = active ? `${active.name}  /  ${active.role}` : 'MAKO  /  MANABU  /  IKUMI';
  out += `<path d="M78 761H1522L1530 769V868L1522 876H78L70 868V769Z" fill="url(#caption)" stroke="#61b9cc" stroke-opacity=".3"/>
<path d="M94 761H${active ? 245 : 356}" stroke="${active?.color ?? '#75e3ee'}" stroke-width="2"/>
${text(100, 787, label, 15, '#87d3e0', 650, 'class="latin" letter-spacing=".5"')}`;
  if (lines.length === 2) {
    out += text(800, 823, lines[0], size, '#f0fcff', 600, 'text-anchor="middle"');
    out += text(800, 860, lines[1], size, '#f0fcff', 600, 'text-anchor="middle"');
  } else if (lines.length === 1) {
    out += text(800, 840, lines[0], size, '#f0fcff', 600, 'text-anchor="middle"');
  } else {
    out += text(800, 840, phase === 'outro' ? 'それでは、本編へ。' : topic, 29, '#e5f8fc', 600, 'text-anchor="middle"');
  }
  return `${out}</svg>`;
}

const rasterCache = new Map();

/**
 * Fast PNG renderer for video builds. Slow portrait/font rasterization is shared
 * across eight-frame intervals at 24fps; scanning light and speaker indicators
 * are composited at the caller's full frame rate. Portrait hover moves less than
 * one output pixel per shared interval. The logo reveal is rendered per-frame.
 * Does not mutate files or perform network calls.
 */
export async function renderHologramRaster(options = {}) {
  if (options.phase === 'logo') return sharp(Buffer.from(await renderHologramFrame(options))).png({ compressionLevel: 1 }).toBuffer();
  const time = Math.max(0, options.time ?? 0);
  const logoDuration = options.logoDuration ?? 4;
  const local = Math.max(0, options.sceneTime ?? time - logoDuration);
  const quantum = Math.floor(time * 3) / 3;
  const localQuantum = Math.floor(local * 3) / 3;
  const key = JSON.stringify([quantum, localQuantum, options.speaker, options.phase, options.subtitleLines, options.topic, options.outputWidth, options.outputHeight]);
  let base = rasterCache.get(key);
  if (!base) {
    base = renderHologramFrame({ ...options, time: quantum, sceneTime: localQuantum, rasterBase: true })
      .then(svg => sharp(Buffer.from(svg)).png({ compressionLevel: 1 }).toBuffer());
    rasterCache.set(key, base);
    if (rasterCache.size > 180) rasterCache.delete(rasterCache.keys().next().value);
  }
  const width = options.outputWidth ?? W, height = options.outputHeight ?? H;
  const activeId = String(options.speaker ?? '').toLowerCase();
  let overlay = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 1600 900"><defs><linearGradient id="s" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#b0fbff" stop-opacity="0"/><stop offset=".6" stop-color="#b0fbff" stop-opacity=".12"/><stop offset="1" stop-color="#b0fbff" stop-opacity="0"/></linearGradient></defs>`;
  PEOPLE.forEach((p, i) => {
    const cx = p.cx + Math.sin(localQuantum * .21) * (i - 1) * 3;
    const top = 232 + Math.sin(localQuantum * .73 + i * 1.42) * 3.3 + (i === 1 ? -9 : 6);
    overlay += `<clipPath id="p${i}"><path d="M${cx - 184} ${top + 10}L${cx + 180} ${top - 3}L${cx + 191} 631L${cx - 189} 642Z"/></clipPath><g transform="translate(${n(cx)} 460) skewY(${p.skew}) scale(${p.depth}) translate(-${n(cx)} -460)" clip-path="url(#p${i})"><rect x="${n(cx - 190)}" y="${n(top + (local * 16 + i * 135) % 380)}" width="380" height="42" fill="url(#s)"/></g>`;
    if (p.id === activeId) {
      overlay += `<circle cx="${n(cx - 165)}" cy="628" r="4" fill="${p.color}" opacity="${n(.65 + Math.sin(time * 2.6) * .15)}"/>`;
      for (let j = 0; j < 7; j++) {
        const bar = 3 + (Math.sin(time * 5.4 + j * 1.6) + 1) * 4;
        overlay += `<path d="M${n(cx + 137 + j * 4)} ${n(631 - bar / 2)}v${n(bar)}" stroke="${p.color}" stroke-width="2" stroke-linecap="round" opacity=".72"/>`;
      }
    }
  });
  overlay += '</svg>';
  return sharp(await base).composite([{ input: Buffer.from(overlay) }]).png({ compressionLevel: 1 }).toBuffer();
}
