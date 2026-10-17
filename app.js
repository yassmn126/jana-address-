'use strict';

/* =============================================================================
 * Jana Address — frontend controller (vanilla JS, no build step)
 *
 * Backend contract (FastAPI batch endpoint):
 *   POST  /api/parse   (local: http://localhost:8000, deployed: Render, see render.yaml)
 *   body  [{ "id": "rec_00417", "raw_address": "..." }, ...]
 *   200   [{ id, raw_address, province, city, district, living_area, street_type,
 *            street_name, house, apartment, entrance, floor, landmark, confidence }, ...]
 *         (a wrapper such as { "results": [...] } is accepted too)
 *
 * If the API is unreachable, the in-browser LocalEngine parses instead, so the
 * interface never dead-ends. Point at another server with ?api=http://host:port/api/parse
 * ========================================================================== */

const LOCAL_API_URL = 'http://localhost:8000/api/parse';
const DEPLOYED_API_URL = 'https://jana-address-api.onrender.com/api/parse';

// ?api= wins; a page opened locally (localhost, 127.0.0.1, ::1, *.localhost, file://) uses the local
// backend; anywhere else (e.g. Vercel) uses the Render deployment.
function resolveApiUrl() {
  if (typeof location === 'undefined') return LOCAL_API_URL;
  const override = new URLSearchParams(location.search).get('api');
  if (override) return override;
  const host = location.hostname;
  const isLocal = location.protocol === 'file:' || host === '' || host === 'localhost' || host.endsWith('.localhost')
    || host === '127.0.0.1' || host === '[::1]' || host === '::1';
  return isLocal ? LOCAL_API_URL : DEPLOYED_API_URL;
}

const CONFIG = {
  apiUrl: resolveApiUrl(),
  reviewThreshold: 0.7,
  pageSize: 50,
  probeTimeoutMs: 1500,
  reprobeMs: 15000,
  storageKey: 'jana-address/session/v1',
};

const SCHEMA_KEYS = ['id', 'raw_address', 'province', 'city', 'district', 'living_area', 'street_type', 'street_name', 'house', 'apartment', 'entrance', 'floor', 'landmark', 'confidence'];
const FIELD_KEYS = SCHEMA_KEYS.slice(2, -1);

/* =============================================================================
 * LocalEngine — rule-based offline parser (fallback when FastAPI is down)
 * ========================================================================== */
