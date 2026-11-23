// Bridge UI v2 — „kabinüveg” (2026-11-22). Vanilla JS, build-lépés nélkül; minden adat a /api/ui/* utakról jön,
// a munkamenet-sütivel (httpOnly — a szkript nem látja, csak a böngésző küldi).
//
// KÖZÖS FELADATOK (11-23, a Kommandant: „nem látom a boardon … azt se, h meg lettek kérdezve”): a munkapadon az
// `ai_task` feladatai beszédes állapottal (melyik ügynök válaszolt már, készül-e az összefoglaló); kattintásra a leírás,
// a válaszok a modellel, az összefoglaló, a DOCX — és a „Szólj bele” mező, amiből folytató feladat indul.
//
// Bal sáv: az ügynökök (Claus/Feldwebel a fő asszisztens, Kimi, DeepSeek, GLM, Faber) állapottal, alattuk a
// FELADAT-ÜGYNÖKÖK (11-23: a Kommandant saját, névvel ellátott munkásai — „+ Új ügynök”, saját ablak a futásokkal, a
// költéssel és a Futtasd most / Leállít / Újraindít / Megszüntet gombokkal), alatta a beszélgetések (postaláda,
// viták). Közép: beszélgetés a választott ügynökkel — gépelésjelző, élő
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
  ugynok: 'Feladat-ügynököt kezelt',
  schedule_recipe: 'Ütemezett egy receptet', capture_inbox: 'Megnézte a leveleket', read_email: 'Elolvasott egy levelet',
  send_email: 'Levelet küldött', create_task: 'Feladatot vett fel', list_tasks: 'Megnézte a feladatokat',
  create_calendar_event: 'Naptárba írt', read_memory: 'A közös memóriában olvasott', search_memory: 'A memóriában keresett',
  statdata: 'StatData-adatot kért', analyze_upload: 'Képet elemzett',
};
const SZINT_NEV = { okos: 'okos', kozep: 'közép', olcso: 'olcsó' };
const FELADAT_SZIN = '#f472b6';
const feladatE = (id) => typeof id === 'string' && /^ugynok:\d+$/.test(id);
const feladatUgynok = (id) => S.adat?.ugynokok.find((u) => u.id === id)?.feladat_ugynok || null;
// egy ügynök jele: a fixeké a UGYNOK-ból, a feladat-ügynököké a nevéből
function jel(ki) {
  if (UGYNOK[ki]) return UGYNOK[ki];
  if (feladatE(ki)) {
    const nev = S.adat?.ugynokok.find((u) => u.id === ki)?.nev || 'Ügynök';
    return { betu: nev.slice(0, 1).toUpperCase(), szin: FELADAT_SZIN, rovid: nev };
  }
  return null;
}

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
  feladatAblak: null,               // a nyitott közös feladat ablaka: {id, d, test, fejCim, jel, rajzolva, dolgozik}
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
  const u = jel(ki);
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
async function gyorsKapcs(f, m, gomb) {
  if (m === 'megszuntet' && !confirm(`Végleg megszünteted: „${f.nev}”? (A futásai naplója megmarad.)`)) return;
  gomb.disabled = true;
  try {
    const j = await api('/api/ui/ugynok/muvelet', { method: 'POST', body: { id: f.id, muvelet: m } });
    ertesit(j.uzenet || 'Rendben.');
    await frissit({ tortenet: S.nezet.id === f.id && m !== 'megszuntet' });
    if (m === 'megszuntet' && S.nezet.id === f.id) nezetValt({ tipus: 'ugynok', id: 'feldwebel' });
  } catch (e) {
    if (e instanceof Hiba401) return;
    ertesit(e.message, true);
    gomb.disabled = false;
  }
}

