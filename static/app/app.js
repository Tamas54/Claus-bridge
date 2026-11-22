// Bridge UI v2 — „kabinüveg” (2026-11-22). Vanilla JS, build-lépés nélkül; minden adat a /api/ui/* utakról jön,
// a munkamenet-sütivel (httpOnly — a szkript nem látja, csak a böngésző küldi).
//
// Bal sáv: az ügynökök (Claus/Feldwebel a fő asszisztens, Kimi, DeepSeek, GLM, Faber) állapottal, alatta a
// beszélgetések (postaláda, nyitott viták). Közép: beszélgetés a választott ügynökkel — gépelésjelző, élő
// eszközhívás-sorok, a válasz alatt halványan a VALÓBAN kiszolgáló modell. Jobb sáv, „munkapad”: jóváhagyásra váró
// tételek gombokkal, friss események, LLM-költés. Telefonon egy oszlop, a két sáv fiókba kerül.

import { diktalas } from './diktalas.js';

const UGYNOK = {
  feldwebel: { betu: 'C', szin: '#22c3e6', rovid: 'Claus' },
  kimi: { betu: 'K', szin: '#a78bfa', rovid: 'Kimi' },
  deepseek: { betu: 'D', szin: '#60a5fa', rovid: 'DeepSeek' },
  glm5: { betu: 'G', szin: '#34d399', rovid: 'GLM' },
  faber: { betu: 'F', szin: '#fbbf24', rovid: 'Faber' },
};
const HELYKITOLTO = {
  feldwebel: 'Írj Clausnak, vagy mondd el a mikrofonnal…',
  kimi: 'Kérdezd Kimit…',
  deepseek: 'Kérdezd DeepSeeket…',
  glm5: 'Kérdezd GLM-et…',
  faber: 'Mit tanuljon meg a Faber?',
  vita: 'Hozzászólás a vitához…',
};
const ESZKOZ_IGE = {
  web_search: 'Keresett a weben', echolot_query: 'Keresett az Echolotban', echolot_engine: 'Echolot-motor',
  ai_query: 'Megkérdezett egy ügynököt', ai_task: 'Feladatot adott ki', execute_recipe: 'Receptet futtatott',
  schedule_recipe: 'Ütemezett egy receptet', capture_inbox: 'Megnézte a leveleket', read_email: 'Elolvasott egy levelet',
  send_email: 'Levelet küldött', create_task: 'Feladatot vett fel', list_tasks: 'Megnézte a feladatokat',
  create_calendar_event: 'Naptárba írt', read_memory: 'A közös memóriában olvasott', search_memory: 'A memóriában keresett',
  statdata: 'StatData-adatot kért', analyze_upload: 'Képet elemzett',
};
const SZINT_NEV = { okos: 'okos', kozep: 'közép', olcso: 'olcsó' };

const S = {
  adat: null,                       // az utolsó /api/ui/allapot
  nezet: { tipus: 'ugynok', id: 'feldwebel' },
  tetelek: [], kartyak: {}, tovabb: false, legkisebb: null,
  vita: null,
  futok: {},                        // nézetkulcs → futó kör
  ideiglenes: {},                   // nézetkulcs → a munkamenetben keletkezett, nem tárolt tételek
  utolsoId: 0,
  eltolas: 0,                       // szerveridő − kliensidő (ms)
  latott: olvas('kabin.latott', {}),
  ref: {},
  idozito: null,
};

// ── apróságok ─────────────────────────────────────────────────
function olvas(kulcs, alap) { try { return JSON.parse(localStorage.getItem(kulcs)) ?? alap; } catch { return alap; } }
function ir(kulcs, ertek) { try { localStorage.setItem(kulcs, JSON.stringify(ertek)); } catch { /* privát mód */ } }
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ms = (iso) => (iso ? Date.parse(String(iso).replace(/(\.\d{3})\d+/, '$1')) : 0);
const szerverMost = () => new Date(Date.now() + S.eltolas).toISOString();
const nezetKulcs = (n = S.nezet) => (n.tipus === 'ugynok' ? `u:${n.id}` : n.tipus === 'vita' ? `v:${n.id}` : 'p');

function el(tag, attrs, ...gyerekek) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k === 'html') e.innerHTML = v;
    else if (k === 'text') e.textContent = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const g of gyerekek.flat(Infinity)) {
    if (g == null || g === false) continue;
    e.append(g instanceof Node ? g : document.createTextNode(String(g)));
  }
  return e;
}

function oraPerc(iso) {
  const d = new Date(ms(iso));
  return d.toLocaleTimeString('hu-HU', { hour: '2-digit', minute: '2-digit' });
}
function napCimke(iso) {
  const d = new Date(ms(iso));
  const ma = new Date(); ma.setHours(0, 0, 0, 0);
  const nap = new Date(d); nap.setHours(0, 0, 0, 0);
  const kul = Math.round((ma - nap) / 86400000);
  if (kul === 0) return 'Ma';
  if (kul === 1) return 'Tegnap';
  return d.toLocaleDateString('hu-HU', { month: 'short', day: 'numeric', weekday: kul < 7 ? 'long' : undefined });
}
function relativ(iso) {
  if (!iso) return '';
  const mp = (Date.now() + S.eltolas - ms(iso)) / 1000;
  if (mp < 60) return 'most';
  if (mp < 3600) return `${Math.floor(mp / 60)} perce`;
  if (mp < 86400 && napCimke(iso) === 'Ma') return oraPerc(iso);
  if (napCimke(iso) === 'Tegnap') return 'tegnap';
  return new Date(ms(iso)).toLocaleDateString('hu-HU', { month: 'short', day: 'numeric' });
}

