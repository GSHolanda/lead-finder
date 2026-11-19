"""Segmentos de negócio: filtros do OpenStreetMap e prefixos CNAE.

Os prefixos CNAE são de classes (4 dígitos) ou subclasses (7 dígitos) da
CNAE 2.x. Revise-os se for importar a base de CNPJ para um nicho específico.

``beneficios`` indica quais aplicações de site costumam fazer sentido para o
segmento. Isso NÃO afirma necessidade: só orienta quais evidências procurar.
"""

from __future__ import annotations

SEGMENTS: dict[str, dict] = {
    "alimentacao": {
        "label": "Restaurantes, lanchonetes e bares",
        "osm": [("amenity", "restaurant"), ("amenity", "fast_food"), ("amenity", "bar"),
                ("amenity", "pub"), ("amenity", "ice_cream"), ("amenity", "food_court")],
        "cnae": ["5611", "5612", "5620"],
        "beneficios": ["catalogo", "localizacao", "whatsapp"],
    },
    "cafe_padaria": {
        "label": "Cafés, padarias e confeitarias",
        "osm": [("amenity", "cafe"), ("shop", "bakery"), ("shop", "pastry"), ("shop", "confectionery")],
        "cnae": ["1091", "4721"],
        "beneficios": ["catalogo", "localizacao", "whatsapp"],
    },
    "beleza": {
        "label": "Salões, barbearias e estética",
        "osm": [("shop", "hairdresser"), ("shop", "beauty"), ("shop", "cosmetics"),
                ("amenity", "beauty_salon"), ("shop", "massage"), ("shop", "tattoo")],
        "cnae": ["9602"],
        "beneficios": ["servicos", "portfolio", "agendamento", "localizacao", "whatsapp"],
    },
    "saude": {
        "label": "Clínicas, consultórios e odontologia",
        "osm": [("amenity", "clinic"), ("amenity", "doctors"), ("amenity", "dentist"),
                ("healthcare", "clinic"), ("healthcare", "physiotherapist"),
                ("healthcare", "psychotherapist"), ("healthcare", "dentist"),
                ("healthcare", "laboratory"), ("healthcare", "alternative")],
        "cnae": ["8630", "8640", "8650", "8690"],
        "beneficios": ["servicos", "agendamento", "localizacao", "whatsapp"],
    },
    "pet": {
        "label": "Pet shops e veterinárias",
        "osm": [("shop", "pet"), ("amenity", "veterinary"), ("shop", "pet_grooming")],
        "cnae": ["7500", "4789004", "9609208"],
        "beneficios": ["servicos", "catalogo", "agendamento", "localizacao", "whatsapp"],
    },
    "automotivo": {
        "label": "Oficinas, autopeças e lava-jatos",
        "osm": [("shop", "car_repair"), ("shop", "tyres"), ("shop", "car_parts"),
                ("shop", "motorcycle_repair"), ("shop", "motorcycle"), ("amenity", "car_wash")],
        "cnae": ["4520", "4530", "4543"],
        "beneficios": ["servicos", "orcamento", "localizacao", "whatsapp"],
    },
    "fitness": {
        "label": "Academias e estúdios",
        "osm": [("leisure", "fitness_centre"), ("leisure", "sports_centre"), ("sport", "yoga")],
        "cnae": ["9313"],
        "beneficios": ["servicos", "localizacao", "whatsapp"],
    },
    "juridico_contabil": {
        "label": "Advocacia e contabilidade",
        "osm": [("office", "lawyer"), ("office", "accountant"), ("office", "notary")],
        "cnae": ["6911", "6920"],
        "beneficios": ["servicos", "localizacao", "whatsapp"],
    },
    "imobiliaria": {
        "label": "Imobiliárias e corretores",
        "osm": [("office", "estate_agent")],
        "cnae": ["6821", "6822"],
        "beneficios": ["catalogo", "localizacao", "whatsapp"],
    },
    "moda": {
        "label": "Roupas, calçados e acessórios",
        "osm": [("shop", "clothes"), ("shop", "shoes"), ("shop", "boutique"), ("shop", "bag"),
                ("shop", "jewelry"), ("shop", "fashion_accessories")],
        "cnae": ["4781", "4782", "4783"],
        "beneficios": ["catalogo", "localizacao", "whatsapp"],
    },
    "construcao": {
        "label": "Materiais de construção e acabamentos",
        "osm": [("shop", "hardware"), ("shop", "doityourself"), ("shop", "paint"),
                ("shop", "tiles"), ("shop", "bathroom_furnishing"), ("trade", "building_supplies")],
        "cnae": ["4741", "4742", "4743", "4744"],
        "beneficios": ["catalogo", "orcamento", "localizacao", "whatsapp"],
    },
    "servicos_casa": {
        "label": "Reformas, marcenaria e serviços técnicos",
        "osm": [("craft", "carpenter"), ("craft", "electrician"), ("craft", "plumber"),
                ("craft", "painter"), ("craft", "glaziery"), ("craft", "metal_construction"),
                ("craft", "upholsterer"), ("craft", "hvac"), ("shop", "electronics_repair"),
                ("craft", "electronics_repair"), ("shop", "computer")],
        "cnae": ["4321", "4322", "4329", "4330", "3101", "9521", "9511"],
        "beneficios": ["servicos", "orcamento", "portfolio", "whatsapp"],
    },
    "moveis_decoracao": {
        "label": "Móveis e decoração",
        "osm": [("shop", "furniture"), ("shop", "interior_decoration"), ("shop", "curtain"),
                ("shop", "houseware"), ("shop", "lighting")],
        "cnae": ["4754", "4755", "4759"],
        "beneficios": ["catalogo", "portfolio", "orcamento", "localizacao", "whatsapp"],
    },
    "floricultura_presentes": {
        "label": "Floriculturas e presentes",
        "osm": [("shop", "florist"), ("shop", "gift"), ("shop", "party")],
        "cnae": ["4789002", "4789001"],
        "beneficios": ["catalogo", "whatsapp", "localizacao"],
    },
    "otica": {
        "label": "Óticas",
        "osm": [("shop", "optician")],
        "cnae": ["4774"],
        "beneficios": ["catalogo", "localizacao", "whatsapp"],
    },
    "educacao": {
        "label": "Escolas livres, idiomas e autoescolas",
        "osm": [("amenity", "language_school"), ("amenity", "driving_school"),
                ("amenity", "music_school"), ("amenity", "dancing_school"), ("amenity", "prep_school")],
        "cnae": ["8591", "8592", "8593", "8599"],
        "beneficios": ["servicos", "localizacao", "whatsapp"],
    },
    "fotografia_grafica": {
        "label": "Fotografia, gráficas e comunicação visual",
        "osm": [("craft", "photographer"), ("shop", "photo"), ("shop", "copyshop"),
                ("craft", "printer"), ("craft", "signmaker")],
        "cnae": ["7420", "1813", "1821"],
        "beneficios": ["portfolio", "orcamento", "servicos", "whatsapp"],
    },
    "hospedagem": {
        "label": "Hotéis e pousadas",
        "osm": [("tourism", "hotel"), ("tourism", "guest_house"), ("tourism", "hostel"),
                ("tourism", "motel")],
        "cnae": ["5510", "5590"],
        "beneficios": ["catalogo", "localizacao", "whatsapp"],
    },
    "mercado": {
        "label": "Mercados, açougues e hortifrutis",
        "osm": [("shop", "supermarket"), ("shop", "convenience"), ("shop", "butcher"),
                ("shop", "greengrocer"), ("shop", "deli"), ("shop", "beverages")],
        "cnae": ["4711", "4712", "4722", "4724", "4723"],
        "beneficios": ["catalogo", "whatsapp", "localizacao"],
    },
}

