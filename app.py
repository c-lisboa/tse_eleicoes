import os
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template

load_dotenv()

app = Flask(__name__)

RESULT_URL = os.getenv("TSE_RESULT_URL", "").strip()
POLL_SECONDS = max(30, int(os.getenv("POLL_SECONDS", "60")))

lock = threading.Lock()
estado = {
    "ok": False,
    "fonte": RESULT_URL,
    "ultima_consulta": None,
    "ultima_atualizacao_tse": None,
    "erro": "Aguardando configuração da URL oficial do EA20.",
    "eleicao": None,
    "cargo": "Presidente",
    "totalizacao_final": None,
    "secoes": {},
    "eleitores": {},
    "votos": {},
    "candidatos": [],
}

sessao = requests.Session()
sessao.headers.update({
    "User-Agent": "PainelResultadosTSE/1.0 (consumo de dados publicos)",
    "Accept": "application/json",
})

def numero(valor):
    """Converte campos numéricos do TSE em inteiros."""
    try:
        return int(str(valor).replace(".", "").strip())
    except (ValueError, TypeError):
        return 0

def percentual(valor):
    """Converte percentual do TSE, que pode usar vírgula decimal."""
    try:
        return float(str(valor).replace(",", "."))
    except (ValueError, TypeError):
        return 0.0

def perc(valor, total):
    return round((valor / total * 100), 2) if total else 0.0

def extrair_resultado(dados):
    cargos = dados.get("carg", [])
    presidente = next(
        (c for c in cargos if str(c.get("cd")) == "1"),
        None
    )

    if not presidente:
        raise ValueError("O arquivo não contém o cargo Presidente (código 1).")

    candidatos = []

    # O EA20 organiza candidatos dentro de agrupamentos e partidos.
    # Não se devem somar votos de partidos/agrupamentos e candidatos juntos.
    for agrupamento in presidente.get("agr", []):
        for partido in agrupamento.get("par", []):
            for candidato in partido.get("cand", []):
                candidatos.append({
                    "numero": str(candidato.get("n", "")),
                    "nome": candidato.get("nm", "Nome não informado"),
                    "partido": partido.get("sg", ""),
                    "situacao": candidato.get("st", ""),
                    "destinacao": candidato.get("dvt", ""),
                    "votos": numero(candidato.get("vap")),
                    "percentual": percentual(candidato.get("pvap")),
                })

    candidatos.sort(key=lambda c: c["votos"], reverse=True)

    s = dados.get("s", {})
    e = dados.get("e", {})
    v = dados.get("v", {})

    return {
        "ok": True,
        "fonte": RESULT_URL,
        "ultima_consulta": datetime.now(
            ZoneInfo("America/Sao_Paulo")
        ).isoformat(timespec="seconds"),
        "ultima_atualizacao_tse": (
            f'{dados.get("dt", "")} {dados.get("ht", "")}'.strip()
        ),
        "erro": None,
        "eleicao": dados.get("ele"),
        "cargo": presidente.get("nmn", "Presidente"),
        "totalizacao_final": str(dados.get("and", "")).lower() == "f",
        "secoes": {
            "total": numero(s.get("ts")),
            "totalizadas": numero(s.get("st")),
            "percentual": percentual(s.get("pst")),
            "nao_instaladas": numero(s.get("sni")),
            "anuladas": numero(s.get("sna")),
        },
        "eleitores": {
            "aptos": numero(e.get("te")),
            "comparecimento": numero(e.get("c")),
            "abstencoes": numero(e.get("a")),
            "percentual_comparecimento": percentual(e.get("pc")),
        },
        "votos": {
            "apurados": numero(v.get("tv")),
            "validos": numero(v.get("vv")),
            "brancos": numero(v.get("vb")),
            "nulos": numero(v.get("vn")),
            "nominais": numero(v.get("vnom")),
            "percentual_validos": percentual(v.get("pvvc")),
        },
        "candidatos": candidatos,
    }

def consultar_tse():
    if not RESULT_URL:
        raise RuntimeError(
            "Configure TSE_RESULT_URL no arquivo .env com a URL do EA20 oficial."
        )

    resposta = sessao.get(RESULT_URL, timeout=(5, 20))
    resposta.raise_for_status()
    dados = resposta.json()

    if not isinstance(dados, dict) or "carg" not in dados:
        raise ValueError("Resposta inesperada: não parece ser um EA20 válido.")

    return extrair_resultado(dados)

def ciclo_atualizacao():
    while True:
        try:
            novo_estado = consultar_tse()
            with lock:
                estado.update(novo_estado)
        except Exception as exc:
            # Preserva os últimos resultados válidos e informa a falha.
            with lock:
                estado["erro"] = str(exc)
                estado["ultima_tentativa"] = datetime.now(
                    ZoneInfo("America/Sao_Paulo")
                ).isoformat(timespec="seconds")
        time.sleep(POLL_SECONDS)

@app.get("/")
def inicio():
    return render_template("index.html")

@app.get("/api/resultados")
def api_resultados():
    with lock:
        resposta = dict(estado)
        resposta["candidatos"] = list(estado["candidatos"])
        resposta["secoes"] = dict(estado["secoes"])
        resposta["eleitores"] = dict(estado["eleitores"])
        resposta["votos"] = dict(estado["votos"])
    return jsonify(resposta)

if __name__ == "__main__":
    thread = threading.Thread(
        target=ciclo_atualizacao,
        daemon=True
    )
    thread.start()

    porta = int(os.getenv("PORT", "5000"))
    app.run(host="127.0.0.1", port=porta, debug=False)
