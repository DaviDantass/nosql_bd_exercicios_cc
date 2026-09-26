import logging
import os
import sys
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne
from pymongo.errors import PyMongoError

load_dotenv()

API_URL = os.getenv("CARTOLA_API_URL", "https://api.cartola.globo.com/atletas/mercado")
DATABASE_NAME = os.getenv("MONGO_DB", "cartola_fc_db")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
LOGGER = logging.getLogger(__name__)


def conectar_mongodb():
    """Lê MONGO_URI do ambiente, testa o servidor e retorna o banco de dados."""
    uri = os.getenv("MONGO_URI")
    if not uri:
        raise ValueError("Defina MONGO_URI no arquivo .env.")
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except PyMongoError:
        client.close()
        raise
    return client[DATABASE_NAME]


def buscar_dados_mercado():
    """Busca e decodifica o JSON da API, falhando em HTTP ou formato inválido."""
    response = requests.get(API_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    dados = response.json()
    if not isinstance(dados, dict):
        raise ValueError("A API não retornou um objeto JSON.")
    return dados


def processar_e_gravar_dados(db, dados_mercado):
    """Valida os dados e atualiza clubes, atletas e mapa de status no MongoDB."""
    clubes = dados_mercado.get("clubes")
    atletas = dados_mercado.get("atletas")
    status = dados_mercado.get("status")
    if not isinstance(clubes, dict) or not isinstance(atletas, list) or not isinstance(status, dict):
        raise ValueError("JSON incompleto: esperados clubes (objeto), atletas (lista) e status (objeto).")
    if not clubes or not atletas or not status:
        raise ValueError("Resposta vazia; as coleções existentes foram preservadas.")

    instante = datetime.now(timezone.utc)
    operacoes_clubes = []
    for chave, clube in clubes.items():
        if not isinstance(clube, dict):
            raise ValueError(f"Clube inválido: {chave}")
        clube_id = clube.get("id")
        if clube_id is None or str(clube_id) != str(chave):
            raise ValueError(f"ID inválido para o clube {chave}")
        documento = dict(clube)
        documento.pop("id", None)
        operacoes_clubes.append(UpdateOne({"_id": clube_id}, {"$set": documento}, upsert=True))

    ids = [atleta.get("atleta_id") if isinstance(atleta, dict) else None for atleta in atletas]
    if any(identificador is None for identificador in ids) or len(set(ids)) != len(ids):
        raise ValueError("Atletas com ID ausente ou repetido; coleção existente preservada.")
    documentos_atletas = [{**atleta, "timestamp_coleta": instante} for atleta in atletas]
    documento_status = {"_id": "status_atletas", "categorias": status, "timestamp_coleta": instante}

    LOGGER.info("Gravando dados dos clubes...")
    db.clubes_rodada_atual.bulk_write(operacoes_clubes, ordered=True)
    LOGGER.info("%d clubes atualizados", len(operacoes_clubes))

    LOGGER.info("Gravando dados dos atletas...")
    db.atletas_rodada_atual.delete_many({})
    db.atletas_rodada_atual.insert_many(documentos_atletas)
    LOGGER.info("%d atletas gravados", len(documentos_atletas))

    LOGGER.info("Gravando categorias de status dos atletas...")
    db.mercado_rodada_atual.replace_one({"_id": "status_atletas"}, documento_status, upsert=True)
    LOGGER.info("Finalizado com sucesso.")


def main():
    """Orquestra conexão, extração e carga e fecha a conexão ao finalizar."""
    LOGGER.info("Conectando ao MongoDB...")
    db = conectar_mongodb()
    try:
        LOGGER.info("Buscando dados na API...")
        dados = buscar_dados_mercado()
        processar_e_gravar_dados(db, dados)
    finally:
        db.client.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        main()
    except (requests.RequestException, PyMongoError, ValueError) as erro:
        LOGGER.error("Coleta interrompida: %s", erro)
        sys.exit(1)
