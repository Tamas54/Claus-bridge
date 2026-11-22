// Diktálás — a Lexonomics `static/js/core/dictate.js` átvéve (2026-11-22), a Bridge-felülethez igazítva.
//
// A Kommandant megnyomja a mikrofont és beszél; a szavak a szövegdobozba kerülnek, ahogy a böngésző felismeri őket,
// küldés előtt átolvashatók és javíthatók. A böngésző saját beszédfelismerője dolgozik (Chrome a Google-nek, Edge a
// Microsoftnak, Safari az Apple-nek küldi a hangot), a lap ezt kiírja, amíg figyel; a hangból semmi nem jut a Bridge-re.
// Ahol nincs ilyen (Firefox), ott nincs gomb.
//
// ÚJ a Bridge-en: HANGPARANCS. Ha a Kommandant szünet után külön mondatként azt mondja: „küldd” (vagy „küldd el”,
// „küldés”, „elküldheted”), a parancsszó NEM kerül a szövegbe, a felismerés leáll, és az üzenet elmegy. Csak a
// VÉGLEGES és ÖNÁLLÓ szakaszt nézzük: a „küldd el Jánosnak a levelet” mondat nem küld, mert nem csak a parancs.

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
// Androidos Chrome-on a folyamatos munkamenet minden eredményben megismétli a korábbi szavakat: ott egy kifejezés
// egy munkamenet, újraindítva.
const EGY_KIFEJEZES = /Android/i.test(navigator.userAgent);
const TETLEN_MS = 60000;           // egy perc szó nélkül: a mikrofon kikapcsol
const KULDD = /^\s*(küldd( el)?|küldés|elküldheted|küldheted)[.!]?\s*$/i;

const SZOVEG = {
  start: 'Diktálás (magyarul) — „küldd”: elküldi',
  stop: 'Diktálás leállítása',
  figyel: 'Figyelek… a hang a böngésző beszédfelismerőjéhez megy. Szünet után „küldd” — elküldöm.',
  tiltva: 'A böngésző nem engedi a mikrofont. A lakat ikonnál engedélyezheted.',
  nincs_mikrofon: 'Nem találok mikrofont.',
  hiba: 'A beszédfelismerés most nem megy. Gépelj, vagy próbáld újra.',
};

export const diktalhato = () => Boolean(Recognition) && window.isSecureContext;

// mikrofon-gomb egy szövegdobozhoz; null, ahol a böngésző nem tud beszédet felismerni.
// { gomb, jegyzet, stop(), cancel(), get kimondott } — `kimondott`: a doboz szövege (részben) hangból jött
export function diktalas(input, { kuldd } = {}) {
  if (!diktalhato()) return null;
  let rec = null;
  let be = false;                // a Kommandant azt akarja, hogy figyeljünk
  let alap = '';                 // a doboz szövege, amikor ez a munkamenet indult
  let kimondott = false;
  let indult = 0;
  let gyorsVeg = 0;
  let utolsoHang = 0;
  const gomb = document.createElement('button');
  gomb.type = 'button';
  gomb.className = 'ikon-gomb mikrofon';
  gomb.setAttribute('aria-pressed', 'false');
  gomb.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12h-2Z"/></svg>';
  const jegyzet = document.createElement('p');
  jegyzet.className = 'diktalas-jegyzet';
  jegyzet.setAttribute('aria-live', 'polite');
  jegyzet.hidden = true;

  const mond = (kulcs) => {
    jegyzet.hidden = !kulcs;
    jegyzet.textContent = kulcs ? SZOVEG[kulcs] : '';
    jegyzet.classList.toggle('hiba', Boolean(kulcs) && kulcs !== 'figyel');
  };
  const rajzol = () => {
    const felirat = SZOVEG[be ? 'stop' : 'start'];
    gomb.classList.toggle('be', be);
    gomb.setAttribute('aria-pressed', String(be));
    gomb.title = felirat;
    gomb.setAttribute('aria-label', felirat);
  };
  const bukik = (kulcs) => { be = false; rajzol(); mond(kulcs); };

  // e munkamenet szavai a doboz meglévő szövege után; mondat elején nagybetű
  const tesz = (hallott) => {
    let szavak = hallott.replace(/\s+/g, ' ').trim();
    if (!szavak) return;
    utolsoHang = Date.now();
    if (!alap.trim() || /[.!?]\s*$|\n\s*$/.test(alap)) szavak = szavak[0].toLocaleUpperCase('hu') + szavak.slice(1);
    input.value = !alap || /\s$/.test(alap) ? alap + szavak : `${alap} ${szavak}`;
    kimondott = true;
    input.dispatchEvent(new Event('input', { bubbles: true }));
  };

  const munkamenet = () => {
    rec = new Recognition();
    rec.lang = 'hu-HU';
    rec.continuous = !EGY_KIFEJEZES;
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    alap = input.value;
    indult = Date.now();
    rec.onresult = (e) => {
      const reszek = [];
      for (let i = 0; i < e.results.length; i += 1) {
        reszek.push({ t: e.results[i][0].transcript, vegleges: e.results[i].isFinal });
      }
      const utolso = reszek[reszek.length - 1];
      if (kuldd && utolso && utolso.vegleges && KULDD.test(utolso.t)) {
        reszek.pop();
        tesz(reszek.map((r) => r.t).join(' '));
        megszakit();
        kuldd();
        return;
      }
      tesz(reszek.map((r) => r.t).join(' '));
    };
    rec.onerror = (e) => {
      if (e.error === 'no-speech' || e.error === 'aborted') return;      // csend: a vége új munkamenetet indít
      console.warn('diktálás', e.error);
      bukik(e.error === 'not-allowed' || e.error === 'service-not-allowed' ? 'tiltva'
        : e.error === 'audio-capture' ? 'nincs_mikrofon' : 'hiba');
    };
    rec.onend = () => {
      rec = null;
      if (!be) return;
      if (Date.now() - utolsoHang > TETLEN_MS) { be = false; rajzol(); mond(null); return; }
      // a böngésző szünet után vagy kb. egy perc múlva zár: az új munkamenet a doboz mostani szövege után folytatja
      gyorsVeg = Date.now() - indult < 1500 ? gyorsVeg + 1 : 0;
      if (gyorsVeg > 3) { bukik('hiba'); return; }
      try { munkamenet(); } catch (err) { console.warn('diktálás', err); bukik('hiba'); }
    };
    rec.start();
  };

  const indit = () => {
    be = true;
    gyorsVeg = 0;
    utolsoHang = Date.now();
    rajzol();
    mond('figyel');
    try { munkamenet(); } catch (err) { console.warn('diktálás', err); bukik('hiba'); }
  };
  // az eddig hallott szavak maradnak; az utolsó kifejezést a böngésző még javíthatja
  const stop = () => { be = false; rajzol(); mond(null); rec?.stop(); };
  // több szó nem kerül a dobozba (elküldték, vagy a Kommandant gépel)
  function megszakit() {
    be = false;
    rajzol();
    mond(null);
    if (rec) { rec.onresult = null; rec.abort(); }
  }
  gomb.addEventListener('click', () => (be ? stop() : indit()));
  input.addEventListener('input', (e) => {
    if (e.isTrusted && be) megszakit();                  // a gépelés átveszi a hangtól
    if (!input.value.trim()) kimondott = false;
  });
  rajzol();
  return {
    gomb, jegyzet, stop, cancel: megszakit,
    reset: () => { kimondott = false; },
    get kimondott() { return kimondott; },
  };
}