const LocalEngine = (() => {
  /* ---------- Compact reference directory ---------- */
  // [canonical city, province, extra aliases (lowercase RU / KZ / LAT)]
  const CITIES = [
    ['Алматы', 'Алматы', ['алма-ата', 'almaty', 'alma-ata']],
    ['Астана', 'Астана', ['нур-султан', 'нурсултан', 'astana', 'nur-sultan', 'целиноград']],
    ['Шымкент', 'Шымкент', ['чимкент', 'shymkent']],
    ['Актобе', 'Актюбинская область', ['ақтөбе', 'актюбинск', 'aktobe']],
    ['Караганда', 'Карагандинская область', ['қарағанды', 'karaganda']],
    ['Костанай', 'Костанайская область', ['қостанай', 'кустанай', 'kostanay']],
    ['Павлодар', 'Павлодарская область', ['pavlodar']],
    ['Усть-Каменогорск', 'Восточно-Казахстанская область', ['өскемен', 'оскемен', 'oskemen', 'ust-kamenogorsk']],
    ['Семей', 'Абайская область', ['семипалатинск', 'semey']],
    ['Уральск', 'Западно-Казахстанская область', ['орал', 'oral', 'uralsk']],
    ['Атырау', 'Атырауская область', ['atyrau']],
    ['Актау', 'Мангистауская область', ['ақтау', 'aktau']],
    ['Кызылорда', 'Кызылординская область', ['қызылорда', 'kyzylorda']],
    ['Тараз', 'Жамбылская область', ['taraz']],
    ['Талдыкорган', 'Жетысуская область', ['талдықорған', 'taldykorgan']],
    ['Петропавловск', 'Северо-Казахстанская область', ['петропавл', 'petropavlovsk']],
    ['Кокшетау', 'Акмолинская область', ['көкшетау', 'kokshetau']],
    ['Туркестан', 'Туркестанская область', ['түркістан', 'turkistan']],
    ['Жезказган', 'Улытауская область', ['жезқазған', 'zhezkazgan']],
    ['Конаев', 'Алматинская область', ['қонаев', 'капчагай', 'қапшағай']],
    ['Темиртау', 'Карагандинская область', ['теміртау']],
    ['Экибастуз', 'Павлодарская область', ['екібастұз']],
    ['Рудный', 'Костанайская область', []],
    ['Каскелен', 'Алматинская область', ['қаскелең']],
    ['Талгар', 'Алматинская область', []],
    ['Боралдай', 'Алматинская область', []],
    ['Косшы', 'Акмолинская область', ['қосшы']],
    ['Балхаш', 'Карагандинская область', ['балқаш']],
    ['Жанаозен', 'Мангистауская область', ['жаңаөзен']],
    ['Риддер', 'Восточно-Казахстанская область', []],
  ];
  const CITY_INDEX = new Map();
  for (const [name, province, aliases] of CITIES) {
    for (const alias of [name.toLowerCase(), ...aliases]) CITY_INDEX.set(alias, { name, province });
  }

  const KZ_PROVINCES = {
    'абай': 'Абайская область', 'ақмола': 'Акмолинская область', 'ақтөбе': 'Актюбинская область',
    'алматы': 'Алматинская область', 'атырау': 'Атырауская область', 'жамбыл': 'Жамбылская область',
    'жетісу': 'Жетысуская область', 'жетысу': 'Жетысуская область', 'қарағанды': 'Карагандинская область',
    'қостанай': 'Костанайская область', 'қызылорда': 'Кызылординская область', 'маңғыстау': 'Мангистауская область',
    'павлодар': 'Павлодарская область', 'түркістан': 'Туркестанская область', 'ұлытау': 'Улытауская область',
    'улытау': 'Улытауская область', 'шығыс қазақстан': 'Восточно-Казахстанская область',
    'батыс қазақстан': 'Западно-Казахстанская область', 'солтүстік қазақстан': 'Северо-Казахстанская область',
  };
  const PROVINCE_ABBR = {
    'вко': 'Восточно-Казахстанская область', 'зко': 'Западно-Казахстанская область',
    'ско': 'Северо-Казахстанская область', 'юко': 'Туркестанская область',
  };
  // City districts that only exist in one city → lets us restore a missing city.
  const DISTRICT_CITY = {
    'алатауский': 'Алматы', 'алмалинский': 'Алматы', 'ауэзовский': 'Алматы', 'бостандыкский': 'Алматы',
    'жетысуский': 'Алматы', 'медеуский': 'Алматы', 'наурызбайский': 'Алматы', 'турксибский': 'Алматы',
    'байконурский': 'Астана', 'аль-фарабийский': 'Шымкент',
  };
  const DISTRICT_PROVINCE = {
    'илийский': 'Алматинская область', 'карасайский': 'Алматинская область', 'талгарский': 'Алматинская область',
    'енбекшиказахский': 'Алматинская область', 'целиноградский': 'Акмолинская область',
    'бурабайский': 'Акмолинская область', 'тупкараганский': 'Мангистауская область', 'сайрамский': 'Туркестанская область',
  };
  const KZ_DISTRICTS = {
    'әуезов': 'Ауэзовский', 'алмалы': 'Алмалинский', 'алатау': 'Алатауский', 'бостандық': 'Бостандыкский',
    'жетісу': 'Жетысуский', 'медеу': 'Медеуский', 'наурызбай': 'Наурызбайский', 'түрксіб': 'Турксибский',
    'байқоңыр': 'Байконурский', 'есіл': 'Есильский', 'сарыарқа': 'Сарыаркинский', 'іле': 'Илийский', 'қарасай': 'Карасайский',
  };
  // Almaty microdistricts → city district (reference lookup, never in the raw string).
  const AREA_DISTRICT = {
    'аксай': 'Ауэзовский', 'жетысу': 'Ауэзовский', 'мамыр': 'Ауэзовский', 'таугуль': 'Ауэзовский', 'сайран': 'Ауэзовский',
    'самал': 'Медеуский', 'орбита': 'Бостандыкский', 'коктем': 'Бостандыкский', 'алмагуль': 'Бостандыкский',
    'калкаман': 'Наурызбайский', 'шанырак': 'Алатауский', 'айгерим': 'Алатауский', 'акбулак': 'Алатауский',
    'нуркент': 'Алатауский', 'кулагер': 'Турксибский', 'жулдыз': 'Турксибский',
  };
  const LIVING_AREAS = { 'пришахтинск': ['Пришахтинск', 'Караганда'], 'майкудук': ['Майкудук', 'Караганда'] };

  /* ---------- Keyword vocabulary ---------- */
  const STREET_TYPES = {
    'улица': ['улица', 'ул', 'көшесі', 'көше', 'көш', 'street', 'str', 'st'],
    'проспект': ['проспект', 'пр', 'пр-т', 'прт', 'просп', 'пр-кт', 'даңғылы', 'даңғ', 'avenue', 'ave'],
    'микрорайон': ['микрорайон', 'мкр', 'мкрн', 'мкр-н', 'м-н', 'микр', 'микрайон', 'mkr', 'microdistrict'],
    'квартал': ['квартал', 'кв-л', 'квл'],
    'переулок': ['переулок', 'пер'],
    'бульвар': ['бульвар', 'б-р', 'бул', 'бульв'],
    'шоссе': ['шоссе'],
    'тупик': ['тупик'],
    'проезд': ['проезд', 'пр-д'],
    'трасса': ['трасса'],
    'аллея': ['аллея'],
    'набережная': ['набережная', 'наб'],
    'площадь': ['площадь', 'пл'],
  };
  const LANDMARKS = [
    'магазин', 'магазина', 'маг', 'супермаркет', 'минимаркет', 'тц', 'трц', 'бц', 'рынок', 'базар', 'кафе', 'ресторан',
    'аптека', 'школа', 'школы', 'сш', 'гимназия', 'детсад', 'садик', 'больница', 'поликлиника', 'мечеть', 'остановка',
    'ост', 'азс', 'банк', 'qazpost', 'kazpost', 'казпочта', 'казпошта', 'почта', 'отделение', 'салон', 'гостиница',
    'отель', 'колледж', 'университет', 'возле', 'около', 'напротив', 'рядом', 'ориентир',
  ];

  const KW = new Map();
  const put = (cat, words, val = null) => words.forEach((w) => KW.set(w, { cat, val }));
  Object.entries(STREET_TYPES).forEach(([val, words]) => put('street', words, val));
  put('living', ['жк', 'ж/м', 'жм', 'тжк', 'мжк']);
  put('house', ['дом', 'д', 'үй', 'уй', 'house', 'здание']);
  put('apartment', ['кв', 'квартира', 'кв-ра', 'kv', 'flat', 'apt', 'apartment', 'пәтер', 'офис', 'оф']);
  put('entrance', ['подъезд', 'подьезд', 'подезд', 'подъзд', 'под', 'п-д', 'пд', 'entrance', 'кіреберіс']);
  put('floor', ['этаж', 'эт', 'floor', 'қабат']);
  put('city', ['город', 'г', 'гор', 'city', 'пос', 'поселок', 'п', 'пгт', 'село', 'с', 'аул', 'ауыл']);
  put('cityPost', ['қаласы', 'ауылы', 'кенті']);
  put('district', ['район', 'р-н', 'р-он', 'рн', 'ауданы', 'district']);
  put('province', ['область', 'обл', 'облысы', 'region']);
  put('landmark', LANDMARKS);

  const MULTI = {
    'жилой массив': { cat: 'living', val: null },
    'жилой комплекс': { cat: 'living', val: null },
    'шағын аудан': { cat: 'street', val: 'микрорайон' },
    'шағын ауданы': { cat: 'street', val: 'микрорайон' },
    'тұйық көше': { cat: 'street', val: 'переулок' },
    'рядом с': { cat: 'landmark', val: null },
  };
  const NUM_FIELDS = new Set(['house', 'apartment', 'entrance', 'floor']);
  const AREA_TYPES = new Set(['микрорайон', 'квартал']);
  const QUOTES = new Set(['«', '»', '"', '“', '”', '„', "'", '(', ')']);
  const NUM_JOINERS = new Set(['№', '#', ':', '-']);
  const STOP = new Set(['и', 'в', 'на', 'у', 'к', 'с', 'по', 'no', 'n']);
  const HOUSE_LETTER = /^[абвгдежзabcdefgh]$/i;

  /* ---------- Text helpers ---------- */
  const titleCase = (s) => s.toLowerCase().split('-').map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join('-');
  const lc = (s) => (s ? s.toLowerCase().replace(/ё/g, 'е') : '');

  function normalize(s) {
    return String(s ?? '')
      .replace(/[\u200B-\u200F\u202A-\u202E\u2060-\u206F\uFEFF\u00AD]/g, '') // invisible service chars
      .replace(/[\u00A0\u2000-\u200A\u2028\u2029\u202F\u205F\u3000\t\r\n]/g, ' ')
      .replace(/[‐‑‒–—]/g, '-')
      .replace(/(\p{L})\s+\.(?=[\p{L}\d])/gu, '$1. ') // "Ул .Курильская" → "Ул. Курильская"
      .replace(/ж\.\s?м\.?(?=[\s,]|$)/giu, 'ж/м')
      .replace(/ж\.\s?к\.?(?=[\s,]|$)/giu, 'жк')
      .replace(/(\d)\s*\/\s*(\d)/g, '$1/$2') // "55 / 6" → "55/6"
      .replace(/\s*([,;])\s*/g, '$1 ')
      .replace(/\s{2,}/g, ' ')
      .trim();
  }

  // Bilingual duplicates: "город Алматы/Алматы қаласы" → "город Алматы"
  const KZ_ADMIN = '(?:қаласы|облысы|ауданы|ауылы|кенті)';
  const RE_KZ_AFTER = new RegExp(`\\s*\\/\\s*[^,/\\d]{1,40}?\\s${KZ_ADMIN}(?=[\\s,.;]|$)`, 'giu');
  const RE_KZ_BEFORE = new RegExp(`[^,/\\d]{1,40}?\\s${KZ_ADMIN}\\s*\\/\\s*`, 'giu');
  const RE_WORD_SLASH = /(\p{L}{3,})\s*\/\s*\p{L}{3,}/gu;
  const stripBilingual = (s) => s.replace(RE_KZ_AFTER, '').replace(RE_KZ_BEFORE, '').replace(RE_WORD_SLASH, '$1');

  const CHUNK_RE = /(\d+(?:\/\d+)*(?:\p{L}(?!\p{L}))?)|(\p{L}+(?:[-/'’`]\p{L}+)*\.?)|(\S)/gu;

  function tokenize(text) {
    const toks = [];
    text.split(/[,;]+/).forEach((seg, si) => {
      seg.trim().split(/\s+/).filter(Boolean).forEach((part) => {
        let first = true;
        for (const m of part.matchAll(CHUNK_RE)) {
          const type = m[1] ? 'num' : m[2] ? 'word' : 'punct';
          const dot = type === 'word' && m[0].endsWith('.');
          const raw = dot ? m[0].slice(0, -1) : m[0];
          toks.push({ raw, norm: lc(raw), type, dot, glue: !first, seg: si, used: false, kw: null });
          first = false;
        }
      });
    });
    // "12 а" → "12а" (house letter typed with a space)
    for (let i = 1; i < toks.length; i++) {
      const t = toks[i], p = toks[i - 1], n = toks[i + 1];
      const nextBlocks = n && n.seg === t.seg && (n.type === 'num' || (n.type === 'word' && !KW.has(n.norm)));
      if (t.type === 'word' && HOUSE_LETTER.test(t.raw) && p.type === 'num' && /^\d+(\/\d+)*$/.test(p.raw) && p.seg === t.seg && !nextBlocks) {
        p.raw += t.raw; p.norm = lc(p.raw);
        toks.splice(i--, 1);
      }
    }
    toks.forEach((t, i) => { t.i = i; });
    return toks;
  }

  function classify(toks) {
    toks.forEach((t, i) => {
      if (t.type !== 'word' || t.part) return;
      const next = toks[i + 1] && toks[i + 1].seg === t.seg ? toks[i + 1] : null;
      const prev = toks[i - 1] && toks[i - 1].seg === t.seg ? toks[i - 1] : null;
      const multi = next && MULTI[`${t.norm} ${next.norm}`];
      if (multi) { t.kw = multi; next.part = true; next.used = true; return; }
      const k = KW.get(t.norm);
      if (!k) return;
      const nextNum = next && (next.type === 'num' || NUM_JOINERS.has(next.raw));
      const nextWord = next && next.type === 'word';
      const nearNum = nextNum || (prev && prev.type === 'num');
      switch (t.norm) {
        case 'д': if (!nextNum) return; break;
        case 'п': if (nextNum) { t.kw = { cat: 'entrance', val: null }; return; } if (!nextWord) return; break;
        case 'г': case 'гор': case 'пос': if (!nextWord) return; break;
        case 'с': if (!t.dot || !nextWord) return; break;
        case 'под': case 'пд': case 'оф': case 'эт': if (!nearNum) return; break;
        case 'st': case 'str': case 'ave': if (!nextWord && !(prev && prev.type === 'word')) return; break;
        default: break;
      }
      t.kw = k;
    });
  }

  const rebuild = (ts) => {
    const s = ts.reduce((acc, t, i) => acc + (i && !t.glue ? ' ' : '') + t.raw, '');
    return s.replace(/^[\s\-/.,]+|[\s\-/.,]+$/g, '') || null;
  };

  /* ---------- Main parser ---------- */
  function parse(rawAddress) {
    const out = Object.fromEntries(FIELD_KEYS.map((k) => [k, null]));
    const ref = {};
    let flags = 0;
    let livingCity = null;

    const toks = tokenize(stripBilingual(normalize(rawAddress)));
    classify(toks);

    const mark = (t, role) => { t.used = true; t.role = role; };
    const freeWord = (i, seg) => { const t = toks[i]; return t && t.seg === seg && t.type === 'word' && !t.used && !t.kw ? t : null; };
    const sameSeg = (a, b) => a && b && a.seg === b.seg;

    // 1) Admin units with explicit markers: область / город / район (RU + KZ).
    for (const t of toks) {
      if (t.used || !t.kw) continue;
      const { cat } = t.kw;
      if (cat === 'province') {
        let name = null, src = [];
        if (t.norm === 'облысы') {
          const p1 = freeWord(t.i - 1, t.seg), p2 = freeWord(t.i - 2, t.seg);
          if (p1 && p2 && KZ_PROVINCES[`${p2.norm} ${p1.norm}`]) { name = KZ_PROVINCES[`${p2.norm} ${p1.norm}`]; src = [p2, p1]; }
          else if (p1) { name = KZ_PROVINCES[p1.norm] || `${titleCase(p1.raw)} облысы`; src = [p1]; }
        } else {
          const p1 = freeWord(t.i - 1, t.seg), n1 = freeWord(t.i + 1, t.seg);
          const isAdj = (w) => w && (/(ская|цкая)$/.test(w.norm) || KZ_PROVINCES[w.norm]);
          const w = isAdj(p1) ? p1 : isAdj(n1) ? n1 : p1 || n1;
          if (w) { name = KZ_PROVINCES[w.norm] || `${titleCase(w.raw)} область`; src = [w]; }
        }
        if (name) { out.province = out.province || name; src.forEach((x) => mark(x, 'province')); mark(t, 'province'); }
      } else if (cat === 'city') {
        const n1 = freeWord(t.i + 1, t.seg);
        if (n1) {
          const hit = CITY_INDEX.get(n1.norm);
          out.city = out.city || (hit ? hit.name : titleCase(n1.raw));
          mark(n1, 'city'); mark(t, 'city');
        }
      } else if (cat === 'cityPost') {
        const p1 = freeWord(t.i - 1, t.seg);
        if (p1) {
          const hit = CITY_INDEX.get(p1.norm);
          out.city = out.city || (hit ? hit.name : titleCase(p1.raw));
          mark(p1, 'city'); mark(t, 'city');
        }
      } else if (cat === 'district') {
        const p1 = freeWord(t.i - 1, t.seg), n1 = freeWord(t.i + 1, t.seg);
        let name = null, src = null;
        if (t.norm === 'ауданы') { if (p1) { name = KZ_DISTRICTS[p1.norm] || titleCase(p1.raw); src = p1; } }
        else if (p1 && /(ский|цкий|ской|ный|ий)$/.test(p1.norm)) { name = titleCase(p1.raw); src = p1; }
        else if (n1) { name = titleCase(n1.raw); src = n1; }
        else if (p1) { name = titleCase(p1.raw); src = p1; }
        if (name) { out.district = out.district || name; mark(src, 'district'); mark(t, 'district'); }
      }
    }

    // 2) Bare gazetteer hits: repeated city, city without "г.", ВКО, known living areas.
    for (const t of toks) {
      if (t.used || t.kw || t.type !== 'word') continue;
      const prev = toks[t.i - 1], next = toks[t.i + 1];
      const prevKw = sameSeg(prev, t) && prev.kw && ['street', 'living', 'landmark'].includes(prev.kw.cat);
      const nextStreet = sameSeg(next, t) && next.kw && next.kw.cat === 'street';
      if (prevKw || nextStreet) continue;
      const segStart = !sameSeg(prev, t) || (prev.used && ['city', 'province', 'district'].includes(prev.role));
      const city = CITY_INDEX.get(t.norm);
      if (city && (segStart || !out.city)) {
        if (!out.city) { out.city = city.name; mark(t, 'city'); }
        else if (out.city === city.name) mark(t, 'city'); // duplicate mention
        continue;
      }
      if (PROVINCE_ABBR[t.norm]) { out.province = out.province || PROVINCE_ABBR[t.norm]; mark(t, 'province'); continue; }
      if (segStart && !out.district && (DISTRICT_CITY[t.norm] || DISTRICT_PROVINCE[t.norm])) { out.district = titleCase(t.raw); mark(t, 'district'); continue; }
      const la = LIVING_AREAS[t.norm];
      if (la && !out.living_area) { out.living_area = la[0]; livingCity = la[1]; mark(t, 'living_area'); }
    }

    // 3) Numbered details: дом / кв / подъезд / этаж, prefix ("кв 70") or postfix ("239 кв").
    const prevNum = (t) => { const p = toks[t.i - 1]; return p && p.seg === t.seg && p.type === 'num' && !p.used ? p : null; };
    const nextNum = (t) => {
      let j = t.i + 1;
      while (toks[j] && toks[j].seg === t.seg && toks[j].type === 'punct' && NUM_JOINERS.has(toks[j].raw)) j++;
      const n = toks[j];
      return n && n.seg === t.seg && n.type === 'num' && !n.used ? n : null;
    };
    const assignNum = (kw, num) => {
      const field = kw.kw.cat;
      out[field] = out[field] || num.raw;
      for (let j = Math.min(kw.i, num.i); j <= Math.max(kw.i, num.i); j++) mark(toks[j], field);
    };
    const numKws = toks.filter((t) => t.kw && NUM_FIELDS.has(t.kw.cat) && !t.used);
    let post = 0, pre = 0;
    for (const k of numKws) {
      const p = prevNum(k), n = nextNum(k), g = toks[k.i + 1];
      if (k.glue && p) post += 2;
      else if (g && g.glue && g.type === 'num') pre += 2;
      else if (p && !n) post += 1;
      else if (n && !p) pre += 1;
    }
    const postfix = post > pre;
    for (const k of numKws) { // glued pairs ("11дом", "кв70") are unambiguous
      const p = prevNum(k), g = toks[k.i + 1];
      if (k.glue && p) assignNum(k, p);
      else if (g && g.glue && g.type === 'num' && !g.used) assignNum(k, g);
    }
    for (const k of numKws) {
      if (k.used) continue;
      const p = prevNum(k), n = nextNum(k);
      const pick = p && n ? (postfix ? p : n) : p || n;
      if (pick) assignNum(k, pick);
    }

    // 4) Streets, microdistricts, ЖК.
    const hasFreeNumAfter = (k) => toks.some((y) => y.i > k && y.type === 'num' && !y.used);
    const collectFwd = (start, isArea) => {
      const seg = toks[start].seg, res = [];
      for (let k = start; k < toks.length; k++) {
        const x = toks[k];
        if (x.seg !== seg || x.used || x.kw) break;
        if (x.type === 'word') { res.push(x); continue; }
        if (x.type === 'punct') {
          if (QUOTES.has(x.raw)) { x.used = true; continue; }
          const nx = toks[k + 1];
          if ((x.raw === '-' || x.raw === '/') && res.length && nx && nx.glue && !nx.used && nx.seg === seg) { res.push(x); continue; }
          break;
        }
        const last = res[res.length - 1];
        if (last && last.type === 'punct' && x.glue) { res.push(x); continue; } // "Самал-2"
        if (!res.length && (isArea || (toks[k + 1] && toks[k + 1].seg === seg && toks[k + 1].type === 'word'))) { res.push(x); continue; } // "мкр 5", "ул. 8 Марта"
        if (isArea && res.length && (out.house || hasFreeNumAfter(k))) { res.push(x); continue; } // "Аксай 3а, 59"
        break;
      }
      return res;
    };
    const collectBack = (t, isArea) => {
      const res = [];
      for (let k = t.i - 1; k >= 0; k--) {
        const x = toks[k];
        if (x.seg !== t.seg || x.used || x.kw) break;
        if (x.type === 'word') { res.unshift(x); if (res.filter((y) => y.type === 'word').length >= 3) break; continue; }
        if (x.type === 'num' && isArea) { res.unshift(x); continue; }
        if (x.raw === '-') { res.unshift(x); continue; }
        if (QUOTES.has(x.raw)) { x.used = true; continue; }
        break;
      }
      return res;
    };
    const streets = [], livings = [];
    for (const t of toks) {
      if (t.used || !t.kw || (t.kw.cat !== 'street' && t.kw.cat !== 'living')) continue;
      const isLiving = t.kw.cat === 'living';
      const isArea = isLiving || AREA_TYPES.has(t.kw.val);
      const start = t.i + 1 + (toks[t.i + 1] && toks[t.i + 1].part ? 1 : 0);
      const nt = toks[start], before = toks[t.i - 1];
      let nameToks;
      if (t.glue && before && before.type === 'num' && !before.used) nameToks = collectBack(t, isArea); // "66квартал"
      else if (nt && nt.seg === t.seg && !nt.used && !nt.kw &&
        (nt.type === 'word' || QUOTES.has(nt.raw) || (nt.type === 'num' && (isArea || (toks[start + 1] && toks[start + 1].type === 'word'))))) nameToks = collectFwd(start, isArea);
      else nameToks = collectBack(t, isArea); // "Рыбинский переулок", "Нурсити 2 микрорайон"
      const name = rebuild(nameToks);
      if (!name) continue;
      mark(t, 'street');
      nameToks.forEach((x) => mark(x, 'street'));
      (isLiving ? livings : streets).push({ type: isLiving ? null : t.kw.val, name, isArea });
    }

    // 5) Landmarks: "магазин береке", "Qazpost Солнечный", "рядом с ТЦ ...".
    const landmarks = [];
    for (const t of toks) {
      if (t.used || !t.kw || t.kw.cat !== 'landmark') continue;
      const res = [t];
      for (let k = t.i + 1; k < toks.length; k++) {
        const x = toks[k];
        if (x.seg !== t.seg || (x.used && !x.part)) break;
        if (x.part) { res.push(x); continue; }
        if (x.kw && x.kw.cat !== 'landmark') break;
        if (x.type === 'word') { res.push(x); continue; }
        if (QUOTES.has(x.raw)) { x.used = true; continue; }
        if (x.raw === '№' && toks[k + 1] && toks[k + 1].type === 'num' && !toks[k + 1].used) { res.push(x, toks[k + 1]); k++; continue; }
        break;
      }
      res.forEach((x) => mark(x, 'landmark'));
      landmarks.push(rebuild(res));
    }
    if (landmarks.length) out.landmark = landmarks.filter(Boolean).join(', ');

    // 6) Bare house number and street without a type ("Кулманова 133", "Туран 55/6").
    let houseIdx = toks.findIndex((t) => t.role === 'house');
    if (!out.house) {
      const nums = toks.filter((t) => t.type === 'num' && !t.used);
      const h = nums.find((t) => { const p = toks[t.i - 1]; return sameSeg(p, t) && p.type === 'word' && !p.used && !p.kw; }) || nums[0];
      if (h) {
        out.house = h.raw; mark(h, 'house'); houseIdx = h.i;
        const dash = toks[h.i + 1], tail = toks[h.i + 2];
        if (!out.apartment && dash && dash.raw === '-' && dash.glue && tail && tail.type === 'num' && tail.glue && !tail.used) {
          out.apartment = tail.raw; mark(dash, 'apartment'); mark(tail, 'apartment'); flags++; // "Абая 10-45"
        }
        const extra = toks.find((t) => t.type === 'num' && !t.used && t.i > h.i);
        if (!out.apartment && extra) { out.apartment = extra.raw; mark(extra, 'apartment'); flags++; }
      }
    }
    let bare = null;
    if (!streets.some((s) => !s.isArea) && houseIdx > 0) {
      let k = houseIdx - 1;
      while (k >= 0 && toks[k].type === 'punct' && !toks[k].used) k--;
      const run = [];
      const seg = toks[k] ? toks[k].seg : -1;
      for (; k >= 0; k--) {
        const x = toks[k];
        if (x.seg !== seg || x.used || x.kw) break;
        if (x.type === 'word' || (x.raw === '-' && run.length)) { run.unshift(x); continue; }
        break;
      }
      bare = rebuild(run);
      if (bare) run.forEach((x) => mark(x, 'street'));
    }

    // 7) Resolve street vs. living area (мкр is a living area only when a street is given too).
    const street = streets.find((s) => !s.isArea);
    const area = streets.find((s) => s.isArea);
    if (street) { out.street_type = street.type; out.street_name = street.name; }
    else if (bare) { out.street_name = bare; }
    else if (area) { out.street_type = area.type; out.street_name = area.name; }
    if ((street || bare) && area && !out.living_area) out.living_area = area.name;
    if (livings.length && !out.living_area) out.living_area = livings[0].name;

    // 8) Reference look-ups for fields absent from the string (flagged as "ref").
    if (!out.city && out.district && DISTRICT_CITY[lc(out.district)]) { out.city = DISTRICT_CITY[lc(out.district)]; ref.city = true; }
    if (!out.city && livingCity) { out.city = livingCity; ref.city = true; }
    if (!out.province && out.city) { const c = CITY_INDEX.get(lc(out.city)); if (c) { out.province = c.province; ref.province = true; } }
    if (!out.province && out.district && DISTRICT_PROVINCE[lc(out.district)]) { out.province = DISTRICT_PROVINCE[lc(out.district)]; ref.province = true; }
    if (out.city === 'Алматы' && !out.district) {
      const areaName = out.living_area || (out.street_type === 'микрорайон' ? out.street_name : null);
      const key = areaName && lc(areaName).split(/[\s\-\d]+/)[0];
      if (key && AREA_DISTRICT[key]) { out.district = AREA_DISTRICT[key]; ref.district = true; }
    }

    // 9) Confidence: coverage of the key fields minus unexplained leftovers.
    const leftovers = toks.filter((t) => !t.used && t.type !== 'punct' && !STOP.has(t.norm)).length;
    let c = 0.22;
    if (out.city) c += ref.city ? 0.12 : 0.2;
    if (out.province) c += 0.05;
    if (out.street_name) c += 0.2;
    if (out.house) c += 0.17;
    if (out.street_type) c += 0.04;
    if (out.apartment || out.entrance || out.floor) c += 0.05;
    if (out.district || out.living_area) c += 0.03;
    c -= Math.min(0.3, leftovers * 0.08) + flags * 0.07;
    if (!out.city) c = Math.min(c, 0.62); // cannot route a delivery without a city
    if (!out.street_name && !out.house) c = Math.min(c, 0.3);
    if (!toks.length) c = 0;
    out.confidence = Math.round(Math.min(0.98, Math.max(0.05, c)) * 100) / 100;
    out._ref = ref;
    return out;
  }

  function safeParse(raw) {
    try { return parse(raw); }
    catch (err) {
      console.warn('[LocalEngine] parse failed', raw, err);
      return { ...Object.fromEntries(FIELD_KEYS.map((k) => [k, null])), confidence: 0.05, _ref: {} };
    }
  }

  return { parse: safeParse, name: 'Local Engine' };
})();

// Built-in demo set: the tricky patterns from the ТЗ plus typical full records.
const DEMO_ADDRESSES = [
  'город Алматы/Алматы қаласы, Мкр Аксай 3а, 59, кв 70',
  'город Алматы/Алматы қаласы, Турксибский район, Рыбинский переулок, дом 4',
  'Алматинская область/Алматы облысы, Илийский район, пос. Боралдай, ул. Аубакирова 15',
  'город Астана/Астана қаласы, Туран 55/6 магазин береке 4подьезд 9этаж',
  'город Астана/Астана қаласы, пр. Кабанбай батыра 53, flat 191, 5 этаж',
  'Актюбинская область/Ақтөбе облысы, город Актобе/Ақтөбе қаласы, Назарбаева8 kv 12',
  'Костанайская область/Қостанай облысы, город Костанай/Қостанай қаласы, Ул .Курильская 9 кв 1',
  'Карагандинская область/Қарағанды облысы, город Караганда/Қарағанды қаласы, Пришахтинск, ул. Сатпаева 7, кв. 14',
  'город Алматы/Алматы қаласы, Алматы, мкр Самал-2, д. 58, кв 12',
  'город Шымкент/Шымкент қаласы, Шымкент, Аль-Фарабийский район, мкр Нурсат, 125/2, кв 8',
  'ВКО, г. Усть-Каменогорск, пр Независимости 25а, эт 3',
  'город Астана/Астана қаласы, ЖК «Томирис», ул. Сыганак 18, кв 45',
  'Павлодарская область/Павлодар облысы, город Павлодар/Павлодар қаласы, ул. Торайгырова 64, 3 подъезд, Qazpost Солнечный',
  'город Атырау/Атырау қаласы, Кулманова 133',
  'город Уральск/Орал қаласы,\u200B пр. Евразия 101,  кв.\u00A033',
  'город Алматы/Алматы қаласы, ул. казибек би 50, кв 7',
  'Нурсити 2 микрорайон 11дом 5 подьезд 239 кв',
  '66квартал 12дом 69квартира',
  'жуалы 19',
  'Абая 10-45',
  'позвонить перед доставкой',
];
const EXAMPLES = [
  'Туран 55/6 магазин береке 4подьезд 9этаж',
  'Нурсити 2 микрорайон 11дом 5 подьезд 239 кв',
  'город Актобе/Ақтөбе қаласы, Ул .Курильская 9 кв 1',
  '66квартал 12дом 69квартира',
];

/* =============================================================================
 * Localization (RU default · KZ · EN) — UI text only; JSON data keys never change
 * ========================================================================== */
const LANGS = {
  ru: { locale: 'ru-RU', html: 'ru' },
  kz: { locale: 'kk-KZ', html: 'kk' },
  en: { locale: 'en-US', html: 'en' },
};
const LANG_KEY = 'jana-address/lang';

const I18N = {
  ru: {
    doc_title: 'Jana Address — ИИ-разбор адресов',
    lang_aria: 'Язык интерфейса',
    status_online: 'Система онлайн',
    copyright: '©2026 Jana Post · Хакатон',
    hero_eyebrow: 'Адресная аналитика · Jana Post',
    hero_line1: 'Одна строка на входе.',
    hero_line2: 'Одиннадцать полей на выходе.',
    hero_lead: 'Двуязычные, слитные, с опечатками — строки доставки превращаются в запись строго по схеме. Всё, что ниже <span class="font-mono text-amber-300">0.70</span> уверенности, уходит на ручную проверку.',
    hero_b2: '11 полей схемы',
    hero_b3: 'Работает офлайн',
    kpi_total_eyebrow: 'Всего',
    kpi_total_label: 'Адресов обработано',
    kpi_ready_eyebrow: 'Валидно',
    kpi_ready_label: 'Высокая уверенность',
    kpi_review_eyebrow: 'На проверку',
    kpi_review_label: 'Очередь ручной проверки',
    kpi_review_title: 'Показать очередь проверки',
    kpi_awaiting: 'Ожидание данных',
    kpi_ready_sub: '{pct} от общего · подтверждено: {n}',
    kpi_review_sub: '{pct} от общего · нажмите для проверки',
    kpi_queue_clear: 'Очередь пуста',
    manual_input: 'ручной ввод',
    ingest_eyebrow: 'Загрузка',
    ingest_title: 'Пакетная загрузка',
    demo_btn: 'Демо-набор',
    dz_aria: 'Загрузить JSON-файл с адресами',
    dz_drop: 'Перетащите <span class="font-mono text-lime-300">sample_input.json</span> сюда',
    dz_browse: 'или <span class="text-slate-300 underline decoration-lime-400/50 underline-offset-4">выберите файл</span> · JSON-массив <span class="font-mono text-slate-400">{ id, raw_address }</span>',
    ingest_idle: 'Файл ещё не загружен. Ответ будет на каждую запись — ничего не пропускается.',
    parsing_n: 'Разбираем {n}…',
    loading_parsed: 'Загружаем размеченные записи…',
    quick_eyebrow: 'Быстрый тест',
    quick_title: 'Разобрать один адрес',
    quick_label: 'Исходный адрес',
    parse_btn: 'Разобрать',
    quick_empty: 'Здесь появится результат: все поля схемы, уверенность и движок, который ответил.',
    preview_add: 'В результаты',
    conf_high: 'Высокая уверенность',
    conf_review: 'На проверку',
    conf_ready: 'Готово',
    conf_verified: 'Подтверждено',
    ms: '{n} мс',
    results_eyebrow: 'Результаты',
    results_title: 'Разобранные записи',
    reset_btn: 'Сбросить',
    reset_title: 'Удалить все записи из этого браузера',
    export_btn: 'Экспорт чистого JSON',
    tabs_aria: 'Фильтр записей',
    tab_all: 'Все',
    tab_review: 'На проверку (< 0.7)',
    tab_ready: 'Готово (≥ 0.7)',
    search_label: 'Поиск по записям',
    search_ph: 'Поиск: id, адрес, город, улица…',
    col_id: 'ID', col_raw: 'Исходный адрес', col_province: 'Область', col_city: 'Город', col_district: 'Район',
    col_living: 'Жилой массив (ЖК)', col_street: 'Улица', col_house: 'Дом', col_flat: 'Квартира', col_conf: 'Уверенность',
    sort_title: 'Сортировать по уверенности',
    sort_asc: 'Сначала низкая уверенность',
    sort_desc: 'Сначала высокая уверенность',
    legend_review: 'нужна проверка · нажмите на строку',
    legend_verified: 'подтверждено оператором',
    legend_ref: 'из справочника, в строке нет',
    ref_tag: 'спр',
    ref_tip: 'Заполнено из справочника — в исходной строке этого нет',
    prev_page: 'Предыдущая страница',
    next_page: 'Следующая страница',
    pager: '{a}–{b} из {n}',
    empty_title: 'Записей пока нет',
    empty_body: 'Перетащите <span class="font-mono text-slate-300">sample_input.json</span> выше или начните со встроенного демо-набора.',
    empty_demo: 'Загрузить демо-набор',
    no_match_q: 'Нет записей по запросу «{q}».',
    no_match_filter: 'Нет записей для этого фильтра.',
    row_aria: 'Редактировать {id}',
    footer_schema: 'схема',
    api_connected: 'подключено',
    api_checking: 'проверка',
    api_offline: 'офлайн → локальный движок',
    ed_eyebrow: 'Ручная проверка оператором',
    ed_title: 'Правка записи',
    ed_close: 'Закрыть',
    ed_raw: 'raw_address · возвращается без изменений',
    ed_note: 'Сохранение отмечает запись как <span class="text-lime-300">подтверждённую</span> и ставит уверенность <span class="font-mono text-lime-300">1.00</span> · <kbd>Ctrl</kbd> + <kbd>Enter</kbd>',
    ed_cancel: 'Отмена',
    ed_save_next: 'Сохранить и далее (ещё {n})',
    ed_save: 'Сохранить и подтвердить',
    f_province: 'Область', h_province: '«Актюбинская область» / «Алматы»',
    f_city: 'Город', h_city: 'Без «г.» и казахской части',
    f_district: 'Район', h_district: 'Без слова «район»',
    f_living_area: 'Жилой массив (ЖК / мкр)', h_living_area: 'Если улица указана отдельно',
    f_street_type: 'Тип улицы', h_street_type: 'улица · проспект · микрорайон…',
    f_street_name: 'Название улицы', h_street_name: 'Без типа, ровно как в строке',
    f_house: 'Дом', h_house: 'С дробью и литерой: 1/2, 12а',
    f_apartment: 'Квартира', h_apartment: 'Только номер',
    f_entrance: 'Подъезд', h_entrance: 'Только номер',
    f_floor: 'Этаж', h_floor: 'Только номер',
    f_landmark: 'Ориентир', h_landmark: '«магазин Береке»',
    pv_living: 'Жилой массив',
    eng_api: 'FastAPI',
    eng_local: 'Локальный движок',
    eng_file: 'Импорт файла',
    eng_connecting: 'Подключение…',
    st_api: 'Подключено к {url}. Нажмите, чтобы проверить снова.',
    st_local: 'FastAPI по адресу {url} недоступен: разбор идёт в браузере. Нажмите, чтобы проверить снова.',
    st_check: 'Проверяем бэкенд FastAPI…',
    t_restored: 'Восстановлено из прошлой сессии: {n}',
    t_parsed: '{n} · {engine} · на проверку: {review}',
    t_api_missing: 'Нет в ответе API: {n} — разобраны локально',
    t_api_ok: 'Бэкенд FastAPI доступен',
    t_api_off: 'Бэкенд недоступен: работает локальный движок',
    t_api_connected: 'Бэкенд FastAPI подключён',
    t_not_json_file: '«{name}» — не JSON-файл',
    t_read_fail: 'Не удалось прочитать файл',
    t_added: '{id} добавлена в результаты',
    t_cleared: 'Сессия очищена',
    t_confirm_reset: 'Удалить из этого браузера все записи ({n})? Если они нужны, сначала сделайте экспорт.',
    t_verified: '{id} подтверждена',
    t_verified_conf: '{id} подтверждена · уверенность 1.00',
    t_queue_clear: 'Очередь проверки пуста',
    t_nothing: 'Пока нечего экспортировать',
    t_exported: 'Экспортировано: {n} → {name}',
    e_expected_array: 'Ожидается JSON-массив вида { id, raw_address }',
    e_invalid_json: 'Файл не является корректным JSON',
    e_no_records: 'В файле нет записей',
    rec: { one: 'запись', few: 'записи', many: 'записей', other: 'записи' },
  },

  kz: {
    doc_title: 'Jana Address — мекенжайларды ЖИ арқылы талдау',
    lang_aria: 'Интерфейс тілі',
    status_online: 'Жүйе желіде',
    copyright: '©2026 Jana Post · Хакатон',
    hero_eyebrow: 'Мекенжай аналитикасы · Jana Post',
    hero_line1: 'Кіріс — бір ретсіз жол.',
    hero_line2: 'Шығыс — он бір таза өріс.',
    hero_lead: 'Екі тілде, бітісіп, қатемен жазылған жеткізу жолдары схемаға сай жазбаға айналады. Сенімділігі <span class="font-mono text-amber-300">0.70</span>-тен төмен жазбалар қолмен тексеруге жіберіледі.',
    hero_b2: 'Схеманың 11 өрісі',
    hero_b3: 'Офлайн жұмыс',
    kpi_total_eyebrow: 'Барлығы',
    kpi_total_label: 'Өңделген мекенжай',
    kpi_ready_eyebrow: 'Жарамды',
    kpi_ready_label: 'Жоғары сенімділік',
    kpi_review_eyebrow: 'Тексеру қажет',
    kpi_review_label: 'Қолмен тексеру кезегі',
    kpi_review_title: 'Тексеру кезегін көрсету',
    kpi_awaiting: 'Деректер күтілуде',
    kpi_ready_sub: 'жалпының {pct} · расталды: {n}',
    kpi_review_sub: 'жалпының {pct} · тексеру үшін басыңыз',
    kpi_queue_clear: 'Кезек бос',
    manual_input: 'қолмен енгізу',
    ingest_eyebrow: 'Жүктеу',
    ingest_title: 'Топтамалап жүктеу',
    demo_btn: 'Демо жиынтық',
    dz_aria: 'Мекенжайлары бар JSON файлын жүктеу',
    dz_drop: '<span class="font-mono text-lime-300">sample_input.json</span> файлын осында сүйреңіз',
    dz_browse: 'немесе <span class="text-slate-300 underline decoration-lime-400/50 underline-offset-4">файлды таңдаңыз</span> · <span class="font-mono text-slate-400">{ id, raw_address }</span> JSON массиві',
    ingest_idle: 'Файл әлі жүктелмеген. Әр жазбаға жауап беріледі — ештеңе өткізіліп кетпейді.',
    parsing_n: '{n} талдануда…',
    loading_parsed: 'Талданған жазбалар жүктелуде…',
    quick_eyebrow: 'Жылдам тест',
    quick_title: 'Бір мекенжайды талдау',
    quick_label: 'Бастапқы мекенжай',
    parse_btn: 'Талдау',
    quick_empty: 'Нәтиже осында шығады: схеманың барлық өрістері, сенімділік және жауап берген өңдегіш.',
    preview_add: 'Нәтижелерге қосу',
    conf_high: 'Жоғары сенімділік',
    conf_review: 'Тексеру қажет',
    conf_ready: 'Дайын',
    conf_verified: 'Расталды',
    ms: '{n} мс',
    results_eyebrow: 'Нәтижелер',
    results_title: 'Талданған жазбалар',
    reset_btn: 'Тазалау',
    reset_title: 'Осы браузердегі барлық жазбаны өшіру',
    export_btn: 'Таза JSON экспорттау',
    tabs_aria: 'Жазбаларды сүзу',
    tab_all: 'Барлығы',
    tab_review: 'Тексеру қажет (< 0.7)',
    tab_ready: 'Дайын (≥ 0.7)',
    search_label: 'Жазбалардан іздеу',
    search_ph: 'Іздеу: id, мекенжай, қала, көше…',
    col_id: 'ID', col_raw: 'Бастапқы мекенжай', col_province: 'Облыс', col_city: 'Қала', col_district: 'Аудан',
    col_living: 'Тұрғын алап (ЖК)', col_street: 'Көше', col_house: 'Үй', col_flat: 'Пәтер', col_conf: 'Сенімділік',
    sort_title: 'Сенімділік бойынша сұрыптау',
    sort_asc: 'Алдымен төмен сенімділік',
    sort_desc: 'Алдымен жоғары сенімділік',
    legend_review: 'тексеру қажет · жолды басыңыз',
    legend_verified: 'оператор растаған',
    legend_ref: 'анықтамалықтан алынды, жолда жоқ',
    ref_tag: 'анықт.',
    ref_tip: 'Анықтамалықтан толтырылды — бастапқы жолда жоқ',
    prev_page: 'Алдыңғы бет',
    next_page: 'Келесі бет',
    pager: '{n} ішінен {a}–{b}',
    empty_title: 'Әзірге жазба жоқ',
    empty_body: 'Жоғарыдағы аймаққа <span class="font-mono text-slate-300">sample_input.json</span> файлын сүйреңіз немесе кірістірілген демо жиынтықтан бастаңыз.',
    empty_demo: 'Демо жиынтықты жүктеу',
    no_match_q: '«{q}» сұрауы бойынша жазба жоқ.',
    no_match_filter: 'Бұл сүзгі бойынша жазба жоқ.',
    row_aria: '{id} жазбасын түзету',
    footer_schema: 'схема',
    api_connected: 'қосулы',
    api_checking: 'тексерілуде',
    api_offline: 'желіден тыс → жергілікті өңдегіш',
    ed_eyebrow: 'Оператордың қолмен тексеруі',
    ed_title: 'Жазбаны түзету',
    ed_close: 'Жабу',
    ed_raw: 'raw_address · өзгеріссіз қайтарылады',
    ed_note: 'Сақтағанда жазба <span class="text-lime-300">расталған</span> деп белгіленіп, сенімділік <span class="font-mono text-lime-300">1.00</span> болады · <kbd>Ctrl</kbd> + <kbd>Enter</kbd>',
    ed_cancel: 'Болдырмау',
    ed_save_next: 'Сақтап, келесіге (тағы {n})',
    ed_save: 'Сақтау және растау',
    f_province: 'Облыс', h_province: '«Актюбинская область» / «Алматы»',
    f_city: 'Қала', h_city: '«г.» сөзінсіз, қазақша бөлігінсіз',
    f_district: 'Аудан', h_district: '«район» сөзінсіз',
    f_living_area: 'Тұрғын алап (ЖК / мкр)', h_living_area: 'Көше бөлек көрсетілсе',
    f_street_type: 'Көше түрі', h_street_type: 'улица · проспект · микрорайон…',
    f_street_name: 'Көше атауы', h_street_name: 'Түрінсіз, жолда қалай жазылса, солай',
    f_house: 'Үй', h_house: 'Бөлшегі мен әрпімен: 1/2, 12а',
    f_apartment: 'Пәтер', h_apartment: 'Тек нөмірі',
    f_entrance: 'Кіреберіс', h_entrance: 'Тек нөмірі',
    f_floor: 'Қабат', h_floor: 'Тек нөмірі',
    f_landmark: 'Бағдар', h_landmark: '«магазин Береке»',
    pv_living: 'Тұрғын алап',
    eng_api: 'FastAPI',
    eng_local: 'Жергілікті өңдегіш',
    eng_file: 'Импортталған файл',
    eng_connecting: 'Қосылуда…',
    st_api: '{url} қосылды. Қайта тексеру үшін басыңыз.',
    st_local: '{url} мекенжайындағы FastAPI қолжетімсіз: талдау браузерде жүреді. Қайта тексеру үшін басыңыз.',
    st_check: 'FastAPI бэкенді тексерілуде…',
    t_restored: 'Алдыңғы сессиядан қалпына келтірілді: {n}',
    t_parsed: '{n} · {engine} · тексеруге: {review}',
    t_api_missing: 'API жауабында жоқ: {n} — жергілікті талданды',
    t_api_ok: 'FastAPI бэкенді қолжетімді',
    t_api_off: 'Бэкенд қолжетімсіз: жергілікті өңдегіш жұмыс істейді',
    t_api_connected: 'FastAPI бэкенді қосылды',
    t_not_json_file: '«{name}» JSON файлы емес',
    t_read_fail: 'Файлды оқу мүмкін болмады',
    t_added: '{id} нәтижелерге қосылды',
    t_cleared: 'Сессия тазаланды',
    t_confirm_reset: 'Осы браузерден барлық жазбаны ({n}) өшіру керек пе? Қажет болса, алдымен экспорттаңыз.',
    t_verified: '{id} расталды',
    t_verified_conf: '{id} расталды · сенімділік 1.00',
    t_queue_clear: 'Тексеру кезегі бос',
    t_nothing: 'Әзірге экспорттайтын ештеңе жоқ',
    t_exported: 'Экспортталды: {n} → {name}',
    e_expected_array: '{ id, raw_address } түріндегі JSON массиві күтілді',
    e_invalid_json: 'Файл дұрыс JSON емес',
    e_no_records: 'Файлда жазба жоқ',
    rec: { other: 'жазба' },
  },

  en: {
    doc_title: 'Jana Address — AI Address Parser',
    lang_aria: 'Interface language',
    status_online: 'System Online',
    copyright: '©2026 Jana Post · Hackathon',
    hero_eyebrow: 'Address intelligence · Jana Post',
    hero_line1: 'One messy line in.',
    hero_line2: 'Eleven clean fields out.',
    hero_lead: 'Bilingual, glued, typo-ridden delivery strings parsed into a schema-compliant record. Anything under <span class="font-mono text-amber-300">0.70</span> confidence goes to a human review queue.',
    hero_b2: '11 schema fields',
    hero_b3: 'Offline fallback',
    kpi_total_eyebrow: 'Total',
    kpi_total_label: 'Addresses processed',
    kpi_ready_eyebrow: 'Validated',
    kpi_ready_label: 'High confidence',
    kpi_review_eyebrow: 'Needs review',
    kpi_review_label: 'Human-in-the-loop queue',
    kpi_review_title: 'Show the review queue',
    kpi_awaiting: 'Awaiting dataset',
    kpi_ready_sub: '{pct} of total · {n} verified',
    kpi_review_sub: '{pct} of total · click to review',
    kpi_queue_clear: 'Queue clear',
    manual_input: 'manual input',
    ingest_eyebrow: 'Ingestion',
    ingest_title: 'Batch upload',
    demo_btn: 'Load demo set',
    dz_aria: 'Upload a JSON file with addresses',
    dz_drop: 'Drop <span class="font-mono text-lime-300">sample_input.json</span> here',
    dz_browse: 'or <span class="text-slate-300 underline decoration-lime-400/50 underline-offset-4">browse files</span> · JSON array of <span class="font-mono text-slate-400">{ id, raw_address }</span>',
    ingest_idle: 'No file loaded yet. Each record gets an answer: nothing is skipped.',
    parsing_n: 'Parsing {n}…',
    loading_parsed: 'Loading parsed records…',
    quick_eyebrow: 'Quick test',
    quick_title: 'Parse a single address',
    quick_label: 'Raw address',
    parse_btn: 'Parse',
    quick_empty: 'Result preview shows here: every schema field, confidence and the engine that answered.',
    preview_add: 'Add to results',
    conf_high: 'High confidence',
    conf_review: 'Needs review',
    conf_ready: 'Ready',
    conf_verified: 'Verified',
    ms: '{n} ms',
    results_eyebrow: 'Results',
    results_title: 'Parsed records',
    reset_btn: 'Reset',
    reset_title: 'Clear all records from this browser',
    export_btn: 'Export Clean JSON',
    tabs_aria: 'Filter records',
    tab_all: 'All',
    tab_review: 'Needs Review (< 0.7)',
    tab_ready: 'Ready (≥ 0.7)',
    search_label: 'Search records',
    search_ph: 'Search id, address, city, street…',
    col_id: 'ID', col_raw: 'Raw Address', col_province: 'Province', col_city: 'City', col_district: 'District',
    col_living: 'Living Area (ЖК)', col_street: 'Street', col_house: 'House', col_flat: 'Flat', col_conf: 'Confidence',
    sort_title: 'Sort by confidence',
    sort_asc: 'Lowest confidence first',
    sort_desc: 'Highest confidence first',
    legend_review: 'needs review · click row to fix',
    legend_verified: 'verified by operator',
    legend_ref: 'filled from reference, not in the string',
    ref_tag: 'ref',
    ref_tip: 'Filled from the reference directory — not present in the raw string',
    prev_page: 'Previous page',
    next_page: 'Next page',
    pager: '{a}–{b} of {n}',
    empty_title: 'No records yet',
    empty_body: 'Drop <span class="font-mono text-slate-300">sample_input.json</span> above, or start with the built-in demo set.',
    empty_demo: 'Load demo dataset',
    no_match_q: 'No records match “{q}”.',
    no_match_filter: 'No records match this filter.',
    row_aria: 'Edit {id}',
    footer_schema: 'schema',
    api_connected: 'connected',
    api_checking: 'checking',
    api_offline: 'offline → local engine',
    ed_eyebrow: 'Human-in-the-loop review',
    ed_title: 'Edit record',
    ed_close: 'Close editor',
    ed_raw: 'raw_address · returned unchanged',
    ed_note: 'Saving marks the record <span class="text-lime-300">verified</span> and sets confidence to <span class="font-mono text-lime-300">1.00</span> · <kbd>Ctrl</kbd> + <kbd>Enter</kbd>',
    ed_cancel: 'Cancel',
    ed_save_next: 'Save & next ({n} left)',
    ed_save: 'Save & verify',
    f_province: 'Province', h_province: '«Актюбинская область» / «Алматы»',
    f_city: 'City', h_city: 'No «г.» prefix, no Kazakh half',
    f_district: 'District', h_district: 'Without the word «район»',
    f_living_area: 'Living area (ЖК / мкр)', h_living_area: 'Only when a street is given too',
    f_street_type: 'Street type', h_street_type: 'улица · проспект · микрорайон…',
    f_street_name: 'Street name', h_street_name: 'Without the type, exactly as written',
    f_house: 'House', h_house: 'Keep fractions and letters: 1/2, 12а',
    f_apartment: 'Flat (apartment)', h_apartment: 'Number only',
    f_entrance: 'Entrance', h_entrance: 'Number only',
    f_floor: 'Floor', h_floor: 'Number only',
    f_landmark: 'Landmark', h_landmark: '«магазин Береке»',
    pv_living: 'Living area',
    eng_api: 'FastAPI',
    eng_local: 'Local Engine',
    eng_file: 'Imported file',
    eng_connecting: 'Connecting…',
    st_api: 'Connected to {url}. Click to re-check.',
    st_local: 'FastAPI at {url} is unreachable: parsing runs in the browser. Click to re-check.',
    st_check: 'Checking the FastAPI backend…',
    t_restored: 'Restored {n} from your last session',
    t_parsed: '{n} via {engine} · {review} to review',
    t_api_missing: '{n} missing from the API response were parsed locally',
    t_api_ok: 'FastAPI backend is reachable',
    t_api_off: 'Backend offline: using Local Engine',
    t_api_connected: 'FastAPI backend connected',
    t_not_json_file: '“{name}” is not a JSON file',
    t_read_fail: 'Could not read the file',
    t_added: '{id} added to results',
    t_cleared: 'Session cleared',
    t_confirm_reset: 'Remove all {n} from this browser? Export first if you need them.',
    t_verified: '{id} verified',
    t_verified_conf: '{id} verified · confidence 1.00',
    t_queue_clear: 'Review queue is clear',
    t_nothing: 'Nothing to export yet',
    t_exported: 'Exported {n} → {name}',
    e_expected_array: 'Expected a JSON array of { id, raw_address }',
    e_invalid_json: 'File is not valid JSON',
    e_no_records: 'No records found in the file',
    rec: { one: 'record', other: 'records' },
  },
};

let lang = 'ru';

function t(key, vars) {
  const raw = I18N[lang][key] ?? I18N.en[key] ?? key;
  return vars ? raw.replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? '')) : raw;
}

function initialLang() {
  const fromUrl = typeof location !== 'undefined' && new URLSearchParams(location.search).get('lang');
  let saved = null;
  try { saved = localStorage.getItem(LANG_KEY); } catch { /* storage blocked */ }
  return [fromUrl, saved].find((l) => l && LANGS[l]) || 'ru';
}

// Static markup: data-i18n (text), data-i18n-html (trusted dictionary HTML), data-i18n-attr ("attr:key;attr:key").
function applyStaticI18n() {
  document.documentElement.lang = LANGS[lang].html;
  document.title = t('doc_title');
  $$('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
  $$('[data-i18n-html]').forEach((el) => { el.innerHTML = t(el.dataset.i18nHtml); });
  $$('[data-i18n-attr]').forEach((el) => {
    el.dataset.i18nAttr.split(';').forEach((pair) => {
      const [attr, key] = pair.split(':');
      el.setAttribute(attr, t(key));
    });
  });
  $$('#langSwitch [data-lang]').forEach((b) => {
    const on = b.dataset.lang === lang;
    b.classList.toggle('is-active', on);
    b.setAttribute('aria-pressed', String(on));
  });
}

function setLang(next) {
  if (!LANGS[next]) return;
  lang = next;
  try { localStorage.setItem(LANG_KEY, next); } catch { /* storage blocked */ }
  applyStaticI18n();
  setEngine(state.engine);
  renderAll();
  if (preview) renderPreview();
  if (Editor.current) Editor.renderMeta();
}

/* =============================================================================
 * Backend client
 * ========================================================================== */
async function fetchWithTimeout(url, opts, ms) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  try { return await fetch(url, { ...opts, signal: ctrl.signal }); }
  finally { clearTimeout(timer); }
}

const Api = {
  // Any HTTP answer (even 405 for GET) means the server is up and CORS lets us in.
  async probe() {
    try { await fetchWithTimeout(CONFIG.apiUrl, { method: 'GET', cache: 'no-store' }, CONFIG.probeTimeoutMs); return true; }
    catch { return false; }
  },
  async parse(items) {
    const res = await fetchWithTimeout(CONFIG.apiUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(items.map(({ id, raw_address }) => ({ id, raw_address }))),
    }, Math.min(300000, 10000 + items.length * 300));
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const list = Array.isArray(data) ? data : (data && (data.results || data.data || data.records || data.items));
    if (!Array.isArray(list)) throw new Error('Unexpected API response shape');
    return list;
  },
};

