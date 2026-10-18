"""Independent benchmark: Claude writes realistic Kazakh delivery addresses AND labels them by our conventions.

    python -m eval.llm_generate --out data/claude_generated.json [--per-theme 24] [--workers 6] [--effort high]

Why: every other gold set in this repo was written by the same author as the rule grammar and the synthetic generator,
so scores on them are optimistic. Here a different writer (Claude Opus 5.5) invents the strings — in 22 themes that cover
regions, languages and the messy ways customers type — and labels them following docs/CONVENTIONS.md + the 42 convention
examples. Labels go through the same copy-not-rewrite validators as everything else; records with label errors are
dropped. Disagreements between this gold and our parser still need a human look (Claude can be wrong about a convention):
`python -m eval.llm_verify --input data/claude_generated.json --gold data/claude_generated.json --provider rules`
lists them, or simply `python -m eval.ab --gold data/claude_generated.json --errors-dir runs/cg`.

Credentials: ANTHROPIC_API_KEY, or none after `ant auth login`. ANTHROPIC_WORKSPACE_ID for user-scoped keys.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from training.validate_labels import FIELDS, validate_record  # noqa: E402

MODEL = "claude-opus-5-5"
THEMES = [
    "Алматы: микрорайоны (Аксай, Самал, Мамыр, Жетысу, Орбита, Коктем, Таугуль, Айнабулак) с номерами, дома, кв, подъезды, этажи; часто без запятых",
    "Алматы: улицы и проспекты (Абая, Толе би, Райымбека, Сейфуллина, Розыбакиева, Жандосова), районы (Бостандыкский, Ауэзовский, Медеуский…), ЖК, офисы, бизнес-центры",
    "Астана: левый берег, ЖК, улицы с казахскими названиями (Мәңгілік Ел, Тұран, Сарайшық, Қабанбай батыр, Кенесары), районы «район Есиль», «Алматы район», «Сарыарка»",
    "Шымкент: микрорайоны (Нурсат, Самал, Кайтпас, Тассай), улицы, районы (Абайский, Аль-Фарабийский, Енбекшинский, Каратауский, Туран)",
    "Караганда и Темиртау: номерные микрорайоны, проспекты (Бухар жырау, Нуркена Абдирова), жилые массивы (Майкудук, Пришахтинск, Юго-Восток)",
    "Актобе, Атырау, Уральск: номерные микрорайоны, улицы, смесь русского и казахского",
    "Актау и Жанаозен: адреса только из микрорайона/дома/квартиры («14 мкр 59 дом 12 кв», «12-5-7», «мкр 27, д 3, кв 91»)",
    "Павлодар, Экибастуз, Усть-Каменогорск, Семей: улицы с инициалами («ул. Ш. Уалиханова»), переулки, дробные дома",
    "Костанай, Петропавловск, Кокшетау, Рудный: русскоязычный север, переулки, «линия», СТ, проезды",
    "Тараз, Кызылорда, Туркестан: юг, много казахского, ауылы рядом с городами",
    "Сёла и посёлки разных областей: «… обл., … р-н, с./пос./аул …, ул. …, д. …»",
    "Полностью казахский язык: «… қаласы, … көшесі 12, 5 пәтер, 3 кіреберіс, 4 қабат», «… ықшам ауданы», «… ауылы», «… облысы»",
    "Латиница и английские слова: «Almaty, Abay ave 150, kv 5», «Astana Mangilik El 55 flat 191», «mkr Samal-2 dom 58»",
    "Слитное написание и опечатки: «66квартал 12дом 69квартира», «Назарбаева8», «подьезд», «улца», «кваритра», лишние пробелы, «Ул .Курильская»",
    "Заметки курьеру внутри адреса: телефоны, имена получателей, ИП/ТОО, коды домофона, «позвонить заранее», ориентиры («напротив магазина Береке», «ТЦ Mega», «школа №65», «Qazpost …»)",
    "Очень короткие строки без города и без типа улицы: «Кулманова 133», «жуалы 19», «Абая 10 кв 5»",
    "Полный формальный формат: «Республика Казахстан, … область, … район, г. …, ул. …, д. …, кв. …» с индексом",
    "Корпус/блок/литер, дробные и литерные дома (55/6, 12а, 33/1), формат «дом-квартира» через дефис («Абая 10-5»)",
    "Все заглавные / все строчные, неразрывные пробелы и странная пунктуация (точки с запятой, слэши, кавычки)",
    "Дубли города и двуязычные склейки («город Актобе/Ақтөбе қаласы», «Алматы Алматы …»), район вместо города («Турксибский район, …»)",
    "Небольшие города: Талгар, Каскелен, Конаев, Есик, Кульсары, Шу, Сарыагаш, Сатпаев, Жезказган, Балхаш, Риддер, Аксу, Степногорск, Лисаковск",
    "Неоднозначные случаи номера микрорайона и дома: «Нурсити 2 микрорайон 11дом», «Мкр Аксай 3а, 59», «мкр Жетысу 20, кв 9», «11 мкр, 112 дом»",
]

# --hard: adversarial themes — what breaks parsers, written the way it really happens
THEMES_HARD = [
    "Несколько опечаток в одной строке: переставленные буквы, пропущенные гласные, лишние буквы и в названиях, и в ключевых словах («улца», «квратира», «подьзд», «Алмты», «Карагнда», «Шымкнт»)",
    "Латинские буквы внутри кириллических слов и наоборот (раскладка): «Aлматы», «yл. Абая», «Aстана», «кв» латиницей, смесь в одном слове",
    "Казахская латиница 2021 и английские переводы: «Almaty qalasy, Abai daŋğyly 10», «Qaskeleñ», «Respublika avenue», «Victory street 5, apartment 12»",
    "Всё слитно или почти без пробелов и разделителей: «Алматы,Абая10,кв5», «ул.Абая,д.10,кв.5,под.2,эт.3», «АбаяДом10кв5», «мкр5д12кв34»",
    "Обратный и перемешанный порядок: детали сначала, город в конце («кв 5, 3 этаж, дом 10, ул. Абая, Алматы»), дом перед улицей («дом 10 по улице Абая»)",
    "Исправления и повторы внутри строки: «Абая 10, нет, 12», «ул. Абая (бывш. Ленина) 10», «Нур-Султан (Астана)», город написан дважды по-разному",
    "Старые и альтернативные названия городов: Целиноград, Семипалатинск, Гурьев, Уральск/Орал, Нур-Султан, Акмола, Алма-Ата, Чимкент, Кустанай, Джамбул",
    "Очень длинные строки: страна, индекс, область, район, город, улица, дом, корпус, квартира, подъезд, этаж, домофон, ориентир, получатель, телефон, время доставки — всё в одной строке",
    "Здания вместо улиц: ТРЦ, БЦ, рынок, вокзал, больница, школа, университет с офисом/кабинетом/бутиком («ТРЦ Mega Alma-Ata, бутик 214», «БЦ Нурлы Тау 1Б, офис 505»)",
    "Дачи и садовые товарищества, участки, линии, аллеи: «СТ Строитель, 5 линия, участок 23», «с/т Мичуринец ул. Садовая 4», «садоводство Энергетик, уч. 112», «дачный массив»",
    "Номера с буквами и диапазонами: «12А/1», «12/1А», «12а-14», «д. 5 стр. 2», «12 корпус 2 блок 3», «дом 7-7а», «22б/1», литеры «лит. А»",
    "Казахские порядковые и падежи: «3-ші қабат», «1-інші кіреберіс», «12-ші пәтер», «Абай даңғылындағы 10 үй», «Сәтбаев көшесінде 5», «Алматы қаласындағы»",
    "Разделители слэшами, точками с запятой, тире и переносами строк: «Актау/14/59/12», «Алматы; Абая; 10; 5», «Алматы — Абая — 10 — 5», строки с переносами",
    "Военные части, абонентские ящики, пункты выдачи и постаматы: «в/ч 12345», «а/я 15», «ПВЗ Kaspi, ул. Абая 10», «постамат в ТЦ Mega», «отделение Казпочты 050000»",
    "Диктовка и разговорный стиль: «улица абая дом десятый квартира пять», «на пересечении Абая и Байтурсынова», «за магазином Магнум», «третий дом от угла», «частный дом»",
    "Названия с инициалами, званиями и «имени»: «ул. им. Т. Рыскулова», «проспект Н. Назарбаева», «ул. Академика Сатпаева», «пр. Ш. Уалиханова», «улица Героя Советского Союза Маншук Маметовой»",
]

INSTRUCTIONS = """You create a BENCHMARK for a Kazakhstan address parser. Write realistic delivery addresses exactly as real
customers of a logistics company type them into a delivery form (Russian, Kazakh, Latin; messy, abbreviated, with typos,
glued numbers, notes for the courier), and label each one with 11 fields following the labeling conventions below EXACTLY.