function balRajzol() {
  const a = S.adat;
  if (!a) return;
  const sorok = a.ugynokok.map((u) => {
    const pont = u.fut || S.futok[`u:${u.id}`] ? 'dolgozik' : u.elerheto ? 'el' : 'ki';
    const aktiv = S.nezet.tipus === 'ugynok' && S.nezet.id === u.id;
    const uj = u.utolso && u.utolso.ki !== 'kommandant' && u.utolso.id > (S.latott[u.id] || 0) && !aktiv;
    const elo = u.utolso ? `${u.utolso.ki === 'kommandant' ? 'Te: ' : ''}${u.utolso.szoveg}` : u.szerep;
    const f = u.feladat_ugynok;
    const sor = el('button', {
      class: `ugynok${aktiv ? ' aktiv' : ''}${f ? ' van-kapcs' : ''}`, title: u.modell ? `kért modell: ${u.modell}` : u.szerep,
      onclick: () => nezetValt({ tipus: 'ugynok', id: u.id }),
    }, avatar(u.id, { pont }),
    el('span', { class: 'ugynok-test' }, el('span', { class: 'ugynok-nev' }, u.nev), el('span', { class: 'ugynok-elo' }, elo)),
    el('span', { class: 'ugynok-ido' }, relativ(u.utoljara)),
    uj ? el('span', { class: 'olvasatlan', 'aria-label': 'új üzenet' }) : null);
    if (!f) return sor;
    // GYORS KIKAPCSOLÁS (11-23, a Kommandant: „a már nem kellő ügynököt ki is kéne a kabinban kapcsolni tudni”):
    // aktív (vagy dolgozik) → ⏻ leállít; leállított / kész → ✕ végleg megszüntet (megerősítéssel)
    const be = f.allapot === 'aktiv' || f.fut;
    const kapcs = el('button', {
      class: `ugynok-kapcs${be ? '' : ' vesz'}`, type: 'button',
      title: be ? `Leállít: „${f.nev}” (az ütemezés szünetel; ha dolgozik, megáll)` : `Megszüntet: „${f.nev}” (${f.allapot_szoveg}; végleg)`,
      'aria-label': be ? `${f.nev} leállítása` : `${f.nev} megszüntetése`,
      onclick: (ev) => { ev.stopPropagation(); gyorsKapcs(f, be ? 'leallit' : 'megszuntet', kapcs); },
    }, be ? '⏻' : '✕');
    return el('div', { class: 'ugynok-sor' }, sor, kapcs);
  });
  const fix = sorok.filter((_, i) => !a.ugynokok[i].feladat_ugynok);
  const feladat = sorok.filter((_, i) => a.ugynokok[i].feladat_ugynok);
  S.ref.ugynokok.replaceChildren(...fix,
    el('div', { class: 'szakasz-cim' }, el('span', {}, 'Feladat-ügynökök'),
      el('button', { class: 'gomb gomb-kicsi', title: 'Új feladat-ügynök indítása', onclick: () => ujUgynokUrlap() }, '+ Új ügynök')),
    ...(feladat.length ? feladat : [el('div', { class: 'ures', style: 'padding:4px 10px' },
      'Nincs még. Mondd Clausnak: „indíts egy ügynököt, ami…”, vagy nyomd meg a gombot.')]));
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
  if (n.tipus === 'ugynok' && feladatE(n.id)) {
    const f = feladatUgynok(n.id);
    ki = n.id; cim = f ? f.nev : 'Feladat-ügynök';
    const elter = f?.utolso_modell && f.utolso_modell !== f.modell_azonosito;   // a kapu tartaléka válaszolt utoljára
    al = !f ? 'megszüntetve' : [f.fut ? 'dolgozik…' : f.allapot_szoveg, elter ? `${f.modell_nev} (utoljára: ${modellRovid(f.utolso_modell)})` : f.modell_nev, f.utemezes_szoveg,
      f.kovetkezo_szoveg ? `köv.: ${f.kovetkezo_szoveg}` : null,
      `e hónap $${f.koltes.honap_usd.toFixed(2)} / $${f.havi_keret_usd.toFixed(0)}`].filter(Boolean).join(' · ');
  } else if (n.tipus === 'ugynok') {
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
  if (t.futas) {
    const f = t.futas;
    meta.append(el('span', { class: 'chip', title: f.koltes_becsult ? 'SiliconFlow-ár szerinti becslés / a futás kerete' : 'a futás költsége / a futás kerete' },
      `${f.koltes_becsult ? '~' : ''}$${Number(f.koltes_usd || 0).toFixed(2)} / $${Number(f.keret_usd || 0).toFixed(2)}`));
    if (f.kert_modell && t.modell && f.kert_modell !== t.modell) meta.append(el('span', { class: 'chip', title: 'a kapu tartaléka válaszolt' }, `kért: ${modellRovid(f.kert_modell)}`));
    if (f.jelzes) meta.append(el('span', { class: 'chip faber' }, 'szólt neked'));
    if (f.trigger && f.trigger !== 'cron') meta.append(el('span', { class: 'chip' }, `indította: ${f.kuldo && f.kuldo !== 'a Kommandant' ? f.kuldo : f.trigger}`));
  }
  if (t.ido) meta.append(el('span', {}, oraPerc(t.ido)));
  if (t.fuggo) meta.append(el('span', {}, 'küldés…'));
  return el('div', { class: `sor ${en ? 'en' : 'o'}${t.hiba ? ' hiba' : ''}` },
    mutatNev ? el('div', { class: 'ki-nev' }, avatar(t.ki, { kicsi: true }), jel(t.ki)?.rovid || t.ki) : null,
    t.targy ? el('div', { class: 'ki-nev', style: 'color:var(--ink)' }, t.targy) : null,
    buborek(t),
    meta.childNodes.length ? meta : null);
}

// MELLÉKLET (2026-11-22): a `[Melléklet #ID: név — címke]` blokk összecsukva — a fejléc látszik, a szöveg lenyitható.
const MELLEKLET_RE = /\[Melléklet #(\d+): ([^\n]*?) — ([^\n]*)\]\n?([\s\S]*?)(?=\n\n\[Melléklet #|$)/g;
function buborek(t) {
  const szoveg = t.szoveg || '';
  const hol = szoveg.indexOf('\n\n[Melléklet #');
  const b = el('div', { class: `buborek${t.fuggo ? ' fuggo' : ''}`, html: md(hol >= 0 ? szoveg.slice(0, hol) : szoveg) });
  if (hol < 0) return b;
  for (const m of szoveg.slice(hol + 2).matchAll(MELLEKLET_RE)) {
    const [, , nev, fej, torzs] = m;
    const link = (fej.match(/szerkesztő: (https:\/\/\S+)/) || [])[1];
    const cimke = fej.split(' · ').filter((x) => !x.startsWith('Studio file_id=') && !x.startsWith('szerkesztő:')).join(' · ');
    b.append(el('details', { class: 'melleklet' },
      el('summary', {}, `📎 ${nev}`, el('span', { class: 'halk' }, ` · ${cimke}`)),
      link ? el('a', { href: link, target: '_blank', rel: 'noopener', class: 'melleklet-link' }, 'megnyitás a Studióban ↗') : null,
      torzs.trim() ? el('div', { class: 'melleklet-szoveg', html: md(torzs.slice(0, 20000)) }) : null));
  }
  return b;
}

async function mellekletFeltolt(fajl, t, rajzol) {
  try {
    const fd = new FormData();
    fd.append('fajl', fajl, fajl.name);
    const r = await fetch('/api/ui/melleklet', { method: 'POST', credentials: 'same-origin', body: fd });
    if (r.status === 401) { belepesNezet(); throw new Hiba401('belépés kell'); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.hiba || `HTTP ${r.status}`);
    Object.assign(t, { allapot: 'kesz', id: j.id, cimke: j.cimke });
  } catch (e) {
    Object.assign(t, { allapot: 'hiba', hiba: e.message });
  }
  rajzol();
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
  if (feladatE(n.id)) {
    const f = feladatUgynok(n.id);
    return el('div', { class: 'atirat-ures' }, el('b', {}, 'Még nem futott'),
      f?.kovetkezo_szoveg ? `Az első futás: ${f.kovetkezo_szoveg}. Ha most kell, nyomd meg a „Futtasd most” gombot.` : 'Hamarosan itt lesz az első futás eredménye.');
  }
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
  if (n.tipus === 'ugynok' && feladatE(n.id)) { ugynokPanelRajzol(); return; }
  if (n.tipus === 'postalada') {
    S.ref.szerkeszto.replaceChildren(el('div', { class: 'szerkeszto-zar' }, 'A postaládára nem lehet válaszolni — írj Clausnak, ő továbbadja.'));
    S.ref.input = null;
    return;
  }
  const ta = el('textarea', { rows: '1', placeholder: HELYKITOLTO[n.tipus === 'vita' ? 'vita' : n.id] || 'Üzenet…', 'aria-label': 'Üzenet' });
  const kuld = el('button', { class: 'ikon-gomb kuld', type: 'submit', 'aria-label': 'Küldés', title: 'Küldés', disabled: true,
    html: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M3.4 20.4 21 12 3.4 3.6 3.3 10l12.6 2-12.6 2z"/></svg>' });
  const dikt = diktalas(ta, { kuldd: () => urlapKuld() });
  // MELLÉKLET: fénykép, PDF, Word… (telefonon a kamera is) → a Studióba és a Feldwebelhez
  const mellekletek = [];
  const csempek = el('div', { class: 'melleklet-csempek' });
  const fajlInput = el('input', { type: 'file', multiple: true, hidden: true });
  const csatol = (n.tipus === 'ugynok' && n.id !== 'faber')
    ? el('button', { class: 'ikon-gomb', type: 'button', 'aria-label': 'Melléklet', title: 'Fénykép vagy dokumentum csatolása',
      onclick: () => fajlInput.click(),
      html: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M21 11.5 12.4 20a5.5 5.5 0 0 1-7.8-7.8l8.9-8.9a3.7 3.7 0 0 1 5.2 5.2l-8.9 8.9a1.8 1.8 0 0 1-2.6-2.6l8.1-8.1"/></svg>' })
    : null;
  function csempeRajzol() {
    csempek.replaceChildren(...mellekletek.map((t, i) => el('span', { class: `melleklet-csempe ${t.allapot}`, title: t.hiba || t.cimke || '' },
      `📎 ${t.nev}`,
      el('span', { class: 'halk' }, t.allapot === 'fel' ? ' · olvasom…' : t.allapot === 'hiba' ? ` · ${t.hiba}` : ` · ${t.cimke || 'kész'}`),
      el('button', { type: 'button', class: 'csempe-x', 'aria-label': 'Eltávolítás', onclick: () => { mellekletek.splice(i, 1); csempeRajzol(); } }, '×'))));
    engedelyez();
  }
  fajlInput.addEventListener('change', () => {
    for (const f of fajlInput.files) {
      const t = { nev: f.name, allapot: 'fel' };
      mellekletek.push(t);
      mellekletFeltolt(f, t, csempeRajzol);
    }
    fajlInput.value = '';
    csempeRajzol();
  });
  const gombok = el('div', { class: 'szerkeszto-gombok' }, csatol, dikt ? dikt.gomb : null, kuld);
  const durva = matchMedia('(pointer: coarse)').matches;
  const urlap = el('form', { class: 'szerkeszto-hej' }, csempek, ta, fajlInput,
    el('div', { class: 'szerkeszto-sor' }, el('span', { class: 'halk' }, durva ? '' : 'Enter: küldés · Shift+Enter: új sor'), gombok));
  const meret = () => { ta.style.height = 'auto'; ta.style.height = `${Math.min(ta.scrollHeight, innerHeight * 0.4)}px`; };
  const kesz = () => mellekletek.filter((t) => t.allapot === 'kesz');
  const engedelyez = () => {
    kuld.disabled = (!ta.value.trim() && !kesz().length) || mellekletek.some((t) => t.allapot === 'fel')
      || Boolean(S.futok[nezetKulcs()]);
  };
  ta.addEventListener('input', () => { meret(); engedelyez(); });
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !durva) { e.preventDefault(); urlapKuld(); }
  });
  function urlapKuld() {
    const szoveg = ta.value.trim();
    const mm = kesz();
    if ((!szoveg && !mm.length) || S.futok[nezetKulcs()] || mellekletek.some((t) => t.allapot === 'fel')) return;
    dikt?.cancel();
    ta.value = ''; meret();
    mellekletek.length = 0; csempeRajzol();
    kuldes(szoveg, mm);
  }
  urlap.addEventListener('submit', (e) => { e.preventDefault(); urlapKuld(); });
  S.ref.szerkeszto.replaceChildren(el('div', { class: 'szerkeszto' }, urlap, dikt ? dikt.jegyzet : null));
  S.ref.input = ta;
  S.ref.engedelyez = engedelyez;
  if (!durva) setTimeout(() => ta.focus(), 30);
}

async function kuldes(szoveg, mellekletek = []) {
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
  const lathato = [szoveg, ...mellekletek.map((t) => `📎 ${t.nev}`)].filter(Boolean).join('\n');
  const fuggo = { ido: indult, ki: 'kommandant', szoveg: lathato, tipus: 'uzenet', csatorna: 'web', fuggo: true };
  (S.ideiglenes[kulcs] ||= []).push(fuggo);
  S.futok[kulcs] = { ugynok: n.id, allapot: 'fut', valaszok: [], eszkozok: [], indult, sajat: fuggo };
  atiratRajzol({ gorget: true });
  fejlecRajzol(); balRajzol(); S.ref.engedelyez?.();
  try {
    const j = await api('/api/ui/chat', { method: 'POST', body: { szoveg, ugynok: n.id, mellekletek: mellekletek.map((t) => t.id) } });
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

// ── FELADAT-ÜGYNÖK: panel az ablak alján + „+ Új ügynök” ──────
function ugynokPanelRajzol() {
  const n = S.nezet;
  const f = feladatUgynok(n.id);
  S.ref.input = null;
  // a 12 mp-es frissítés újrarajzolja: a lenyitott részletek maradjanak nyitva, futó művelet és gépelés közben ne
  // rajzoljon; a félig megírt utasítás megmarad
  const regi = S.ref.szerkeszto.querySelector('.ugynok-panel');
  if (regi?.dataset.dolgozik) return;
  const regiTa = regi?.querySelector('textarea.utasitas');
  if (regiTa && document.activeElement === regiTa && regi.dataset.id === n.id) return;
  const vazlat = regi?.dataset.id === n.id ? (regiTa?.value || '') : '';
  const nyitva = Boolean(regi?.querySelector('details')?.open) && regi.dataset.id === n.id;
  if (!f) { S.ref.szerkeszto.replaceChildren(el('div', { class: 'szerkeszto-zar' }, 'Ez az ügynök már nincs meg.')); return; }
  const hiba = el('div', { class: 'kartya-hiba', hidden: true });
  const gombok = el('div', { class: 'kartya-gombok' });
  const muvelet = async (m, kerdes) => {
    if (kerdes && !confirm(kerdes)) return;
    const panel = S.ref.szerkeszto.querySelector('.ugynok-panel');
    if (panel) panel.dataset.dolgozik = '1';
    gombok.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    try {
      const j = await api('/api/ui/ugynok/muvelet', { method: 'POST', body: { id: n.id, muvelet: m } });
      ertesit(j.uzenet || 'Rendben.');
      if (panel) delete panel.dataset.dolgozik;
      if (m === 'megszuntet') { await frissit({}); nezetValt({ tipus: 'ugynok', id: 'feldwebel' }); return; }
      await frissit({ tortenet: true });
    } catch (e) {
      if (e instanceof Hiba401) return;
      if (panel) delete panel.dataset.dolgozik;
      hiba.hidden = false; hiba.textContent = e.message;
      gombok.querySelectorAll('button').forEach((b) => { b.disabled = false; });
    }
  };
  gombok.append(
    el('button', { class: 'gomb gomb-fo gomb-kicsi', disabled: f.fut, title: 'Egy futás most, az ütemezéstől függetlenül', onclick: () => muvelet('futtat') }, f.fut ? 'Dolgozik…' : 'Futtasd most'),
    f.allapot === 'aktiv'
      ? el('button', { class: 'gomb gomb-kicsi', title: 'Az ütemezés szünetel; ha éppen dolgozik, megáll', onclick: () => muvelet('leallit') }, 'Leállít')
      : el('button', { class: 'gomb gomb-kicsi', onclick: () => muvelet('ujraindit') }, 'Újraindít'),
    el('button', { class: 'gomb gomb-kicsi', title: 'A legutóbbi jelentés Word-dokumentumként (szerkesztő-link a Studióban)', onclick: () => muvelet('dokumentum') }, 'Word'),
    el('button', { class: 'gomb gomb-kicsi', title: 'Modell, extra prompt, ütemezés, keret, jogosultságok', onclick: () => ujUgynokUrlap(f) }, 'Szerkeszt'),
    el('button', { class: 'gomb gomb-kicsi gomb-vesz', onclick: () => muvelet('megszuntet', `Végleg megszünteted: „${f.nev}”?`) }, 'Megszüntet'));
  // ÚJ UTASÍTÁS (11-23, a Kommandant: „itt egyébként nem tudok az agentnek új utasítást adni”): egy azonnali futás
  // ugyanazzal az ügynökkel, az utasítással a lap tetején; az állandó feladat marad (arra a „Szerkeszt” való)
  const ta = el('textarea', { class: 'utasitas', rows: '2', maxlength: '4000', 'aria-label': 'Új utasítás az ügynöknek',
    placeholder: f.fut ? 'Most dolgozik — a futás végén küldheted az új utasítást…' : 'Új utasítás az ügynöknek… (pl. „a tanulmány itt van: …, ebből dolgozz”)' });
  ta.value = vazlat;
  const kuldGomb = el('button', { class: 'gomb gomb-fo gomb-kicsi', type: 'submit', disabled: !vazlat.trim() || f.fut,
    title: 'Egy futás most ezzel az utasítással; az állandó feladat nem változik' }, 'Küldöm');
  ta.addEventListener('input', () => { kuldGomb.disabled = !ta.value.trim() || f.fut; });
  ta.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !matchMedia('(pointer: coarse)').matches) { e.preventDefault(); urlap.requestSubmit(); }
  });
  const urlap = el('form', { class: 'utasitas-sor' }, ta, kuldGomb);
  urlap.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const szoveg = ta.value.trim();
    if (!szoveg || f.fut) return;
    const panel = S.ref.szerkeszto.querySelector('.ugynok-panel');
    if (panel) panel.dataset.dolgozik = '1';
    kuldGomb.disabled = true; ta.disabled = true; hiba.hidden = true;
    try {
      const j = await api('/api/ui/ugynok/utasitas', { method: 'POST', body: { id: n.id, szoveg } });
      ta.value = '';
      ertesit(j.uzenet || 'Elküldtem — dolgozik rajta.');
      if (panel) delete panel.dataset.dolgozik;
      await frissit({ tortenet: true });
    } catch (e) {
      if (panel) delete panel.dataset.dolgozik;
      if (e instanceof Hiba401) return;
      hiba.hidden = false; hiba.textContent = e.message;
      kuldGomb.disabled = false; ta.disabled = false;
    }
  });
  const k = f.koltes;
  const valodi = f.utolso_modell && f.utolso_modell !== f.modell_azonosito ? modellRovid(f.utolso_modell) : '';
  const adat = [
    ['Feladat', f.feladat],
    ['Modell', `${f.modell_nev}${valodi ? ` · utoljára a kapu tartaléka válaszolt: ${valodi}` : ''}${f.szolgaltato === 'siliconflow' ? ' · tudásbázis nélkül' : ''}`],
    f.extra_prompt ? ['Extra prompt', f.extra_prompt] : null,
    ['Megvitathatja', f.vitapartnerek?.length ? `${f.vitapartnerek.map((p) => p.nev).join(', ')} (legfeljebb ${f.vita_max} kérdés/futás)` : 'senkivel'],
    ['Feladatot adhat', f.utasithat?.length ? f.utasithat.map((p) => p.nev).join(', ') : 'senkinek'],
    ['Ütemezés', `${f.utemezes_szoveg}${f.kovetkezo_szoveg ? ` · legközelebb ${f.kovetkezo_szoveg}` : ''}${f.ok ? ` · ${f.ok}` : ''}`],
    ['Szól', f.ertesites === 'mindig' ? 'minden futás után' : 'ha van mit jelezni'],
    ['Word-dokumentum', { auto: 'magától, ha kutatás / életrajz / összefoglaló a feladat', mindig: 'minden futás után', soha: 'nem kell' }[f.dokumentum || 'auto']],
    f.kaszkad ? ['Kaszkád', 'BE (mérésre): a gyűjtés Haikuval, az írás a saját modelljével'] : null,
    ['Költés', `utolsó $${Number(k.utolso_usd || 0).toFixed(2)} · e hónap $${k.honap_usd.toFixed(2)} / $${f.havi_keret_usd.toFixed(0)} · összesen $${k.osszes_usd.toFixed(2)} (${k.futasok} futás) · keret $${f.keret_usd.toFixed(2)}/futás`],
  ];
  S.ref.szerkeszto.replaceChildren(el('div', { class: 'szerkeszto' }, el('div', { class: 'szerkeszto-hej ugynok-panel', 'data-id': n.id },
    el('details', { open: nyitva }, el('summary', {}, el('span', {}, allapotChip(f.fut ? 'dolgozik' : f.allapot_szoveg, f.fut ? 'fut' : f.allapot === 'aktiv' ? 'kesz' : 'semleges')),
      el('span', { class: 'halk' }, ` ${f.feladat.slice(0, 90)}${f.feladat.length > 90 ? '…' : ''}`)),
    el('dl', { class: 'ugynok-adat' }, adat.filter(Boolean).map(([c, v]) => [el('dt', {}, c), el('dd', {}, v)]))),
    urlap, gombok, hiba,
    el('div', { class: 'halk', style: 'margin-top:6px' }, 'Kifelé nem lép: levélből tervezet, naptárból javaslat lesz. Módosíthatod a „Szerkeszt” gombbal, vagy szólj Clausnak.'))));
}

