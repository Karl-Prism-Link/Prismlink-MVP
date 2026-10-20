#!/usr/bin/env node
// Renders the PRISM LINK promo to MP4 frame-by-frame (no browser needed).
//
//   npm i @resvg/resvg-js ffmpeg-static
//   curl -L -o inter.zip https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip
//   unzip -j inter.zip 'extras/ttf/Inter-*.ttf' -d fonts
//   node render-promo.mjs --cut 20 --out promo-20s.mp4
//
// Each frame is drawn as SVG, rasterised with resvg, and piped into ffmpeg.

import { Resvg } from '@resvg/resvg-js';
import ffmpegPath from 'ffmpeg-static';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const W = 1920, H = 1080, FPS = 30;
const FONT_DIR = process.env.FONT_DIR || path.join(process.cwd(), 'fonts');

const C = {
  navy: '#09283E', navyDeep: '#05161F', teal: '#087F98', cyan: '#00BACC',
  mint: '#168565', ink: '#19364A', mute: '#7E909D', line: '#E5EFF3',
  paper: '#F7FAFC', mist: '#EAF9FB', white: '#FFFFFF',
};

/* ---------- timing helpers ---------- */
const clamp = (v, a = 0, b = 1) => (v < a ? a : v > b ? b : v);
const E = {
  out: p => 1 - Math.pow(1 - p, 3),
  out5: p => 1 - Math.pow(1 - p, 5),
  inOut: p => (p < .5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2),
  back: p => { const c = 2.1; return 1 + (c + 1) * Math.pow(p - 1, 3) + c * Math.pow(p - 1, 2); },
};
const prog = (t, t0, d = .6, e = E.out) => e(clamp((t - t0) / d));
/* fade + rise, returns { o, y } */
const rise = (t, t0, dist = 22, d = .65, e = E.out) => {
  const p = prog(t, t0, d, e);
  return { o: p, y: (1 - p) * dist };
};

/* ---------- svg primitives ---------- */
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const g = (attrs, inner) => `<g ${attrs}>${inner}</g>`;

function txt(x, y, s, o = {}) {
  const { size = 32, weight = 300, fill = '#fff', anchor = 'start', ls = 0, op = 1 } = o;
  const lines = String(s).split('\n');
  const body = lines
    .map((ln, i) => `<tspan x="${x}" dy="${i === 0 ? 0 : size * (o.lh || 1.08)}">${esc(ln)}</tspan>`)
    .join('');
  return `<text x="${x}" y="${y}" font-family="Inter" font-size="${size}" font-weight="${weight}"
    fill="${fill}" text-anchor="${anchor}" letter-spacing="${ls}" opacity="${op}"
    xml:space="preserve">${body}</text>`;
}

