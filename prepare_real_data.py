#!/usr/bin/env python3
"""Import reference ("real area") polygons from real_polygons_for_tool.xlsx.

Sheet `for_tool`, columns: C = Country, D = City, E = real area (km²), F = polygon as
GeoJSON (Polygon / MultiPolygon, or LineString / MultiLineString rings).

For every row it writes
  urban_cities/<id>/real_boundary.geojson
  urban_cities/<id>/real_meta.json           {"real_area", "bounds"}
and registers / updates the city in cities_all.json (name, country, real_area, bounds;
lat/lon default to the polygon centroid for cities that are new). Existing per-source
results are kept. Run add_city.py / run_urban.py afterwards to compute the new cities.

Usage: python prepare_real_data.py [--xlsx path]
The city-id rules below are kept verbatim from the original tool so ids stay stable.
"""
import argparse
import json
import re
import unicodedata
from collections import Counter

import openpyxl
from shapely.ops import unary_union

from urban import boundary, registry
from urban.config import BASE

XLSX = BASE / "real_polygons_for_tool.xlsx"

CYR = {
    'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'yo','ж':'zh',
    'з':'z','и':'i','й':'j','к':'k','л':'l','м':'m','н':'n','о':'o',
    'п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts',
    'ч':'ch','ш':'sh','щ':'shch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu',
    'я':'ya','қ':'k','ң':'n','ұ':'u','ғ':'g','ә':'a','і':'i',
}
def transliterate(s):
    return ''.join(CYR.get(c, c) for c in s.lower())

def to_id(text):
    """Make a URL-safe lowercase ID from any text."""
    t = transliterate(text)
    t = unicodedata.normalize('NFKD', t)
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn' and ord(c) < 128)
    t = re.sub(r'[^\x00-\x7F]', '', t)
    return re.sub(r'[^a-z0-9]+', '_', t.lower()).strip('_')

def strip_nonlatin_parens(name):
    """Remove parenthesized content that contains non-ASCII (Arabic, etc.)."""
    return re.sub(r'\s*\([^)]*\)', lambda m: '' if any(ord(c) > 127 for c in m.group()) else m.group(), name).strip()

def get_base_and_qualifier(name):
    """Return (base_without_parens, first_paren_content)."""
    m = re.search(r'\(([^)]+)\)', name)
    qual = m.group(1).strip() if m else ''
    base = re.sub(r'\s*\([^)]*\)', '', name).strip()
    return base, qual

# ── Human-readable name for display ──────────────────────────────────────────
CYR_CITY_EN = {
    'Астана':'Astana','Алматы':'Almaty','Актобе':'Aktobe','Атырау':'Atyrau',
    'Уральск':'Uralsk','Актау':'Aktau','Кокшетау':'Kokshetau',
    'Степногорск':'Stepnogorsk','Атбасар':'Atbasar','Ерейментау':'Ereymentau',
    'Щучинск':'Shchuchinsk','Есик':'Yesik','Каскелен':'Kaskelen',
    'Талгар':'Talgar','Талдыкорган':'Taldykorgan','Аягоз':'Ayagoz',
    'Зайсан':'Zaisan','Семей':'Semey','Тараз':'Taraz','Шу':'Shu',
    'Аксай':'Aksai','Балхаш':'Balkhash','Жезказган':'Zhezkazgan',
    'Кызылорда':'Kyzylorda','Жанаозен':'Zhanaozen','Павлодар':'Pavlodar',
    'Экибастуз':'Ekibastuz','Петропавловск':'Petropavlovsk',
    'Туркестан':'Turkestan','Костанай':'Kostanay','Рудный':'Rudny',
    'Аральск':'Aralsk','Сатпаев':'Satpaev','Темиртау':'Temirtau',
    'Шахтинск':'Shakhtinsk','Бурабай':'Burabay','Шымкент':'Shymkent',
    'Индерборский':'Inderborskiy','Бейнеу':'Beyneu','Шиели':'Shieli',
    'Сарыкемер':'Sarykemer','Бишкек':'Bishkek','Одесса':'Odessa',
    'Белая Церковь':'Bila Tserkva','Байсерке':'Bayserke',
    'Конаев':'Konaev',
    'Усть-Каменогорск':'Ust-Kamenogorsk',
    'Акколь':'Akkol','Кульсары':'Kulsary',
    'Алтай':'Altai','Шамалган':'Shamalgan',
    'Айтеке би':'Aiteke Bi','Курмангазы':'Kurmangazy',
    'Акмолинская область':'Akmola Region','Зыряновск':'Zyryanovsky',
}

COUNTRY_EN = {
    'казахстан':'Kazakhstan','кыргызстан':'Kyrgyzstan','украина':'Ukraine',
    'south africa':'South Africa','nigeria':'Nigeria','ghana':'Ghana',
    'botswana':'Botswana','namibia':'Namibia','zambia':'Zambia',
    'zimbabwe':'Zimbabwe','angola':'Angola','mozambique':'Mozambique',
    'madagascar':'Madagascar',"côte d'ivoire":"Côte d'Ivoire",
    'brazil':'Brazil','mexico':'Mexico','colombia':'Colombia',
    'chile':'Chile','peru':'Peru','argentina':'Argentina',
    'ecuador':'Ecuador','bolivia':'Bolivia','el salvador':'El Salvador',
    'guatemala':'Guatemala','honduras':'Honduras','nicaragua':'Nicaragua',
    'costa rica':'Costa Rica','panama':'Panama',
    'dominican republic':'Dominican Republic','jamaica':'Jamaica',
    'india':'India','pakistan':'Pakistan','bangladesh':'Bangladesh',
    'nepal':'Nepal','indonesia':'Indonesia','philippines':'Philippines',
    'vietnam':'Vietnam','thailand':'Thailand','malaysia':'Malaysia',
    'laos':'Laos','turkey':'Turkey','saudi arabia':'Saudi Arabia',
    'lebanon':'Lebanon','egypt':'Egypt','morocco':'Morocco',
    'algeria':'Algeria','tunisia':'Tunisia','qatar':'Qatar',
}