// a modell rövid neve, mint a szerveren (cimzett.modell_rovid): 'claude-sonnet-5-5' → 'Sonnet 5.5'
function modellRovid(m) {
  if (!m) return '';
  if (m.startsWith('claude-')) { const r = m.slice(7).split('-'); return `${r[0][0].toUpperCase()}${r[0].slice(1)} ${r.slice(1).join('.')}`; }
  return m.split('/').pop();
}
const MODELL_TARTALEK = [{ kulcs: 'sonnet', nev: 'Claude Sonnet 5.5', szolgaltato: 'anthropic' }];

// „+ Új ügynök” és „Szerkeszt”: ugyanaz az űrlap; szerkesztésnél csak a változás megy (a szerver dönti el, mi változott)
function ujUgynokUrlap(f = null) {
  const szerk = Boolean(f);
  const mezo = (cimke, input, sugo) => el('label', { class: 'urlap-mezo' }, el('span', {}, cimke), input, sugo ? el('small', { class: 'halk' }, sugo) : null);
  const nev = el('input', { maxlength: '60', placeholder: 'pl. Kamatfigyelő', required: true, value: f?.nev || '' });
  const feladat = el('textarea', { rows: '4', maxlength: '4000', placeholder: 'pl. Nézd meg az MNB legfrissebb közleményeit, és szólj, ha kamatdöntés van (mennyi lett, mihez képest).', required: true });
  feladat.value = f?.feladat || '';
  const modellek = S.adat?.feladat_ugynok_modellek?.length ? S.adat.feladat_ugynok_modellek : MODELL_TARTALEK;
  const modell = el('select', {}, modellek.map((m) => el('option', { value: m.kulcs, selected: m.kulcs === (f?.modell || 'sonnet') },
    `${m.nev}${m.szolgaltato === 'siliconflow' ? ' — SiliconFlow, tudásbázis nélkül' : ''}`)));
  const extra = el('textarea', { rows: '3', maxlength: '2000', placeholder: 'pl. Légy tömör: legfeljebb 3 mondat, a számok táblázatban.' });
  extra.value = f?.extra_prompt || '';
  const mikor = el('select', {}, [...(szerk ? [['marad', `Marad: ${f.utemezes_szoveg}`]] : []), ['most', szerk ? 'Egyszer (nincs ütemezés)' : 'Egyszer, most'],
    ['napi', 'Minden nap'], ['hetkoznap', 'Hétköznap'], ['heti', 'Hetente'], ['oranken', 'Óránként'], ['ekkor', 'Egyszer, ekkor'], ['cron', 'Saját cron']]
    .map(([v, t]) => el('option', { value: v }, t)));
  const ora = el('input', { type: 'time', value: '07:00' });
  const nap = el('select', {}, ['vasárnap', 'hétfő', 'kedd', 'szerda', 'csütörtök', 'péntek', 'szombat'].map((t, i) => el('option', { value: String(i), selected: i === 1 }, t)));
  const ekkor = el('input', { type: 'datetime-local' });
  const cron = el('input', { placeholder: '0 7 * * *', spellcheck: 'false', value: f?.utemezes || '' });
  const ertesites = el('select', {}, szerk ? null : el('option', { value: '' }, 'Magától (figyelőnél: ha van mit jelezni)'),
    el('option', { value: 'ha_van', selected: f?.ertesites === 'ha_van' }, 'Csak ha van mit jelezni'),
    el('option', { value: 'mindig', selected: f?.ertesites === 'mindig' }, 'Minden futás után'));
  const keret = el('input', { type: 'number', min: '0.05', max: '5', step: '0.05', value: String(f?.keret_usd ?? '0.50') });
  const havi = el('input', { type: 'number', min: '1', max: '50', step: '1', value: String(f?.havi_keret_usd ?? '10') });
  const vitaMax = el('input', { type: 'number', min: '1', max: '6', step: '1', value: String(f?.vita_max ?? 3) });
  const dokumentum = el('select', {}, [['auto', 'Magától (kutatás, életrajz, összefoglaló)'], ['mindig', 'Minden futás után'], ['soha', 'Nem kell']]
    .map(([v, t]) => el('option', { value: v, selected: v === (f?.dokumentum || 'auto') }, t)));
  const kaszkad = el('input', { type: 'checkbox', checked: Boolean(f?.kaszkad) });
  // jogosultságok: a fix partnerek és a többi feladat-ügynök (önmagát nem)
  const tobbi = (S.adat?.ugynokok || []).filter((u) => u.feladat_ugynok && u.id !== f?.id).map((u) => ({ id: u.id, nev: u.nev }));
  const pipak = (lehet, van) => {
    const valasztott = new Set((van || []).map((p) => p.id));
    const dobozok = lehet.map((p) => el('label', { class: 'pipa' }, el('input', { type: 'checkbox', value: p.id, checked: valasztott.has(p.id) }), ` ${p.nev}`));
    return { doboz: el('div', { class: 'pipak' }, dobozok.length ? dobozok : el('span', { class: 'halk' }, 'nincs kit')),
      ertek: () => dobozok.map((d) => d.firstChild).filter((i) => i.checked).map((i) => i.value) };
  };
  const vita = pipak([{ id: 'kimi', nev: 'Kimi' }, { id: 'deepseek', nev: 'DeepSeek' }, { id: 'glm5', nev: 'GLM-5.3' }, { id: 'feldwebel', nev: 'Feldwebel' }, ...tobbi], f?.vitapartnerek);
  const utasit = pipak([...tobbi, { id: 'faber', nev: 'Faber (csak kívánság)' }], f?.utasithat);
  const oraMezo = mezo('Időpont', ora), napMezo = mezo('Nap', nap), ekkorMezo = mezo('Mikor', ekkor), cronMezo = mezo('Cron', cron, 'perc óra nap hónap hétnap, Budapest-idő; legsűrűbb 15 perc');
  const igazit = () => {
    const v = mikor.value;
    oraMezo.hidden = !['napi', 'hetkoznap', 'heti'].includes(v); napMezo.hidden = v !== 'heti';
    ekkorMezo.hidden = v !== 'ekkor'; cronMezo.hidden = v !== 'cron';
  };
  mikor.addEventListener('change', igazit); igazit();
  const hiba = el('div', { class: 'kartya-hiba', hidden: true });
  const indit = el('button', { class: 'gomb gomb-fo', type: 'submit' }, szerk ? 'Mentés' : 'Indítás');
  const urlap = el('form', { class: 'urlap' }, mezo('Név', nev), mezo('Feladat', feladat, 'Önállóan érthetően: mit nézzen, mikor szóljon, mit adjon vissza.'),
    mezo('Modell', modell, 'A tudásbázist csak Claude-modell olvashatja; a SiliconFlow-modellek nem kapják meg.'),
    mezo('Extra prompt (viselkedés, szerep)', extra, 'A szabályok után kerül a promptba: a szabályokat és a jogokat nem írhatja felül.'),
    mezo('Mikor fusson', mikor), oraMezo, napMezo, ekkorMezo, cronMezo, mezo('Szóljon', ertesites),
    mezo('Word-dokumentum', dokumentum, 'A jelentés Wordben is, szerkesztő-linkkel (a kabinban és a Telegramon).'),
    el('label', { class: 'pipa' }, kaszkad, ' Kaszkád — csak mérésre: a gyűjtés Haikuval, az írás a választott modellel'),
    el('div', { class: 'urlap-sor' }, mezo('Keret / futás ($)', keret), mezo('Havi keret ($)', havi)),
    el('fieldset', { class: 'urlap-mezo jogok' }, el('legend', {}, 'Megvitathatja velük (futás közben)'), vita.doboz, mezo('Legfeljebb ennyi kérdés futásonként', vitaMax)),
    el('fieldset', { class: 'urlap-mezo jogok' }, el('legend', {}, 'Feladatot adhat nekik'), utasit.doboz,
      el('small', { class: 'halk' }, 'Ügynöknek a saját keretéből; a Fabernek csak kívánság, amit te hagysz jóvá.')),
    el('div', { class: 'halk' }, 'Kifelé nem lép: levélből tervezet, naptárból javaslat lesz, amit te hagysz jóvá.'),
    hiba, el('div', { class: 'kartya-gombok' }, indit, el('button', { class: 'gomb', type: 'button', onclick: () => d.close() }, 'Mégse')));
  const d = el('dialog', { class: 'modal' },
    el('div', { class: 'modal-fej' }, el('b', {}, szerk ? `Szerkesztés · ${f.nev}` : 'Új feladat-ügynök'), el('button', { class: 'ikon-gomb', type: 'button', 'aria-label': 'Bezár', onclick: () => d.close() }, '✕')),
    el('div', { class: 'modal-test urlap-test' }, urlap));
  const utemezes = () => {
    const [h, m] = (ora.value || '07:00').split(':').map(Number);
    switch (mikor.value) {
      case 'marad': return null;
      case 'napi': return [`${m} ${h} * * *`, false];
      case 'hetkoznap': return [`${m} ${h} * * 1-5`, false];
      case 'heti': return [`${m} ${h} * * ${nap.value}`, false];
      case 'oranken': return ['0 * * * *', false];
      case 'ekkor': {
        const t = new Date(ekkor.value);
        if (Number.isNaN(t.getTime())) throw new Error('add meg, mikor fusson');
        return [`${t.getMinutes()} ${t.getHours()} ${t.getDate()} ${t.getMonth() + 1} *`, true];
      }
      case 'cron': return [cron.value.trim(), false];
      default: return ['', true];
    }
  };
  urlap.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    hiba.hidden = true; indit.disabled = true;
    try {
      const ut = utemezes();
      const body = { feladat: feladat.value.trim(), modell: modell.value, extra_prompt: extra.value.trim(), ertesites: ertesites.value,
        keret_usd: Number(keret.value), havi_keret_usd: Number(havi.value), vitapartnerek: vita.ertek(), utasithat: utasit.ertek(),
        vita_max: Number(vitaMax.value), dokumentum: dokumentum.value, kaszkad: kaszkad.checked };
      if (ut) { body.utemezes = ut[0]; body.egyszeri = ut[1]; }
      const j = szerk
        ? await api('/api/ui/ugynok/modosit', { method: 'POST', body: { id: f.id, uj_nev: nev.value.trim(), ...body } })
        : await api('/api/ui/ugynok', { method: 'POST', body: { nev: nev.value.trim(), ...body } });
      d.close();
      ertesit(j.uzenet || (szerk ? 'Mentve.' : 'Elindítottam.'));
      await frissit({ tortenet: szerk });
      if (!szerk) nezetValt({ tipus: 'ugynok', id: j.ugynok.id });
    } catch (e) {
      if (e instanceof Hiba401) return;
      hiba.hidden = false; hiba.textContent = e.message;
    } finally { indit.disabled = false; }
  });
  d.addEventListener('close', () => d.remove());
  document.body.append(d);
  d.showModal();
  setTimeout(() => (szerk ? feladat : nev).focus(), 30);
}