// markdown-szerű megjelenítés: előbb MINDEN escape-elve, utána csak a saját jelöléseink
function md(szoveg) {
  let s = esc(szoveg);
  const tar = [];
  const tegy = (h) => `\u0000${tar.push(h) - 1}\u0000`;
  s = s.replace(/```[\w-]*\n?([\s\S]*?)```/g, (_, kod) => tegy(`<pre><code>${kod.replace(/\n$/, '')}</code></pre>`));
  s = s.replace(/`([^`\n]+)`/g, (_, k) => tegy(`<code>${k}</code>`));
  s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, t, u) => tegy(`<a href="${u}" target="_blank" rel="noopener noreferrer">${t}</a>`));
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, (_, e, u) => e + tegy(`<a href="${u}" target="_blank" rel="noopener noreferrer">${u}</a>`));
  s = s.replace(/\*\*([^*\n][^*]*?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/^#{1,4}\s+(.+)$/gm, '<span class="cim">$1</span>');
  s = s.replace(/^&gt;\s?(.*)$/gm, '<span class="idezet">$1</span>');
  s = s.replace(/^\[([^\]\n]{3,400})\]$/gm, '<span class="jegyzet">[$1]</span>');
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => tar[+i]);
}

function avatar(ki, { kicsi = false, pont = null } = {}) {
  const u = UGYNOK[ki];
  const betu = u ? u.betu : ki === 'postalada' ? '✉' : (ki || '?').slice(0, 1).toUpperCase();
  const szin = u ? u.szin : '#94a3b8';
  return el('span', { class: `avatar${kicsi ? ' kicsi' : ''}`, style: `background:${szin}`, 'aria-hidden': 'true' },
    betu, pont ? el('span', { class: `pont ${pont}` }) : null);
}

let ertesitoIdo = null;
function ertesit(szoveg, hiba = false) {
  document.querySelector('.ertesito')?.remove();
  const e = el('div', { class: `ertesito${hiba ? ' hiba' : ''}`, role: 'status' }, szoveg);
  document.body.append(e);
  clearTimeout(ertesitoIdo);
  ertesitoIdo = setTimeout(() => e.remove(), 4200);
}

class Hiba401 extends Error {}

async function api(ut, { method = 'GET', body } = {}) {
  const r = await fetch(ut, {
    method, credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401) { belepesNezet(); throw new Hiba401('belépés kell'); }
  let j = null;
  try { j = await r.json(); } catch { /* nem JSON */ }
  if (!r.ok) {
    const e = new Error(j?.hiba || `HTTP ${r.status}`);
    e.status = r.status; e.adat = j;
    throw e;
  }
  return j;
}

// ── BELÉPÉS ───────────────────────────────────────────────────
function belepesNezet() {
  clearInterval(S.idozito);
  S.idozito = null;
  const gyoker = document.getElementById('gyoker');
  const sav = el('p', { class: 'uzenet-sav', 'aria-live': 'polite' });
  const kodInput = el('input', { inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: '6', placeholder: '······', 'aria-label': 'Belépőkód' });
  const kodGomb = el('button', { class: 'gomb gomb-fo', type: 'submit', style: 'width:auto' }, 'Belépés');
  const kodResz = el('form', { class: 'kod-resz', hidden: true }, el('p', { class: 'halk', style: 'margin:16px 0 0' },
    'Kattints a linkre a Telegramban — vagy írd be ide a kódot (a kezdőképernyőre tett alkalmazásban ez kell):'),
  el('div', { class: 'kod-sor' }, kodInput, kodGomb));
  let keres = null;
  let figyelo = null;
  const linkGomb = el('button', { class: 'gomb gomb-fo', type: 'button' }, 'Belépőlink a Telegramodra');
  linkGomb.addEventListener('click', async () => {
    linkGomb.disabled = true;
    sav.className = 'uzenet-sav'; sav.textContent = 'Küldöm…';
    try {
      const r = await fetch('/app/belepolink', { method: 'POST', credentials: 'same-origin' });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j.ok) throw new Error(j.hiba || `HTTP ${r.status}`);
      keres = j.keres;
      sav.className = 'uzenet-sav ok';
      sav.textContent = 'Elküldtem a Telegramodra. 10 percig érvényes.';
      kodResz.hidden = false;
      kodInput.focus();
      // ha a linket ugyanebben a böngészőben nyitja meg (másik lapon), ez a lap magától belép
      clearInterval(figyelo);
      figyelo = setInterval(async () => {
        const v = await fetch('/api/ui/allapot', { credentials: 'same-origin' }).catch(() => null);
        if (v && v.ok) { clearInterval(figyelo); indit(); }
      }, 3000);
      setTimeout(() => clearInterval(figyelo), 10 * 60 * 1000);
    } catch (e) {
      sav.className = 'uzenet-sav hiba'; sav.textContent = e.message;
    } finally {
      setTimeout(() => { linkGomb.disabled = false; }, 4000);
    }
  });
  kodResz.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    kodGomb.disabled = true;
    try {
      const r = await fetch('/app/belepes/kod', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keres, kod: kodInput.value }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j.ok) throw new Error(j.hiba || `HTTP ${r.status}`);
      clearInterval(figyelo);
      indit();
    } catch (e) {
      sav.className = 'uzenet-sav hiba'; sav.textContent = e.message;
    } finally { kodGomb.disabled = false; }
  });
  gyoker.className = 'belepes';
  gyoker.replaceChildren(el('main', { class: 'belepes-doboz' },
    el('div', { class: 'marka' }, el('img', { src: '/app/ikon.svg', alt: '', width: '40', height: '40' }),
      el('div', {}, el('b', {}, 'CLAUS BRIDGE'), el('small', {}, 'kabin'))),
    el('h1', {}, 'Belépés'),
    el('p', {}, 'Jelszó nincs: a Bridge a Telegramodra küld egy egyszer használatos linket és egy kódot.'),
    linkGomb, kodResz, sav,
    el('p', { class: 'halk', style: 'margin-top:18px' }, el('a', { href: '/dashboard' }, 'Régi műszerfal'))));
}

// ── A KABIN VÁZA ──────────────────────────────────────────────
function kabinEpit() {
  const gyoker = document.getElementById('gyoker');
  gyoker.className = '';
  const R = S.ref;
  R.ugynokok = el('div');
  R.beszelgetesek = el('div');
  R.bal = el('aside', { class: 'bal', 'aria-label': 'Ügynökök és beszélgetések' },
    el('div', { class: 'bal-fej' },
      el('div', { class: 'marka' }, el('img', { src: '/app/ikon.svg', alt: '', width: '30', height: '30' }),
        el('div', {}, el('b', {}, 'CLAUS BRIDGE'), el('small', {}, 'kabin'))),
      el('button', { class: 'ikon-gomb csak-mobil', 'aria-label': 'Bezár', onclick: fiokZar }, '✕')),
    el('div', { class: 'bal-gorgo' },
      el('div', { class: 'szakasz-cim' }, 'Ügynökök'), R.ugynokok,
      el('div', { class: 'szakasz-cim' }, 'Beszélgetések'), R.beszelgetesek),
    el('div', { class: 'bal-lab' },
      el('a', { class: 'gomb gomb-kicsi', href: '/dashboard' }, 'Régi műszerfal'),
      el('button', { class: 'gomb gomb-kicsi', onclick: kilep }, 'Kilépés')));

  R.fejId = el('div', { class: 'fejlec-id' });
  R.padSzamlalo = el('span', { class: 'szamlalo sarga', hidden: true });
  R.fejlec = el('header', { class: 'fejlec' },
    el('button', { class: 'ikon-gomb csak-mobil', 'aria-label': 'Ügynökök', onclick: () => fiokNyit('bal') }, '☰'),
    R.fejId,
    el('button', { class: 'ikon-gomb jelveny csak-szuk', 'aria-label': 'Munkapad', title: 'Munkapad', onclick: () => fiokNyit('jobb') },
      el('span', { html: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M4 5h16v2H4zm0 6h10v2H4zm0 6h16v2H4z"/></svg>' }), R.padSzamlalo));
  R.atirat = el('div', { class: 'atirat', role: 'log', 'aria-live': 'polite' });
  R.szerkeszto = el('div');
  R.kozep = el('main', { class: 'kozep' }, R.fejlec, R.atirat, R.szerkeszto);

  R.munkapad = el('div', { class: 'jobb-gorgo' });
  R.jobb = el('aside', { class: 'jobb', 'aria-label': 'Munkapad' },
    el('div', { class: 'jobb-fej' }, el('b', {}, 'Munkapad'),
      el('button', { class: 'ikon-gomb csak-szuk', 'aria-label': 'Bezár', onclick: fiokZar }, '✕')),
    R.munkapad);
  R.kabin = el('div', { class: 'kabin' }, R.bal, R.kozep, R.jobb, el('div', { class: 'fatyol', onclick: fiokZar }));
  gyoker.replaceChildren(R.kabin);
  szerkesztoEpit();
}

function fiokNyit(melyik) { S.ref.kabin.classList.add(`${melyik}-nyitva`); }
function fiokZar() { S.ref.kabin.classList.remove('bal-nyitva', 'jobb-nyitva'); }

async function kilep() {
  await fetch('/app/kilepes', { method: 'POST', credentials: 'same-origin' }).catch(() => null);
  belepesNezet();
}

// ── BAL SÁV ───────────────────────────────────────────────────
function balRajzol() {
  const a = S.adat;
  if (!a) return;
  S.ref.ugynokok.replaceChildren(...a.ugynokok.map((u) => {
    const pont = u.fut || S.futok[`u:${u.id}`] ? 'dolgozik' : u.elerheto ? 'el' : 'ki';
    const aktiv = S.nezet.tipus === 'ugynok' && S.nezet.id === u.id;
    const uj = u.utolso && u.utolso.ki !== 'kommandant' && u.utolso.id > (S.latott[u.id] || 0) && !aktiv;
    const elo = u.utolso ? `${u.utolso.ki === 'kommandant' ? 'Te: ' : ''}${u.utolso.szoveg}` : u.szerep;
    return el('button', {
      class: `ugynok${aktiv ? ' aktiv' : ''}`, title: u.modell ? `kért modell: ${u.modell}` : u.szerep,
      onclick: () => nezetValt({ tipus: 'ugynok', id: u.id }),
    }, avatar(u.id, { pont }),
    el('span', { class: 'ugynok-test' }, el('span', { class: 'ugynok-nev' }, u.nev), el('span', { class: 'ugynok-elo' }, elo)),
    el('span', { class: 'ugynok-ido' }, relativ(u.utoljara)),
    uj ? el('span', { class: 'olvasatlan', 'aria-label': 'új üzenet' }) : null);
  }));
  const p = a.postalada;
  const pUj = p && p.id > (S.latott.postalada || 0);
  const elemek = [el('button', {
    class: `beszelgetes-elem${S.nezet.tipus === 'postalada' ? ' aktiv' : ''}`,
    onclick: () => nezetValt({ tipus: 'postalada' }),
  }, el('span', { class: 'jel' }, '✉'), el('span', {}, 'Postaláda — a Claude-októl'), pUj ? el('span', { class: 'szamlalo' }, 'új') : null)];
  for (const v of a.vitak || []) {
    elemek.push(el('button', {
      class: `beszelgetes-elem${S.nezet.tipus === 'vita' && S.nezet.id === v.id ? ' aktiv' : ''}`,
      title: `${v.cim} — ${v.ki}`, onclick: () => nezetValt({ tipus: 'vita', id: v.id }),
    }, el('span', { class: 'jel' }, '#'), el('span', {}, v.cim), el('span', { class: 'halk' }, relativ(v.ido))));
  }
  if (!(a.vitak || []).length) elemek.push(el('div', { class: 'ures', style: 'padding:4px 10px' }, 'Nincs nyitott vita.'));
  S.ref.beszelgetesek.replaceChildren(...elemek);
}

// ── FEJLÉC ────────────────────────────────────────────────────
function fejlecRajzol() {
  const n = S.nezet;
  let cim, al, ki;
  if (n.tipus === 'ugynok') {
    const u = S.adat?.ugynokok.find((x) => x.id === n.id) || { nev: n.id, elerheto: true };
    const fut = S.futok[`u:${n.id}`];
    ki = n.id; cim = u.nev;
    al = fut ? 'dolgozik…' : u.elerheto ? `elérhető${u.megjegyzes ? ` · ${u.megjegyzes}` : ''}` : `nem elérhető${u.megjegyzes ? ` · ${u.megjegyzes}` : ''}`;
    if (n.id === 'feldwebel') al += ' · közös a Telegrammal';
  } else if (n.tipus === 'vita') {
    ki = 'vita'; cim = S.vita ? `Vita #${S.vita.id} · ${S.vita.cim}` : `Vita #${n.id}`;
    al = S.vita ? `${S.vita.allapot === 'open' ? 'nyitott' : 'lezárt'} · indította: ${S.vita.ki}` : '';
  } else {
    ki = 'postalada'; cim = 'Postaláda'; al = 'üzenetek neked a Claude-októl és a Bridge-től';
  }
  S.ref.fejId.replaceChildren(
    n.tipus === 'vita' ? el('span', { class: 'avatar', style: 'background:#334155;color:var(--ink)' }, '#') : avatar(ki),
    el('div', {}, el('b', {}, cim), el('small', {}, al)));
}