def country_en(raw):
    return COUNTRY_EN.get(raw.lower(), raw)

def display_name(country, name):
    """Return clean English display name."""
    # Try Cyrillic lookup
    base, qual = get_base_and_qualifier(name)
    # Check multi-word Cyrillic keys
    for k, v in CYR_CITY_EN.items():
        if base.startswith(k) or k in base:
            if qual and any(ord(c) < 128 and c.isalpha() for c in qual):
                return f"{v} ({qual})"
            return v
    # Strip Arabic/non-Latin parens, keep English
    clean = strip_nonlatin_parens(name)
    clean = re.sub(r'\s*\([^)]*\)', '', clean).strip()  # remove remaining parens
    return clean if clean else name

# ── Manual ID overrides (where auto-gen doesn't match existing IDs) ───────────
OVERRIDE_ID = {
    ('Algeria',  'Alger'):                       'algiers',
    ('India',    'Delhi NCR'):                   'delhi',
    ('Pakistan', 'Islamabad / Rawalpindi'):       'islamabad',
    ('Morocco',  'Rabat'):                        'rabat',
    ('Morocco',  'Meknès'):                       'meknes',
    ('Egypt',    "Ismaïlia"):                     'ismailia',
    ('Algeria',  'Boumerdes'):                    'boumerdes',
}



def read_rows(xlsx):
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    ws = wb["for_tool"] if "for_tool" in wb.sheetnames else wb.worksheets[0]
    entries = []
    for row in ws.iter_rows(min_row=1, values_only=True):
        cells = list(row) + [None] * 6
        country_raw, city_name, area_raw, poly_str = cells[2], cells[3], cells[4], cells[5]
        if not country_raw or not city_name or str(city_name).strip() == "City":
            continue
        country_raw, city_name = str(country_raw).strip(), str(city_name).strip()
        base_name, qualifier = get_base_and_qualifier(city_name)
        base_id = OVERRIDE_ID.get((country_raw, base_name)) or to_id(base_name)
        try:
            area = float(area_raw) if area_raw not in (None, "") else None
        except (TypeError, ValueError):
            area = None
        try:
            geojson = json.loads(poly_str) if poly_str else None
        except (json.JSONDecodeError, TypeError):
            geojson = None
        entries.append(dict(country_raw=country_raw, city_name_raw=city_name, base_name=base_name,
                            qualifier=qualifier, base_id=base_id, area=area, geojson=geojson))

    # Same collision rule as the original tool: qualifier suffix, then _2, _3 ...
    colliding = {cid for cid, n in Counter(e["base_id"] for e in entries).items() if n > 1}
    used = set()
    for e in entries:
        bid = e["base_id"]
        if bid in colliding:
            qual = to_id(e["qualifier"]) if e["qualifier"] else ""
            cand = f"{bid}_{qual[:12]}" if qual else bid
            final, n = cand, 2
            while final in used:
                final = f"{cand}_{n}"
                n += 1
            e["city_id"] = final
        else:
            e["city_id"] = bid
        used.add(e["city_id"])
        e["country"] = country_en(e["country_raw"])
        e["display_name"] = display_name(e["country_raw"], e["city_name_raw"])
    return entries


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xlsx", default=str(XLSX))
    a = ap.parse_args()

    reg = registry.load()
    entries = read_rows(a.xlsx)
    new, updated, no_poly = 0, 0, 0
    for e in entries:
        cid = e["city_id"]
        entry = {"id": cid, "name": e["display_name"], "country": e["country"],
                 "real_area": round(e["area"], 2) if e["area"] else None,
                 "has_real_polygon": bool(e["geojson"])}
        if e["geojson"]:
            bounds = boundary.write_real_polygon(cid, e["geojson"], e["display_name"], e["country"],
                                                 entry["real_area"])
            entry["bounds"] = bounds
            existing = registry.find(reg, cid)
            if existing is None or existing.get("lat") is None:
                polys = boundary._rings_to_polygons(e["geojson"], None, 0)
                if polys:
                    c = unary_union(polys).centroid
                    entry["lat"], entry["lon"] = round(c.y, 5), round(c.x, 5)
        else:
            no_poly += 1
        if registry.find(reg, cid) is None:
            new += 1
        else:
            updated += 1
        registry.upsert(reg, entry)
    registry.save(reg)
    print(f"{len(entries)} rows: {new} new cities, {updated} updated, {no_poly} without polygon")
    todo = [c["id"] for c in reg["cities"] if not c.get("sources")]
    if todo:
        print(f"{len(todo)} cities have no results yet — compute them with: python run_urban.py --source worldpop")


if __name__ == "__main__":
    main()