// ── MUNKAPAD ──────────────────────────────────────────────────
function munkapadRajzol() {
  const a = S.adat;
  if (!a) return;
  const jv = a.jovahagyas || { faber: [], draftok: [] };
  const db = jv.faber.length + jv.draftok.length;
  const kozos = a.kozos_feladatok || [];
  const futo = kozos.filter((f) => f.fajta === 'fut').length;
  // a fiók jelvénye (telefonon ez látszik): sárga = jóváhagyásra vár; ha az nincs, kék = közös feladat fut
  S.ref.padSzamlalo.hidden = !db && !futo;
  S.ref.padSzamlalo.textContent = db || futo;
  S.ref.padSzamlalo.className = `szamlalo${db ? ' sarga' : ''}`;
  S.ref.padSzamlalo.title = db ? 'jóváhagyásra vár' : 'közös feladat fut';
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

  const kozosBlokk = el('section', { class: 'blokk' },
    el('div', { class: 'szakasz-cim' }, el('span', {}, 'Közös feladatok'),
      futo ? el('span', { class: 'szamlalo', title: 'ennyin dolgoznak most' }, futo) : null),
    kozos.length ? kozos.map(kozosFeladatSor)
      : el('div', { class: 'ures' }, 'Még nincs közös feladat. Mondd Clausnak: „add ki Kiminek, DeepSeeknek és GLM-nek: …”'),
    kozos.length ? el('a', { class: 'halk tovabb-link', href: '/dashboard' }, 'Régebbiek: a régi műszerfal „AI feladatok” fülén') : null);

  const esemenyek = el('section', { class: 'blokk' }, el('div', { class: 'szakasz-cim' }, 'Friss események'),
    (a.esemenyek || []).length ? a.esemenyek.map(esemenySor) : el('div', { class: 'ures' }, 'Csend.'));

  const peldanyok = Object.entries(a.peldanyok || {});
  // a hiányzó blokk (null) kimarad — a replaceChildren különben „null” szöveget írna ki
  S.ref.munkapad.replaceChildren(...[jovahagyas, kozosBlokk, friss, esemenyek, koltesBlokk(a.llm_kapu),
    peldanyok.length ? el('section', { class: 'blokk' }, el('div', { class: 'szakasz-cim' }, 'Claude-példányok'),
      peldanyok.map(([nev, p]) => el('div', { class: 'jelenlet-sor' }, el('span', {}, nev),
        el('span', { title: p.last_activity_source || '' }, relativ(p.last_seen))))) : null].filter(Boolean));
}