const rect = (x, y, w, h, o = {}) => {
  const { fill = '#fff', r = 0, op = 1, stroke = 'none', sw = 0 } = o;
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}"
    opacity="${op}" stroke="${stroke}" stroke-width="${sw}"/>`;
};

const roundLeft = (x, y, w, h, r) => `<path d="M${x + r} ${y} H${x + w} V${y + h} H${x + r}
  A${r} ${r} 0 0 1 ${x} ${y + h - r} V${y + r} A${r} ${r} 0 0 1 ${x + r} ${y} Z" fill="url(#callSide)"/>`;

const circle = (cx, cy, r, o = {}) => {
  const { fill = 'none', stroke = 'none', sw = 2, op = 1 } = o;
  return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${fill}" stroke="${stroke}"
    stroke-width="${sw}" opacity="${op}"/>`;
};

/* ---------- shared pieces ---------- */
function glyph(x, y, size) {
  const s = size / 86;
  const tri = (tx, ty, sc, fill, op) =>
    `<g transform="translate(${x + 43 + tx * s} ${y + 43 + ty * s}) scale(${sc * s})">
       <path d="M0 -43 L43 35 L-43 35 Z" fill="${fill}" opacity="${op}"/></g>`;
  return tri(-13, 0, .86, C.cyan, .9) + tri(0, 6, .8, C.teal, 1) + tri(13, 0, .86, '#DFF9FF', .92);
}

/* PRISM (bold) + LINK (light) with the prism glyph, centred on cx at baseline y */
function lockup(cx, y, scale = 1) {
  const wP = 334 * scale, wL = 238 * scale, gap = 58 * scale, gl = 96 * scale;
  const total = gl + 22 * scale + wP + gap + wL;
  const left = cx - total / 2;
  const gx = left, wx = left + gl + 22 * scale;
  return `<g>
    ${glyph(gx, y - 78 * scale, 86 * scale)}
    ${txt(wx, y, 'PRISM', { size: 92 * scale, weight: 700, fill: '#fff', ls: 12 * scale })}
    ${txt(wx + wP + gap, y, 'LINK', { size: 92 * scale, weight: 250, fill: '#A9DBE7', ls: 12 * scale })}
  </g>`;
}

/* ---------- defs ---------- */
const defs = () => `<defs>
  <radialGradient id="warm" cx="22%" cy="16%" r="85%">
    <stop offset="0" stop-color="#3B2B21"/><stop offset=".45" stop-color="#1D1611"/>
    <stop offset="1" stop-color="#100C09"/></radialGradient>
  <radialGradient id="navyBg" cx="50%" cy="46%" r="70%">
    <stop offset="0" stop-color="#10455A"/><stop offset=".58" stop-color="#09283E"/>
    <stop offset="1" stop-color="#05161F"/></radialGradient>
  <radialGradient id="endBg" cx="50%" cy="42%" r="66%">
    <stop offset="0" stop-color="#10455A"/><stop offset=".6" stop-color="#09283E"/>
    <stop offset="1" stop-color="#04131C"/></radialGradient>
  <linearGradient id="streak" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#00BACC" stop-opacity="0"/>
    <stop offset=".22" stop-color="#00BACC" stop-opacity=".25"/>
    <stop offset=".5" stop-color="#8AF1FF" stop-opacity=".8"/>
    <stop offset=".78" stop-color="#00BACC" stop-opacity=".25"/>
    <stop offset="1" stop-color="#00BACC" stop-opacity="0"/></linearGradient>
  <linearGradient id="barGrad" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#8AF1FF"/><stop offset="1" stop-color="#087F98"/></linearGradient>
  <linearGradient id="phoneBody" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#3A332D"/><stop offset=".6" stop-color="#15110E"/>
    <stop offset="1" stop-color="#0B0908"/></linearGradient>
  <linearGradient id="screenBg" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#0E2230"/><stop offset="1" stop-color="#07161F"/></linearGradient>
  <linearGradient id="callSide" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#0B2B3A"/><stop offset="1" stop-color="#0F4356"/></linearGradient>
  <linearGradient id="usBubble" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#087F98"/><stop offset="1" stop-color="#0AA7B8"/></linearGradient>
  <linearGradient id="pageLight" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#EEF7FA"/><stop offset=".55" stop-color="#F7FBFD"/>
    <stop offset="1" stop-color="#E6F4F8"/></linearGradient>
  <linearGradient id="dashBg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#F2F8FA"/><stop offset="1" stop-color="#E9F3F7"/></linearGradient>
  <filter id="blur26" x="-20%" y="-200%" width="140%" height="500%">
    <feGaussianBlur stdDeviation="26"/></filter>
  <filter id="soft" x="-30%" y="-30%" width="160%" height="160%">
    <feGaussianBlur stdDeviation="10"/></filter>
  <clipPath id="screenClip"><rect x="1414" y="204" width="272" height="592" rx="40"/></clipPath>
  <clipPath id="cardClip"><rect x="86" y="86" width="1748" height="908" rx="34"/></clipPath>
  <clipPath id="dashClip"><rect x="120" y="84" width="1680" height="912" rx="26"/></clipPath>
</defs>`;

/* ---------- scene A · 0.0–3.2 the missed call ---------- */
function sceneA(t, start, dur) {
  const k = dur / 3.2, tt = x => start + x * k;
  let s = '';

  const pK = rise(t, tt(.10));
  s += g(`opacity="${pK.o}" transform="translate(0 ${pK.y})"`,
    txt(130, 262, '3:47 PM  \u00b7  SATURDAY', { size: 24, weight: 600, fill: '#C79A72', ls: 7 }));

  const pH = rise(t, tt(.26), 26);
  s += g(`opacity="${pH.o}" transform="translate(0 ${pH.y})"`,
    txt(126, 430, 'Every chair', { size: 132, weight: 300 }) +
    txt(126, 560, 'is full.', { size: 132, weight: 300 }));

  const pS = rise(t, tt(.62));
  s += g(`opacity="${pS.o}" transform="translate(0 ${pS.y})"`,
    txt(130, 700, 'One phone is ringing.', { size: 40, weight: 300, fill: '#E7D8CB' }) +
    txt(130, 758, 'No one can reach it.', { size: 40, weight: 300, fill: '#E07A5F' }));

  /* phone */
  const pP = prog(t, tt(.2), .8);
  let ph = '';
  ph += rect(1400, 190, 300, 620, { fill: 'url(#phoneBody)', r: 52 });
  ph += rect(1414, 204, 272, 592, { fill: 'url(#screenBg)', r: 40 });
  ph += `<g clip-path="url(#screenClip)">`;
  for (let i = 0; i < 3; i++) {
    const cycle = 1.5 * k, ph2 = ((t - start) / cycle + i / 3) % 1;
    const r = 34 + ph2 * 120;
    ph += circle(1550, 470, r, { stroke: '#6FE4F2', sw: 2, op: (1 - ph2) * .7 });
  }
  ph += circle(1550, 452, 44, { fill: '#124D60', op: .95 });
  ph += circle(1550, 452, 44, { stroke: '#6FE4F2', sw: 1.5, op: .5 });
  ph += txt(1550, 543, 'INCOMING CALL', { size: 12, weight: 600, fill: '#6FA6B8', ls: 3, anchor: 'middle' });
  ph += txt(1550, 578, 'Jordan M.', { size: 24, weight: 300, fill: '#DFF6FB', anchor: 'middle' });
  ph += `</g>`;
  s += g(`opacity="${pP}" transform="translate(0 ${(1 - pP) * 34})"`, ph);

  /* missed call stamp */
  const pM = prog(t, tt(2.15), .4, E.back);
  if (pM > 0.001) {
    const sc = 1.65 - .65 * pM;
    s += `<g opacity="${clamp(pM * 1.6)}" transform="translate(1550 700) rotate(-6) scale(${sc})">
      ${rect(-128, -34, 256, 68, { fill: '#C8432F', r: 10 })}
      ${txt(0, 9, 'MISSED CALL', { size: 20, weight: 800, fill: '#fff', anchor: 'middle', ls: 5 })}
    </g>`;
  }
  return s;
}

/* ---------- scene B · 3.2–6.4 prism link answers ---------- */
function sceneB(t, start, dur) {
  const k = dur / 3.2, tt = x => start + x * k;
  let s = '';
  const fl = prog(t, tt(.1), 1.8, E.out);
  s += `<g transform="rotate(-9 960 540)" opacity="${fl * .95}">
    <rect x="-500" y="428" width="2900" height="220" fill="url(#streak)" filter="url(#blur26)"/></g>`;

  const pL = prog(t, tt(.15), .8, E.back);
  s += `<g opacity="${prog(t, tt(.15), .35)}" transform="translate(960 500) scale(${.94 + .06 * pL}) translate(-960 -500)">
    ${lockup(960, 500, 1)}</g>`;

  let bars = '';
  const n = 26;
  for (let i = 0; i < n; i++) {
    const h = 14 + 66 * Math.abs(Math.sin((t - start) * 3.1 + i * .44)) * (.55 + .45 * Math.sin(i * .7));
    bars += rect(960 + (i - (n - 1) / 2) * 15 - 4, 690 - h / 2, 9, h, { fill: 'url(#barGrad)', r: 5, op: .92 });
  }
  s += g(`opacity="${prog(t, tt(.55), .6)}"`, bars);
  s += g(`opacity="${prog(t, tt(.85), .6)}"`,
    txt(960, 812, 'ANSWERS IN ONE RING', { size: 22, weight: 600, fill: '#BFEAF4', ls: 10, anchor: 'middle' }));
  return s;
}

/* ---------- scene C · booking call ---------- */
function bubble(lines, o) {
  const size = o.size || 27, pad = 24, lh = 1.38, tagH = o.tag ? 26 : 0;
  const arr = Array.isArray(lines) ? lines : [lines];
  const chars = Math.max(...arr.map(l => l.length));
  const w = Math.min(760, chars * size * .525 + pad * 2);
  const h = pad * 2 + tagH + arr.length * size * lh;
  const x = o.align === 'right' ? o.right - w : o.left;
  let s = rect(x, o.y, w, h, { fill: o.fill, r: 22 });
  if (o.tag) s += txt(x + pad, o.y + pad + 12, o.tag.toUpperCase(),
    { size: 12, weight: 700, fill: o.tagColor, ls: 3 });
  s += `<text x="${x + pad}" y="${o.y + pad + tagH + size * .92}" font-family="Inter" font-size="${size}"
    font-weight="300" fill="${o.color}" xml:space="preserve">` +
    arr.map((l, i) => `<tspan x="${x + pad}" dy="${i === 0 ? 0 : size * lh}">${esc(l)}</tspan>`).join('') + `</text>`;
  return { svg: s, w, h };
}

function sceneC(t, start, dur) {
  const k = dur / 5.6, tt = x => start + x * k;
  let s = '';
  s += rect(86, 86, 1748, 908, { fill: '#fff', r: 34 });
  s += roundLeft(86, 86, 600, 908, 34);

  /* left panel */
  const pDot = prog(t, tt(.05), .4);
  s += `<g opacity="${pDot}">${circle(156, 196, 6, { fill: '#2EE6A6' })}` +
    txt(176, 202, 'LIVE CALL \u00b7 00:07', { size: 17, weight: 700, fill: '#8FD7E6', ls: 5 }) + `</g>`;
  s += rect(150, 246, 96, 96, { fill: '#124D60', r: 26 }) +
    txt(198, 310, 'JM', { size: 30, weight: 600, fill: '#9FE6F2', anchor: 'middle' });
  s += txt(272, 292, 'Jordan M.', { size: 32, weight: 600, fill: '#fff' }) +
    txt(272, 332, 'INBOUND \u00b7 +64 21 \u2022\u2022\u2022 \u2022\u2022\u2022', { size: 15, weight: 500, fill: '#8FB8C6', ls: 3 });

  let bars = '';
  for (let i = 0; i < 18; i++) {
    const h = 16 + 74 * Math.abs(Math.sin((t - start) * 4 + i * .55));
    bars += rect(152 + i * 24, 580 - h / 2, 8, h, { fill: '#7FD8E8', r: 4, op: .85 });
  }
  s += g(`opacity="${prog(t, tt(.25), .5)}"`, bars);
  s += txt(150, 930, 'ANSWERED BY PRISM LINK', { size: 15, weight: 600, fill: '#7FA8B6', ls: 5 });

  /* transcript */
  const b1 = bubble('Hi \u2014 any openings this Thursday?',
    { left: 748, y: 186, fill: '#EEF4F7', color: '#19364A', tag: 'Caller', tagColor: '#8BA0AD', size: 27 });
  const b2 = bubble(['Thursday, 2:30 with Amara.', 'Shall I book it?'],
    { right: 1720, y: 336, fill: 'url(#usBubble)', color: '#fff', tag: 'Prism Link', tagColor: '#CDEFF5', align: 'right', size: 27 });
  const b3 = bubble('Yes please.',
    { left: 748, y: 536, fill: '#EEF4F7', color: '#19364A', tag: 'Caller', tagColor: '#8BA0AD', size: 27 });

  s += g(`opacity="${prog(t, tt(.3), .45)}" transform="translate(0 ${(1 - prog(t, tt(.3), .45)) * 18})"`, b1.svg);
  s += g(`opacity="${prog(t, tt(1.65), .45)}" transform="translate(0 ${(1 - prog(t, tt(1.65), .45)) * 18})"`, b2.svg);
  s += g(`opacity="${prog(t, tt(3.05), .45)}" transform="translate(0 ${(1 - prog(t, tt(3.05), .45)) * 18})"`, b3.svg);

  /* booked card */
  const pb = prog(t, tt(4.25), .5, E.back);
  if (pb > .001) {
    const w = 470, h = 92, x = 1720 - w, y = 700, sc = .92 + .08 * pb;
    s += `<g opacity="${clamp(pb * 1.5)}" transform="translate(${x + w / 2} ${y + h / 2}) scale(${sc}) translate(${-x - w / 2} ${-y - h / 2})">
      ${rect(x, y, w, h, { fill: '#EEFAF4', r: 20, stroke: '#C9EEDE', sw: 1 })}
      ${circle(x + 48, y + h / 2, 22, { fill: '#168565' })}
      ${txt(x + 48, y + h / 2 + 8, '\u2713', { size: 22, weight: 800, fill: '#fff', anchor: 'middle' })}
      ${txt(x + 86, y + 38, 'Booked \u00b7 Thu 2:30 PM', { size: 24, weight: 600, fill: '#0F6B4C' })}
      ${txt(x + 86, y + 66, 'Amara \u00b7 Colour \u00b7 45 min', { size: 15, weight: 500, fill: '#4F8D78', ls: 1 })}
    </g>`;
  }
  return s;
}

/* ---------- scene D · dashboard ---------- */
function sceneD(t, start, dur) {
  const k = dur / 4, tt = x => start + x * k;
  let s = '';
  s += rect(120, 84, 1680, 912, { fill: '#fff', r: 26 });
  let nav = `<path d="M146 84 H370 V996 H146 A26 26 0 0 1 120 970 V110 A26 26 0 0 1 146 84 Z" fill="#0B2B3A"/>`;
  nav += txt(156, 152, 'PRISM', { size: 17, weight: 700, fill: '#fff', ls: 4 }) +
    txt(156 + 78, 152, 'LINK', { size: 17, weight: 250, fill: '#7FC9DA', ls: 4 });
  ['Dashboard', 'Calls', 'Bookings', 'Inbox', 'Setup'].forEach((it, i) => {
    const y = 196 + i * 46;
    if (i === 0) nav += rect(144, y, 202, 40, { fill: '#124D60', r: 10 });
    nav += txt(160, y + 26, it, { size: 15, weight: i === 0 ? 600 : 400, fill: i === 0 ? '#DFF6FB' : '#9DC3CF' });
  });
  s += `<g clip-path="url(#dashClip)">${nav}</g>`;

  s += txt(400, 172, 'This week', { size: 26, weight: 600, fill: '#19364A' });
  s += txt(1740, 172, 'MON 12 \u2013 FRI 16', { size: 13, weight: 600, fill: '#7E909D', ls: 4, anchor: 'end' });

  const cols = ['MON 12', 'TUE 13', 'WED 14', 'THU 15', 'FRI 16'];
  const chips = [[['9:00', 'Cut'], ['13:30', 'Colour']], [['10:00', 'Colour']],
                 [['9:30', 'Cut'], ['15:00', 'Style']], [['11:00', 'Cut']], [['8:30', 'Cut'], ['12:00', 'Colour']]];
  const cw = 257, gap = 14, y0 = 212, ch = 330;
  cols.forEach((c, i) => {
    const x = 400 + i * (cw + gap);
    s += rect(x, y0, cw, ch, { fill: '#F6FAFB', r: 14, stroke: '#E5EFF3', sw: 1 });
    s += txt(x + 16, y0 + 30, c, { size: 12, weight: 700, fill: '#8BA0AD', ls: 3 });
    chips[i].forEach((sl, j) => {
      const cy = y0 + 76 + j * 78;
      s += rect(x + 12, cy, cw - 24, 56, { fill: '#EAF3F8', r: 10 });
      s += txt(x + 24, cy + 25, sl[0], { size: 12, weight: 700, fill: '#40789A' });
      s += txt(x + 24, cy + 44, sl[1], { size: 12, weight: 400, fill: '#6C93A8' });
    });
  });

  /* the booking that just landed */
  const pn = prog(t, tt(1.5), .5, E.back);
  if (pn > .001) {
    const x = 400 + 3 * (cw + gap) + 12, y = y0 + 232, w = cw - 24, h = 72, sc = .9 + .1 * pn;
    s += `<g opacity="${clamp(pn * 1.5)}" transform="translate(${x + w / 2} ${y + h / 2}) scale(${sc}) translate(${-x - w / 2} ${-y - h / 2})">
      ${rect(x, y, w, h, { fill: '#E7F7F1', r: 10, stroke: '#BFE9D8', sw: 1 })}
      ${txt(x + 12, y + 28, '2:30 \u00b7 Jordan M.', { size: 12, weight: 700, fill: '#0F6B4C' })}
      ${txt(x + 12, y + 50, 'Colour \u00b7 Amara', { size: 12, weight: 400, fill: '#4F8D78' })}
    </g>`;
  }

  /* summary + inbox */
  s += rect(400, 580, 790, 180, { fill: '#fff', r: 14, stroke: '#E7EFF3', sw: 1 });
  s += txt(424, 614, 'LAST CALL SUMMARY', { size: 11, weight: 700, fill: '#8BA0AD', ls: 4 });
  s += txt(424, 654, '\u201cJordan booked a colour with Amara for Thursday 2:30 PM.', { size: 15, weight: 300, fill: '#5D7484' });
  s += txt(424, 680, 'Asked about parking \u2014 told her to use the rear entrance.\u201d', { size: 15, weight: 300, fill: '#5D7484' });
  s += txt(424, 722, 'CALL \u00b7 2M 14S \u00b7 THU 2:30 PM', { size: 11, weight: 700, fill: '#9FB3BF', ls: 3 });

  s += rect(1206, 580, 534, 180, { fill: '#fff', r: 14, stroke: '#E7EFF3', sw: 1 });
  s += txt(1230, 614, 'INBOX', { size: 11, weight: 700, fill: '#8BA0AD', ls: 4 });
  [['Message \u00b7 Priya', 'New'], ['Rebook \u00b7 Sam T.', 'New'], ['Call \u00b7 Thu 2:30 PM', 'Logged']].forEach((r, i) => {
    const y = 640 + i * 38;
    s += txt(1230, y, r[0], { size: 14, weight: 400, fill: '#5D7484' });
    s += txt(1716, y, r[1], { size: 12, weight: 600, fill: r[1] === 'New' ? '#168565' : '#9FB3BF', anchor: 'end' });
    if (i < 2) s += rect(1230, y + 14, 486, 1, { fill: '#F0F5F7' });
  });
  s += rect(400, 792, 1340, 1, { fill: '#EDF3F6' });
  s += txt(400, 830, 'TODAY · 3 CALLS ANSWERED · 0 MISSED · 2 BOOKINGS', { size: 11, weight: 700, fill: '#9FB3BF', ls: 3 });
  s += txt(1740, 830, 'GOOGLE CALENDAR CONNECTED', { size: 11, weight: 700, fill: '#168565', ls: 3, anchor: 'end' });
  return s;
}

/* ---------- rich text (per-word colour) ---------- */
function rich(x, y, parts, o = {}) {
  const { size = 56, weight = 300, anchor = 'start', ls = 0 } = o;
  const body = parts.map(p => `<tspan fill="${p.c || '#fff'}" font-weight="${p.w || weight}">${esc(p.t)}</tspan>`).join('');
  return `<text x="${x}" y="${y}" font-family="Inter" font-size="${size}" font-weight="${weight}"
    text-anchor="${anchor}" letter-spacing="${ls}" xml:space="preserve">${body}</text>`;
}

/* ---------- scene E · claims ---------- */
function sceneE(t, start, dur) {
  const k = dur / 2.6, tt = x => start + x * k;
  const rows = [
    [['Answers ', '#fff'], ['every', '#7CEAF7', 600], [' call \u2014 24/7', '#fff']],
    [['Books ', '#fff'], ['real', '#7CEAF7', 600], [' appointments', '#fff']],
    [['Remembers ', '#fff'], ['every', '#7CEAF7', 600], [' client', '#fff']],
  ];
  let s = '';
  rows.forEach((r, i) => {
    const p = prog(t, tt(.08 + i * .5), .6);
    const y = 414 + i * 138;
    s += `<g opacity="${p}" transform="translate(${(1 - p) * -26} 0)">
      ${txt(432, y - 6, '0' + (i + 1), { size: 17, weight: 700, fill: '#5C93A4', ls: 4 })}
      ${rich(520, y, r.map(([t2, c, w]) => ({ t: t2, c, w })), { size: 56 })}</g>`;
    if (i < 2) s += rect(432, y + 40, 1056, 1, { fill: '#ffffff', op: .12 });
  });
  return s;
}

/* ---------- scene F · end card ---------- */
function sceneF(t, start, dur) {
  const k = dur / 1.4, tt = x => start + x * k;
  let s = '';
  const fl = prog(t, tt(.05), 1.1);
  s += `<g transform="rotate(-9 960 540)" opacity="${fl * .9}">
    <rect x="-500" y="440" width="2900" height="200" fill="url(#streak)" filter="url(#blur26)"/></g>`;
  const pL = prog(t, tt(.05), .55, E.back);
  s += `<g opacity="${prog(t, tt(.05), .3)}" transform="translate(960 470) scale(${.9 + .1 * pL}) translate(-960 -470)">
    ${lockup(960, 470, 1)}</g>`;
  const t1 = rise(t, tt(.3));
  s += g(`opacity="${t1.o}" transform="translate(0 ${t1.y})"`,
    txt(960, 594, 'Every call, captured.', { size: 38, weight: 300, fill: '#C7ECF4', anchor: 'middle' }));
  const t2 = rise(t, tt(.55));
  s += g(`opacity="${t2.o}" transform="translate(0 ${t2.y})"`,
    txt(960, 668, '14-DAY FREE TRIAL \u00b7 NO CARD', { size: 18, weight: 700, fill: '#7FB9C8', ls: 8, anchor: 'middle' }));
  const t3 = rise(t, tt(.7));
  s += g(`opacity="${t3.o}" transform="translate(0 ${t3.y})"`,
    txt(960, 712, 'prismlink.co', { size: 24, weight: 500, fill: '#fff', ls: 3, anchor: 'middle' }));
  return s;
}

/* ---------- timelines ---------- */
const CUTS = {
  '20': { dur: 20, scenes: [
    ['A', 0, 3.2, 3.2], ['B', 3.2, 6.4, 3.2], ['C', 6.4, 12, 5.6],
    ['D', 12, 16, 4], ['E', 16, 18.6, 2.6], ['F', 18.6, 20, 1.4]] },
  '15': { dur: 15, scenes: [
    ['A', 0, 2.2, 2.2], ['B', 2.2, 4.6, 2.4], ['C', 4.6, 9.4, 4.8],
    ['D', 9.4, 12.4, 3], ['E', 12.4, 14, 1.6], ['F', 14, 15, 1]] },
  '6': { dur: 6, scenes: [['A', 0, 1.4, 1.4], ['C', 1.4, 4.4, 3], ['F', 4.4, 6, 1.6]] },
};
const DRAW = { A: sceneA, B: sceneB, C: sceneC, D: sceneD, E: sceneE, F: sceneF };
const BG = {
  A: () => rect(0, 0, W, H, { fill: 'url(#warm)' }),
  B: () => rect(0, 0, W, H, { fill: 'url(#navyBg)' }),
  C: () => rect(0, 0, W, H, { fill: 'url(#pageLight)' }),
  D: () => rect(0, 0, W, H, { fill: 'url(#dashBg)' }),
  E: () => rect(0, 0, W, H, { fill: 'url(#navyBg)' }),
  F: () => rect(0, 0, W, H, { fill: 'url(#endBg)' }),
};

function renderSVG(t, cutId) {
  const cut = CUTS[cutId];
  const list = cut.scenes;
  let idx = list.findIndex(sc => t >= sc[1] && t < sc[2]);
  if (idx < 0) idx = t <= 0 ? 0 : list.length - 1;
  const paint = (sc, op) =>
    `<g opacity="${op}">${BG[sc[0]]()}${DRAW[sc[0]](t, sc[1], sc[2] - sc[1])}</g>`;
  let body = paint(list[idx], 1);
  const fade = prog(t, list[idx][1], .3, E.inOut);
  if (idx > 0 && fade < 1) body += paint(list[idx - 1], 1 - fade);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
    ${defs()}<rect width="${W}" height="${H}" fill="#05161F"/>${body}</svg>`;
}

/* ---------- raster + encode ---------- */
const fontFiles = fs.readdirSync(FONT_DIR).filter(f => /\.ttf$/.test(f)).map(f => path.join(FONT_DIR, f));
if (!fontFiles.length) throw new Error(`no .ttf files in ${FONT_DIR}`);

function png(t, cutId) {
  const r = new Resvg(renderSVG(t, cutId), {
    fitTo: { mode: 'width', value: W },
    font: { fontFiles, loadSystemFonts: false, defaultFontFamily: 'Inter' },
  });
  return r.render().asPng();
}

const argv = process.argv.slice(2);
const arg = (n, d) => { const i = argv.indexOf('--' + n); return i < 0 ? d : argv[i + 1]; };
const cutId = arg('cut', '20');
const out = arg('out', `promo-${cutId}s.mp4`);
const still = arg('still', null);
const dur = CUTS[cutId].dur;

if (still) {
  fs.writeFileSync(out, png(parseFloat(still), cutId));
  console.log('still ->', out);
} else {
  const frames = Math.round(dur * FPS);
  const ff = spawn(ffmpegPath, ['-y', '-f', 'image2pipe', '-framerate', String(FPS), '-i', '-',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '20', '-pix_fmt', 'yuv420p',
    '-movflags', '+faststart', out], { stdio: ['pipe', 'ignore', 'pipe'] });
  let err = '';
  ff.stderr.on('data', d => { err = (err + d).slice(-800); });
  const done = new Promise((res, rej) => ff.on('close', c => c === 0 ? res() : rej(new Error(err))));
  for (let i = 0; i < frames; i++) {
    const buf = png(i / FPS, cutId);
    if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
    if (i % 60 === 0) process.stdout.write(`\r${cutId}s: frame ${i}/${frames}`);
  }
  ff.stdin.end();
  await done;
  console.log(`\r${cutId}s: ${frames} frames -> ${out} (${(fs.statSync(out).size / 1e6).toFixed(1)} MB)`);
}
