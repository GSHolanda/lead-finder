"""Configurações persistidas no banco local (tabela ``settings``)."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from leadfinder.db import dumps, loads

DEFAULTS: dict[str, Any] = {
    # Quem está prospectando (usado nas mensagens)
    "vendedor_nome": "",
    "vendedor_empresa": "",
    "vendedor_cidade": "Brasília",
    "servico_descricao": "desenvolvimento de sites",
    # Segmentos com maior aderência ao serviço (chaves de discovery.segments)
    "segmentos_alvo": [],  # vazio = todos os segmentos conhecidos com peso igual
    # Ollama (análise local)
    "ollama_url": "http://localhost:11434",
    "ollama_model": "qwen2.5:7b",
    # Busca web para verificação: "nenhum" | "searxng" | "brave"
    "busca_provedor": "nenhum",
    "searxng_url": "http://localhost:8888",
    "brave_api_key": "",
    "busca_max_candidatos": 5,
    "busca_min_consultas": 2,
    "adivinhar_dominio": True,
    # Rede
    "http_timeout": 12,
    "intervalo_por_host": 1.5,
    # Descoberta
    "overpass_url": "https://overpass-api.de/api/interpreter",
    "nominatim_url": "https://nominatim.openstreetmap.org",
    # Piloto
    "piloto_limite_erro": 0.10,
    "piloto_minimo_revisados": 20,
}

SECRET_KEYS = {"brave_api_key"}


def get_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    data = dict(DEFAULTS)
    for row in conn.execute("SELECT key, value FROM settings"):
        data[row["key"]] = loads(row["value"], DEFAULTS.get(row["key"]))
    env_key = os.environ.get("LEADFINDER_BRAVE_KEY")
    if env_key and not data.get("brave_api_key"):
        data["brave_api_key"] = env_key
    return data


def public_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    data = get_settings(conn)
    for k in SECRET_KEYS:
        data[k] = "••••••" if data.get(k) else ""
    return data


def update_settings(conn: sqlite3.Connection, changes: dict[str, Any]) -> dict[str, Any]:
    for key, value in changes.items():
        if key not in DEFAULTS:
            continue
        if key in SECRET_KEYS and value == "••••••":
            continue  # valor mascarado vindo da interface: manter o atual
        default = DEFAULTS[key]
        if isinstance(default, bool):
            value = value in (True, "true", "1", 1, "on")
        elif isinstance(default, int) and not isinstance(default, bool):
            value = int(value)
        elif isinstance(default, float):
            value = float(value)
        elif isinstance(default, list) and isinstance(value, str):
            value = [v.strip() for v in value.split(",") if v.strip()]
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, dumps(value)),
        )
    conn.commit()
    return public_settings(conn)


def make_client(settings: dict[str, Any]):
    from leadfinder.net import HttpClient

    return HttpClient(
        timeout=float(settings.get("http_timeout", 12)),
        min_interval=float(settings.get("intervalo_por_host", 1.5)),
    )