# Tradução de valores comuns de tags do OSM para português (exibição)
OSM_VALUE_LABELS = {
    "restaurant": "restaurante", "fast_food": "lanchonete", "bar": "bar", "pub": "bar",
    "ice_cream": "sorveteria", "cafe": "café", "bakery": "padaria", "pastry": "confeitaria",
    "confectionery": "doceria", "hairdresser": "salão/barbearia", "beauty": "estética",
    "cosmetics": "cosméticos", "massage": "massagem", "tattoo": "tatuagem",
    "clinic": "clínica", "doctors": "consultório", "dentist": "odontologia",
    "physiotherapist": "fisioterapia", "psychotherapist": "psicoterapia",
    "laboratory": "laboratório", "pet": "pet shop", "veterinary": "veterinária",
    "pet_grooming": "banho e tosa", "car_repair": "oficina mecânica", "tyres": "pneus",
    "car_parts": "autopeças", "motorcycle_repair": "oficina de motos", "motorcycle": "motos",
    "car_wash": "lava-jato", "fitness_centre": "academia", "sports_centre": "centro esportivo",
    "lawyer": "advocacia", "accountant": "contabilidade", "notary": "cartório",
    "estate_agent": "imobiliária", "clothes": "roupas", "shoes": "calçados",
    "boutique": "boutique", "bag": "bolsas", "jewelry": "joalheria",
    "fashion_accessories": "acessórios", "hardware": "ferragens",
    "doityourself": "materiais de construção", "paint": "tintas", "tiles": "pisos e revestimentos",
    "bathroom_furnishing": "louças e metais", "carpenter": "marcenaria",
    "electrician": "eletricista", "plumber": "encanador", "painter": "pintura",
    "glaziery": "vidraçaria", "metal_construction": "serralheria", "upholsterer": "tapeçaria/estofados",
    "hvac": "ar-condicionado", "electronics_repair": "conserto de eletrônicos",
    "computer": "informática", "furniture": "móveis", "interior_decoration": "decoração",
    "curtain": "cortinas", "houseware": "utilidades domésticas", "lighting": "iluminação",
    "florist": "floricultura", "gift": "presentes", "party": "artigos de festa",
    "optician": "ótica", "language_school": "escola de idiomas", "driving_school": "autoescola",
    "music_school": "escola de música", "dancing_school": "escola de dança",
    "prep_school": "cursinho", "photographer": "fotografia", "photo": "foto",
    "copyshop": "copiadora", "printer": "gráfica", "signmaker": "comunicação visual",
    "hotel": "hotel", "guest_house": "pousada", "hostel": "hostel", "motel": "motel",
    "supermarket": "supermercado", "convenience": "conveniência", "butcher": "açougue",
    "greengrocer": "hortifruti", "deli": "empório", "beverages": "bebidas",
    "food_court": "praça de alimentação", "beauty_salon": "salão de beleza", "yoga": "yoga",
    "building_supplies": "materiais de construção",
}