// ── ÁTIRAT ────────────────────────────────────────────────────
function lentVan() { const a = S.ref.atirat; return a.scrollHeight - a.scrollTop - a.clientHeight < 140; }
function leGorget() { const a = S.ref.atirat; a.scrollTop = a.scrollHeight; }

function eszkozSor(e) {
  let args = {};
  try { args = JSON.parse(e.args || '{}'); } catch { /* csonkolt */ }
  const mit = ESZKOZ_IGE[e.nev] || `Eszköz: ${e.nev}`;
  const min = args.query || args.prompt || args.name || args.title || args.url || args.model || '';
  const allapot = e.allapot === 'fut' ? 'fut' : e.allapot === 'hiba' ? 'hiba' : 'kesz';
  return el('details', { class: `eszkoz ${allapot}`, 'data-kulcs': `${e.nev}|${e.args}` },
    el('summary', {}, el('span', { class: 'nyil' }, '▸'), el('span', { class: 'mit' },
      allapot === 'hiba' ? `${mit} — nem sikerült` : mit), min ? el('span', { class: 'min' }, String(min).slice(0, 120)) : null,
    e.ms != null ? el('span', { class: 'min' }, `${(e.ms / 1000).toFixed(1)} mp`) : null),
    el('div', { class: 'eszkoz-test' }, el('b', {}, 'bemenet '), e.args || '{}',
      e.eredmeny ? ['\n\n', el('b', {}, 'eredmény '), e.eredmeny] : (allapot === 'fut' ? '\n\n… fut' : '')));
}

