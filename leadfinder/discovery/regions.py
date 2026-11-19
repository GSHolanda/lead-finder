"""Regiões de busca: Distrito Federal (inteiro ou por Região Administrativa) e
municípios do entorno (Goiás).

- "DF inteiro" e municípios do entorno usam a fronteira administrativa do OSM.
- Regiões Administrativas/bairros usam busca por raio a partir de um ponto
  geocodificado no Nominatim (gratuito, 1 requisição/segundo).
"""

from __future__ import annotations

DF_AREA = "Distrito Federal (todo)"

REGIOES_ADMINISTRATIVAS = [
    "Asa Sul", "Asa Norte", "Lago Sul", "Lago Norte", "Sudoeste", "Noroeste",
    "Cruzeiro", "Octogonal", "Taguatinga", "Ceilândia", "Sol Nascente", "Águas Claras",
    "Arniqueira", "Vicente Pires", "Guará", "Núcleo Bandeirante", "Riacho Fundo",
    "Riacho Fundo II", "Candangolândia", "Park Way", "Samambaia", "Recanto das Emas",
    "Gama", "Santa Maria", "Sobradinho", "Sobradinho II", "Planaltina", "Paranoá",
    "Itapoã", "São Sebastião", "Jardim Botânico", "Brazlândia", "SIA", "SCIA/Estrutural",
    "Varjão", "Fercal",
]

# Municípios goianos do entorno (RIDE-DF). Nomes como no OpenStreetMap.
ENTORNO_GO = [
    "Águas Lindas de Goiás", "Valparaíso de Goiás", "Luziânia", "Cidade Ocidental",
    "Novo Gama", "Formosa", "Planaltina", "Santo Antônio do Descoberto", "Alexânia",
    "Cristalina", "Padre Bernardo", "Cocalzinho de Goiás", "Abadiânia", "Cabeceiras",
    "Vila Boa", "Água Fria de Goiás", "Mimoso de Goiás",
]


def region_options() -> dict:
    return {
        "df": DF_AREA,
        "regioes_administrativas": REGIOES_ADMINISTRATIVAS,
        "entorno_go": ENTORNO_GO,
    }


def overpass_area_clause(region_type: str, name: str | None = None) -> str:
    """Cláusula Overpass que define ``.a`` como área de busca."""
    if region_type == "df":
        return 'area["ISO3166-2"="BR-DF"]->.a;'
    if region_type == "municipio_go":
        if not name:
            raise ValueError("informe o município")
        safe = name.replace('"', "")
        return (
            'area["ISO3166-2"="BR-GO"]->.go;'
            f'rel(area.go)["boundary"="administrative"]["admin_level"="8"]["name"="{safe}"];'
            "map_to_area->.a;"
        )
    raise ValueError(f"tipo de região desconhecido: {region_type}")


def geocode_query(name: str, region_type: str) -> str:
    if region_type == "municipio_go":
        return f"{name}, Goiás, Brasil"
    return f"{name}, Distrito Federal, Brasil"