CUISINE_LABELS = {
    "pizza": "pizza", "burger": "hambúrguer", "japanese": "culinária japonesa",
    "sushi": "sushi", "brazilian": "culinária brasileira", "regional": "culinária regional",
    "italian": "culinária italiana", "chinese": "culinária chinesa", "arab": "culinária árabe",
    "lebanese": "culinária libanesa", "mexican": "culinária mexicana", "steak_house": "churrascaria",
    "barbecue": "churrasco", "coffee_shop": "cafeteria", "ice_cream": "sorvetes",
    "sandwich": "sanduíches", "chicken": "frango", "seafood": "frutos do mar",
    "vegetarian": "vegetariana", "vegan": "vegana", "juice": "sucos", "acai": "açaí",
    "pastel": "pastel", "snack": "lanches", "cake": "bolos", "bakery": "padaria",
    "fish": "peixes", "portuguese": "culinária portuguesa", "northeastern": "culinária nordestina",
    "mineira": "culinária mineira", "fine_dining": "alta gastronomia", "buffet": "buffet",
    "kebab": "kebab", "hot_dog": "cachorro-quente", "tapioca": "tapioca", "crepe": "crepe",
}


def segment_label(key: str | None) -> str:
    if not key:
        return "Não classificado"
    seg = SEGMENTS.get(key)
    return seg["label"] if seg else key


def segment_for_tags(tags: dict) -> tuple[str | None, str | None]:
    """Identifica o segmento a partir das tags do OSM. Retorna (chave, tag_bruta)."""
    for key, seg in SEGMENTS.items():
        for k, v in seg["osm"]:
            if tags.get(k) == v:
                return key, f"{k}={v}"
    return None, None


def segment_for_cnae(cnae: str | None) -> str | None:
    if not cnae:
        return None
    digits = "".join(c for c in str(cnae) if c.isdigit())
    best, best_len = None, 0
    for key, seg in SEGMENTS.items():
        for prefix in seg["cnae"]:
            if digits.startswith(prefix) and len(prefix) > best_len:
                best, best_len = key, len(prefix)
    return best


def category_label_for_tags(tags: dict) -> str | None:
    for k in ("shop", "amenity", "craft", "office", "healthcare", "leisure", "tourism"):
        v = tags.get(k)
        if v:
            return OSM_VALUE_LABELS.get(v, v.replace("_", " "))
    return None
