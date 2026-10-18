#!/usr/bin/env python3
"""hard3k — an independent, adversarial, NATIONWIDE test set for Jana Address (+ a separate international set).

Written without reading the parser, the lexicon or training/synth_grammar.py, so it does not share their blind spots.
Inputs: the labeling spec (docs/CONVENTIONS.md + decisions log), the gold conventions of the Claude-written sets, and
general knowledge of how Kazakhstani delivery addresses are typed.

Gold is exact BY CONSTRUCTION: a record is assembled piece by piece; every value piece is remembered as the gold of its
field (copy-not-rewrite) and noise pieces are not. The only gold values that are not verbatim spans are the ones the
spec normalizes: `province` (canonical table), `street_type` (lemma), a city written in Kazakh spelling (→ Russian name,
Q2) and a house with корпус/блок/строение (→ «12/2», Q7).

Coverage: every one of the 17 regions and 3 republican cities is drawn with equal probability (then a regional centre
or a smaller town of that region), so no city dominates — Astana and Almaty get 1/20 each like everybody else.

    python -m eval.hard3k_generate --n 3000 --seed 2026 --out data/hard3k            # the frozen exam
    python -m eval.hard3k_generate --n 3000 --seed 7 --out runs/hard3k_dev           # any other seed = development

writes  hard3k_s<seed>_input.json   (id, raw_address — what the jury would send)
        hard3k_s<seed>_gold.json    (full jury schema + _theme, _difficulty, _region, _tags)
        hard3k_s<seed>_manifest.json (counts per theme / region / tag, sha256 of both files)
        intl_s<seed>_gold.json      (--intl N: foreign addresses, our convention in docs/AI_MODE.md; not jury data)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path

FIELDS = ["province", "city", "district", "living_area", "street_type", "street_name",
          "house", "apartment", "entrance", "floor", "landmark"]
SPAN_FIELDS = {"city", "district", "living_area", "street_name", "house", "apartment", "entrance", "floor", "landmark"}

# =============================================================================================== reference data
# canonical (repo province.yaml) → (Russian written variants, Kazakh written form)
REGIONS = {
    "Акмолинская область": (["Акмолинская область", "Акмолинская обл.", "Акмолинская обл"], "Ақмола облысы"),
    "Актюбинская область": (["Актюбинская область", "Актюбинская обл.", "Актюбинская обл"], "Ақтөбе облысы"),
    "Алматинская область": (["Алматинская область", "Алматинская обл.", "Алматинская обл"], "Алматы облысы"),
    "Атырауская область": (["Атырауская область", "Атырауская обл."], "Атырау облысы"),
    "Восточно-Казахстанская область": (["Восточно-Казахстанская область", "ВКО", "Восточно-Казахстанская обл."], "Шығыс Қазақстан облысы"),
    "Жамбылская область": (["Жамбылская область", "Жамбылская обл.", "Жамбылская обл"], "Жамбыл облысы"),
    "Западно-Казахстанская область": (["Западно-Казахстанская область", "ЗКО"], "Батыс Қазақстан облысы"),
    "Карагандинская область": (["Карагандинская область", "Карагандинская обл.", "Карагандинская обл"], "Қарағанды облысы"),
    "Костанайская область": (["Костанайская область", "Костанайская обл."], "Қостанай облысы"),
    "Кызылординская область": (["Кызылординская область", "Кызылординская обл."], "Қызылорда облысы"),
    "Мангистауская область": (["Мангистауская область", "Мангистауская обл."], "Маңғыстау облысы"),
    "Павлодарская область": (["Павлодарская область", "Павлодарская обл."], "Павлодар облысы"),
    "Северо-Казахстанская область": (["Северо-Казахстанская область", "СКО"], "Солтүстік Қазақстан облысы"),
    "Туркестанская область": (["Туркестанская область", "Туркестанская обл."], "Түркістан облысы"),
    "область Абай": (["область Абай", "Абайская область"], "Абай облысы"),            # Q4 open
    "область Жетісу": (["область Жетісу", "Жетысуская область"], "Жетісу облысы"),    # Q4 open
    "область Ұлытау": (["область Ұлытау", "Улытауская область"], "Ұлытау облысы"),    # Q4 open
}
NEW_REGIONS = {"область Абай", "область Жетісу", "область Ұлытау"}
UNITS = list(REGIONS) + ["Астана", "Алматы", "Шымкент"]          # 20 top-level units, drawn uniformly

S = lambda *names: [(n, "улица") for n in names]          # noqa: E731
P = lambda *names: [(n, "проспект") for n in names]       # noqa: E731

# Major cities: districts [(ru, kk)], streets [(name, lemma)], named micros, numbered micros?, ЖК, massivs.
CITIES = {
    "Алматы": dict(rep=True, region="Алматы", kk="Алматы", lat=["Almaty"], old=["Алма-Ата"],
                   districts=[("Алатауский", "Алатау"), ("Алмалинский", "Алмалы"), ("Ауэзовский", "Әуезов"), ("Бостандыкский", "Бостандық"),
                              ("Жетысуский", "Жетісу"), ("Медеуский", "Медеу"), ("Наурызбайский", "Наурызбай"), ("Турксибский", "Түрксіб")],
                   streets=S("Толе би", "Жандосова", "Тимирязева", "Сатпаева", "Розыбакиева", "Наурызбай батыра", "Кабанбай батыра", "Богенбай батыра",
                             "Курмангазы", "Шевченко", "Желтоксан", "Байтурсынова", "Момышулы", "Саина", "Утеген батыра", "Брусиловского",
                             "Жарокова", "Мынбаева", "Ауэзова", "Манаса", "Байзакова", "Муканова", "Курильская", "Кулманова", "Жуалы",
                             "Шагабутдинова", "Айтеке би", "Казыбек би", "Карасай батыра", "Жамбыла", "Макатаева", "Гоголя", "Пушкина")
                           + P("Абая", "Райымбека", "Гагарина", "Назарбаева", "Достык", "Аль-Фараби", "Суюнбая", "Рыскулова", "Сейфуллина")
                           + [("Рыбинский", "переулок"), ("Тополиный", "переулок")],
                   micros=["Аксай-1", "Аксай-2", "Аксай-3", "Аксай-3а", "Аксай-4", "Аксай-5", "Самал-1", "Самал-2", "Самал-3", "Орбита-1", "Орбита-2",
                           "Орбита-3", "Жетысу-1", "Жетысу-2", "Жетысу-3", "Мамыр-1", "Мамыр-4", "Мамыр-7", "Таугуль", "Таугуль-1", "Коктем-1", "Коктем-2",
                           "Алмагуль", "Калкаман-2", "Шанырак-2", "Алгабас", "Нуркент", "Айнабулак-3", "Тастак-1", "Курылысшы", "Сайран", "Акбулак",
                           "Жулдыз-1", "Кулагер", "Зердели", "Аккент", "Дорожник", "Баянаул", "Казахфильм"],
                   numbered=False,
                   zhk=["Комфорт Таун", "Asyl Arman", "Шахристан", "Хан Тенгри", "Алтын Булак", "Жайлау", "Sensata City", "Grand Park", "Нурлы Жол", "Ақ Ерке"],
                   massivs=["Шугыла", "Ремизовка", "Каменское плато"]),
    "Астана": dict(rep=True, region="Астана", kk="Астана", lat=["Astana"], old=["Нур-Султан"],
                   districts=[("Алматы", "Алматы"), ("Байконур", "Байқоңыр"), ("Есиль", "Есіл"), ("Нура", "Нұра"), ("Сарыарка", "Сарыарқа")],
                   streets=P("Кабанбай батыра", "Мангилик Ел", "Туран", "Сарыарка", "Республики", "Богенбай батыра", "Тауелсиздик", "Улы Дала", "Абая")
                           + S("Сыганак", "Достык", "Кенесары", "Бейбитшилик", "Сейфуллина", "Иманова", "Пушкина", "Ауэзова", "Жубанова", "Сауран",
                               "Ахмета Байтурсынулы", "Шамши Калдаякова", "Акмешит", "Кошкарбаева", "Керей Жанибек хандар", "Хусейн бен Талал", "Е-10"),
                   micros=["Коктал-1", "Жагалау-2", "Самал", "Целинный", "Юго-Восток", "Акмол"], numbered=False,
                   zhk=["Хайвил", "Highvill", "Северное сияние", "Нурсая", "Изумрудный квартал", "Арнау", "Триумф Астаны", "Green Quarter", "Жагалау", "Expo Boulevard"],
                   massivs=["Ильинка", "Пригородный", "Юго-Восток"]),
    "Шымкент": dict(rep=True, region="Шымкент", kk="Шымкент", lat=["Shymkent"], old=["Чимкент"],
                    districts=[("Абайский", "Абай"), ("Аль-Фарабийский", "Әл-Фараби"), ("Енбекшинский", "Еңбекші"), ("Каратауский", "Қаратау"), ("Туран", "Тұран")],
                    streets=P("Тауке хана", "Республики", "Кунаева") + S("Байтурсынова", "Жибек жолы", "Момышулы", "Казыбек би", "Толе би", "Желтоксан",
                                                                       "Байдибек би", "Рыскулова", "Сайрамская", "Туркестанская", "Ергобек", "Аргынбекова"),
                    micros=["Нурсат", "Нурсат-2", "Самал-1", "Самал-3", "Кайтпас-1", "Кайтпас-2", "Туран", "Казыгурт", "Север", "Восток", "Наурыз",
                            "Асар", "Мирас", "Бозарык", "Шапагат", "Тассай"], numbered=False,
                    zhk=["Томирис", "Жулдыз", "Шымкент Сити", "Асыл Тас"], massivs=["Акжайык", "Тассай", "Бозарык"]),
    "Караганда": dict(rep=False, region="Карагандинская область", kk="Қарағанды", lat=["Karaganda", "Qaragandy"],
                      districts=[("Казыбек би", "Қазыбек би"), ("Октябрьский", "Октябрь")],
                      streets=P("Бухар жырау", "Строителей", "Нуркена Абдирова") + S("Ерубаева", "Гоголя", "Мичурина", "Терешковой", "Ермекова",
                                                                                   "Таттимбета", "Муканова", "Лободы", "Алиханова", "Комиссарова", "Сатпаева", "Пичугина"),
                      micros=["Степной-2", "Степной-4", "Голубые пруды", "Кунгей", "Восток-5", "Мамраева"], numbered=True,
                      zhk=["Шахтёр", "Жас Канат", "Таугуль"], massivs=["Пришахтинск", "Майкудук", "Михайловка", "Федоровка", "Сортировка"]),
    "Актобе": dict(rep=False, region="Актюбинская область", kk="Ақтөбе", lat=["Aktobe", "Aqtobe"],
                   districts=[("Алматы", "Алматы"), ("Астана", "Астана")],
                   streets=P("Абилкайыр хана", "Санкибай батыра", "Алии Молдагуловой") + S("Есет батыра", "Маресьева", "Шернияза", "Алтынсарина",
                                                                                       "Бр. Жубановых", "Некрасова", "Тургенева", "Айтеке би", "Казангапа"),
                   micros=["Батыс-2", "Жанаконыс", "Авиагородок", "Кызылжар"], numbered=True,
                   zhk=["Лесная поляна", "Ак Шагала"], massivs=["Курмыш", "Заречный"]),
    "Тараз": dict(rep=False, region="Жамбылская область", kk="Тараз", lat=["Taraz"], districts=[],
                  streets=P("Жамбыла") + S("Толе би", "Желтоксан", "Абая", "Сулейменова", "Казыбек би", "Айтеке би", "Байзак батыра", "Рысбек батыра"),
                  micros=["Самал", "Аса", "Жайлау", "Мынбулак", "Талас", "Салтанат", "Алатау"], numbered=True, zhk=["Арай"], massivs=["Карасу"]),
    "Актау": dict(rep=False, region="Мангистауская область", kk="Ақтау", lat=["Aktau", "Aqtau"], districts=[],
                  streets=S("Есет батыра", "Шевченко") + P("Абая"), micros=["Шыгыс-2", "Самал"], numbered=True, triple=True,
                  zhk=["Хазар", "Каспий"], massivs=["Приморский"]),
    "Атырау": dict(rep=False, region="Атырауская область", kk="Атырау", lat=["Atyrau"], districts=[],
                   streets=S("Сатпаева", "Махамбета", "Курмангазы", "Абая", "Гагарина") + P("Азаттык", "Студенческий"),
                   micros=["Авангард-1", "Авангард-2", "Авангард-3", "Авангард-4", "Нурсая", "Жилгородок", "Привокзальный", "Балыкшы", "Береке"],
                   numbered=False, zhk=["Атырау Сити"], massivs=["Бирлик"]),
    "Павлодар": dict(rep=False, region="Павлодарская область", kk="Павлодар", lat=["Pavlodar"], districts=[],
                     streets=S("Торайгырова", "Естая", "Академика Сатпаева", "Ломова", "Кутузова", "1 Мая", "Кривенко", "Лермонтова", "Камзина") + P("Назарбаева"),
                     micros=["Химгородок", "Сарыарка"], numbered=False, zhk=["Достык"], massivs=["Зеленстрой"]),
    "Усть-Каменогорск": dict(rep=False, region="Восточно-Казахстанская область", kk="Өскемен", lat=["Ust-Kamenogorsk", "Oskemen"], districts=[],
                             streets=P("Казахстан", "Абая") + S("Протозанова", "Тохтарова", "Ушанова", "Кабанбай батыра", "Михаэлиса", "Новаторов"),
                             micros=["Ахмирово", "Защита"], numbered=False, zhk=["Иртыш"], massivs=["Ахмирово"]),
    "Семей": dict(rep=False, region="область Абай", kk="Семей", lat=["Semey"], old=["Семипалатинск"], districts=[],
                  streets=P("Шакарима") + S("Абая", "Ауэзова", "Найманбаева", "Интернациональная", "Глинки", "Валиханова"),
                  micros=["Юго-Восток", "Восточный"], numbered=False, zhk=["Шугыла"], massivs=["Жана Семей"]),
    "Костанай": dict(rep=False, region="Костанайская область", kk="Қостанай", lat=["Kostanay", "Qostanai"], districts=[],
                     streets=S("Баймагамбетова", "Тарана", "Байтурсынова", "Майлина", "Уральская", "Наримановская", "Каирбекова", "Гоголя") + P("Аль-Фараби", "Абая"),
                     micros=["КЖБИ", "Аэропорт", "Юбилейный"], numbered=True, zhk=["Береке"], massivs=["Кустанайский"]),
    "Кокшетау": dict(rep=False, region="Акмолинская область", kk="Көкшетау", lat=["Kokshetau"], districts=[],
                     streets=S("Ауэзова", "Абая", "Абылай хана", "Акана Серэ", "Горького", "Валиханова") + P("Назарбаева"),
                     micros=["Центральный", "Васильковский", "Боровской", "Сарыарка"], numbered=False, zhk=["Кокше"], massivs=["Станционный"]),
    "Петропавловск": dict(rep=False, region="Северо-Казахстанская область", kk="Петропавл", lat=["Petropavlovsk"], districts=[],
                          streets=S("Конституции Казахстана", "Жамбыла", "Интернациональная", "Казахстанской правды", "Пушкина", "Маяковского", "Букетова"),
                          micros=["Береке", "Рабочий"], numbered=False, zhk=["Солнечный"], massivs=["Подгора"]),
    "Уральск": dict(rep=False, region="Западно-Казахстанская область", kk="Орал", lat=["Uralsk", "Oral"], districts=[],
                    streets=P("Евразия", "Достык-Дружба") + S("Сарайшык", "Ихсанова", "Победы", "Жангир хана", "Темир Масина"),
                    micros=["Строитель", "Жулдыз", "Кунаева", "Самал"], numbered=False, zhk=["Атамекен"], massivs=["Деркул"]),
    "Кызылорда": dict(rep=False, region="Кызылординская область", kk="Қызылорда", lat=["Kyzylorda", "Qyzylorda"], districts=[],
                      streets=P("Абая") + S("Желтоксан", "Токмаганбетова", "Ауэзова", "Жибек жолы", "Айтеке би", "Байтурсынова"),
                      micros=["Шугыла", "Акмешит", "Мерей", "Саулет"], numbered=False, zhk=["Нұрлы"], massivs=["Тасбогет"]),
    "Туркестан": dict(rep=False, region="Туркестанская область", kk="Түркістан", lat=["Turkestan", "Turkistan"], districts=[],
                      streets=S("Тауке хана", "Есим хана", "Байдибек би", "Кожанова"), micros=["Жана кала", "Отырар", "Ынтымак"], numbered=False,
                      zhk=["Керуен"], massivs=["Бекзат"]),
    "Талдыкорган": dict(rep=False, region="область Жетісу", kk="Талдықорған", lat=["Taldykorgan"], districts=[],
                        streets=S("Тауелсиздик", "Жансугурова", "Абая", "Кабанбай батыра", "Шевченко", "Тынышпаева"),
                        micros=["Жетысу", "Самал", "Мушелтой", "Каратал", "Коктем"], numbered=False, zhk=["Жетісу"], massivs=["Отенай"]),
    "Темиртау": dict(rep=False, region="Карагандинская область", kk="Теміртау", lat=["Temirtau"], districts=[],
                     streets=P("Республики", "Металлургов") + S("Мира") + [("Независимости", "бульвар")], micros=[], numbered=True, zhk=[],
                     massivs=["Химпоселок"]),
    "Экибастуз": dict(rep=False, region="Павлодарская область", kk="Екібастұз", lat=["Ekibastuz"], districts=[],
                      streets=S("Энергетиков", "Машхур Жусупа", "Ауэзова", "Сатпаева"), micros=["Голубой"], numbered=True, zhk=[], massivs=[]),
    "Жезказган": dict(rep=False, region="область Ұлытау", kk="Жезқазған", lat=["Zhezkazgan"], districts=[],
                      streets=S("Алашахана", "Сейфуллина", "Желтоксан") + P("Мира"), micros=["Кенгир"], numbered=True, zhk=[], massivs=[]),
    "Конаев": dict(rep=False, region="Алматинская область", kk="Қонаев", lat=["Konaev"], districts=[],
                   streets=S("Кунаева", "Индустриальная", "Абая", "Капшагайская"), micros=[], numbered=True, zhk=[], massivs=[]),
}
GENERIC_STREETS = S("Абая", "Байтурсынова", "Жамбыла", "Сейфуллина", "Желтоксан", "Тауелсиздик", "Конаева", "Мира", "Строителей",
                    "Гагарина", "Победы", "Ауэзова", "Валиханова", "Момышулы", "Достык", "Бейбитшилик", "Абылай хана", "Кабанбай батыра",
                    "Есет батыра", "Алтынсарина", "Майлина", "Сатпаева", "Комсомольская", "Пушкина", "Чехова", "Горького", "Шевченко",
                    "Молодежная", "Школьная", "Ыбырая Алтынсарина", "Торайгырова", "Казыбек би") + P("Независимости", "Абая", "Республики")
# Smaller towns (Russian spelling only: their Kazakh→Russian mapping is not something a labeler can be sure of — Q14).
TOWNS = {
    "Курчатов": ("область Абай", False), "Аягоз": ("область Абай", False), "Степногорск": ("Акмолинская область", True),
    "Щучинск": ("Акмолинская область", False), "Атбасар": ("Акмолинская область", False), "Кандыагаш": ("Актюбинская область", False),
    "Хромтау": ("Актюбинская область", True), "Шалкар": ("Актюбинская область", False), "Есик": ("Алматинская область", False),
    "Талгар": ("Алматинская область", False), "Каскелен": ("Алматинская область", False), "Кульсары": ("Атырауская область", True),
    "Риддер": ("Восточно-Казахстанская область", False), "Алтай": ("Восточно-Казахстанская область", False),
    "Шу": ("Жамбылская область", False), "Каратау": ("Жамбылская область", True), "Жанатас": ("Жамбылская область", True),
    "Текели": ("область Жетісу", False), "Жаркент": ("область Жетісу", False), "Ушарал": ("область Жетісу", False),
    "Аксай": ("Западно-Казахстанская область", True), "Балхаш": ("Карагандинская область", True), "Шахтинск": ("Карагандинская область", True),
    "Сарань": ("Карагандинская область", False), "Рудный": ("Костанайская область", True), "Лисаковск": ("Костанайская область", True),
    "Аркалык": ("Костанайская область", True), "Житикара": ("Костанайская область", True), "Аральск": ("Кызылординская область", False),
    "Казалинск": ("Кызылординская область", False), "Жанаозен": ("Мангистауская область", True), "Форт-Шевченко": ("Мангистауская область", False),
    "Аксу": ("Павлодарская область", True), "Булаево": ("Северо-Казахстанская область", False), "Мамлютка": ("Северо-Казахстанская область", False),
    "Кентау": ("Туркестанская область", True), "Арыс": ("Туркестанская область", False), "Сарыагаш": ("Туркестанская область", False),
    "Жетысай": ("Туркестанская область", False), "Ленгер": ("Туркестанская область", False), "Сатпаев": ("область Ұлытау", True),
    "Каражал": ("область Ұлытау", False),
}
for _t, (_reg, _num) in TOWNS.items():
    _r = random.Random(_t)
    CITIES[_t] = dict(rep=False, region=_reg, kk=None, lat=[], districts=[], streets=_r.sample(GENERIC_STREETS, 9),
                      micros=[], numbered=_num, triple=_t == "Жанаозен", zhk=[], massivs=[], town=True)

BY_UNIT: dict[str, list[str]] = {u: [] for u in UNITS}
for _c, _d in CITIES.items():
    BY_UNIT[_d["region"]].append(_c)
assert all(BY_UNIT[u] for u in UNITS), [u for u in UNITS if not BY_UNIT[u]]

# villages used with an oblast district (Russian spelling only — Kazakh-spelled villages are Q14, still open)
VILLAGES = [
    ("Алматинская область", "Карасайский", "Қарасай", "с.", ["Иргели", "Шамалган", "Кыргауылды", "Коксай", "Райымбек"]),
    ("Алматинская область", "Илийский", "Іле", "пос.", ["Боралдай", "Отеген батыр", "Байсерке", "Жетыген", "Покровка"]),
    ("Алматинская область", "Талгарский", "Талғар", "с.", ["Туздыбастау", "Бесагаш", "Кайнар", "Панфилово"]),
    ("Алматинская область", "Енбекшиказахский", "Еңбекшіқазақ", "с.", ["Шелек", "Байтерек"]),
    ("Алматинская область", "Жамбылский", "Жамбыл", "с.", ["Узынагаш", "Каргалы", "Мынбаев"]),
    ("Туркестанская область", "Сайрамский", "Сайрам", "с.", ["Аксу", "Карабулак", "Манкент", "Кутарыс"]),
    ("Туркестанская область", "Тюлькубасский", "Түлкібас", "с.", ["Тюлькубас", "Шакпак баба"]),
    ("Акмолинская область", "Целиноградский", "Целиноград", "с.", ["Косшы", "Талапкер", "Акмол", "Кабанбай батыр"]),
    ("Карагандинская область", "Бухар-Жырауский", "Бұқар жырау", "пос.", ["Ботакара", "Доскей", "Габидена Мустафина"]),
    ("Карагандинская область", "Абайский", "Абай", "пос.", ["Топар", "Южный", "Дубовка"]),
    ("Жамбылская область", "Байзакский", "Байзақ", "с.", ["Сарыкемер", "Бурылбайтал"]),
    ("Костанайская область", "Костанайский", "Қостанай", "пос.", ["Затобольск", "Мичурино"]),
    ("Актюбинская область", "Каргалинский", "Қарғалы", "с.", ["Бадамша", "Степное"]),
    ("Атырауская область", "Макатский", "Мақат", "пос.", ["Доссор", "Байшонас"]),
    ("Восточно-Казахстанская область", "Глубоковский", "Глубокое", "пос.", ["Глубокое", "Белоусовка", "Верхнеберезовский"]),
    ("Западно-Казахстанская область", "Зеленовский", "Бәйтерек", "с.", ["Переметное", "Мичурино"]),
    ("Кызылординская область", "Сырдарьинский", "Сырдария", "пос.", ["Теренозек"]),
    ("Мангистауская область", "Мунайлинский", "Мұнайлы", "с.", ["Мангистау", "Баскудук", "Атамекен"]),
    ("Павлодарская область", "Павлодарский", "Павлодар", "с.", ["Мичурино", "Павлодарское"]),
    ("Северо-Казахстанская область", "Кызылжарский", "Қызылжар", "с.", ["Бесколь", "Пеньки"]),
    ("область Абай", "Бескарагайский", "Бесқарағай", "с.", ["Бескарагай", "Канонерка"]),
    ("область Жетісу", "Карасайский", "Қарасай", "с.", []),                      # placeholder removed below
    ("область Жетісу", "Ескельдинский", "Ескелді", "пос.", ["Карабулак", "Ескельды би"]),
    ("область Ұлытау", "Улытауский", "Ұлытау", "с.", ["Улытау", "Карсакпай"]),
]
VILLAGES = [v for v in VILLAGES if v[4]]
VILLAGE_KW = {"с.": ["с.", "село", "с ", "С."], "пос.": ["пос.", "п.", "поселок", "посёлок"]}
VILLAGE_STREETS = ["Абая", "Ленина", "Гагарина", "Мира", "Школьная", "Центральная", "Садовая", "Байтерек", "Жибек жолы", "Наурыз",
                   "Желтоксан", "Достык", "Молодежная"]

LANDMARK_OBJECTS = ["магазин {s}", "маг. {s}", "маг.{s}", "ТЦ {s}", "ТРЦ {s}", "ТД {s}", "БЦ {s}", "аптека {s}", "кафе {s}", "рынок {s}",
                    "школа №{n}", "СШ №{n}", "остановка {s}", "мечеть {s}", "Qazpost {s}", "Казпочта", "магазин «{s}»", "супермаркет {s}"]
SHOPS = ["Береке", "береке", "Магнум", "Small", "Анвар", "Дина", "Жанна", "Арзан", "Галмарт", "Мега", "Достык Плаза", "Керуен", "Хан Шатыр",
         "Евразия", "Нур", "Европа", "Биосфера", "Солнечный", "Айгерим", "Арай", "Сая", "Табыс", "Атамекен", "Ласточка"]
MARKERS = ["ориентир ", "ориентир: ", "напротив ", "рядом ", "возле ", "около ", "Ориентир - "]

PHONES = ["тел. 8 701 123 45 67", "тел: +7 (777) 123-45-67", "87011234567", "тел 8(727)2501010", "+77015554433", "моб. 8-705-321-65-87"]
NOTES = ["позвонить за час", "звонить заранее", "оставить у двери", "вход со двора", "до 18:00", "с 9 до 18", "после 19:00", "получатель Айгерим",
         "получатель: Ерлан", "не работает лифт", "домофон 45", "код 1234#", "домофон не работает", "курьеру позвонить", "доставка после обеда",
         "собака во дворе", "2 звонка", "калитка синяя"]
POSTCODES = ["050000", "010000", "160011", "100012", "130000", "040800", "090000", "110000", "140000", "150000", "030000", "060000", "080000"]
COUNTRY = ["Казахстан", "Республика Казахстан", "РК", "Kazakhstan", "KZ"]

TYPE_RU = {
    "улица": ["ул.", "ул. ", "ул", "улица", "Ул.", "УЛ.", "ул.", "ул."], "проспект": ["пр.", "пр-т", "проспект", "просп.", "пр-кт", "пр."],
    "переулок": ["пер.", "переулок", "пер"], "микрорайон": ["мкр", "мкр.", "микрорайон", "м-н", "мкрн", "Мкр", "МКР", "мкр-н", "мкр"],
    "квартал": ["квартал", "кв-л"], "бульвар": ["б-р", "бульвар"],
}
TYPE_RU_TYPO = {"улица": ["улца", "улитца"], "проспект": ["проспкт", "праспект"], "микрорайон": ["микрорайн", "микраён", "мкрн."]}
TYPE_KK_AFTER = {"улица": ["көшесі", "көш."], "проспект": ["даңғылы"], "микрорайон": ["ықшам ауданы", "шағын ауданы"]}
TYPE_LAT_BEFORE = {"улица": ["ul.", "ul", "ulitsa"], "проспект": ["pr.", "prospekt"], "микрорайон": ["mkr", "mkr."]}
TYPE_LAT_AFTER = {"улица": ["street", "st."], "проспект": ["avenue", "ave"], "микрорайон": ["microdistrict"]}
TYPE_KZLAT_AFTER = {"улица": ["köşesi"], "проспект": ["daŋğyly"], "микрорайон": ["şağyn audany"]}

APT = {"kw": ["кв {x}", "кв. {x}", "кв.{x}", "квартира {x}", "кв{x}", "Кв. {x}"], "num": ["{x} кв", "{x}кв", "{x} квартира", "{x}квартира", "{x} кв."],
       "kk_kw": ["пәтер {x}"], "kk_num": ["{x} пәтер", "{x}-пәтер"], "lat": ["kv {x}", "kv.{x}", "kv. {x}", "flat {x}", "apt {x}", "apartment {x}"],
       "office": ["оф. {x}", "офис {x}", "каб. {x}", "бутик {x}", "павильон {x}", "оф.{x}"]}
ENT = {"kw": ["подъезд {x}", "подьезд {x}", "под. {x}", "п-д {x}", "подъезд №{x}"], "num": ["{x} подъезд", "{x}подъезд", "{x}подьезд", "{x} подьезд", "{x}-й подъезд", "{x} под."],
       "kk_kw": ["кіреберіс {x}"], "kk_num": ["{x} кіреберіс", "{x}-кіреберіс"], "lat": ["podezd {x}", "pod. {x}", "entrance {x}"]}
FLR = {"kw": ["этаж {x}", "эт. {x}", "эт.{x}"], "num": ["{x} этаж", "{x}этаж", "{x}-й этаж", "{x} эт.", "{x}эт"],
       "kk_kw": ["қабат {x}"], "kk_num": ["{x} қабат", "{x}{ord} қабат"], "lat": ["etazh {x}", "floor {x}", "{x} etazh", "{x} floor"]}
HOUSE = {"kw": ["дом {x}", "д. {x}", "д.{x}", "д {x}", "дом{x}", "Д. {x}", "дом № {x}"], "num": ["{x} дом", "{x}дом"],
         "kk_kw": ["үй {x}"], "kk_num": ["{x} үй", "{x}-үй"], "lat": ["dom {x}", "d. {x}", "house {x}"]}
BUILDING = [("корпус", " {k}"), ("корп.", " {k}"), ("корп.", "{k}"), ("блок", " {k}"), ("стр.", " {k}")]
WRONG_LAYOUT = {"кв": "rd", "подъезд": "gjl]tpl", "этаж": "'nf;"}


def kk_ordinal(n: int) -> str:
    """Kazakh ordinal suffix written after digits: 3-ші, 6-шы, 10-шы, 50-ші."""
    last = n % 10
    if last in (6, 9):
        return "-шы"
    if last != 0:
        return "-ші"
    if n % 100 == 0:
        return "-ші"
    return "-ші" if (n // 10) % 10 in (5, 7, 8) else "-шы"


def gold_clean(s: str | None) -> str | None:
    if s is None:
        return None
    s = "".join(ch for ch in s if ch not in ("​", "﻿", "⁠", "­", "‎", "‏"))
    s = re.sub(r"\s+", " ", s.replace(" ", " ")).strip()
    return s or None


# =============================================================================================== record builder
class Rec:
    def __init__(self, rnd: random.Random, theme: str):
        self.rnd = rnd
        self.theme = theme
        self.parts: list[list] = []          # [kind, text, field]
        self.g: dict = {f: None for f in FIELDS}
        self.canon: set[str] = set()         # gold fields that are NOT verbatim spans
        self.tags: set[str] = set()
        self.region: str | None = None       # reporting only

    def n(self, s: str):
        if s:
            self.parts.append(["n", s, None])

    def v(self, field: str, s: str, gold: str | None = None):
        self.parts.append(["v", s, field])
        if gold is None:
            self.g[field] = gold_clean(s)
            self.canon.discard(field)
        else:
            self.g[field] = gold
            self.canon.add(field)

    def setc(self, field: str, value: str | None):
        self.g[field] = value
        self.canon.add(field)

    def sep(self, weights=(0.72, 0.18, 0.10)):
        self.n(self.rnd.choices([", ", " ", ","], weights=weights)[0])

    def raw(self) -> str:
        return "".join(p[1] for p in self.parts)


def pick(rnd, xs):
    return rnd.choice(xs)


def pick_city(r: Rec, need=None) -> str:
    """Region first (uniform over the 20 units), then a city of that region: nationwide coverage by design."""
    rnd = r.rnd
    for _ in range(200):
        unit = pick(rnd, UNITS)
        options = [c for c in BY_UNIT[unit] if need is None or need(CITIES[c])]
        if not options:
            continue
        majors = [c for c in options if not CITIES[c].get("town")]
        towns = [c for c in options if CITIES[c].get("town")]
        city = pick(rnd, towns) if towns and (not majors or rnd.random() < 0.4) else pick(rnd, majors)
        r.region = CITIES[city]["region"]
        return city
    raise RuntimeError("no city satisfies the constraint")


def tpl(r: Rec, template: str, field: str, value: str, **fmt):
    template = template.replace("{ord}", fmt.get("ord", ""))
    a, b = template.split("{x}")
    r.n(a)
    r.v(field, value)
    r.n(b)


def gen_house(rnd, plain: bool = False) -> str:
    n = rnd.choices([rnd.randint(1, 30), rnd.randint(31, 120), rnd.randint(121, 350)], weights=[0.45, 0.4, 0.15])[0]
    if plain:
        return str(n)
    roll = rnd.random()
    if roll < 0.13:
        return f"{n}{rnd.choice(['а', 'а', 'б', 'в', 'г', 'А', 'Б'])}"
    if roll < 0.27:
        return f"{n}/{rnd.randint(1, 12)}"
    if roll < 0.29:
        return f"{n}/{rnd.randint(1, 9)}{rnd.choice(['а', 'б'])}"
    return str(n)


def gen_apt(rnd) -> str:
    return str(rnd.choices([rnd.randint(1, 60), rnd.randint(61, 250), rnd.randint(251, 520)], weights=[0.45, 0.45, 0.10])[0])


# =============================================================================================== building blocks
def city_written(r: Rec, city: str, form: str):
    """form: ru | g | gorod | kk | lat | old."""
    c = CITIES[city]
    rnd = r.rnd
    r.region = c["region"]
    if c["rep"]:
        r.setc("province", city)
    if form == "g":
        r.n(pick(rnd, ["г. ", "г.", "г ", "Г. "])); r.v("city", city)
    elif form == "gorod":
        r.n(pick(rnd, ["город ", "Город "])); r.v("city", city)
    elif form == "kk" and c.get("kk") and c["kk"] != city:
        r.v("city", c["kk"], gold=city); r.tags.add("city_kk_to_ru")
        if rnd.random() < 0.5:
            r.n(pick(rnd, [" қаласы", " қ.", " қ"]))
    elif form == "kk" and c.get("kk"):
        r.v("city", c["kk"]); r.n(pick(rnd, [" қаласы", " қ."]))
    elif form == "lat" and c.get("lat"):
        r.v("city", pick(rnd, c["lat"])); r.tags.add("latin_city")
        if rnd.random() < 0.2:
            r.n(" qalasy")
    elif form == "old" and c.get("old"):
        r.v("city", pick(rnd, c["old"])); r.tags.add("old_city_name")
    else:
        r.v("city", city)


def db_prefix(r: Rec, city: str):
    """How Jana Post's DB glues region / city before the client's free text (brief, slides 6–7)."""
    c = CITIES[city]
    rnd = r.rnd
    r.region = c["region"]
    kk = c.get("kk") or city
    if c["rep"]:
        r.n("город "); r.v("city", city); r.n(f"/{kk} қаласы")
        r.setc("province", city)
        return
    if rnd.random() < 0.5:
        ru_forms, kk_reg = REGIONS[c["region"]]
        r.v("province", ru_forms[0], gold=c["region"]); r.n(f"/{kk_reg}, ")
        if c["region"] in NEW_REGIONS:
            r.tags.update({"conv:unsettled", "new_region"})
    r.n("город "); r.v("city", city); r.n(f"/{kk} қаласы")


def city_written_dup(r: Rec, city: str):
    """City repeated by the client after the DB prefix; gold stays the (identical) city."""
    rnd = r.rnd
    form = rnd.choice(["ru", "g", "gorod", "upper"])
    word = city.upper() if form == "upper" else city
    pre = {"ru": "", "g": pick(rnd, ["г. ", "г."]), "gorod": "город ", "upper": ""}[form]
    r.n(pre + word)
    r.tags.add("city_duplicated")


def district_written(r: Rec, city: str, style: str):
    ds = CITIES[city]["districts"]
    if not ds:
        return False
    ru, kk = pick(r.rnd, ds)
    if style == "kk":
        r.v("district", kk); r.n(pick(r.rnd, [" ауданы", " ауд."]))
    elif style == "pre":
        r.n(pick(r.rnd, ["район ", "р-н ", "Район "])); r.v("district", ru)
    else:
        r.v("district", ru); r.n(pick(r.rnd, [" район", " р-н", " р-н.", " р/н", " район"]))
    if city == "Астана" and ru == "Алматы":
        r.tags.add("district_named_like_city")
    return True


def street_locator(r: Rec, city: str, lang: str = "ru", typo: bool = False, allow_no_type: bool = True) -> str:
    rnd = r.rnd
    name, lemma = pick(rnd, CITIES[city]["streets"])
    if lang == "lat":
        name = translit(name)
    if rnd.random() >= (0.78 if allow_no_type else 1.0):
        r.v("street_name", name); r.tags.add("street_no_type")
        return "street"
    r.setc("street_type", lemma)
    if lang == "kk" and lemma in TYPE_KK_AFTER:
        r.v("street_name", name); r.n(" " + pick(rnd, TYPE_KK_AFTER[lemma]))
    elif lang == "kzlat" and lemma in TYPE_KZLAT_AFTER:
        r.v("street_name", kz_latin(name)); r.n(" " + pick(rnd, TYPE_KZLAT_AFTER[lemma]))
    elif lang == "lat":
        if lemma in TYPE_LAT_AFTER and rnd.random() < 0.5:
            r.v("street_name", name); r.n(" " + pick(rnd, TYPE_LAT_AFTER[lemma]))
        else:
            kw = pick(rnd, TYPE_LAT_BEFORE.get(lemma, ["ul."]))
            r.n(kw + ("" if kw.endswith(".") and rnd.random() < 0.3 else " ")); r.v("street_name", name)
    else:
        if typo and lemma in TYPE_RU_TYPO and rnd.random() < 0.6:
            kw = typo_word(rnd, lemma) if rnd.random() < 0.6 else pick(rnd, TYPE_RU_TYPO[lemma])
            r.tags.add("keyword_typo")
        else:
            kw = pick(rnd, TYPE_RU.get(lemma, ["ул."]))
        if rnd.random() < 0.12 and lemma in ("улица", "проспект", "переулок"):
            r.v("street_name", name); r.n(" " + lemma); r.tags.add("type_after_name")
        else:
            glue = kw.endswith(".") and rnd.random() < 0.35
            r.n(kw + ("" if glue else " ")); r.v("street_name", name)
    return "street"


def micro_locator(r: Rec, city: str, lang: str = "ru") -> tuple[str, bool]:
    """Microdistrict as the locator (no separate street). Returns (kind, name_has_number)."""
    rnd = r.rnd
    c = CITIES[city]
    r.setc("street_type", "микрорайон")
    if c.get("numbered") and (not c["micros"] or rnd.random() < 0.6):
        n = str(rnd.randint(1, 35))
        form = rnd.choice(["{n} мкр", "мкр {n}", "{n}мкр", "мкр.{n}", "микрорайон {n}", "{n} микрорайон", "{n} мкрн"]
                          if lang != "kk" else ["{n}{o} ықшам аудан", "{n} ықшам ауданы", "{n}{o} шағын аудан"])
        a, b = form.replace("{o}", kk_ordinal(int(n))).split("{n}")
        r.n(a); r.v("street_name", n); r.n(b)
        r.tags.add("numbered_micro")
        return "micro_num", True
    name = pick(rnd, c["micros"]) if c["micros"] else "Самал"
    if lang == "lat":
        name = translit(name)
    has_num = bool(re.search(r"\d", name))
    if rnd.random() < 0.3 and has_num:   # «Аксай-3а» ↔ «Аксай 3а»
        name = name.replace("-", " ")
    if lang == "kk":
        r.v("street_name", name); r.n(" " + pick(rnd, TYPE_KK_AFTER["микрорайон"]))
    elif lang == "lat":
        r.n(pick(rnd, TYPE_LAT_BEFORE["микрорайон"]) + " "); r.v("street_name", name)
    elif rnd.random() < 0.15:
        r.v("street_name", name); r.n(" " + pick(rnd, ["мкр", "микрорайон", "мкр."])); r.tags.add("type_after_name")
    else:
        r.n(pick(rnd, TYPE_RU["микрорайон"]) + (" " if rnd.random() < 0.85 else "")); r.v("street_name", name)
    return "micro_named", has_num


def house_part(r: Rec, style: str, locator_kind: str, name_has_num: bool, lang: str = "ru", force_kw: bool = False):
    """House after the locator. Rule M: a named micro with a number keeps it, then the house is the second number."""
    rnd = r.rnd
    h = gen_house(rnd)
    bare_ok = not force_kw
    if lang == "kk":
        r.n(pick(rnd, [", ", " "])); tpl(r, pick(rnd, HOUSE["kk_num" if style == "num" else "kk_kw"]), "house", h); return
    if lang in ("lat", "kzlat"):
        if rnd.random() < 0.5:
            r.n(pick(rnd, [" ", ", "])); r.v("house", h)
        else:
            r.n(", "); tpl(r, pick(rnd, HOUSE["lat"]), "house", h)
        return
    if bare_ok and rnd.random() < 0.55:
        if locator_kind == "micro_num" or (locator_kind == "micro_named" and name_has_num):
            r.n(pick(rnd, [", ", ", ", " "]))      # «Мкр Аксай 3а, 59» / «мкр 12, 34»
        elif locator_kind == "micro_named":
            r.n(pick(rnd, [", ", " "]))            # «мкр Жетысу 20» — one number = the house
        else:
            sep = pick(rnd, [" ", " ", ", ", "", ","]) if h[0].isdigit() else " "
            if sep == "" and r.parts and r.parts[-1][2] == "street_name" and r.parts[-1][1][-1:].isdigit():
                sep = " "                          # «ул. Е-10» + «5» must not glue into «Е-105»
            r.n(sep)
            if sep == "":
                r.tags.add("glued_house")
        r.v("house", h)
    else:
        r.n(pick(rnd, [", ", " "]))
        tpl(r, pick(rnd, HOUSE["num" if style == "num" else "kw"]), "house", h)
    if rnd.random() < 0.07 and "/" not in h and h.isdigit():   # корпус / блок / строение → «12/2» (Q7)
        word, tail = pick(rnd, BUILDING)
        k = str(rnd.randint(1, 6)) if word != "блок" or rnd.random() < 0.6 else rnd.choice(["А", "Б", "В"])
        r.n(pick(rnd, [" ", ", "]) + word + tail.replace("{k}", k))
        r.g["house"] = f"{h}/{k}"; r.canon.add("house"); r.tags.update({"house_building_join", "conv:team"})


def details(r: Rec, style: str, lang: str = "ru", p_apt=0.8, p_ent=0.3, p_flr=0.28, office=False):
    """Apartment / entrance / floor in one numbering style per record (rule 1)."""
    rnd = r.rnd
    items = [k for k, p in (("apt", p_apt), ("ent", p_ent), ("flr", p_flr)) if rnd.random() < p]
    if rnd.random() < 0.35:
        rnd.shuffle(items)
    joiner = rnd.choices([", ", " ", ","], weights=[0.6, 0.32, 0.08])[0]
    key = {"ru": style, "kk": "kk_" + ("num" if style == "num" else "kw"), "lat": "lat", "kzlat": "lat"}[lang]
    for it in items:
        r.n(joiner)
        if it == "apt":
            if office:
                tpl(r, pick(rnd, APT["office"]), "apartment", str(rnd.randint(1, 520))); r.tags.update({"office_unit", "conv:team"}); continue
            tpl(r, pick(rnd, APT[key]), "apartment", gen_apt(rnd))
        elif it == "ent":
            tpl(r, pick(rnd, ENT[key]), "entrance", str(rnd.randint(1, 12)))
        else:
            val = str(rnd.randint(1, 22))
            tpl(r, pick(rnd, FLR[key]), "floor", val, ord=kk_ordinal(int(val)))


def landmark(r: Rec, with_marker: bool | None = None):
    rnd = r.rnd
    obj = pick(rnd, LANDMARK_OBJECTS).replace("{s}", pick(rnd, SHOPS)).replace("{n}", str(rnd.randint(1, 120)))
    if with_marker is None:
        with_marker = rnd.random() < 0.75
    r.sep()
    if with_marker:
        r.n(pick(rnd, MARKERS))
    r.v("landmark", obj)
    r.tags.add("landmark")


def noise_tail(r: Rec, p=0.5):
    rnd = r.rnd
    if rnd.random() < p:
        r.n(pick(rnd, [", ", " ", ". ", "; "]) + pick(rnd, NOTES)); r.tags.add("courier_note")
    if rnd.random() < p * 0.4:
        r.n(pick(rnd, [", ", " "]) + pick(rnd, PHONES)); r.tags.add("phone")


def noise_head(r: Rec, p=0.25):
    rnd = r.rnd
    if rnd.random() < p:
        r.n(pick(rnd, POSTCODES) + pick(rnd, [", ", " "])); r.tags.add("postcode")
    if rnd.random() < p * 0.6:
        r.n(pick(rnd, COUNTRY) + pick(rnd, [", ", " "])); r.tags.add("country")


# ------------------------------------------------------------------------------------------- transliteration
_TR = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
       "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
       "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya", "ә": "a", "ғ": "g", "қ": "k", "ң": "n", "ө": "o", "ұ": "u", "ү": "u", "һ": "h", "і": "i"}
_KZ = {"а": "a", "ә": "ä", "б": "b", "в": "v", "г": "g", "ғ": "ğ", "д": "d", "е": "e", "ж": "j", "з": "z", "и": "i", "й": "i", "к": "k", "қ": "q",
       "л": "l", "м": "m", "н": "n", "ң": "ŋ", "о": "o", "ө": "ö", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ұ": "ū", "ү": "ü", "ф": "f",
       "х": "h", "һ": "h", "ц": "ts", "ч": "ch", "ш": "ş", "щ": "şş", "ъ": "", "ы": "y", "і": "ı", "ь": "", "э": "e", "ю": "iu", "я": "ia", "ё": "io"}


def _map(s, table):
    out = []
    for ch in s:
        lo = ch.lower()
        if lo in table:
            t = table[lo]
            out.append(t.capitalize() if ch != lo and t else t)
        else:
            out.append(ch)
    return "".join(out)


def translit(s: str) -> str:
    return _map(s, _TR)


def kz_latin(s: str) -> str:
    return _map(s, _KZ)


# =============================================================================================== themes
def living_area(r: Rec, city: str, lang="ru"):
    rnd = r.rnd
    c = CITIES[city]
    kinds = (["zhk", "zhk"] if c["zhk"] else []) + (["micro"] if c["micros"] else []) + (["massiv"] if c["massivs"] else [])
    kind = pick(rnd, kinds or ["massiv"])
    if kind == "zhk":
        name = pick(rnd, c["zhk"])
        if lang == "kk" and rnd.random() < 0.6:
            r.v("living_area", name); r.n(" тұрғын үй кешені")
        else:
            kw = pick(rnd, ["ЖК ", "ЖК ", "жк ", "Ж/К ", "ЖК«", "жилой комплекс "])
            if kw == "ЖК«":
                r.n("ЖК «"); r.v("living_area", name); r.n("»")
            else:
                r.n(kw); r.v("living_area", name)
        r.tags.add("living_zhk")
    elif kind == "micro":
        r.n(pick(rnd, ["мкр ", "мкр. ", "микрорайон ", "м-н "])); r.v("living_area", pick(rnd, c["micros"])); r.tags.add("living_micro")
    else:
        name = pick(rnd, c["massivs"] or ["Шугыла"])
        if rnd.random() < 0.3:
            r.v("living_area", name); r.tags.add("living_bare")
        else:
            r.n(pick(rnd, ["ж/м ", "жилой массив ", "ж.м. ", "жилмассив "])); r.v("living_area", name); r.tags.add("living_massiv")


def base_urban(r: Rec, city: str, *, prefix: bool, lang="ru", style=None, typo=False, office=False,
               p_district=0.18, p_living=0.0, p_landmark=0.12, p_dup_city=0.25, city_form=None, reverse=False):
    rnd = r.rnd
    style = style or rnd.choice(["kw", "kw", "num"])
    c = CITIES[city]
    order = ["city"]
    if rnd.random() < p_district and c["districts"]:
        order.append("district")
    if rnd.random() < p_living and (c["zhk"] or c["micros"] or c["massivs"]):
        order.append("living")
    order.append("locator")
    if reverse:
        order = order[::-1]

    def b_city():
        if prefix:
            db_prefix(r, city)
            if rnd.random() < p_dup_city:
                r.n(", "); city_written_dup(r, city)
        else:
            city_written(r, city, city_form or rnd.choice(["ru", "ru", "g", "gorod", "kk"]))

    def b_locator():
        if "living" not in order and rnd.random() < 0.3 and (c["micros"] or c.get("numbered")):
            kind, num = micro_locator(r, city, lang)
        else:
            kind, num = street_locator(r, city, lang, typo=typo), False
        house_part(r, style, kind, num, lang)

    for i, b in enumerate(order):
        if i:
            if order[i - 1] == "living" or b == "living" and reverse:
                r.n(", ")
            else:
                r.sep()
        if b == "city":
            b_city()
        elif b == "district":
            district_written(r, city, rnd.choice(["post", "post", "pre"]))
        elif b == "living":
            living_area(r, city, lang)
        else:
            b_locator()
            if not reverse:
                details(r, style, lang, office=office)
    if reverse:
        details(r, style, lang, office=office)
    if rnd.random() < p_landmark:
        landmark(r)
    r.region = c["region"]


def t_realistic_db(r: Rec):
    base_urban(r, pick_city(r), prefix=True, p_living=0.12, p_landmark=0.1)
    noise_tail(r, 0.12)


def t_realistic_free(r: Rec):
    city = pick_city(r)
    noise_head(r, 0.15)
    base_urban(r, city, prefix=False, p_living=0.1, p_landmark=0.1, city_form=r.rnd.choice(["ru", "g", "gorod"]))
    noise_tail(r, 0.12)


def t_glued(r: Rec):
    rnd = r.rnd
    city = pick_city(r)
    if rnd.random() < 0.5:
        db_prefix(r, city); r.n(", ")
    else:
        city_written(r, city, "ru"); r.n(pick(rnd, [", ", " "]))
    roll = rnd.random()
    if roll < 0.35:                               # «66квартал 12дом 69квартира»
        n = str(rnd.randint(1, 40))
        kv = rnd.random() < 0.4
        r.setc("street_type", "квартал" if kv else "микрорайон")
        r.v("street_name", n); r.n("квартал" if kv else pick(rnd, ["мкр", "микрорайон"]))
        r.n(pick(rnd, [" ", ""])); r.v("house", str(rnd.randint(1, 80))); r.n("дом")
        r.n(pick(rnd, [" ", ""])); r.v("apartment", gen_apt(rnd)); r.n(pick(rnd, ["квартира", "кв"]))
        if rnd.random() < 0.4:
            r.n(" "); r.v("entrance", str(rnd.randint(1, 9))); r.n("подьезд")
    elif roll < 0.7:                              # «Назарбаева8кв12»
        name, lemma = pick(rnd, [s for s in CITIES[city]["streets"] if not re.search(r"\d", s[0])])
        if rnd.random() < 0.5:
            r.setc("street_type", lemma); r.n({"улица": "ул.", "проспект": "пр.", "переулок": "пер."}.get(lemma, "ул."))
        r.v("street_name", name); r.v("house", gen_house(rnd, plain=True))
        r.n(pick(rnd, ["кв", "кв.", "-кв"])); r.v("apartment", gen_apt(rnd))
        if rnd.random() < 0.4:
            r.n(" "); r.v("floor", str(rnd.randint(1, 15))); r.n("этаж")
    else:                                         # «Туран 55/6 магазин береке 4подьезд 9этаж»
        name, _ = pick(rnd, CITIES[city]["streets"])
        r.v("street_name", name); r.n(" "); r.v("house", gen_house(rnd))
        if rnd.random() < 0.6:
            r.n(" "); r.v("landmark", pick(rnd, ["магазин ", "маг. "]) + pick(rnd, SHOPS))
        r.n(" "); r.v("entrance", str(rnd.randint(1, 9))); r.n(pick(rnd, ["подьезд", "подъезд"]))
        r.n(" "); r.v("floor", str(rnd.randint(1, 16))); r.n("этаж")
    r.tags.add("glued")


def t_micro(r: Rec):
    rnd = r.rnd
    city = pick_city(r, need=lambda c: bool(c["micros"]) or c.get("numbered"))
    c = CITIES[city]
    if rnd.random() < 0.6:
        db_prefix(r, city)
    else:
        city_written(r, city, rnd.choice(["ru", "g"]))
    r.sep()
    if c.get("triple") and rnd.random() < 0.45:   # Актау / Жанаозен «12-5-34» = мкр-дом-кв
        m, h, a = rnd.randint(1, 35), rnd.randint(1, 60), rnd.randint(1, 120)
        if rnd.random() < 0.5:
            r.v("street_name", str(m)); r.n("-"); r.v("house", str(h)); r.n("-"); r.v("apartment", str(a))
            r.tags.add("aktau_triple")                    # no type word → street_type null (gold of the Claude sets)
        else:
            r.setc("street_type", "микрорайон")
            r.v("street_name", str(m)); r.n(pick(rnd, [" мкр", "мкр"]))
            r.n(", "); r.v("house", str(h)); r.n(pick(rnd, [" дом", "дом", " д."]))
            r.n(", "); r.v("apartment", str(a)); r.n(pick(rnd, [" кв", "кв", " кв."]))
        r.tags.add("aktau")
        return
    if c["micros"] and rnd.random() < 0.5:        # «Нурсити 2 микрорайон 11дом 5 подьезд 239 кв»
        base = pick(rnd, c["micros"])
        name = base if re.search(r"\d", base) else f"{base}{pick(rnd, ['-', ' '])}{rnd.randint(1, 5)}"
        r.setc("street_type", "микрорайон")
        r.v("street_name", name); r.n(" " + pick(rnd, ["микрорайон", "мкр", "мкр."]) + " ")
        r.v("house", str(rnd.randint(1, 60))); r.n(pick(rnd, ["дом", " дом"]))
        r.n(" "); r.v("entrance", str(rnd.randint(1, 9))); r.n(pick(rnd, [" подьезд", " подъезд", "подьезд"]))
        r.n(" "); r.v("apartment", gen_apt(rnd)); r.n(pick(rnd, [" кв", "кв", " квартира"]))
        r.tags.add("micro_number_first")
        return
    kind, num = micro_locator(r, city)
    house_part(r, rnd.choice(["kw", "num"]), kind, num)
    details(r, rnd.choice(["kw", "num"]), p_apt=0.85, p_ent=0.35, p_flr=0.3)


def t_living(r: Rec):
    rnd = r.rnd
    city = pick_city(r, need=lambda c: bool(c["zhk"] or c["massivs"] or c["micros"]))
    lang = "kk" if rnd.random() < 0.15 else "ru"
    if rnd.random() < 0.5:
        db_prefix(r, city)
    else:
        city_written(r, city, rnd.choice(["ru", "g", "gorod"]))
    r.sep()
    if rnd.random() < 0.25 and CITIES[city]["districts"]:
        district_written(r, city, "kk" if lang == "kk" else rnd.choice(["post", "pre"])); r.sep()
    living_area(r, city, lang)
    r.n(pick(rnd, [", ", ", ", ","]))
    street_locator(r, city, lang, allow_no_type=False)
    style = rnd.choice(["kw", "num"])
    house_part(r, style, "street", False, lang)
    details(r, style, lang, p_apt=0.85, p_ent=0.3, p_flr=0.3)
    if rnd.random() < 0.15:
        landmark(r)


def t_dacha(r: Rec):
    """Garden societies (Q15): «СТ Строитель, 5 линия, участок 23»."""
    rnd = r.rnd
    if rnd.random() < 0.6:
        region, dru, dkk, vkw, villages = pick(rnd, VILLAGES)
        r.region = region
        r.v("province", pick(rnd, REGIONS[region][0]), gold=region); r.n(", ")
        r.v("district", dru); r.n(pick(rnd, [" р-н", " район"])); r.n(", ")
        if region in NEW_REGIONS:
            r.tags.update({"new_region", "conv:unsettled"})
    else:
        city = pick_city(r, need=lambda c: not c["rep"] or True)
        city_written(r, city, "ru"); r.n(", ")
    soc = pick(rnd, ["Строитель", "Мичуринец", "Геолог", "Энергетик", "Ветеран", "Шахтер-2", "Иртыш-1", "Дружба", "Садовод", "Нефтяник"])
    r.n(pick(rnd, ["СТ ", "СНТ ", "с/т ", "СОТ ", "садовое товарищество "])); r.v("living_area", soc); r.tags.add("dacha")
    r.n(pick(rnd, [", ", " "]))
    if rnd.random() < 0.6:
        n = str(rnd.randint(1, 14)); r.setc("street_type", "линия")
        if rnd.random() < 0.5:
            r.v("street_name", n); r.n(pick(rnd, [" линия", "линия"]))
        else:
            r.n("линия "); r.v("street_name", n)
    else:
        name = pick(rnd, ["Вишнёвая", "Вишневая", "Яблоневая", "Садовая", "Солнечная", "Абрикосовая", "Центральная"])
        r.setc("street_type", "улица"); r.n(pick(rnd, ["ул. ", "ул.", "улица "])); r.v("street_name", name)
    r.n(pick(rnd, [", ", " "]))
    tpl(r, pick(rnd, ["участок {x}", "уч. {x}", "уч.{x}", "{x} участок", "{x}участок", "дом {x}", "д. {x}"]), "house", str(rnd.randint(1, 250)))
    r.tags.add("conv:team")


def t_kazakh(r: Rec):
    rnd = r.rnd
    city = pick_city(r, need=lambda c: bool(c.get("kk")))
    c = CITIES[city]
    if rnd.random() < 0.35:
        db_prefix(r, city)
    else:
        city_written(r, city, "kk")
    r.sep()
    if rnd.random() < 0.35 and c["districts"]:
        district_written(r, city, "kk"); r.sep()
    if rnd.random() < 0.3 and (c["micros"] or c.get("numbered")):
        kind, num = micro_locator(r, city, "kk")
    else:
        kind, num = street_locator(r, city, "kk", allow_no_type=False), False
    style = rnd.choice(["kw", "num", "num"])
    house_part(r, style, kind, num, "kk")
    details(r, style, "kk", p_apt=0.85, p_ent=0.3, p_flr=0.35)
    r.tags.add("kazakh")


def t_latin(r: Rec):
    rnd = r.rnd
    city = pick_city(r, need=lambda c: bool(c.get("lat")))
    lang = rnd.choice(["lat", "lat", "kzlat"])
    if rnd.random() < 0.3:
        noise_head(r, 0.4)
    city_written(r, city, "lat"); r.sep()
    if lang == "kzlat":
        cands = [s for s in CITIES[city]["streets"] if s[1] in ("улица", "проспект")] or [("Абай", "проспект")]
        name, lemma = pick(rnd, cands)
        r.setc("street_type", lemma); r.v("street_name", kz_latin(name)); r.n(" " + pick(rnd, TYPE_KZLAT_AFTER[lemma]))
        r.n(pick(rnd, [" ", ", "])); r.v("house", gen_house(rnd).replace("а", "a").replace("б", "b").replace("в", "v").replace("г", "g").replace("А", "A").replace("Б", "B"))
        r.n(", "); r.v("apartment", gen_apt(rnd)); r.n(pick(rnd, [" päter", " pater"]))
        if rnd.random() < 0.4:
            r.n(", "); r.v("floor", str(rnd.randint(1, 16))); r.n(" qabat")
        r.tags.add("kz_latin_2021")
        return
    if rnd.random() < 0.3 and CITIES[city]["micros"]:
        kind, num = micro_locator(r, city, "lat")
    else:
        kind, num = street_locator(r, city, "lat", allow_no_type=True), False
    h = gen_house(rnd).replace("а", "a").replace("б", "b").replace("в", "v").replace("г", "g").replace("А", "A").replace("Б", "B")
    r.n(", " if kind == "micro_named" and num else pick(rnd, [" ", ", "]))
    if rnd.random() < 0.4:
        tpl(r, pick(rnd, HOUSE["lat"]), "house", h)
    else:
        r.v("house", h)
    details(r, "kw", "lat", p_apt=0.85, p_ent=0.25, p_flr=0.25)
    r.tags.add("latin")


def t_corpus_office(r: Rec):
    rnd = r.rnd
    city = pick_city(r, need=lambda c: not c.get("town"))
    if rnd.random() < 0.5:
        db_prefix(r, city)
    else:
        city_written(r, city, rnd.choice(["ru", "g"]))
    r.sep()
    if rnd.random() < 0.3:
        r.v("landmark", pick(rnd, ["БЦ ", "ТЦ ", "ТРЦ "]) + pick(rnd, SHOPS)); r.sep()
    street_locator(r, city, allow_no_type=False)
    h = str(rnd.randint(1, 250))
    r.n(pick(rnd, [" ", ", "]))
    tpl(r, pick(rnd, ["{x}", "д. {x}", "дом {x}"]), "house", h)
    if rnd.random() < 0.75:
        word, tail = pick(rnd, BUILDING)
        k = str(rnd.randint(1, 6)) if word != "блок" or rnd.random() < 0.5 else rnd.choice(["А", "Б", "В"])
        r.n(pick(rnd, [" ", ", "]) + word + tail.replace("{k}", k))
        r.g["house"] = f"{h}/{k}"; r.canon.add("house"); r.tags.add("house_building_join")
    details(r, "kw", office=rnd.random() < 0.6, p_apt=0.95, p_ent=0.2, p_flr=0.5)
    r.tags.add("conv:team")


def t_landmark_noise(r: Rec):
    rnd = r.rnd
    city = pick_city(r)
    noise_head(r, 0.6)
    if rnd.random() < 0.5:
        db_prefix(r, city)
    else:
        city_written(r, city, rnd.choice(["ru", "g", "gorod"]))
    r.sep()
    if rnd.random() < 0.3:
        landmark(r, with_marker=False); r.sep()
    street_locator(r, city)
    style = rnd.choice(["kw", "num"])
    house_part(r, style, "street", False)
    details(r, style, p_apt=0.8, p_ent=0.35, p_flr=0.35)
    if not r.g["landmark"]:
        landmark(r)
    noise_tail(r, 0.9)


TYPO_SWAPS = [("а", "о"), ("е", "и"), ("и", "е"), ("о", "а"), ("я", "а"), ("ы", "и")]


def typo_word(rnd, w: str) -> str:
    if len(w) < 5:
        return w
    i = rnd.randint(1, len(w) - 2)
    op = rnd.choice(["drop", "swap", "dup", "sub"])
    if op == "drop":
        return w[:i] + w[i + 1:]
    if op == "swap":
        return w[:i] + w[i + 1] + w[i] + w[i + 2:]
    if op == "dup":
        return w[:i] + w[i] + w[i:]
    for a, b in TYPO_SWAPS:
        if a in w[1:]:
            j = w.index(a, 1)
            return w[:j] + b + w[j + 1:]
    return w


def apply_case(r: Rec, mode: str):
    f = str.upper if mode == "upper" else str.lower
    for p in r.parts:
        p[1] = f(p[1])
        if p[0] == "v" and p[2] not in r.canon:
            r.g[p[2]] = gold_clean(p[1])
    r.tags.add("case_" + mode)


def sprinkle_invisible(r: Rec):
    rnd = r.rnd
    for p in r.parts:
        if p[0] == "n" and " " in p[1] and rnd.random() < 0.35:
            p[1] = p[1].replace(" ", rnd.choice([" ", " ​", "​ ", "  "]), 1)
        if p[0] == "v" and p[2] in ("street_name", "city") and p[2] not in r.canon and len(p[1]) > 7 and rnd.random() < 0.12:
            i = rnd.randint(2, len(p[1]) - 3)
            p[1] = p[1][:i] + rnd.choice(["­", "​"]) + p[1][i:]   # invisible INSIDE a value — gold excludes it
    if rnd.random() < 0.3:
        r.parts.insert(0, ["n", "﻿", None])
    r.tags.add("invisible_chars")


def t_typos_format(r: Rec):
    rnd = r.rnd
    city = pick_city(r)
    base_urban(r, city, prefix=rnd.random() < 0.5, typo=True, p_landmark=0.15)
    for p in r.parts:            # typos inside the street name: the gold copies the typo (copy-not-rewrite)
        if p[0] == "v" and p[2] == "street_name" and rnd.random() < 0.45 and not re.search(r"\d", p[1]):
            p[1] = typo_word(rnd, p[1]); r.g["street_name"] = gold_clean(p[1]); r.tags.add("name_typo")
    for p in r.parts:            # keyword typos in the details
        if p[0] == "n":
            if "подъезд" in p[1] and rnd.random() < 0.6:
                p[1] = p[1].replace("подъезд", pick(rnd, ["подезд", "подьезд", "поъезд"])); r.tags.add("keyword_typo")
            if "квартира" in p[1] and rnd.random() < 0.6:
                p[1] = p[1].replace("квартира", pick(rnd, ["кваритра", "квартра", "квартиа"])); r.tags.add("keyword_typo")
    fmt = rnd.random()
    if fmt < 0.2:
        apply_case(r, "upper")
    elif fmt < 0.35:
        apply_case(r, "lower")
    if rnd.random() < 0.55:
        sprinkle_invisible(r)
    if rnd.random() < 0.35:
        for p in r.parts:
            if p[0] == "n" and p[1] == ", " and rnd.random() < 0.4:
                p[1] = rnd.choice([" ,", "  ", " , ", ",  "]); r.tags.add("messy_punct")
    if rnd.random() < 0.15:
        r.parts.insert(0, ["n", rnd.choice(["  ", " ", "\t"]), None]); r.n(rnd.choice(["  ", ".", " .", "\n"])); r.tags.add("edge_whitespace")


def t_order_dupes(r: Rec):
    rnd = r.rnd
    roll = rnd.random()
    if roll < 0.3:                                   # reverse order: details first, city last
        base_urban(r, pick_city(r), prefix=False, reverse=True, city_form=rnd.choice(["ru", "g"])); r.tags.add("reverse_order")
    elif roll < 0.5:                                 # city district instead of the city → city null
        rep = pick(rnd, ["Алматы", "Астана", "Шымкент"])
        r.region = rep
        ru, kk = pick(rnd, CITIES[rep]["districts"])
        r.v("district", ru); r.n(f" район/{kk} ауданы" if rnd.random() < 0.5 else pick(rnd, [" район", " р-н"]))
        r.sep()
        street_locator(r, rep, allow_no_type=rnd.random() < 0.5)
        house_part(r, "kw", "street", False, force_kw=rnd.random() < 0.5)
        details(r, "kw", p_apt=0.6)
        r.tags.add("district_instead_of_city")
    elif roll < 0.7:                                 # region + city in free text
        city = pick_city(r, need=lambda c: not c["rep"])
        reg = CITIES[city]["region"]
        r.v("province", pick(rnd, REGIONS[reg][0]), gold=reg)
        if reg in NEW_REGIONS:
            r.tags.update({"new_region", "conv:unsettled"})
        r.sep(); city_written(r, city, rnd.choice(["ru", "g", "gorod"])); r.sep()
        kind = street_locator(r, city); house_part(r, "kw", kind, False); details(r, "kw")
        r.tags.add("region_in_text")
    elif roll < 0.85:                                # old city names: Нур-Султан / Алма-Ата / Чимкент / Семипалатинск
        city = pick(rnd, ["Астана", "Алматы", "Шымкент", "Семей"])
        city_written(r, city, "old")
        r.sep(); kind = street_locator(r, city); house_part(r, "kw", kind, False); details(r, "kw")
    else:                                            # Astana's district called «Алматы»
        r.region = "Астана"
        r.n(pick(rnd, ["г. ", "город ", ""])); r.v("city", "Астана"); r.setc("province", "Астана"); r.sep()
        pre = rnd.random() < 0.5
        if pre:
            r.n(pick(rnd, ["район ", "р-н "]))
        r.v("district", "Алматы")
        if not pre:
            r.n(pick(rnd, [" район", " р-н"]))
        r.sep(); kind = street_locator(r, "Астана"); house_part(r, "kw", kind, False); details(r, "kw")
        r.tags.add("district_named_like_city")


def t_corrections(r: Rec):
    """Client corrects themselves mid-string — the LAST value wins; old street names in brackets."""
    rnd = r.rnd
    city = pick_city(r)
    city_written(r, city, rnd.choice(["ru", "g"])); r.sep()
    name, lemma = pick(rnd, [s for s in CITIES[city]["streets"] if not re.search(r"\d", s[0])])
    r.setc("street_type", lemma); r.n({"улица": "ул.", "проспект": "пр.", "переулок": "пер."}.get(lemma, "ул.") + " "); r.v("street_name", name)
    roll = rnd.random()
    if roll < 0.35:
        r.n(" (" + pick(rnd, ["бывш. ", "бывшая ", "старое название ", "", "б. "]) + pick(rnd, ["Ленина", "Фурманова", "Карла Маркса", "Комсомольская", "Мира", "Советская"]) + ")")
        r.n(" "); r.v("house", gen_house(rnd)); details(r, "kw", p_apt=0.9)
        r.tags.add("old_name_in_brackets")
    elif roll < 0.7:
        h1, h2 = rnd.randint(1, 120), rnd.randint(1, 120)
        if h1 == h2:
            h2 += 1
        r.n(f" {h1}" + pick(rnd, [", нет, ", " нет ", ", вернее ", " (ошибся) ", ", точнее "])); r.v("house", str(h2))
        details(r, "kw", p_apt=0.9)
        r.tags.add("correction_house")
    else:
        r.n(" "); r.v("house", gen_house(rnd)); r.n(" ")
        a1, a2 = gen_apt(rnd), gen_apt(rnd)
        if a1 == a2:
            a2 = str(int(a2) + 1)
        if rnd.random() < 0.7:
            r.n(f"кв {a1}" + pick(rnd, [" — исправляю: кв ", ", нет, кв ", ". Исправление: кв ", ", ошибка, правильно кв "])); r.v("apartment", a2)
        else:
            r.n(f"{a1} пәтер, жоқ қателестім "); r.v("apartment", a2); r.n(" пәтер")
        r.tags.add("correction_apartment")
    r.tags.add("conv:team")


def t_villages(r: Rec):
    rnd = r.rnd
    region, dru, dkk, vkw, villages = pick(rnd, VILLAGES)
    r.region = region
    roll = rnd.random()
    if roll < 0.4:                                    # DB style: region + district bilingual, then the village
        rf, rk = REGIONS[region]
        r.v("province", rf[0], gold=region); r.n(f"/{rk}, ")
        r.v("district", dru); r.n(f" район/{dkk} ауданы, ")
    elif roll < 0.75:
        r.v("province", pick(rnd, REGIONS[region][0]), gold=region); r.n(", ")
        r.v("district", dru); r.n(pick(rnd, [" р-н", " район", " р-он"])); r.n(", ")
    else:                                             # district only — city stays the village
        r.v("district", dru); r.n(pick(rnd, [" район", " р-н"])); r.n(", ")
    if region in NEW_REGIONS and r.g["province"]:
        r.tags.update({"new_region", "conv:unsettled"})
    kw = pick(rnd, VILLAGE_KW[vkw])
    r.n(kw + ("" if kw.endswith(" ") else " ")); r.v("city", pick(rnd, villages))
    r.n(pick(rnd, [", ", " "]))
    if rnd.random() < 0.8:
        r.setc("street_type", "улица"); r.n(pick(rnd, ["ул. ", "ул.", "улица "])); r.v("street_name", pick(rnd, VILLAGE_STREETS))
    else:
        r.v("street_name", pick(rnd, VILLAGE_STREETS))
    r.n(pick(rnd, [" ", ", ", " д. ", ", дом "])); r.v("house", gen_house(rnd))
    if rnd.random() < 0.25:
        r.n(", "); r.v("apartment", str(rnd.randint(1, 30))); r.n(pick(rnd, [" кв", "кв"]))
    if rnd.random() < 0.2:
        noise_tail(r, 1.0)
    r.tags.add("rural")


def t_towns(r: Rec):
    """Smaller towns of every region — the long tail a hackathon set built around Astana/Almaty never sees."""
    rnd = r.rnd
    town = pick(rnd, [c for c in CITIES if CITIES[c].get("town")])
    c = CITIES[town]
    r.region = c["region"]
    roll = rnd.random()
    if roll < 0.45:
        db_prefix(r, town)
    elif roll < 0.75:
        r.v("province", pick(rnd, REGIONS[c["region"]][0]), gold=c["region"]); r.sep()
        city_written(r, town, rnd.choice(["g", "gorod", "ru"]))
    else:
        city_written(r, town, rnd.choice(["g", "gorod", "ru"]))
    if r.g["province"] and c["region"] in NEW_REGIONS:
        r.tags.update({"new_region", "conv:unsettled"})
    r.sep()
    style = rnd.choice(["kw", "num"])
    if c.get("triple") and rnd.random() < 0.4:
        m, h, a = rnd.randint(1, 35), rnd.randint(1, 60), rnd.randint(1, 120)
        r.v("street_name", str(m)); r.n("-"); r.v("house", str(h)); r.n("-"); r.v("apartment", str(a))
        r.tags.add("aktau_triple")
    elif c.get("numbered") and rnd.random() < 0.5:
        kind, num = micro_locator(r, town)
        house_part(r, style, kind, num)
        details(r, style)
    else:
        kind = street_locator(r, town)
        house_part(r, style, kind, False)
        details(r, style)
    if rnd.random() < 0.1:
        landmark(r)
    noise_tail(r, 0.15)
    r.tags.add("town")


def t_wrong_layout(r: Rec):
    """Only the KEYWORD is typed in the English layout («rd 12» = «кв 12»); values stay unambiguous."""
    rnd = r.rnd
    city = pick_city(r)
    city_written(r, city, rnd.choice(["ru", "g"])); r.sep()
    street_locator(r, city, allow_no_type=False)
    r.n(" "); r.v("house", gen_house(rnd))
    for w in rnd.sample(["кв", "подъезд", "этаж"], k=rnd.randint(1, 3)):
        r.n(", " if rnd.random() < 0.6 else " ")
        r.n(WRONG_LAYOUT[w] + " ")
        r.v({"кв": "apartment", "подъезд": "entrance", "этаж": "floor"}[w], str(rnd.randint(1, 200 if w == "кв" else 15)))
    r.tags.add("wrong_layout_keyword")


def t_minimal(r: Rec):
    rnd = r.rnd
    roll = rnd.random()
    if roll < 0.45:                                    # «жуалы 19», «Кулманова 133»
        city = pick_city(r)
        name, _ = pick(rnd, [s for s in CITIES[city]["streets"] if not re.search(r"\d", s[0])])
        r.v("street_name", name.lower() if rnd.random() < 0.4 else name); r.n(" "); r.v("house", gen_house(rnd))
    elif roll < 0.6:
        city_written(r, pick_city(r), "ru")
    elif roll < 0.72:
        r.n(pick(rnd, ["кв ", "кв."])); r.v("apartment", gen_apt(rnd))
    else:                                              # nothing parseable → all null
        r.n(pick(rnd, ["", " ", "-", "???", "доставка", "нет адреса", "самовывоз", "0", "...", "адрес уточнить по телефону", "​"]))
        r.tags.add("unparseable")
    r.tags.add("minimal")


_RU2EN = dict(zip("йцукенгшщзхъфывапролджэячсмитьбю.,ЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ",
                  "qwertyuiop[]asdfghjkl;'zxcvbnm,./?QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>"))
_HOMO = {"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
         "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X"}


def t_extreme(r: Rec):
    """Rare but real: a whole string typed in the English layout, Latin lookalikes inside Cyrillic words, house ranges,
    PO boxes, «№» houses, quoted names, line breaks, Kazakh case endings, an apartment without a house."""
    rnd = r.rnd
    city = pick_city(r, need=lambda c: not c.get("town"))
    kind = rnd.choice(["layout", "homoglyph", "range", "pobox", "numsign", "quoted", "newline", "kk_case", "apt_only"])
    if kind == "layout":                      # gold = the intended Cyrillic text (team decision, commit 5440428)
        name, lemma = pick(rnd, [s for s in CITIES[city]["streets"] if s[1] == "улица" and not re.search(r"[\d-]", s[0])] or [("Абая", "улица")])
        h, a = str(rnd.randint(1, 150)), str(rnd.randint(1, 200))
        text = f"{city}, ул. {name} {h}" + (f", кв {a}" if rnd.random() < 0.6 else "")
        r.n(text.translate(str.maketrans(_RU2EN)))
        r.setc("city", city); r.setc("street_type", "улица"); r.setc("street_name", name); r.setc("house", h)
        if ", кв " in text:
            r.setc("apartment", a)
        if CITIES[city]["rep"]:
            r.setc("province", city)
        r.tags.update({"wrong_layout_whole", "conv:team"})
        r.region = CITIES[city]["region"]
        return
    if kind == "apt_only":
        city_written(r, city, rnd.choice(["ru", "g"])); r.sep()
        street_locator(r, city, allow_no_type=False); r.n(pick(rnd, [", кв ", ", кв. ", " кв "])); r.v("apartment", gen_apt(rnd))
        r.tags.add("apartment_without_house")
        return
    if kind == "kk_case":                     # «Абай даңғылындағы 10 үй» — the case ending sits on the type word
        c = CITIES[city]
        kk = c.get("kk") or city
        if kk != city:
            r.v("city", kk, gold=city); r.tags.add("city_kk_to_ru")
        else:
            r.v("city", kk)
        if c["rep"]:
            r.setc("province", city)
        r.region = c["region"]
        r.n(pick(rnd, [" қаласындағы ", " қаласы, "]))
        name, lemma = pick(rnd, [s for s in CITIES[city]["streets"] if s[1] in ("улица", "проспект")])
        r.setc("street_type", lemma); r.v("street_name", name)
        r.n(" " + ("көшесіндегі" if lemma == "улица" else "даңғылындағы") + " ")
        r.v("house", str(rnd.randint(1, 150))); r.n(" үй, "); r.v("apartment", gen_apt(rnd)); r.n(" пәтер")
        r.tags.add("kazakh_case_ending")
        return
    city_written(r, city, rnd.choice(["ru", "g"]))
    r.n("\n" if kind == "newline" else rnd.choice([", ", " "]))
    name, lemma = pick(rnd, [s for s in CITIES[city]["streets"] if not re.search(r"\d", s[0])])
    r.setc("street_type", lemma); r.n({"улица": "ул. ", "проспект": "пр. ", "переулок": "пер. "}.get(lemma, "ул. "))
    if kind == "quoted":
        r.n("«"); r.v("street_name", name); r.n("»")
    elif kind == "homoglyph":                 # visually identical Latin letters inside a Cyrillic name — copied as written
        chars = list(name)
        idx = [i for i, ch in enumerate(chars) if ch in _HOMO]
        for i in rnd.sample(idx, k=min(len(idx), rnd.randint(1, 2))):
            chars[i] = _HOMO[chars[i]]
        r.v("street_name", "".join(chars)); r.tags.add("conv:unsettled")
    else:
        r.v("street_name", name)
    if kind == "range":
        a = rnd.randint(1, 150)
        r.n(" "); r.v("house", rnd.choice([f"{a}-{a + 2}", f"{a}а-{a + 2}", f"{a}/1-{a}/3"][:2]))
    elif kind == "numsign":
        r.n(pick(rnd, [" №", " № ", ", №"])); r.v("house", str(rnd.randint(1, 150)))
    elif kind == "pobox":                     # a PO box next to a real street is ignored
        r.n(" "); r.v("house", str(rnd.randint(1, 150))); r.n(f", а/я {rnd.randint(1, 300)}")
    else:
        r.n(" "); r.v("house", gen_house(rnd))
    r.n("\n" if kind == "newline" else ", ")
    r.n(pick(rnd, ["кв ", "кв. "])); r.v("apartment", gen_apt(rnd))
    r.tags.add("extreme_" + kind)


THEMES = [  # (name, weight, fn, difficulty)
    ("realistic_db_prefix", 700, t_realistic_db, "realistic"),
    ("realistic_free_text", 360, t_realistic_free, "realistic"),
    ("regional_towns", 260, t_towns, "realistic"),
    ("glued_tokens", 140, t_glued, "hard"),
    ("microdistricts", 150, t_micro, "hard"),
    ("living_area_plus_street", 140, t_living, "hard"),
    ("dacha_societies", 50, t_dacha, "hard"),
    ("kazakh", 150, t_kazakh, "hard"),
    ("latin", 130, t_latin, "hard"),
    ("corpus_block_office", 100, t_corpus_office, "hard"),
    ("landmarks_and_noise", 140, t_landmark_noise, "hard"),
    ("typos_case_invisible", 160, t_typos_format, "hard"),
    ("order_dupes_regions", 140, t_order_dupes, "hard"),
    ("corrections_brackets", 60, t_corrections, "hard"),
    ("villages_rural", 140, t_villages, "hard"),
    ("wrong_layout_keyword", 30, t_wrong_layout, "hard"),
    ("minimal_garbage", 30, t_minimal, "hard"),
    ("extreme_mix", 120, t_extreme, "hard"),
]


# =============================================================================================== international (not jury data)
INTL = [  # (country, template builder)
    "RU", "RU", "RU", "KG", "KG", "UZ", "UZ", "CN", "TR", "DE", "AE", "GE", "AM", "AZ", "BY", "US",
]


def intl_record(r: Rec, code: str):
    rnd = r.rnd
    r.region = code
    h, a = str(rnd.randint(1, 180)), str(rnd.randint(1, 300))
    if code == "RU":
        city, reg = pick(rnd, [("Москва", None), ("Новосибирск", "Новосибирская область"), ("Омск", "Омская обл."),
                               ("Екатеринбург", "Свердловская область"), ("Оренбург", "Оренбургская область"), ("Химки", "Московская обл.")])
        street, lemma, kw = pick(rnd, [("Советская", "улица", "ул. "), ("Ленина", "улица", "ул. "), ("Ленинский", "проспект", "пр-т "),
                                       ("Мира", "проспект", "пр. "), ("Тверская", "улица", "улица ")])
        r.n(pick(rnd, ["Россия, ", "РФ, ", "Российская Федерация, "]))
        if rnd.random() < 0.5:
            r.n({"Москва": "125009, ", "Новосибирск": "630099, ", "Омск": "644007, ", "Екатеринбург": "620014, ",
                 "Оренбург": "460000, ", "Химки": "141400, "}[city])
        if reg:
            r.v("province", reg); r.n(", ")
        r.n(pick(rnd, ["г. ", "город "])); r.v("city", city); r.n(", ")
        if lemma == "проспект" and kw == "пр-т " and rnd.random() < 0.5:
            r.v("street_name", street); r.n(" пр-т")
        else:
            r.n(kw); r.v("street_name", street)
        r.setc("street_type", lemma)
        r.n(pick(rnd, [", д. ", " ", ", дом "])); r.v("house", h)
        r.n(pick(rnd, [", кв. ", ", кв ", " кв."])); r.v("apartment", a)
    elif code == "KG":
        if rnd.random() < 0.6:
            r.n(pick(rnd, ["Кыргызстан, ", "Кыргызская Республика, ", "Киргизия, "]))
            if rnd.random() < 0.4:
                r.v("province", "Чуйская область"); r.n(", ")
            r.n("г. "); r.v("city", "Бишкек"); r.n(", ")
        else:
            r.v("city", "Бишкек"); r.n(", ")
        if rnd.random() < 0.5:
            r.n("мкр "); r.v("street_name", pick(rnd, ["Джал-29", "Асанбай", "Восток-5", "Тунгуч", "Аламедин-1"])); r.setc("street_type", "микрорайон")
            r.n(", д. "); r.v("house", h)
        else:
            r.n(pick(rnd, ["ул. ", "пр. "])); nm = pick(rnd, ["Киевская", "Токтогула", "Московская", "Чуй"])
            r.setc("street_type", "проспект" if nm == "Чуй" else "улица"); r.v("street_name", nm); r.n(" "); r.v("house", h)
        r.n(", кв "); r.v("apartment", a)
    elif code == "UZ":
        if rnd.random() < 0.5:
            r.n("Uzbekistan, "); r.v("city", "Tashkent"); r.n(", ")
            r.v("street_name", pick(rnd, ["Amir Temur", "Navoi", "Shota Rustaveli", "Mustaqillik"])); r.setc("street_type", "улица")
            r.n(pick(rnd, [" street ", " str. "])); r.v("house", h); r.n(", apt "); r.v("apartment", a)
        else:
            uz_city = pick(rnd, ["Ташкент", "Самарканд"])
            r.n("Узбекистан, г. "); r.v("city", uz_city); r.n(", ")
            if uz_city == "Ташкент" and rnd.random() < 0.5:
                r.v("district", pick(rnd, ["Юнусабадский", "Мирзо-Улугбекский"])); r.n(" район, ")
            r.n("ул. "); r.v("street_name", pick(rnd, ["Навои", "Бабура", "Амира Темура"])); r.setc("street_type", "улица")
            r.n(", д. "); r.v("house", h); r.n(", кв. "); r.v("apartment", a)
    elif code == "CN":
        r.n("Китай, г. "); r.v("city", pick(rnd, ["Урумчи", "Пекин", "Гуанчжоу"])); r.n(", ул. ")
        r.v("street_name", pick(rnd, ["Синьхуа", "Хэпин", "Чжуншань"])); r.setc("street_type", "улица"); r.n(" "); r.v("house", h)
        r.n(", кв "); r.v("apartment", a)
    elif code == "TR":
        r.n(pick(rnd, ["Турция, ", "Türkiye, "])); r.n("г. "); r.v("city", pick(rnd, ["Стамбул", "Анкара", "Анталья"]))
        r.n(", ул. "); r.v("street_name", pick(rnd, ["Истикляль", "Ататюрка", "Багдад"])); r.setc("street_type", "улица"); r.n(" "); r.v("house", h)
        r.n(", кв "); r.v("apartment", a)
    elif code == "DE":
        if rnd.random() < 0.5:
            r.n(pick(rnd, ["10115 ", "80331 "])); r.v("city", pick(rnd, ["Berlin", "München"])); r.n(", ")
        else:
            r.n("Deutschland, "); r.v("city", pick(rnd, ["Berlin", "Hamburg"])); r.n(", ")
        r.v("street_name", pick(rnd, ["Invalidenstraße", "Leopoldstraße", "Hauptstraße", "Friedrichstraße"])); r.n(" "); r.v("house", h)
        if rnd.random() < 0.6:
            r.n(", Wohnung "); r.v("apartment", a)
    elif code == "AE":
        r.n(pick(rnd, ["UAE, ", "ОАЭ, "])); r.v("city", "Dubai"); r.n(", ")
        r.v("street_name", pick(rnd, ["Sheikh Zayed", "Al Wasl"])); r.setc("street_type", "дорога"); r.n(" Road "); r.v("house", h)
        r.n(", apt "); r.v("apartment", a)
    elif code in ("GE", "AM", "AZ", "BY"):
        ctry, city, streets = {"GE": ("Грузия", "Тбилиси", [("Руставели", "проспект"), ("Чавчавадзе", "проспект")]),
                               "AM": ("Армения", "Ереван", [("Абовяна", "улица"), ("Маштоца", "проспект")]),
                               "AZ": ("Азербайджан", "Баку", [("Нефтяников", "проспект"), ("Низами", "улица")]),
                               "BY": ("Беларусь", "Минск", [("Независимости", "проспект"), ("Немига", "улица")])}[code]
        r.n(ctry + ", г. "); r.v("city", city); r.n(", ")
        nm, lemma = pick(rnd, streets)
        r.n("пр. " if lemma == "проспект" else "ул. "); r.v("street_name", nm); r.setc("street_type", lemma)
        r.n(" "); r.v("house", h); r.n(", кв "); r.v("apartment", a)
    elif code == "US":
        r.v("house", h); r.n(" "); r.v("street_name", pick(rnd, ["5th", "Madison", "Lexington"])); r.setc("street_type", "проспект")
        r.n(" Avenue, Apt "); r.v("apartment", a + pick(rnd, ["", "B"])); r.n(", ")
        r.v("city", "New York"); r.n(", "); r.v("province", "NY"); r.n(" 10022, USA")
    r.g["_country"] = code
    r.tags.add("international")


# =============================================================================================== validation
PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def jnorm(v):
    if v is None:
        return None
    v = re.sub(r"\s+", " ", PUNCT.sub("", str(v).lower())).strip()
    return v or None


def validate(rec: dict, canon: set[str]) -> list[str]:
    errs = []
    raw_n = jnorm(gold_clean(rec["raw_address"])) or ""
    for f in SPAN_FIELDS:
        val = rec.get(f)
        if val is None or f in canon:
            continue
        if jnorm(val) is None or jnorm(val) not in raw_n:
            errs.append(f"{f}={val!r} not a span of raw")
    for f in ("apartment", "entrance", "floor"):
        if rec.get(f) is not None and not re.fullmatch(r"\d+[A-Za-zА-Яа-яЁё]?", rec[f]):
            errs.append(f"{f}={rec[f]!r} not numeric")
    if rec.get("house") is not None and not re.fullmatch(r"[\dA-Za-zА-Яа-яЁё/-]+", rec["house"]):
        errs.append(f"house={rec['house']!r} odd")
    if rec.get("street_type") is not None and rec.get("street_name") is None:
        errs.append("street_type without street_name")
    return errs


def _build(theme_fn, name, rnd_seed, intl_code=None):
    r = Rec(random.Random(rnd_seed), name)
    if intl_code:
        intl_record(r, intl_code)
    else:
        theme_fn(r)
    return r


def generate(n: int, seed: int) -> tuple[list[dict], dict]:
    rnd = random.Random(seed)
    total_w = sum(w for _, w, _, _ in THEMES)
    quotas = [(name, max(1, round(n * w / total_w)), fn, diff) for name, w, fn, diff in THEMES]
    quotas[0] = (quotas[0][0], quotas[0][1] + n - sum(q for _, q, _, _ in quotas), quotas[0][2], quotas[0][3])
    out, seen, rejected = [], set(), Counter()
    for name, q, fn, diff in quotas:
        made = attempts = 0
        while made < q and attempts < q * 30:
            attempts += 1
            r = _build(fn, name, rnd.random())
            raw = r.raw()
            if raw in seen and "unparseable" not in r.tags:
                rejected["duplicate"] += 1
                continue
            rec = {"raw_address": raw, **{f: r.g[f] for f in FIELDS}}
            errs = validate(rec, r.canon)
            if errs:
                rejected["invalid:" + errs[0].split("=")[0]] += 1
                continue
            seen.add(raw)
            out.append({**rec, "_theme": name, "_difficulty": diff, "_region": r.region, "_tags": sorted(r.tags)})
            made += 1
    rnd.shuffle(out)
    return [{"id": f"hx_{seed}_{i:05d}", **rec} for i, rec in enumerate(out, 1)], dict(rejected)


def generate_intl(n: int, seed: int) -> list[dict]:
    rnd = random.Random(seed * 31 + 7)
    out, seen = [], set()
    while len(out) < n:
        code = pick(rnd, INTL)
        r = _build(None, "international", rnd.random(), intl_code=code)
        raw = r.raw()
        if raw in seen:
            continue
        rec = {"raw_address": raw, **{f: r.g[f] for f in FIELDS}}
        if validate(rec, r.canon):
            continue
        seen.add(raw)
        out.append({"id": f"ix_{seed}_{len(out) + 1:04d}", **rec, "country": code, "_theme": "international",
                    "_region": code, "_tags": sorted(r.tags)})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--intl", type=int, default=150, help="size of the separate international set (0 = none)")
    ap.add_argument("--out", default="data/hard3k")
    a = ap.parse_args(argv)
    recs, rejected = generate(a.n, a.seed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    p_in, p_gold = out / f"hard3k_s{a.seed}_input.json", out / f"hard3k_s{a.seed}_gold.json"
    p_in.write_text(json.dumps([{"id": r["id"], "raw_address": r["raw_address"]} for r in recs], ensure_ascii=False, indent=1), encoding="utf-8")
    p_gold.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    files = [p_in, p_gold]
    if a.intl:
        p_intl = out / f"intl_s{a.seed}_gold.json"
        p_intl.write_text(json.dumps(generate_intl(a.intl, a.seed), ensure_ascii=False, indent=1), encoding="utf-8")
        files.append(p_intl)
    manifest = {"n": len(recs), "seed": a.seed, "generator": "eval/hard3k_generate.py",
                "themes": dict(Counter(r["_theme"] for r in recs)), "regions": dict(Counter(r["_region"] for r in recs).most_common()),
                "difficulty": dict(Counter(r["_difficulty"] for r in recs)),
                "tags": dict(Counter(t for r in recs for t in r["_tags"]).most_common()),
                "fields_present": {f: sum(1 for r in recs if r[f] is not None) for f in FIELDS},
                "rejected_while_generating": rejected,
                "sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    (out / f"hard3k_s{a.seed}_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("n", "themes", "regions", "difficulty", "rejected_while_generating", "sha256")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