// TWEET-TERVEZET (2026-11-23): a teljes szöveg és az X szerinti karakterszám — a Jóváhagyom KIPOSZTOLJA (nyilvános,
// visszavonhatatlan), ezért még egy megerősítést kér; az Elvetem nem posztol.
function tweetKartya(d) {
  const sor = el('div', { class: 'kartya-gombok' });
  const hiba = el('div', { class: 'kartya-hiba', hidden: !d.hiba }, d.hiba ? `Előző próba: ${d.hiba}` : '');
  const dont = async (dontes) => {
    if (dontes === 'approve' && !window.confirm(`Kiposztoljam az X-re?\n\n${d.szoveg}`)) return;
    sor.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    try {
      const j = await api('/api/ui/draft/dont', { method: 'POST', body: { id: d.id, dontes } });
      const r = j.eredmeny || {};
      ertesit(dontes === 'reject' ? 'Elvetve — nem ment ki.' : r.url ? `Kiment: ${r.url}` : 'Kész.');
      await frissit({});
    } catch (e) {
      if (e instanceof Hiba401) return;
      hiba.hidden = false; hiba.textContent = e.message;
      sor.querySelectorAll('button').forEach((b) => { b.disabled = false; });
    }
  };
  sor.append(el('button', { class: 'gomb gomb-fo gomb-kicsi', onclick: () => dont('approve') }, 'Jóváhagyom — kiposztolom'),
    el('button', { class: 'gomb gomb-kicsi', onclick: () => dont('reject') }, 'Elvetem'));
  const forras = d.forras?.startsWith('ügynök') ? `${d.forras.slice(8)} ügynök tervezete` : 'a Feldwebel tervezete';
  const tul = d.karakter > d.max;
  return el('div', { class: 'draft' }, el('b', {}, '🐦 X/Twitter-poszt'),
    el('small', {}, `${forras} · ${d.karakter}/${d.max} karakter${tul ? ' — TÚL HOSSZÚ' : ''}`),
    el('div', { class: 'tweet-szoveg' }, d.szoveg), sor, hiba);
}