/* =============================================================================
 * Utilities
 * ========================================================================== */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ESC[c]);
const pad = (n, w = 5) => String(n).padStart(w, '0');
const fmtInt = (n) => Number(n).toLocaleString(LANGS[lang].locale);
const plural = (n) => {
  const forms = I18N[lang].rec;
  return `${fmtInt(n)} ${forms[new Intl.PluralRules(LANGS[lang].locale).select(n)] || forms.other}`;
};
const pct = (part, total) => (total ? `${Math.round((part / total) * 100)}%` : '0%');
const tick = () => new Promise((r) => setTimeout(r, 0));
const debounce = (fn, ms) => { let tm; return (...a) => { clearTimeout(tm); tm = setTimeout(() => fn(...a), ms); }; };
const needsReview = (r) => r.confidence < CONFIG.reviewThreshold;
const engineLabel = (e) => t({ api: 'eng_api', local: 'eng_local', file: 'eng_file' }[e] || e);

const ICONS = {
  check: '<path d="M20 6 9 17l-5-5"/>',
  alert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 12a1 1 0 0 0-1 1v1a1 1 0 0 1-1 1 1 1 0 0 1 1 1v1a1 1 0 0 0 1 1"/><path d="M14 18a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1 1 1 0 0 1-1-1v-1a1 1 0 0 0-1-1"/>',
  db: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>',
  plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
};
const icon = (name, cls = 'h-4 w-4', sw = 1.9) =>
  `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;

function toast(message, kind = 'ok') {
  const box = $('#toasts');
  const el = document.createElement('div');
  const tone = { ok: 'text-lime-300', warn: 'text-amber-300', error: 'text-rose-400' }[kind];
  el.className = 'toast';
  el.innerHTML = `<span class="${tone}">${icon(kind === 'ok' ? 'check' : kind === 'warn' ? 'alert' : 'x', 'h-4 w-4', 2.2)}</span><span>${esc(message)}</span>`;
  box.appendChild(el);
  setTimeout(() => { el.classList.add('is-out'); setTimeout(() => el.remove(), 260); }, kind === 'ok' ? 3200 : 5200);
}

/* =============================================================================
 * State
 * ========================================================================== */
const state = {
  records: [],
  fileName: null, // null = typed in by hand (shown as the localized "manual input")
  lastRun: null, // { engine, ms, count }
  filter: 'all',
  query: '',
  sort: null, // 'asc' | 'desc' | null
  page: 1,
  engine: 'checking',
};
let uidSeq = 0;
let testSeq = 0;

function cleanValue(v) {
  if (v === undefined || v === null) return null;
  const s = String(v).replace(/\s+/g, ' ').trim();
  return s === '' || s.toLowerCase() === 'null' ? null : s;
}

function toRecord(item, parsed, engine) {
  const r = { id: String(item.id), raw_address: item.raw_address };
  for (const k of FIELD_KEYS) r[k] = cleanValue(parsed && parsed[k]);
  const c = Number(parsed && parsed.confidence);
  r.confidence = Number.isFinite(c) ? Math.round(Math.min(1, Math.max(0, c)) * 100) / 100 : 0;
  r._uid = ++uidSeq;
  r._engine = engine;
  r._ref = (parsed && parsed._ref) || {};
  r._verified = false;
  return r;
}

const persist = debounce(() => {
  try {
    const records = state.records.map(({ _search, ...r }) => r);
    localStorage.setItem(CONFIG.storageKey, JSON.stringify({ v: 1, fileName: state.fileName, lastRun: state.lastRun, records }));
  } catch { /* storage full or blocked: the session simply isn't remembered */ }
}, 400);

function restoreSession() {
  try {
    const saved = JSON.parse(localStorage.getItem(CONFIG.storageKey) || 'null');
    if (!saved || saved.v !== 1 || !Array.isArray(saved.records) || !saved.records.length) return false;
    state.records = saved.records.map((r) => ({ ...r, _uid: ++uidSeq }));
    state.fileName = saved.fileName || null;
    state.lastRun = saved.lastRun || null;
    return true;
  } catch { return false; }
}

/* =============================================================================
 * Parsing pipeline (API first, LocalEngine fallback)
 * ========================================================================== */
async function parseItems(items, onProgress = () => {}) {
  // Only call FastAPI when the last probe found it up: a refused connection costs ~2 s on Windows.
  const apiUp = probing ? await probing : state.engine === 'api';
  const t0 = performance.now();
  let records;
  let engine = 'api';
  try {
    if (!apiUp) throw new Error('API offline');
    const list = await Api.parse(items);
    const byId = new Map(list.filter(Boolean).map((r) => [String(r.id), r]));
    let filled = 0;
    records = items.map((it) => {
      const hit = byId.get(String(it.id));
      if (hit) return toRecord(it, hit, 'api');
      filled++;
      return toRecord(it, LocalEngine.parse(it.raw_address), 'local');
    });
    setEngine('api');
    if (filled) toast(t('t_api_missing', { n: plural(filled) }), 'warn');
  } catch (err) {
    engine = 'local';
    setEngine('local');
    records = [];
    for (let i = 0; i < items.length; i++) {
      records.push(toRecord(items[i], LocalEngine.parse(items[i].raw_address), 'local'));
      if ((i + 1) % 250 === 0) { onProgress((i + 1) / items.length); await tick(); }
    }
  }
  onProgress(1);
  return { records, engine, ms: Math.round(performance.now() - t0) };
}

function toList(data) {
  if (Array.isArray(data)) return data;
  if (data && typeof data === 'object') {
    const inner = data.records || data.data || data.items || data.addresses || data.results;
    return Array.isArray(inner) ? inner : [data];
  }
  throw new Error(t('e_expected_array'));
}

function toItems(list) {
  return list.map((r, i) => {
    if (typeof r === 'string') return { id: `rec_${pad(i + 1)}`, raw_address: r };
    const obj = r && typeof r === 'object' ? r : {};
    const raw = obj.raw_address ?? obj.address ?? obj.raw ?? '';
    return { ...obj, id: obj.id != null ? String(obj.id) : `rec_${pad(i + 1)}`, raw_address: String(raw) };
  });
}

function parseJsonText(text) {
  const clean = text.replace(/^\uFEFF/, '');
  try { return JSON.parse(clean); }
  catch {
    const lines = clean.split(/\r?\n/).filter((l) => l.trim());
    try { return lines.map((l) => JSON.parse(l)); } // JSON Lines
    catch { throw new Error(t('e_invalid_json')); }
  }
}

async function ingest(text, fileName) {
  const items = toItems(toList(parseJsonText(text)));
  if (!items.length) throw new Error(t('e_no_records'));

  // A file that already carries parsed fields (e.g. API output) is loaded for review as-is.
  const preParsed = items.every((r) => 'confidence' in r && ('city' in r || 'street_name' in r));
  showProgress(true, preParsed ? t('loading_parsed') : t('parsing_n', { n: plural(items.length) }));

  let result;
  if (preParsed) {
    const t0 = performance.now();
    result = { records: items.map((it) => toRecord(it, it, 'file')), engine: 'file', ms: Math.round(performance.now() - t0) };
  } else {
    result = await parseItems(items, (p) => setProgress(p));
  }

  state.records = result.records;
  state.fileName = fileName;
  state.lastRun = { engine: result.engine, ms: result.ms, count: result.records.length };
  state.page = 1;
  state.filter = 'all';
  state.query = '';
  $('#search').value = '';
  setTimeout(() => showProgress(false), 350);
  renderAll();
  persist();

  const review = result.records.filter(needsReview).length;
  toast(t('t_parsed', { n: plural(result.records.length), engine: engineLabel(result.engine), review: fmtInt(review) }));
}

/* =============================================================================
 * Rendering
 * ========================================================================== */
function animateNumber(el, to) {
  const from = Number(el.dataset.value || 0);
  el.dataset.value = String(to);
  cancelAnimationFrame(el._raf);
  if (from === to) { el.textContent = fmtInt(to); return; }
  const t0 = performance.now(), dur = 650;
  const step = (now) => {
    const p = Math.min(1, (now - t0) / dur);
    el.textContent = fmtInt(Math.round(from + (to - from) * (1 - Math.pow(1 - p, 3))));
    if (p < 1) el._raf = requestAnimationFrame(step);
  };
  el._raf = requestAnimationFrame(step);
}

function counts() {
  const total = state.records.length;
  let review = 0, verified = 0;
  for (const r of state.records) { if (needsReview(r)) review++; if (r._verified) verified++; }
  return { total, review, ready: total - review, verified };
}

function renderKpis() {
  const { total, review, ready, verified } = counts();
  animateNumber($('#kpiTotal'), total);
  animateNumber($('#kpiReady'), ready);
  animateNumber($('#kpiReview'), review);
  $('#kpiTotalBar').style.width = total ? '100%' : '0%';
  $('#kpiReadyBar').style.width = pct(ready, total);
  $('#kpiReviewBar').style.width = pct(review, total);

  const run = state.lastRun;
  $('#kpiTotalSub').textContent = total
    ? `${state.fileName || t('manual_input')}${run ? ` · ${engineLabel(run.engine)}` : ''}`
    : t('kpi_awaiting');
  $('#kpiReadySub').textContent = total ? t('kpi_ready_sub', { pct: pct(ready, total), n: fmtInt(verified) }) : '—';
  $('#kpiReviewSub').textContent = total ? (review ? t('kpi_review_sub', { pct: pct(review, total) }) : t('kpi_queue_clear')) : '—';
}

function renderTabs() {
  const { total, review, ready } = counts();
  const map = { all: total, review, ready };
  $$('#tabs [data-count]').forEach((el) => { el.textContent = fmtInt(map[el.dataset.count]); });
  $$('#tabs .tab').forEach((el) => {
    const on = el.dataset.filter === state.filter;
    el.classList.toggle('is-active', on);
    el.setAttribute('aria-selected', String(on));
  });
}

function searchText(r) {
  if (!r._search) r._search = [r.id, r.raw_address, ...FIELD_KEYS.map((k) => r[k])].filter(Boolean).join(' ').toLowerCase();
  return r._search;
}

function visibleRecords() {
  const q = state.query.trim().toLowerCase();
  let list = state.records;
  if (state.filter === 'review') list = list.filter(needsReview);
  else if (state.filter === 'ready') list = list.filter((r) => !needsReview(r));
  if (q) list = list.filter((r) => searchText(r).includes(q));
  if (state.sort) {
    const dir = state.sort === 'asc' ? 1 : -1;
    list = [...list].sort((a, b) => (a.confidence - b.confidence) * dir);
  }
  return list;
}

const NIL = '<span class="nil">—</span>';
const refTag = (r, key) => (r._ref && r._ref[key] ? `<span class="ref-tag" title="${esc(t('ref_tip'))}">${esc(t('ref_tag'))}</span>` : '');
const cell = (r, key, extra = '') => `<td class="${extra}">${r[key] ? esc(r[key]) : NIL}${refTag(r, key)}</td>`;

function confidenceCell(r) {
  const review = needsReview(r);
  const width = Math.round(r.confidence * 100);
  return `<div class="flex items-center justify-end gap-2.5">
      ${r._verified ? `<span class="badge-verified">${icon('check', 'h-3 w-3', 2.6)}${esc(t('conf_verified'))}</span>` : ''}
      <div class="h-1 w-14 overflow-hidden rounded-full bg-slate-800">
        <div class="h-full rounded-full ${review ? 'bg-amber-400 shadow-[0_0_8px_#fbbf24]' : 'bg-lime-400 shadow-[0_0_8px_#a3e635]'}" style="width:${width}%"></div>
      </div>
      <span class="w-9 text-right font-mono text-xs tabular-nums ${review ? 'text-amber-300' : 'text-lime-300'}">${r.confidence.toFixed(2)}</span>
    </div>`;
}

function rowHtml(r) {
  const review = needsReview(r);
  const street = r.street_name || r.street_type
    ? `${r.street_type ? `<span class="text-slate-500">${esc(r.street_type)}</span> ` : ''}${r.street_name ? esc(r.street_name) : ''}`
    : NIL;
  return `<tr class="${review ? 'is-review' : r._verified ? 'is-verified' : ''}" data-uid="${r._uid}" tabindex="0" aria-label="${esc(t('row_aria', { id: r.id }))}">
    <td class="whitespace-nowrap font-mono text-xs ${review ? 'text-amber-300' : 'text-slate-400'}">
      <span class="inline-flex items-center gap-1.5">${review ? icon('alert', 'h-3.5 w-3.5', 2) : ''}${esc(r.id)}</span>
    </td>
    <td class="min-w-[250px] max-w-[330px]"><span class="line-clamp-2 text-slate-300" title="${esc(r.raw_address)}">${r.raw_address ? esc(r.raw_address) : NIL}</span></td>
    ${cell(r, 'province', 'min-w-[130px]')}
    ${cell(r, 'city', 'whitespace-nowrap')}
    ${cell(r, 'district', 'whitespace-nowrap')}
    ${cell(r, 'living_area', 'min-w-[110px]')}
    <td class="min-w-[170px]">${street}</td>
    ${cell(r, 'house', 'whitespace-nowrap font-mono text-[13px]')}
    ${cell(r, 'apartment', 'whitespace-nowrap font-mono text-[13px]')}
    <td class="text-right">${confidenceCell(r)}</td>
  </tr>`;
}

// Rendered outside the 1180px-wide table so it stays centred on narrow screens.
function emptyStateHtml() {
  if (!state.records.length) {
    return `<div class="mx-auto max-w-sm px-6 py-16 text-center">
        <div class="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl border border-line-strong bg-ink-900 text-lime-400 shadow-[0_0_30px_-8px_rgba(163,230,53,.6)]">${icon('db', 'h-5 w-5')}</div>
        <p class="font-display text-lg text-slate-100">${esc(t('empty_title'))}</p>
        <p class="mt-1.5 text-sm text-slate-400">${t('empty_body')}</p>
        <button type="button" data-action="demo" class="btn btn-ghost mt-5">${icon('plus', 'h-4 w-4', 2)}${esc(t('empty_demo'))}</button>
      </div>`;
  }
  let message = esc(t('no_match_filter'));
  if (state.query) {
    const [before, after = ''] = t('no_match_q').split('{q}');
    message = `${esc(before)}<span class="text-slate-300">${esc(state.query)}</span>${esc(after)}`;
  }
  return `<p class="px-6 py-14 text-center text-sm text-slate-500">${message}</p>`;
}

function renderTable() {
  const list = visibleRecords();
  const pages = Math.max(1, Math.ceil(list.length / CONFIG.pageSize));
  state.page = Math.min(Math.max(1, state.page), pages);
  const start = (state.page - 1) * CONFIG.pageSize;
  const slice = list.slice(start, start + CONFIG.pageSize);

  $('#tbody').innerHTML = slice.map(rowHtml).join('');
  $('#tableWrap').classList.toggle('hidden', !slice.length);
  $('#emptyState').classList.toggle('hidden', Boolean(slice.length));
  $('#emptyState').innerHTML = slice.length ? '' : emptyStateHtml();
  $('#resultCount').textContent = state.records.length ? `(${fmtInt(state.records.length)})` : '';
  $('#pagerInfo').textContent = list.length
    ? t('pager', { a: fmtInt(start + 1), b: fmtInt(start + slice.length), n: fmtInt(list.length) })
    : '';
  $('#prevPage').disabled = state.page <= 1;
  $('#nextPage').disabled = state.page >= pages;
  $('#prevPage').classList.toggle('opacity-40', state.page <= 1);
  $('#nextPage').classList.toggle('opacity-40', state.page >= pages);

  const sortBtn = $('#sortConf');
  sortBtn.classList.toggle('text-lime-300', Boolean(state.sort));
  sortBtn.title = t(state.sort === 'asc' ? 'sort_asc' : state.sort === 'desc' ? 'sort_desc' : 'sort_title');
}

function renderAll() {
  renderKpis();
  renderTabs();
  renderTable();
  renderIngestMeta();
  $('#exportBtn').disabled = !state.records.length;
}

function setEngine(mode) {
  state.engine = mode;
  const pill = $('#statusPill');
  if (!pill) return;
  const host = (() => { try { return new URL(CONFIG.apiUrl).host; } catch { return CONFIG.apiUrl; } })();
  pill.dataset.state = mode === 'checking' ? 'checking' : 'online';
  $('#statusEngine').textContent = mode === 'checking' ? t('eng_connecting') : mode === 'api' ? `FastAPI · ${host}` : t('eng_local');
  pill.title = t(mode === 'api' ? 'st_api' : mode === 'checking' ? 'st_check' : 'st_local', { url: CONFIG.apiUrl });
  $('#footerApi').textContent = `api · ${CONFIG.apiUrl} · ${t(mode === 'api' ? 'api_connected' : mode === 'checking' ? 'api_checking' : 'api_offline')}`;
}

let probing = null; // in-flight probe, shared so a parse can wait for it

async function checkBackend(manual) {
  if (!probing) {
    setEngine('checking');
    probing = Api.probe().finally(() => { probing = null; });
  }
  const ok = await probing;
  setEngine(ok ? 'api' : 'local');
  if (manual) toast(t(ok ? 't_api_ok' : 't_api_off'), ok ? 'ok' : 'warn');
  return ok;
}

// While offline, quietly re-probe so the UI switches to FastAPI as soon as it comes up.
function watchBackend() {
  setInterval(async () => {
    if (state.engine !== 'local' || document.hidden || probing) return;
    if (await Api.probe()) { setEngine('api'); toast(t('t_api_connected')); }
  }, CONFIG.reprobeMs);
}

/* =============================================================================
 * Ingestion UI
 * ========================================================================== */
function showProgress(on, label = '') {
  $('#ingestProgress').classList.toggle('hidden', !on);
  if (on) { $('#ingestLabel').textContent = label; setProgress(0.04); }
}
function setProgress(p) {
  const v = Math.round(Math.min(1, Math.max(0, p)) * 100);
  $('#ingestBar').style.width = `${v}%`;
  $('#ingestPct').textContent = `${v}%`;
}

function renderIngestMeta() {
  const box = $('#ingestMeta');
  const run = state.lastRun;
  if (!state.records.length || !run) {
    box.innerHTML = `<span class="flex items-start gap-2"><span class="dot is-slate mt-[7px]"></span><span>${esc(t('ingest_idle'))}</span></span>`;
    return;
  }
  box.innerHTML = `
    <span class="chip !text-slate-300">${icon('file', 'h-3.5 w-3.5')}${esc(state.fileName || t('manual_input'))}</span>
    <span class="chip">${esc(plural(run.count))}</span>
    <span class="chip ${run.engine === 'api' ? '!text-lime-300' : ''}"><span class="dot ${run.engine === 'api' ? '' : 'is-slate'}"></span>${esc(engineLabel(run.engine))}</span>
    <span class="chip">${esc(t('ms', { n: fmtInt(run.ms) }))}</span>`;
}

async function handleFile(file) {
  if (!file) return;
  if (!/\.(json|jsonl)$/i.test(file.name) && file.type && !file.type.includes('json')) {
    toast(t('t_not_json_file', { name: file.name }), 'error');
    return;
  }
  try {
    await ingest(await file.text(), file.name);
  } catch (err) {
    showProgress(false);
    toast(err.message || t('t_read_fail'), 'error');
  }
}

function loadDemo() {
  const data = DEMO_ADDRESSES.map((raw, i) => ({ id: `rec_${pad(417 + i)}`, raw_address: raw }));
  ingest(JSON.stringify(data), 'demo_input.json').catch((err) => toast(err.message, 'error'));
}

function bindIngestion() {
  const zone = $('#dropzone');
  const input = $('#fileInput');
  zone.addEventListener('click', () => input.click());
  zone.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
  input.addEventListener('change', () => { handleFile(input.files[0]); input.value = ''; });

  let depth = 0;
  zone.addEventListener('dragenter', (e) => { e.preventDefault(); depth++; zone.classList.add('is-drag'); });
  zone.addEventListener('dragover', (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
  zone.addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; zone.classList.remove('is-drag'); } });
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    depth = 0;
    zone.classList.remove('is-drag');
    handleFile(e.dataTransfer.files[0]);
  });
  // A file dropped outside the zone must not navigate away from the app.
  window.addEventListener('dragover', (e) => e.preventDefault());
  window.addEventListener('drop', (e) => e.preventDefault());

  $('#demoBtn').addEventListener('click', loadDemo);
}

/* =============================================================================
 * Quick test
 * ========================================================================== */
const PREVIEW_FIELDS = [
  ['province', 'col_province'], ['city', 'col_city'], ['district', 'col_district'], ['living_area', 'pv_living'],
  ['street_type', 'f_street_type'], ['street_name', 'col_street'], ['house', 'col_house'], ['apartment', 'col_flat'],
  ['entrance', 'f_entrance'], ['floor', 'f_floor'], ['landmark', 'f_landmark'],
];
let preview = null; // { rec, engine, ms, added }

function renderPreview() {
  const { rec, engine, ms, added } = preview;
  const review = needsReview(rec);
  const box = $('#quickResult');
  box.className = 'mt-5 flex flex-1 flex-col rounded-2xl border border-line bg-ink-950/50 p-4';
  box.innerHTML = `
    <div class="mb-3 flex flex-wrap items-center gap-2">
      <span class="conf-pill ${review ? 'is-amber' : 'is-lime'}"><span class="dot ${review ? 'is-amber' : ''}"></span>
        <span class="font-mono">${rec.confidence.toFixed(2)}</span>${esc(t(review ? 'conf_review' : 'conf_high'))}</span>
      <span class="chip">${esc(engineLabel(engine))} · ${esc(t('ms', { n: fmtInt(ms) }))}</span>
      <button type="button" id="previewAdd" class="btn btn-ghost ml-auto !px-3 !py-1.5 text-xs" ${added ? 'disabled' : ''}>${icon('plus', 'h-3.5 w-3.5', 2)}${esc(t('preview_add'))}</button>
    </div>
    <dl class="grid grid-cols-2 gap-2 sm:grid-cols-3">
      ${PREVIEW_FIELDS.map(([key, label]) => `
        <div class="min-w-0 rounded-xl border border-line/80 bg-ink-850/70 px-3 py-2 ${key === 'landmark' ? 'col-span-2' : ''} ${key === 'street_name' && rec[key] ? 'border-lime-400/25' : ''}">
          <dt class="truncate font-mono text-[10px] uppercase tracking-[0.14em] text-slate-500">${esc(t(label))}</dt>
          <dd class="mt-0.5 truncate text-[13.5px] ${rec[key] ? 'text-slate-100' : 'text-slate-600'}" title="${esc(rec[key] || '')}">${rec[key] ? esc(rec[key]) : 'null'}${refTag(rec, key)}</dd>
        </div>`).join('')}
    </dl>`;
}

async function quickParse(raw) {
  const input = $('#quickInput');
  const value = (raw ?? input.value).trim();
  if (!value) {
    input.classList.remove('shake'); void input.offsetWidth; input.classList.add('shake');
    input.focus();
    return;
  }
  input.value = value;
  const btn = $('#quickBtn');
  btn.disabled = true;
  try {
    const item = { id: `test_${pad(++testSeq, 3)}`, raw_address: value };
    const { records, engine, ms } = await parseItems([item]);
    preview = { rec: records[0], engine, ms, added: false };
    renderPreview();
  } finally {
    btn.disabled = false;
  }
}

function addPreviewToResults() {
  if (!preview || preview.added) return;
  preview.added = true;
  state.records.unshift(preview.rec);
  if (!state.lastRun) state.lastRun = { engine: preview.engine, ms: preview.ms, count: 0 };
  state.lastRun.count = state.records.length;
  state.page = 1;
  toast(t('t_added', { id: preview.rec.id }));
  renderPreview();
  renderAll();
  persist();
}

function bindQuickTest() {
  $('#quickForm').addEventListener('submit', (e) => { e.preventDefault(); quickParse(); });
  $('#examples').innerHTML = EXAMPLES.map((ex) => `<button type="button" class="example-chip" data-example="${esc(ex)}" title="${esc(ex)}">${esc(ex)}</button>`).join('');
  $('#examples').addEventListener('click', (e) => {
    const chip = e.target.closest('[data-example]');
    if (chip) quickParse(chip.dataset.example);
  });
  $('#quickResult').addEventListener('click', (e) => { if (e.target.closest('#previewAdd')) addPreviewToResults(); });
}

/* =============================================================================
 * Table interactions
 * ========================================================================== */
function bindTable() {
  $('#emptyState').addEventListener('click', (e) => { if (e.target.closest('[data-action="demo"]')) loadDemo(); });
  const tbody = $('#tbody');
  tbody.addEventListener('click', (e) => {
    const row = e.target.closest('tr[data-uid]');
    if (row) Editor.open(Number(row.dataset.uid));
  });
  tbody.addEventListener('keydown', (e) => {
    const row = e.target.closest('tr[data-uid]');
    if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); Editor.open(Number(row.dataset.uid)); }
  });

  $('#tabs').addEventListener('click', (e) => {
    const tab = e.target.closest('[data-filter]');
    if (!tab) return;
    state.filter = tab.dataset.filter;
    state.page = 1;
    renderTabs();
    renderTable();
  });

  const onSearch = debounce(() => { state.query = $('#search').value; state.page = 1; renderTable(); }, 90);
  $('#search').addEventListener('input', onSearch);

  $('#sortConf').addEventListener('click', () => {
    state.sort = state.sort === null ? 'asc' : state.sort === 'asc' ? 'desc' : null;
    renderTable();
  });
  $('#prevPage').addEventListener('click', () => { state.page--; renderTable(); });
  $('#nextPage').addEventListener('click', () => { state.page++; renderTable(); });

  $('#kpiReviewCard').addEventListener('click', () => {
    state.filter = 'review';
    state.page = 1;
    renderTabs();
    renderTable();
    $('#tabs').scrollIntoView({ behavior: 'smooth', block: 'center' });
  });

  $('#exportBtn').addEventListener('click', exportJson);
  $('#resetBtn').addEventListener('click', () => {
    if (!state.records.length) return;
    if (!confirm(t('t_confirm_reset', { n: plural(state.records.length) }))) return;
    Object.assign(state, { records: [], fileName: null, lastRun: null, filter: 'all', query: '', page: 1 });
    $('#search').value = '';
    try { localStorage.removeItem(CONFIG.storageKey); } catch { /* ignore */ }
    renderAll();
    toast(t('t_cleared'));
  });
}

function flashRow(uid) {
  const row = $(`#tbody tr[data-uid="${uid}"]`);
  if (row) { row.classList.remove('is-flash'); void row.offsetWidth; row.classList.add('is-flash'); }
}