Hard rules for the labels (they are checked automatically):
* COPY, never rewrite: city, district, living_area, street_name, house, apartment, entrance, floor, landmark are cut from
  raw_address exactly as written — same letters, same case, same typos. Only province (canonical name from the table) and
  street_type (lemma) are normalized. The only other exceptions are the ones the conventions name (Kazakh-spelled known city
  → Russian name; «12 корпус 2» → house «12/2»).
* null when the information is not written in the string. Never fill district, city or province from your own knowledge.
* Follow the conventions' decisions even where you would decide differently (microdistrict vs living_area, корпус → «12/2»,
  ЖК name without «ЖК», office number → apartment, «ЮКО» → «Туркестанская область»).
* Real street, microdistrict and settlement names of Kazakhstan; vary cities, numbers and styles; no two strings alike.

"""


def system_prompt() -> str:
    conv = (ROOT / "docs" / "CONVENTIONS.md").read_text(encoding="utf-8")
    ex = json.loads((ROOT / "data" / "conventions_examples.json").read_text(encoding="utf-8"))["examples"]
    shots = [{"raw_address": e["raw_address"], **e["expected"]} for e in ex]
    return (INSTRUCTIONS + "# Labeling conventions\n\n" + conv + "\n\n# Labeled examples\n"
            + json.dumps(shots, ensure_ascii=False, indent=0))


FIELD = {"anyOf": [{"type": "string"}, {"type": "null"}]}
SCHEMA = {
    "type": "object",
    "properties": {"records": {"type": "array", "items": {
        "type": "object",
        "properties": {"raw_address": {"type": "string"}, **{f: FIELD for f in FIELDS},
                       "tags": {"type": "array", "items": {"type": "string"}}},
        "required": ["raw_address", *FIELDS, "tags"],
        "additionalProperties": False}}},
    "required": ["records"],
    "additionalProperties": False,
}


def generate(theme: str, n: int, effort: str, system: str) -> list[dict]:
    import anthropic

    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    client = anthropic.Anthropic(max_retries=6, **({"default_headers": {"anthropic-workspace-id": workspace}} if workspace else {}))
    user = (f"Theme: {theme}\nWrite {n} different addresses for this theme and label them. In `tags` name what makes each "
            f"one hard (e.g. glued, kazakh, latin, typo, micro_number, landmark, phone, no_city, korpus, dash, village).")
    with client.beta.messages.stream(
        model=MODEL, max_tokens=32000, system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}, "effort": effort},
        cache_control={"type": "ephemeral"},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError(f"refusal on theme {theme[:40]}")
    text = next(b.text for b in msg.content if b.type == "text")
    recs = json.loads(text)["records"]
    for r in recs:
        r["theme"] = theme.split(":")[0]
    return recs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/claude_generated.json")
    ap.add_argument("--per-theme", type=int, default=24)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--themes", type=int, default=0, help="only the first N themes (smoke test)")
    ap.add_argument("--hard", action="store_true", help="adversarial themes (THEMES_HARD) instead of the regional ones")
    a = ap.parse_args(argv)
    system = system_prompt()
    themes = THEMES_HARD if a.hard else THEMES
    themes = themes[: a.themes] if a.themes else themes
    t0 = time.time()
    raw: list[dict] = []
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futs = {pool.submit(generate, th, a.per_theme, a.effort, system): th for th in themes}
        for f in as_completed(futs):
            th = futs[f]
            try:
                got = f.result()
                raw += got
                print(f"  {len(got):3d} from «{th[:50]}…»  ({time.time() - t0:.0f}s)", file=sys.stderr)
            except Exception as e:  # noqa: BLE001 — one failed theme must not lose the others
                print(f"  FAILED «{th[:50]}…»: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
    seen, kept, dropped = set(), [], []
    for r in raw:
        key = " ".join(r["raw_address"].split()).lower()
        if key in seen or not r["raw_address"].strip():
            continue
        seen.add(key)
        fields = {f: (r.get(f) or None) for f in FIELDS}
        errs = [i for i in validate_record(r["raw_address"], fields) if i["severity"] == "error"]
        if errs:
            dropped.append({"raw_address": r["raw_address"], "fields": fields,
                            "errors": [f"{i['code']}:{i['field']}" for i in errs]})
            continue
        kept.append({"id": f"cg_{len(kept) + 1:04d}", "raw_address": r["raw_address"], **fields,
                     "_tags": r.get("tags", []), "_theme": r.get("theme")})
    Path(a.out).write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")
    Path(a.out).with_suffix(".dropped.json").write_text(json.dumps(dropped, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(raw)} generated, {len(kept)} kept, {len(dropped)} dropped by the validators, "
          f"{len(raw) - len(kept) - len(dropped)} duplicates → {a.out} ({time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