// 🐗 HOG (2026-11-23, hog.py): az oldal címe + a kártya-főcím, a tweet-szöveg karakterszámmal és a mód; az elemzés és
// a tényvonalak összecsukva. A Jóváhagyom KÉT nyilvános lépés: előbb az ÁLLANDÓ oldal (hog_publish), aztán a tweet —
// ezért megerősítést kér. Az Elvetem semmit nem tesz ki. Ha az oldal már él (a tweet bukott), azt is mutatja, és a
// „Válasz a telefonról” linket (x.com/intent: a Kommandant a saját fiókjából posztol).
function hogKartya(d) {
  const h = d.hog || {};
  const sor = el('div', { class: 'kartya-gombok' });
  const hiba = el('div', { class: 'kartya-hiba', hidden: !d.hiba }, d.hiba ? `Előző próba: ${d.hiba}` : '');
  const kint = !!h.oldal_url;
  const modSzoveg = { valasz: `VÁLASZ erre: ${h.cel_url}`, idezet: `IDÉZET erre: ${h.cel_url}`,
    sajat: 'SAJÁT POSZT — az eredeti poszt linkje nincs meg (küldd el a Feldwebelnek, és válasz lesz belőle)' }[h.mod] || h.mod;
  const modNev = h.mod === 'valasz' ? 'válasz' : h.mod === 'idezet' ? 'idézet' : 'saját poszt';
  const dont = async (dontes) => {
    const kerdes = kint ? `Újra próbáljam a tweetet?\n\nAz oldal már él: ${h.oldal_url}`
      : `Kiteszem az ÁLLANDÓ, nyilvános oldalt, és kiposztolom (${modNev})?\n\n«${h.cim}»\n\n${h.komment}`;
    if (dontes === 'approve' && !window.confirm(kerdes)) return;
    sor.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    try {
      const j = await api('/api/ui/draft/dont', { method: 'POST', body: { id: d.id, dontes } });
      const r = j.eredmeny || {};
      let uz = r.error || 'Kész.';
      if (dontes === 'reject') uz = r.uzenet || 'Elvetve — semmi nem ment ki.';
      else if (r.tweet_url) uz = `Kiment: ${r.tweet_url} · oldal: ${r.oldal_url}`;
      else if (r.oldal_url) uz = `Az oldal él: ${r.oldal_url} — a tweet nem ment ki`;
      ertesit(uz, !!r.error);
      await frissit({});
    } catch (e) {
      if (e instanceof Hiba401) return;
      hiba.hidden = false; hiba.textContent = e.message;
      sor.querySelectorAll('button').forEach((b) => { b.disabled = false; });
    }
  };
  sor.append(el('button', { class: 'gomb gomb-fo gomb-kicsi', onclick: () => dont('approve') },
    kint ? 'Tweet újra' : 'Jóváhagyom — kiposztolom'),
  el('button', { class: 'gomb gomb-kicsi', onclick: () => dont('reject') }, kint ? 'Lezárom' : 'Elvetem'));
  const hs = h.hossz || {};
  const intent = h.intent || {};
  const telefon = intent[h.mod] || intent.valasz || intent.sajat || intent.idezet;
  const cs = h.csomag || {};
  const koltes = Number(h.koltes_usd || 0).toFixed(3);
  return el('div', { class: 'draft hog' },
    el('b', {}, '🐗 HOG'),
    el('small', {}, `${modSzoveg} · Sonnet $${koltes} · ${cs.articles ?? '?'} tudósítás, ${cs.regions ?? '?'} régió`),
    el('div', { class: 'hog-cim' }, h.cim),
    h.fejlec ? el('div', { class: 'hog-fejlec' }, h.fejlec) : null,
    el('small', {}, h.mod === 'valasz'
      ? `Válasz (${hs.komment}/${hs.max}, link nélkül, a kártyaképpel; a link 2–3 perc múlva a válaszod alá)`
      : `Tweet (${hs.idezet}/${hs.max}, a linkkel — a link a kártya)`),
    el('div', { class: 'tweet-szoveg' }, h.mod === 'valasz' ? h.komment : h.idezet),
    h.komment_hu ? el('small', { class: 'hog-hu' }, `Magyarul: ${h.komment_hu}`) : null,
    h.megjegyzes ? el('small', { class: 'hog-hu' }, h.megjegyzes) : null,
    el('details', {}, el('summary', {}, `Elemzés és ${(h.tenyek || []).length} tényvonal (${h.lang})`),
      el('div', { class: 'tweet-szoveg' }, h.elemzes),
      el('ol', { class: 'hog-tenyek' }, (h.tenyek || []).map((t) => el('li', {}, t))),
      h.mod === 'valasz' ? el('div', {}, el('small', {}, `Idézet-változat (ha a válasz nem megy): ${h.idezet}`)) : null),
    kint ? el('div', {}, el('small', {}, 'Az oldal ÉL: '),
      el('a', { href: h.oldal_url, target: '_blank', rel: 'noopener' }, h.oldal_url)) : null,
    telefon ? el('div', {}, el('a', { href: telefon, target: '_blank', rel: 'noopener' }, '📱 Válasz a telefonról')) : null,
    sor, hiba);
}

