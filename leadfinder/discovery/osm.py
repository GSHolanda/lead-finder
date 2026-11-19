"""Descoberta via OpenStreetMap (Overpass API + Nominatim) — fontes gratuitas.

Uso responsável: as instâncias públicas têm limites de uso. Consultamos uma
região/segmento por vez e registramos a origem (tipo/id do objeto OSM) para
cada lead. Os dados do OSM são © colaboradores do OpenStreetMap (ODbL).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from urllib.parse import urlencode

from leadfinder import urls
from leadfinder.discovery import regions
from leadfinder.discovery.segments import SEGMENTS, category_label_for_tags, segment_for_tags
from leadfinder.leads import LeadRecord, upsert_record
from leadfinder.net import HttpClient

WEBSITE_TAGS = ["website", "contact:website", "url", "contact:url", "website:menu", "brand:website"]
SOCIAL_TAGS = {
    "contact:instagram": "instagram", "instagram": "instagram",
    "contact:facebook": "facebook", "facebook": "facebook",
    "contact:tiktok": "tiktok", "contact:twitter": "twitter", "contact:youtube": "youtube",
    "contact:linkedin": "linkedin",
}
PHONE_TAGS = ["phone", "contact:phone", "mobile", "contact:mobile", "phone:mobile"]
WHATSAPP_TAGS = ["contact:whatsapp", "whatsapp", "phone:whatsapp"]
EMAIL_TAGS = ["email", "contact:email"]


@dataclass
class DiscoveryResult:
    query: str
    total_elements: int
    created: int
    merged: int
    skipped_unnamed: int
    error: str | None = None


def build_query(segment_keys: list[str], area_clause: str | None = None,
                around: tuple[float, float, int] | None = None, timeout: int = 120) -> str:
    filters = []
    for key in segment_keys:
        for k, v in SEGMENTS[key]["osm"]:
            filters.append((k, v))
    if not filters:
        raise ValueError("nenhum segmento informado")
    scope = "(area.a)" if area_clause else f"(around:{around[2]},{around[0]},{around[1]})"
    body = "".join(f'nwr["{k}"="{v}"]["name"]{scope};' for k, v in filters)
    head = f"[out:json][timeout:{timeout}];"
    return f"{head}{area_clause or ''}({body});out center tags;"


def _first(tags: dict, keys: list[str]) -> str | None:
    for k in keys:
        v = tags.get(k)
        if v:
            return v.split(";")[0].strip()
    return None


def element_to_record(el: dict, default_city: str | None = None, default_uf: str | None = None) -> LeadRecord | None:
    tags = el.get("tags") or {}
    name = tags.get("name")
    if not name:
        return None
    seg, raw = segment_for_tags(tags)
    links: list[tuple[str, str]] = []
    for t in WEBSITE_TAGS:
        if tags.get(t):
            for part in tags[t].split(";"):
                if part.strip():
                    links.append((part.strip(), f"osm:{t}"))
    for t, platform in SOCIAL_TAGS.items():
        if tags.get(t):
            u = urls.social_handle_url(platform, tags[t])
            if u:
                links.append((u, f"osm:{t}"))
    center = el.get("center") or {}
    lat = el.get("lat", center.get("lat"))
    lon = el.get("lon", center.get("lon"))
    brand = tags.get("brand") or tags.get("brand:wikidata")
    osm_type, osm_id = el.get("type"), el.get("id")
    return LeadRecord(
        name=name,
        source="osm",
        source_ref=f"{osm_type}/{osm_id}",
        source_url=f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
        segment=seg,
        category_raw=category_label_for_tags(tags) or raw,
        phone=_first(tags, PHONE_TAGS),
        whatsapp=_first(tags, WHATSAPP_TAGS),
        email=_first(tags, EMAIL_TAGS),
        street=tags.get("addr:street"),
        housenumber=tags.get("addr:housenumber"),
        neighborhood=tags.get("addr:suburb") or tags.get("addr:neighbourhood") or tags.get("addr:district"),
        city=tags.get("addr:city") or default_city,
        uf=tags.get("addr:state") or default_uf,
        postcode=tags.get("addr:postcode"),
        lat=lat,
        lon=lon,
        is_chain=bool(brand),
        chain_reason=f"tag brand no OSM: {tags.get('brand') or tags.get('brand:wikidata')}" if brand else None,
        links=links,
        raw={"type": osm_type, "id": osm_id, "tags": tags},
    )


def geocode(client: HttpClient, nominatim_url: str, query: str) -> tuple[float, float] | None:
    params = urlencode({"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "br"})
    res, data = client.get_json(f"{nominatim_url.rstrip('/')}/search?{params}")
    if not res.ok or not isinstance(data, list) or not data:
        return None
    return float(data[0]["lat"]), float(data[0]["lon"])


def discover(
    conn: sqlite3.Connection,
    client: HttpClient,
    settings: dict,
    segment_keys: list[str],
    region_type: str,
    region_name: str | None = None,
    radius_m: int = 2500,
    limit: int | None = None,
) -> DiscoveryResult:
    """Executa a busca no OSM e grava os leads.

    region_type: "df" | "municipio_go" | "ra" (Região Administrativa/bairro, por raio)
    """
    default_city, default_uf = None, None
    if region_type == "ra":
        point = geocode(client, settings["nominatim_url"], regions.geocode_query(region_name or "", "ra"))
        if not point:
            return DiscoveryResult("", 0, 0, 0, 0, error=f"não foi possível geocodificar '{region_name}'")
        query = build_query(segment_keys, around=(point[0], point[1], int(radius_m)))
        default_uf = "DF"
    else:
        query = build_query(segment_keys, area_clause=regions.overpass_area_clause(region_type, region_name))
        if region_type == "df":
            default_uf = "DF"
        else:
            default_city, default_uf = region_name, "GO"
    res, data = client.post_json(settings["overpass_url"], form={"data": query}, timeout=180)
    if not res.ok or not isinstance(data, dict):
        return DiscoveryResult(query, 0, 0, 0, 0, error=f"Overpass: {res.error_detail or res.error or res.status}")
    elements = data.get("elements", [])
    created = merged = unnamed = 0
    for el in elements[: limit or len(elements)]:
        rec = element_to_record(el, default_city=default_city, default_uf=default_uf)
        if rec is None:
            unnamed += 1
            continue
        if region_type == "ra" and not rec.neighborhood:
            rec.neighborhood = region_name
        _, was_created = upsert_record(conn, rec)
        created += int(was_created)
        merged += int(not was_created)
    conn.commit()
    return DiscoveryResult(query, len(elements), created, merged, unnamed)