/* =============================================================================
 * Edit modal (human-in-the-loop)
 * ========================================================================== */
const FIELD_DEFS = [
  { key: 'province' },
  { key: 'city' },
  { key: 'district' },
  { key: 'living_area' },
  { key: 'street_type', list: 'streetTypes' },
  { key: 'street_name' },
  { key: 'house', mono: true },
  { key: 'apartment', mono: true },
  { key: 'entrance', mono: true },
  { key: 'floor', mono: true },
  { key: 'landmark', wide: true },
];

const Editor = {
  current: null,
  get dialog() { return $('#editor'); },

  // Labels and hints carry data-i18n keys, so applyStaticI18n() re-translates them in place.
  build() {
    $('#editorFields').innerHTML = FIELD_DEFS.map((f) => `
      <label class="block min-w-0 ${f.wide ? 'sm:col-span-2' : ''}">
        <span class="flex items-center gap-1 font-mono text-[10.5px] uppercase tracking-[0.14em] text-slate-400">
          <span data-i18n="f_${f.key}">${esc(t(`f_${f.key}`))}</span><span class="ref-tag hidden" data-ref-for="${f.key}" data-i18n="ref_tag" data-i18n-attr="title:ref_tip">${esc(t('ref_tag'))}</span>
        </span>
        <input name="${f.key}" class="field-input mt-1.5 ${f.mono ? 'font-mono' : ''}" placeholder="null" autocomplete="off" spellcheck="false" ${f.list ? `list="${f.list}"` : ''} />
        <span class="mt-1 block truncate text-[11px] text-slate-500" data-i18n="h_${f.key}">${esc(t(`h_${f.key}`))}</span>
      </label>`).join('');
  },

  open(uid) {
    const r = state.records.find((x) => x._uid === uid);
    if (!r) return;
    this.current = r;
    $('#editorId').textContent = r.id;
    $('#editorRaw').textContent = r.raw_address || '—';
    const form = $('#editorForm');
    for (const f of FIELD_DEFS) {
      form.elements[f.key].value = r[f.key] ?? '';
      $(`[data-ref-for="${f.key}"]`).classList.toggle('hidden', !(r._ref && r._ref[f.key]));
    }
    this.renderMeta();
    if (!this.dialog.open) this.dialog.showModal();
    const firstEmpty = FIELD_DEFS.find((f) => !r[f.key] && ['city', 'street_name', 'house'].includes(f.key));
    form.elements[(firstEmpty || FIELD_DEFS[0]).key].focus();
  },

  // Language-dependent bits of the open dialog (confidence pill, engine chip, "save & next" counter).
  renderMeta() {
    const r = this.current;
    if (!r) return;
    const review = needsReview(r);
    $('#editorMeta').innerHTML = `
      <span class="conf-pill ${review ? 'is-amber' : 'is-lime'} !py-1"><span class="dot ${review ? 'is-amber' : ''}"></span><span class="font-mono">${r.confidence.toFixed(2)}</span>${esc(t(r._verified ? 'conf_verified' : review ? 'conf_review' : 'conf_ready'))}</span>
      <span class="chip !py-1">${esc(engineLabel(r._engine))}</span>`;
    const left = state.records.filter((x) => x !== r && needsReview(x)).length;
    const next = $('#saveNextBtn');
    next.classList.toggle('hidden', left === 0);
    next.textContent = t('ed_save_next', { n: fmtInt(left) });
  },

  save(goNext) {
    const r = this.current;
    if (!r) return;
    const form = $('#editorForm');
    for (const k of FIELD_KEYS) r[k] = cleanValue(form.elements[k].value);
    r.confidence = 1;
    r._verified = true;
    r._ref = {};
    delete r._search;
    renderAll();
    persist();
    flashRow(r._uid);

    if (goNext) {
      const next = this.nextInQueue(r);
      if (next) { toast(t('t_verified', { id: r.id })); this.open(next._uid); return; }
      toast(t('t_queue_clear'));
    } else {
      toast(t('t_verified_conf', { id: r.id }));
    }
    this.close();
  },

  nextInQueue(after) {
    const list = state.records;
    const from = list.indexOf(after);
    for (let step = 1; step <= list.length; step++) {
      const cand = list[(from + step) % list.length];
      if (cand !== after && needsReview(cand)) return cand;
    }
    return null;
  },

  close() {
    if (this.dialog.open) this.dialog.close();
    this.current = null;
  },
};