function draftKartya(d) {
  if (d.fajta === 'tweet') return tweetKartya(d);
  if (d.fajta === 'hog') return hogKartya(d);
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
    el('small', {}, [d.mikor ? `${d.mikor} · ` : '', d.forras?.startsWith('ügynök') ? `${d.forras.slice(8)} ügynök javaslata` : 'e-mailből kinyert javaslat']),
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

// ── KÖZÖS FELADATOK (ai_task) ─────────────────────────────────
const FOLYTATAS_RE = /^↳ #\d+: /;
const feladatJel = (f) => [f.allapot, f.allapot_szoveg, f.valaszolt, f.szintezis].join('|');

function ugynokPontok(lista) {
  if (!lista?.length) return null;
  const ikon = { kesz: '✓', hiba: '✕', var: '…' };
  const cim = { kesz: 'válaszolt', hiba: 'hibával tért vissza', var: 'még dolgozik' };
  return el('span', { class: 'fu-ugynokok' }, lista.map((u) => el('span', { class: `fu-ugynok ${u.allapot}`, title: `${u.nev}: ${cim[u.allapot]}` }, `${u.nev} ${ikon[u.allapot]}`)));
}

function kozosFeladatSor(f) {
  const cim = f.elozmeny ? f.cim.replace(FOLYTATAS_RE, '') : f.cim;
  const al = [`#${f.id}`, f.elozmeny ? `hozzászólás #${f.elozmeny}-hez` : null, `kiadta: ${f.ki}`, f.reszlet,
    f.fajta === 'hiba' && f.hibakod ? f.hibakod : null].filter(Boolean).join(' · ');
  return el('button', { class: 'esemeny', title: 'Részletek: a feladat, a válaszok, az összefoglaló — és itt szólhatsz bele', onclick: () => feladatNyit(f.id) },
    el('span', { class: 'jel' }, f.elozmeny ? '↳' : '⁂'),
    el('span', {}, el('span', { class: 'cim' }, cim), el('small', {}, al), ugynokPontok(f.ugynokok)),
    el('span', { class: 'jobbra' }, allapotChip(f.allapot_szoveg, f.fajta), el('small', {}, relativ(f.ido))));
}

// a részletek egy ablakban; a folytatás és az eredeti közt ugyanebben az ablakban lépked, a ✕ / Esc / háttér vissza a táblára
function feladatNyit(id) {
  if (S.feladatAblak) {
    Object.assign(S.feladatAblak, { id, rajzolva: false, jel: null });
    S.feladatAblak.test.replaceChildren(el('div', { class: 'ures' }, 'Betöltés…'));
    feladatTolt();
    return;
  }
  const test = el('div', { class: 'modal-test feladat-test' }, el('div', { class: 'ures' }, 'Betöltés…'));
  const fejCim = el('b', {}, `Közös feladat #${id}`);
  const d = el('dialog', { class: 'modal szeles' },
    el('div', { class: 'modal-fej' }, fejCim, el('button', { class: 'ikon-gomb', 'aria-label': 'Bezár', title: 'Vissza a táblára', onclick: () => d.close() }, '✕')),
    test);
  const fa = { id, d, test, fejCim, jel: null, rajzolva: false, dolgozik: false };
  d.addEventListener('close', () => { d.remove(); if (S.feladatAblak === fa) S.feladatAblak = null; });
  d.addEventListener('click', (ev) => { if (ev.target === d) d.close(); });
  document.body.append(d);
  d.showModal();
  S.feladatAblak = fa;
  feladatTolt();
}

async function feladatTolt() {
  const fa = S.feladatAblak;
  if (!fa) return;
  const id = fa.id;
  let j;
  try {
    j = await api(`/api/ui/feladat/${id}`);
  } catch (e) {
    if (e instanceof Hiba401 || S.feladatAblak !== fa || fa.id !== id) return;
    fa.test.replaceChildren(el('div', { class: 'kartya-hiba' }, e.message),
      el('div', { class: 'kartya-gombok', style: 'margin-top:12px' }, el('button', { class: 'gomb gomb-kicsi', onclick: () => fa.d.close() }, 'Vissza a táblára')));
    return;
  }
  if (S.feladatAblak !== fa || fa.id !== id) return;
  // frissítéskor a lenyitott részek, a görgetés és a félig megírt hozzászólás marad
  const nyitott = fa.rajzolva ? new Set([...fa.test.querySelectorAll('details[data-kulcs]')].filter((x) => x.open).map((x) => x.dataset.kulcs)) : null;
  const vazlat = fa.rajzolva ? (fa.test.querySelector('textarea')?.value || '') : '';
  const gorges = fa.rajzolva ? fa.test.scrollTop : 0;
  fa.jel = feladatJel(j);
  fa.fejCim.textContent = `#${j.id} · ${j.elozmeny ? j.cim.replace(FOLYTATAS_RE, '↳ ') : j.cim}`;
  fa.test.replaceChildren(...feladatReszletek(j, nyitott, vazlat));
  fa.test.scrollTop = gorges;
  fa.rajzolva = true;
}

const resz = (cim, ...gyerekek) => el('section', { class: 'feladat-resz' }, el('div', { class: 'szakasz-cim' }, cim), ...gyerekek);

function feladatReszletek(j, nyitott, vazlat) {
  const nyit = (kulcs, alap) => (nyitott ? nyitott.has(kulcs) : alap);
  const nodes = [el('div', { class: 'feladat-fej' }, allapotChip(j.allapot_szoveg, j.fajta),
    el('span', {}, `kiadta: ${j.ki} · ${napCimke(j.ido)} ${oraPerc(j.ido)}${j.kesz_ido ? ` · kész: ${oraPerc(j.kesz_ido)}` : ''}`),
    ugynokPontok(j.ugynokok))];
  if (j.reszlet) nodes.push(el('div', { class: 'halk', style: 'margin-top:4px' }, j.reszlet));
  if (j.hibakod) nodes.push(el('div', { class: 'kartya-hiba' }, `Hibakód: ${j.hibakod}`));
  if (j.elozmeny_feladat) {
    nodes.push(el('div', { class: 'feladat-kapcs' }, 'Ez a hozzászólásod folytatása ennek: ',
      el('button', { class: 'link-gomb', onclick: () => feladatNyit(j.elozmeny_feladat.id) }, `← #${j.elozmeny_feladat.id} ${j.elozmeny_feladat.cim}`)));
  }
  const gombok = el('div', { class: 'kartya-gombok feladat-gombok' });
  if (j.export) gombok.append(el('a', { class: 'gomb gomb-fo gomb-kicsi', href: j.export, download: '' }, 'Word (DOCX) letöltése'));
  for (const f of j.fajlok || []) gombok.append(el('a', { class: 'gomb gomb-kicsi', href: f.url, download: '' }, `${f.nev} · ${String(f.formatum).toUpperCase()}`));
  if (gombok.childNodes.length) nodes.push(gombok);
  if (j.hozzaszolas) nodes.push(resz('A hozzászólásod', el('div', { class: 'feladat-szoveg feladat-hozzaszolas' }, j.hozzaszolas)));
  nodes.push(resz('A feladat', el('div', { class: 'feladat-szoveg', html: md(j.leiras || '(nincs leírás)') }),
    j.kontextus ? el('details', { class: 'feladat-kontextus', 'data-kulcs': 'kontextus', open: nyit('kontextus', false) },
      el('summary', {}, `Amit kontextusként kaptak (${j.kontextus.length.toLocaleString('hu-HU')} karakter)`),
      el('div', { class: 'feladat-szoveg' }, j.kontextus)) : null));
  const sz = j.szintezis;
  nodes.push(resz('Összefoglaló', sz
    ? [el('div', { class: 'feladat-meta' }, sz.modell ? el('span', { class: 'modell', title: 'a szintézist író modell' }, sz.modell) : el('span', {}, sz.szerep || ''),
      sz.hibakod ? allapotChip(sz.hibakod, 'hiba') : null, el('span', {}, oraPerc(sz.ido))),
    el('div', { class: 'feladat-szoveg', html: md(sz.szoveg || '') })]
    : el('div', { class: 'ures' }, j.fajta === 'fut' ? 'Még készül — ha kész, itt lesz (az ablak magától frissül).' : 'Nem készült összefoglaló.')));
  const valaszok = (j.valaszok || []).map((v, i) => {
    const kulcs = `v:${v.ugynok}:${i}`;
    return el('details', { class: 'feladat-valasz', 'data-kulcs': kulcs, open: nyit(kulcs, !sz) },
      el('summary', {}, avatar(v.ugynok, { kicsi: true }), el('b', {}, v.nev),
        v.modell ? el('span', { class: 'modell', title: 'a kért modell; ha a kapu tartaléka válaszolt, a válasz végén ott a jegyzet' }, v.modell) : null,
        v.hibas ? allapotChip(v.hibakod || 'hibás', 'hiba') : null,
        el('span', { class: 'halk jobbra' }, oraPerc(v.ido))),
      el('div', { class: 'feladat-szoveg', html: md(v.szoveg || '') }));
  });
  const varnak = (j.ugynokok || []).filter((u) => u.allapot === 'var')
    .map((u) => el('div', { class: 'feladat-var' }, avatar(u.id, { kicsi: true }), `${u.nev} — még dolgozik…`));
  nodes.push(resz(`Az ügynökök válaszai · ${j.valaszolt}${j.vart ? `/${j.vart}` : ''}`,
    valaszok.length || varnak.length ? [valaszok, varnak]
      : el('div', { class: 'ures' }, j.fajta === 'fut' ? 'Dolgoznak rajta — a válaszok itt jelennek meg (az ablak magától frissül).' : 'Nincs válasz.')));
  if ((j.folytatasok || []).length) {
    nodes.push(resz('Hozzászólásaid és a folytatások', j.folytatasok.map((f) => el('button', { class: 'esemeny', onclick: () => feladatNyit(f.id) },
      el('span', { class: 'jel' }, '↳'),
      el('span', {}, el('span', { class: 'cim' }, f.hozzaszolas || '(a hozzászólás szövege nem olvasható)'), el('small', {}, `#${f.id} · ${relativ(f.ido)}`)),
      el('span', { class: 'jobbra' }, allapotChip(f.allapot_szoveg, f.fajta))))));
  }
  nodes.push(hozzaszolasUrlap(j, vazlat));
  nodes.push(el('div', { class: 'kartya-gombok', style: 'margin-top:14px' },
    el('button', { class: 'gomb gomb-kicsi', onclick: () => S.feladatAblak?.d.close() }, 'Vissza a táblára')));
  return nodes;
}

// HOZZÁSZÓLÁS (11-23, a Kommandant: „abba is bele kéne tudnom szólni”): folytató közös feladat indul, a szövegeddel
// legfelül és a korábbi válaszokkal; az ablak rögtön a folytatásra lép (onnan link vissza ide).
function hozzaszolasUrlap(j, vazlat) {
  const ta = el('textarea', { rows: '3', maxlength: '4000', 'aria-label': 'Hozzászólás az ügynököknek',
    placeholder: 'Szólj bele: mit nézzenek meg újra, mi hiányzik, merre menjenek tovább…' });
  ta.value = vazlat || '';
  const kuld = el('button', { class: 'gomb gomb-fo gomb-kicsi', type: 'submit', disabled: !ta.value.trim() }, 'Elküldöm az ügynököknek');
  const hiba = el('div', { class: 'kartya-hiba', hidden: true });
  ta.addEventListener('input', () => { kuld.disabled = !ta.value.trim(); });
  const urlap = el('form', { class: 'feladat-urlap' }, ta,
    el('div', { class: 'kartya-gombok' }, kuld, el('span', { class: 'halk' }, j.mod === 'dispatch'
      ? 'Folytató feladat indul: mindegyik ügynök a saját részét folytatja, a szövegeddel legfelül.'
      : 'Folytató feladat indul: mindhárom ügynök megkapja, a korábbi válaszokkal együtt.')), hiba);
  urlap.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const szoveg = ta.value.trim();
    if (!szoveg) return;
    const fa = S.feladatAblak;
    kuld.disabled = true; ta.disabled = true; hiba.hidden = true;
    if (fa) fa.dolgozik = true;
    try {
      const r = await api(`/api/ui/feladat/${j.id}/hozzaszolas`, { method: 'POST', body: { szoveg } });
      if (fa) fa.dolgozik = false;
      ertesit(`Elküldve — a folytatás: #${r.id}. Az ügynökök dolgoznak rajta.`);
      feladatNyit(r.id);
      frissit({}).catch(() => {});
    } catch (e) {
      if (fa) fa.dolgozik = false;
      if (e instanceof Hiba401) return;
      hiba.hidden = false; hiba.textContent = e.message;
      kuld.disabled = false; ta.disabled = false;
    }
  });
  return resz('Szólj bele', urlap);
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
  // a nyitott közös feladat magától frissül, ha a listában változott (új válasz, összefoglaló, kész) — gépelés közben nem
  const fa = S.feladatAblak;
  if (fa?.jel && !fa.dolgozik) {
    const f = (a.kozos_feladatok || []).find((x) => x.id === fa.id);
    const gepel = fa.test.contains(document.activeElement) && document.activeElement.tagName === 'TEXTAREA';
    if (f && feladatJel(f) !== fa.jel && !gepel) feladatTolt().catch(hibaKezel);
  }
  if (S.nezet.tipus === 'ugynok' && feladatE(S.nezet.id)) {
    if (!feladatUgynok(S.nezet.id)) { nezetValt({ tipus: 'ugynok', id: 'feldwebel' }); return; }
    ugynokPanelRajzol();
  }
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