function eszkozBlokk(lista) {
  return el('div', { class: 'eszkozok' }, lista.map(eszkozSor));
}

function allapotChip(szoveg, fajta) { return el('span', { class: `allapot ${fajta}` }, szoveg); }

function faberAllapot(c) {
  if (c.var_dontesre) return allapotChip('Jóváhagyásra vár', 'var');
  if (c.dontes && ['var', 'vár'].includes((c.allapot || '').toLowerCase())) {
    return allapotChip(c.dontes.dontes === 'mehet' ? 'Továbbítva: mehet' : 'Továbbítva: mégse', 'fut');
  }
  const a = (c.allapot || '').toLowerCase();
  if (/(eles|éles|kesz|kész|merge|siker|ok$)/.test(a)) return allapotChip(c.allapot, 'kesz');
  if (/(hiba|bukott|fail|elvet|visszavon|megse|mégse)/.test(a)) return allapotChip(c.allapot, a.includes('hiba') || a.includes('bukott') ? 'hiba' : 'semleges');
  return allapotChip(c.allapot || 'ismeretlen', 'fut');
}

function faberKartya(c, { tomor = false } = {}) {
  const hibaSav = el('div', { class: 'kartya-hiba', hidden: true });
  const gombok = c.var_dontesre ? el('div', { class: 'kartya-gombok' },
    el('button', { class: 'gomb gomb-fo gomb-kicsi', onclick: (ev) => faberDont(c.id, 'mehet', ev.currentTarget.parentNode, hibaSav) }, 'Mehet'),
    el('button', { class: 'gomb gomb-kicsi', onclick: (ev) => faberDont(c.id, 'megse', ev.currentTarget.parentNode, hibaSav) }, 'Mégse')) : null;
  return el('div', { class: `kartya${c.var_dontesre ? ' var' : ''}` },
    el('div', { class: 'kartya-fej' }, avatar('faber', { kicsi: true }), el('b', {}, `Faber · #${c.id}`),
      c.repo ? el('span', { class: 'chip' }, c.repo) : null, el('span', { class: 'allapot-hely', style: 'margin-left:auto' }, faberAllapot(c))),
    el('p', {}, c.kivansag || '(nincs leírás)'),
    c.szoveg ? el('p', { class: 'halk' }, c.szoveg) : null,
    !tomor && c.jelentes ? el('details', {}, el('summary', {}, 'Jelentés'), el('div', { class: 'jelentes' }, c.jelentes)) : null,
    c.diffstat ? el('details', { open: !tomor && c.diffstat.length < 600 }, el('summary', {}, 'Változás (diffstat)'), el('div', { class: 'diffstat' }, c.diffstat)) : null,
    gombok, hibaSav);
}

async function faberDont(id, dontes, gombSor, hibaSav) {
  gombSor?.querySelectorAll('button').forEach((b) => { b.disabled = true; });
  try {
    await api('/api/ui/faber/dont', { method: 'POST', body: { id, dontes } });
    ertesit(dontes === 'mehet' ? `Mehet — továbbítva a Fabernek (#${id}).` : `Mégse — továbbítva a Fabernek (#${id}).`);
    await frissit({ tortenet: S.nezet.tipus === 'ugynok' && S.nezet.id === 'faber' });
  } catch (e) {
    if (e instanceof Hiba401) return;
    if (hibaSav) { hibaSav.hidden = false; hibaSav.textContent = e.message; }
    gombSor?.querySelectorAll('button').forEach((b) => { b.disabled = false; });
    if (e.status === 409) await frissit({ tortenet: true });
  }
}