function bindEditor() {
  Editor.build();
  const dialog = Editor.dialog;
  const form = $('#editorForm');
  form.addEventListener('submit', (e) => { e.preventDefault(); Editor.save(false); });
  $('#saveNextBtn').addEventListener('click', () => Editor.save(true));
  $$('[data-close]', dialog).forEach((b) => b.addEventListener('click', () => Editor.close()));
  dialog.addEventListener('click', (e) => { if (e.target === dialog) Editor.close(); }); // backdrop click
  dialog.addEventListener('close', () => { Editor.current = null; });
  form.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); Editor.save(false); }
  });
}

/* =============================================================================
 * Export — schema keys only, in schema order, regardless of UI language
 * ========================================================================== */
function exportJson() {
  if (!state.records.length) { toast(t('t_nothing'), 'warn'); return; }
  const clean = state.records.map((r) => Object.fromEntries(SCHEMA_KEYS.map((k) => [k, r[k] ?? null])));
  const name = state.fileName && /input/i.test(state.fileName)
    ? state.fileName.replace(/input/i, 'output').replace(/\.jsonl$/i, '.json')
    : 'jana_address_output.json';
  const blob = new Blob([JSON.stringify(clean, null, 2)], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
  toast(t('t_exported', { n: plural(clean.length), name }));
}

/* =============================================================================
 * Boot
 * ========================================================================== */
function bindGlobalKeys() {
  document.addEventListener('keydown', (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement && document.activeElement.tagName);
    if (e.key === '/' && !typing && !Editor.dialog.open) { e.preventDefault(); $('#search').focus(); }
  });
}

function init() {
  lang = initialLang();
  bindIngestion();
  bindQuickTest();
  bindTable();
  bindEditor();
  bindGlobalKeys();
  $('#statusPill').addEventListener('click', () => checkBackend(true));
  $('#langSwitch').addEventListener('click', (e) => {
    const b = e.target.closest('[data-lang]');
    if (b) setLang(b.dataset.lang);
  });

  applyStaticI18n();
  const restored = restoreSession();
  setEngine('checking');
  renderAll();
  if (restored) toast(t('t_restored', { n: plural(state.records.length) }));
  checkBackend(false);
  watchBackend();
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
}