// VISSZA: belépés nélkül megnyitott védett lap (régi műszerfal, Telegramban kapott link) → a szerver ide küld
// `?vissza=…`-val; a belépés után oda megyünk vissza. Csak saját, relatív útra (nincs nyitott átirányítás).
const VISSZA_ERVENY_MS = 15 * 60 * 1000;
function visszaJo(v) { return typeof v === 'string' && /^\/(?!\/)[^\\\s]*$/.test(v) && (v.startsWith('/dashboard') || v.startsWith('/api/')); }
function visszaMegjegyez() {
  const v = new URLSearchParams(location.search).get('vissza');
  if (!v) return;
  if (visszaJo(v)) ir('kabin.vissza', { v, t: Date.now() });
  history.replaceState(null, '', '/app/');
}
function visszaUgras() {
  const m = olvas('kabin.vissza', null);
  if (!m) return false;
  try { localStorage.removeItem('kabin.vissza'); } catch { /* privát mód */ }
  if (!visszaJo(m.v) || Date.now() - (m.t || 0) > VISSZA_ERVENY_MS) return false;
  location.replace(m.v);
  return true;
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
  if (visszaUgras()) return;
  if (S.adat.most) S.eltolas = ms(S.adat.most) - Date.now();
  S.utolsoId = S.adat.utolso_id;
  const mentett = olvas('kabin.nezet', null);
  if (mentett && (mentett.tipus === 'postalada' || (mentett.tipus === 'ugynok' && (UGYNOK[mentett.id] || feladatUgynok(mentett.id))) || mentett.tipus === 'vita')) S.nezet = mentett;
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
visszaMegjegyez();
indit();