function tetelCsomopont(t) {
  if (t.tipus === 'eszkozok') return eszkozBlokk(t.eszkozok);
  if (t.tipus === 'rendszer') return el('div', { class: 'rendszer-sor' }, t.szoveg);
  if (t.tipus === 'esemeny') {
    return el('div', { class: 'rendszer-sor' }, el('b', {}, `Faber · #${t.kerelem}: ${t.allapot}`), t.szoveg ? ` — ${t.szoveg}` : '');
  }
  if (t.tipus === 'kartya') {
    const c = S.kartyak[t.kerelem];
    if (c && c.kapu_sor === t.id) return el('div', { class: 'sor o' }, faberKartya(c));
    return el('div', { class: 'rendszer-sor' }, `Faber-kérelem #${t.kerelem} (korábbi változat): ${t.szoveg}`);
  }
  const en = t.ki === 'kommandant';
  const nezoUgynok = S.nezet.tipus === 'ugynok' ? S.nezet.id : null;
  const mutatNev = !en && (S.nezet.tipus !== 'ugynok' || t.ki !== nezoUgynok);
  const meta = el('div', { class: 'meta' });
  if (en && t.csatorna) meta.append(el('span', { class: 'chip csatorna' }, t.csatorna === 'web' ? 'web' : 'tg'));
  if (t.faber) {
    const sz = { kivansag: 'kívánság', jovahagy: 'élesítés', elvet: 'elvetés', vissza: 'visszavonás', allapot: 'állapot', parancs: 'parancs' }[t.faber.szandek] || t.faber.szandek;
    meta.append(el('span', { class: 'chip faber' }, `→ Faber · ${sz}${t.faber.repo ? ` · ${t.faber.repo}` : ''}${t.faber.kerelem ? ` · #${t.faber.kerelem}` : ''}`));
  }
  if (!en && t.modell) meta.append(el('span', { class: 'modell', title: 'a választ ténylegesen kiszolgáló modell' }, t.modell));
  if (!en && t.eszkozok?.length) meta.append(el('span', { class: 'chip' }, `eszközök: ${t.eszkozok.join(', ')}`));
  if (t.ido) meta.append(el('span', {}, oraPerc(t.ido)));
  if (t.fuggo) meta.append(el('span', {}, 'küldés…'));
  return el('div', { class: `sor ${en ? 'en' : 'o'}${t.hiba ? ' hiba' : ''}` },
    mutatNev ? el('div', { class: 'ki-nev' }, avatar(t.ki, { kicsi: true }), UGYNOK[t.ki]?.rovid || t.ki) : null,
    t.targy ? el('div', { class: 'ki-nev', style: 'color:var(--ink)' }, t.targy) : null,
    el('div', { class: `buborek${t.fuggo ? ' fuggo' : ''}`, html: md(t.szoveg || '') }),
    meta.childNodes.length ? meta : null);
}

function atiratRajzol({ gorget = null } = {}) {
  const lent = gorget ?? lentVan();
  const kulcs = nezetKulcs();
  const lista = [...S.tetelek, ...(S.ideiglenes[kulcs] || [])].sort((a, b) => (ms(a.ido) - ms(b.ido)) || ((a.id || 0) - (b.id || 0)));
  const nodes = [];
  if (S.tovabb && S.nezet.tipus !== 'vita') {
    nodes.push(el('div', { class: 'regebbi' }, el('button', { class: 'gomb gomb-kicsi', onclick: regebbiBetolt }, 'Régebbi üzenetek')));
  }
  let nap = null;
  for (const t of lista) {
    const c = t.ido ? napCimke(t.ido) : null;
    if (c && c !== nap) { nap = c; nodes.push(el('div', { class: 'nap' }, c)); }
    nodes.push(tetelCsomopont(t));
  }
  if (!lista.length && !S.futok[kulcs]) nodes.push(uresAllapot());
  S.ref.elo = el('div', { class: 'elo' });
  nodes.push(S.ref.elo);
  S.ref.atirat.replaceChildren(...nodes);
  eloRajzol();
  if (lent) leGorget();
}

function uresAllapot() {
  const n = S.nezet;
  if (n.tipus === 'postalada') return el('div', { class: 'atirat-ures' }, el('b', {}, 'Üres a postaláda'), 'A Claude-ok ide írnak neked.');
  if (n.tipus === 'vita') return el('div', { class: 'atirat-ures' }, 'Betöltés…');
  const u = UGYNOK[n.id]?.rovid || n.id;
  return el('div', { class: 'atirat-ures' }, el('b', {}, `Még nem beszéltetek ${u === 'Claus' ? 'Clausszal' : `— ${u}`}`),
    n.id === 'faber' ? 'Mondd el, mit tanuljon meg: a Faber kipróbálja, és jóváhagyásra hozza.' : 'Írj, vagy nyomd meg a mikrofont, és mondd.');
}

// az élő kör: eszközsorok, az eddigi válaszok és a gépelésjelző — csak ez rajzolódik újra másodpercenként
function eloRajzol() {
  const doboz = S.ref.elo;
  if (!doboz) return;
  const kor = S.futok[nezetKulcs()];
  if (!kor) { doboz.replaceChildren(); return; }
  const nyitott = new Set([...doboz.querySelectorAll('details[open]')].map((d) => d.dataset.kulcs));
  const lent = lentVan();
  const nodes = [];
  if (kor.eszkozok?.length) nodes.push(eszkozBlokk(kor.eszkozok));
  for (const v of kor.valaszok || []) nodes.push(tetelCsomopont({ ...v, tipus: 'uzenet', modell: v.ki === kor.ugynok || v.ki === 'feldwebel' ? kor.modell : null }));
  if (kor.allapot === 'fut') {
    const fut = (kor.eszkozok || []).filter((e) => e.allapot === 'fut').map((e) => ESZKOZ_IGE[e.nev] || e.nev);
    nodes.push(el('div', { class: 'sor o' }, el('div', { class: 'gepel', 'aria-label': 'gépel' }, el('span'), el('span'), el('span')),
      el('div', { class: 'gepel-szoveg' }, `${UGYNOK[kor.ugynok]?.rovid || kor.ugynok} dolgozik${fut.length ? ` — ${fut.join(', ').toLowerCase()}` : '…'}`)));
  }
  doboz.replaceChildren(...nodes);
  doboz.querySelectorAll('details').forEach((d) => { if (nyitott.has(d.dataset.kulcs)) d.open = true; });
  if (lent) leGorget();
}

async function tortenetBetolt({ csendes = false } = {}) {
  const n = S.nezet;
  const kulcs = nezetKulcs(n);
  if (n.tipus === 'vita') {
    const v = await api(`/api/ui/vita/${n.id}`);
    if (nezetKulcs() !== kulcs) return;
    S.vita = v;
    S.tetelek = v.bejegyzesek.map((b) => ({ id: b.id, ido: b.ido, ki: b.ki === 'kommandant' ? 'kommandant' : b.ki, szoveg: b.szoveg, tipus: 'uzenet' }));
    if (v.kontextus) S.tetelek.unshift({ id: 0, ido: v.ido, ki: 'rendszer', tipus: 'rendszer', szoveg: v.kontextus });
    if (v.dontes) S.tetelek.push({ id: 1e9, ido: szerverMost(), ki: 'rendszer', tipus: 'rendszer', szoveg: `Döntés: ${v.dontes}` });
    S.tovabb = false;
    fejlecRajzol();
    atiratRajzol({ gorget: !csendes });
    return;
  }
  const ablak = n.tipus === 'ugynok' ? n.id : 'postalada';
  const j = await api(`/api/ui/beszelgetes?ablak=${encodeURIComponent(ablak)}`);
  if (nezetKulcs() !== kulcs) return;
  S.tetelek = j.tetelek;
  S.kartyak = j.kartyak || {};
  S.tovabb = j.tovabb;
  S.legkisebb = j.legkisebb_id;
  // egy másik ablakban (vagy újratöltés előtt) indított, még futó kör: követjük
  for (const k of j.futo_korok || []) {
    if (!S.futok[kulcs]) { S.futok[kulcs] = { ...k }; korKovet(k.kor_id, kulcs); }
  }
  const max = Math.max(0, ...S.tetelek.map((t) => t.id || 0));
  if (max > (S.latott[ablak] || 0)) { S.latott[ablak] = max; ir('kabin.latott', S.latott); }
  atiratRajzol({ gorget: !csendes ? true : null });
  balRajzol();
}

async function regebbiBetolt() {
  if (!S.legkisebb) return;
  const ablak = S.nezet.tipus === 'ugynok' ? S.nezet.id : 'postalada';
  const a = S.ref.atirat;
  const elotteMagas = a.scrollHeight;
  const j = await api(`/api/ui/beszelgetes?ablak=${encodeURIComponent(ablak)}&elotte=${S.legkisebb}`);
  S.tetelek = [...j.tetelek, ...S.tetelek];
  S.tovabb = j.tovabb;
  S.legkisebb = j.legkisebb_id ?? S.legkisebb;
  Object.assign(S.kartyak, j.kartyak || {});
  atiratRajzol({ gorget: false });
  a.scrollTop = a.scrollHeight - elotteMagas;
}

function nezetValt(n) {
  S.nezet = n;
  ir('kabin.nezet', n);
  S.tetelek = []; S.vita = null; S.tovabb = false;
  fiokZar();
  fejlecRajzol();
  balRajzol();
  szerkesztoEpit();
  S.ref.atirat.replaceChildren(el('div', { class: 'atirat-ures' }, 'Betöltés…'));
  tortenetBetolt().catch(hibaKezel);
}

// ── SZERKESZTŐ (gépelés + diktálás) ──────────────────────────
function szerkesztoEpit() {
  const n = S.nezet;
  if (n.tipus === 'postalada') {
    S.ref.szerkeszto.replaceChildren(el('div', { class: 'szerkeszto-zar' }, 'A postaládára nem lehet válaszolni — írj Clausnak, ő továbbadja.'));
    S.ref.input = null;
    return;
  }
  const ta = el('textarea', { rows: '1', placeholder: HELYKITOLTO[n.tipus === 'vita' ? 'vita' : n.id] || 'Üzenet…', 'aria-label': 'Üzenet' });
  const kuld = el('button', { class: 'ikon-gomb kuld', type: 'submit', 'aria-label': 'Küldés', title: 'Küldés', disabled: true,
    html: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M3.4 20.4 21 12 3.4 3.6 3.3 10l12.6 2-12.6 2z"/></svg>' });
  const dikt = diktalas(ta, { kuldd: () => urlapKuld() });
  const gombok = el('div', { class: 'szerkeszto-gombok' }, dikt ? dikt.gomb : null, kuld);
  const durva = matchMedia('(pointer: coarse)').matches;
  const urlap = el('form', { class: 'szerkeszto-hej' }, ta,
    el('div', { class: 'szerkeszto-sor' }, el('span', { class: 'halk' }, durva ? '' : 'Enter: küldés · Shift+Enter: új sor'), gombok));
  const meret = () => { ta.style.height = 'auto'; ta.style.height = `${Math.min(ta.scrollHeight, innerHeight * 0.4)}px`; };
  const engedelyez = () => { kuld.disabled = !ta.value.trim() || Boolean(S.futok[nezetKulcs()]); };
  ta.addEventListener('input', () => { meret(); engedelyez(); });
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !durva) { e.preventDefault(); urlapKuld(); }
  });
  function urlapKuld() {
    const szoveg = ta.value.trim();
    if (!szoveg || S.futok[nezetKulcs()]) return;
    dikt?.cancel();
    ta.value = ''; meret(); engedelyez();
    kuldes(szoveg);
  }
  urlap.addEventListener('submit', (e) => { e.preventDefault(); urlapKuld(); });
  S.ref.szerkeszto.replaceChildren(el('div', { class: 'szerkeszto' }, urlap, dikt ? dikt.jegyzet : null));
  S.ref.input = ta;
  S.ref.engedelyez = engedelyez;
  if (!durva) setTimeout(() => ta.focus(), 30);
}

async function kuldes(szoveg) {
  const n = { ...S.nezet };
  const kulcs = nezetKulcs(n);
  if (n.tipus === 'vita') {
    try {
      await api(`/api/ui/vita/${n.id}`, { method: 'POST', body: { szoveg } });
      await tortenetBetolt();
    } catch (e) { hibaKezel(e); }
    return;
  }
  const indult = szerverMost();
  const fuggo = { ido: indult, ki: 'kommandant', szoveg, tipus: 'uzenet', csatorna: 'web', fuggo: true };
  (S.ideiglenes[kulcs] ||= []).push(fuggo);
  S.futok[kulcs] = { ugynok: n.id, allapot: 'fut', valaszok: [], eszkozok: [], indult, sajat: fuggo };
  atiratRajzol({ gorget: true });
  fejlecRajzol(); balRajzol(); S.ref.engedelyez?.();
  try {
    const j = await api('/api/ui/chat', { method: 'POST', body: { szoveg, ugynok: n.id } });
    S.futok[kulcs].kor_id = j.kor_id;
    korKovet(j.kor_id, kulcs);
  } catch (e) {
    delete S.futok[kulcs];
    fuggo.fuggo = false;
    if (!(e instanceof Hiba401)) {
      S.ideiglenes[kulcs].push({ ido: szerverMost(), ki: n.id, tipus: 'uzenet', hiba: true, szoveg: `Nem ment el: ${e.message}` });
    }
    if (nezetKulcs() === kulcs) { atiratRajzol({ gorget: true }); fejlecRajzol(); S.ref.engedelyez?.(); }
  }
}

async function korKovet(korId, kulcs) {
  let hibak = 0;
  for (;;) {
    await new Promise((r) => setTimeout(r, 900));
    let j;
    try {
      j = await api(`/api/ui/chat/${korId}`);
      hibak = 0;
    } catch (e) {
      if (e instanceof Hiba401) return;
      if (e.status === 404 || ++hibak > 20) {
        korVege(kulcs, { allapot: 'hiba', hiba: e.status === 404 ? 'A kör elveszett (újraindult a Bridge?). Az előzményben megnézem, mi ért célba.' : e.message, valaszok: [], eszkozok: [] });
        return;
      }
      continue;
    }
    const kor = S.futok[kulcs];
    if (!kor) return;
    Object.assign(kor, j);
    if (nezetKulcs() === kulcs) eloRajzol();
    if (j.allapot !== 'fut') { korVege(kulcs, j); return; }
  }
}

async function korVege(kulcs, j) {
  const kor = S.futok[kulcs] || {};
  delete S.futok[kulcs];
  // A kör után az előzmény a forrás (messages-tábla); ami nem tárolódik (API-hiba, parancs-kimenet, Faber-nyugta,
  // az eszközök részletei), az a munkamenet végéig ideiglenesként marad, időrendben a helyén.
  const ideig = (S.ideiglenes[kulcs] ||= []);
  if (kor.sajat) kor.sajat.fuggo = false;
  const elsoIdo = j.valaszok?.[0]?.ido || szerverMost();
  if (j.eszkozok?.length) ideig.push({ tipus: 'eszkozok', eszkozok: j.eszkozok, ido: new Date(ms(elsoIdo) - 1).toISOString() });
  const valaszok = (j.valaszok || []).map((v) => ({ ...v, tipus: 'uzenet', modell: v.ki === kor.ugynok || v.ki === 'feldwebel' ? j.modell : null }));
  if (j.allapot === 'hiba' && j.hiba) valaszok.push({ ido: szerverMost(), ki: kor.ugynok || 'feldwebel', tipus: 'uzenet', hiba: true, szoveg: j.hiba });
  if (nezetKulcs() === kulcs) {
    try { await tortenetBetolt({ csendes: true }); } catch (e) { hibaKezel(e); }
  }
  const indultMs = ms(kor.indult) - 5000;
  const tarolt = (ki) => S.tetelek.some((t) => t.ki === ki && t.tipus === 'uzenet' && ms(t.ido) >= indultMs);
  if (kor.sajat && S.tetelek.some((t) => t.ki === 'kommandant' && (t.szoveg || '').trim() === kor.sajat.szoveg.trim() && ms(t.ido) >= indultMs)) {
    S.ideiglenes[kulcs] = ideig.filter((t) => t !== kor.sajat);
  }
  for (const v of valaszok) {
    if (!v.hiba && tarolt(v.ki)) continue;
    S.ideiglenes[kulcs].push(v);
  }
  if (nezetKulcs() === kulcs) atiratRajzol({});
  fejlecRajzol(); balRajzol(); S.ref.engedelyez?.();
  frissit({}).catch(() => {});
}

// ── MUNKAPAD ──────────────────────────────────────────────────
function munkapadRajzol() {
  const a = S.adat;
  if (!a) return;
  const jv = a.jovahagyas || { faber: [], draftok: [] };
  const db = jv.faber.length + jv.draftok.length;
  S.ref.padSzamlalo.hidden = !db;
  S.ref.padSzamlalo.textContent = db;
  document.title = db ? `(${db}) Claus Bridge — kabin` : 'Claus Bridge — kabin';

  const jovahagyas = el('section', { class: 'blokk' },
    el('div', { class: 'szakasz-cim' }, el('span', {}, 'Jóváhagyásra vár'), db ? el('span', { class: 'szamlalo sarga' }, db) : null),
    db ? null : el('div', { class: 'ures' }, 'Nincs döntésre váró tétel.'),
    jv.faber.map((c) => el('div', { class: 'munkapad-kartya' }, faberKartya(c, { tomor: true }))),
    jv.draftok.map(draftKartya));

  const friss = (jv.faber_friss || []).length ? el('section', { class: 'blokk' },
    el('div', { class: 'szakasz-cim' }, 'Faber — legutóbb'),
    jv.faber_friss.map((c) => el('button', { class: 'esemeny', onclick: () => nezetValt({ tipus: 'ugynok', id: 'faber' }) },
      el('span', { class: 'jel' }, 'F'), el('span', {}, el('span', { class: 'cim' }, `#${c.id} ${c.kivansag}`), el('small', {}, c.repo || '')),
      el('span', { class: 'jobbra' }, faberAllapot(c), el('small', {}, relativ(c.frissitve)))))) : null;

  const esemenyek = el('section', { class: 'blokk' }, el('div', { class: 'szakasz-cim' }, 'Friss események'),
    (a.esemenyek || []).length ? a.esemenyek.map(esemenySor) : el('div', { class: 'ures' }, 'Csend.'));

  const peldanyok = Object.entries(a.peldanyok || {});
  S.ref.munkapad.replaceChildren(jovahagyas, friss, esemenyek, koltesBlokk(a.llm_kapu),
    peldanyok.length ? el('section', { class: 'blokk' }, el('div', { class: 'szakasz-cim' }, 'Claude-példányok'),
      peldanyok.map(([nev, p]) => el('div', { class: 'jelenlet-sor' }, el('span', {}, nev),
        el('span', { title: p.last_activity_source || '' }, relativ(p.last_seen))))) : null);
}

function draftKartya(d) {
  const sor = el('div', { class: 'kartya-gombok' });
  const hiba = el('div', { class: 'kartya-hiba', hidden: true });
  const dont = async (dontes) => {
    sor.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    try {
      const j = await api('/api/ui/draft/dont', { method: 'POST', body: { id: d.id, dontes } });
      const r = j.eredmeny || {};
      ertesit(dontes === 'reject' ? 'Elvetve.' : r.calendar_created ? 'Jóváhagyva — a naptáresemény létrejött.' : 'Jóváhagyva.');
      await frissit({});
    } catch (e) {
      if (e instanceof Hiba401) return;
      hiba.hidden = false; hiba.textContent = e.message;
      sor.querySelectorAll('button').forEach((b) => { b.disabled = false; });
    }
  };
  sor.append(el('button', { class: 'gomb gomb-fo gomb-kicsi', onclick: () => dont('approve') }, 'Jóváhagyom'),
    el('button', { class: 'gomb gomb-kicsi', onclick: () => dont('reject') }, 'Elvetem'));
  return el('div', { class: 'draft' }, el('b', {}, `${d.fajta === 'calendar' ? '📅' : '⏰'} ${d.cim}`),
    el('small', {}, [d.mikor ? `${d.mikor} · ` : '', 'e-mailből kinyert javaslat']),
    d.reszlet ? el('div', { class: 'halk', style: 'margin-top:4px' }, d.reszlet) : null, sor, hiba);
}

function esemenySor(e) {
  const jel = { feladat: '☐', vita: '#', brief: '≡' }[e.tipus] || '·';
  const a = (e.allapot || '').toLowerCase();
  const fajta = /(completed|done|kész|resolved|closed)/.test(a) ? 'kesz' : /(progress|fut|open)/.test(a) ? 'fut'
    : /(fail|hiba|cancel)/.test(a) ? 'hiba' : 'semleges';
  const felirat = { pending: 'vár', in_progress: 'folyamatban', completed: 'kész', open: 'nyitott', resolved: 'lezárva', failed: 'hiba', cancelled: 'törölve' }[a] || e.allapot;
  return el('button', { class: 'esemeny', onclick: () => esemenyNyit(e) },
    el('span', { class: 'jel' }, jel),
    el('span', {}, el('span', { class: 'cim' }, e.cim), el('small', {}, e.ki || '')),
    el('span', { class: 'jobbra' }, felirat ? allapotChip(felirat, fajta) : null, el('small', {}, relativ(e.ido))));
}

async function esemenyNyit(e) {
  if (e.tipus === 'vita') { nezetValt({ tipus: 'vita', id: e.id }); return; }
  if (e.tipus === 'feladat') { modal(`Feladat #${e.id}`, `${e.cim}\n\n${e.ki} · ${e.allapot}\n\n${e.reszlet || '(nincs leírás)'}`); return; }
  if (e.tipus === 'brief') {
    try {
      const b = await api(`/api/ui/brief/${e.id}`);
      modal(e.cim, b.szoveg, true);
    } catch (err) { hibaKezel(err); }
  }
}

function modal(cim, szoveg, mdvel = false) {
  const d = el('dialog', { class: 'modal' },
    el('div', { class: 'modal-fej' }, el('b', {}, cim), el('button', { class: 'ikon-gomb', 'aria-label': 'Bezár', onclick: () => d.close() }, '✕')),
    el('div', { class: 'modal-test', html: mdvel ? md(szoveg) : esc(szoveg) }));
  d.addEventListener('close', () => d.remove());
  d.addEventListener('click', (ev) => { if (ev.target === d) d.close(); });
  document.body.append(d);
  d.showModal();
}

function koltesBlokk(k) {
  const fej = el('div', { class: 'szakasz-cim' }, 'LLM-költés');
  if (!k) return el('section', { class: 'blokk' }, fej, el('div', { class: 'ures' }, 'Nincs adat.'));
  if (!k.elerheto) {
    return el('section', { class: 'blokk' }, fej, el('div', { class: 'meroora' },
      el('div', { class: 'halk' }, 'Az Anthropic-kapu ki van kapcsolva (nincs kulcs): minden a SiliconFlow-on megy.')));
  }
  const arany = k.keret_usd ? Math.min(1, k.koltes_usd / k.keret_usd) : 0;
  const sorok = Object.entries(k.modellek || {}).map(([szint, modell]) => {
    const s = (k.szintenkent || {})[szint] || { hivasok: 0, usd: 0 };
    return el('tr', {}, el('td', {}, SZINT_NEV[szint] || szint, ' ', el('small', {}, modell)), el('td', {}, `${s.hivasok}×`), el('td', {}, `$${Number(s.usd).toFixed(3)}`));
  });
  return el('section', { class: 'blokk' }, fej, el('div', { class: 'meroora' },
    el('div', { class: 'nagy' }, `$${Number(k.koltes_usd).toFixed(2)} `, el('small', {}, `/ $${Number(k.keret_usd).toFixed(0)} · ${k.honap}`)),
    el('div', { class: 'sav' }, el('i', { class: arany > 0.9 ? 'forro' : arany > 0.7 ? 'meleg' : '', style: `width:${(arany * 100).toFixed(1)}%` })),
    el('div', { class: 'halk' }, `${k.hivasok} hívás · elsődleges: ${k.elsodleges}${k.keret_kimerult ? ' · ⚠ a havi keret elfogyott' : ''}`),
    el('table', { class: 'szint-tabla' }, el('tbody', {}, sorok)),
    el('div', { class: 'halk', style: 'margin-top:6px' }, k.megjegyzes || '')));
}

// ── FRISSÍTÉS ─────────────────────────────────────────────────
async function frissit({ tortenet = false } = {}) {
  const a = await api('/api/ui/allapot');
  if (a.most) S.eltolas = ms(a.most) - Date.now();
  const valtozott = a.utolso_id !== S.utolsoId;
  S.utolsoId = a.utolso_id;
  S.adat = a;
  balRajzol();
  fejlecRajzol();
  munkapadRajzol();
  const kulcs = nezetKulcs();
  if ((tortenet || valtozott) && !S.futok[kulcs] && S.nezet.tipus !== 'vita') {
    await tortenetBetolt({ csendes: true });
  }
}

function hibaKezel(e) {
  if (e instanceof Hiba401) return;
  console.warn(e);
  ertesit(e.message || String(e), true);
}

async function indit() {
  try {
    S.adat = await api('/api/ui/allapot');
  } catch (e) {
    if (!(e instanceof Hiba401)) {
      document.getElementById('gyoker').replaceChildren(el('div', { class: 'atirat-ures' }, el('b', {}, 'A Bridge most nem válaszol'), e.message));
    }
    return;
  }
  if (S.adat.most) S.eltolas = ms(S.adat.most) - Date.now();
  S.utolsoId = S.adat.utolso_id;
  const mentett = olvas('kabin.nezet', null);
  if (mentett && (mentett.tipus === 'postalada' || (mentett.tipus === 'ugynok' && UGYNOK[mentett.id]) || mentett.tipus === 'vita')) S.nezet = mentett;
  kabinEpit();
  balRajzol(); fejlecRajzol(); munkapadRajzol();
  S.ref.atirat.replaceChildren(el('div', { class: 'atirat-ures' }, 'Betöltés…'));
  tortenetBetolt().catch((e) => {
    if (S.nezet.tipus === 'vita') nezetValt({ tipus: 'ugynok', id: 'feldwebel' });
    else hibaKezel(e);
  });
  clearInterval(S.idozito);
  S.idozito = setInterval(() => { if (document.visibilityState === 'visible') frissit({}).catch(hibaKezel); }, 12000);
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && S.idozito) frissit({}).catch(hibaKezel);
});
addEventListener('keydown', (e) => { if (e.key === 'Escape') fiokZar(); });
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/app/sw.js', { scope: '/app/' }).catch(() => {});
indit();
