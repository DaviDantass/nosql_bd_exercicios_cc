import logging
import os
import sys

import requests
from dotenv import load_dotenv
from pymongo import ASCENDING, MongoClient
from pymongo.errors import PyMongoError

load_dotenv()

API_BASE_URL = os.getenv("OPENF1_BASE_URL", "https://api.openf1.org/v1").rstrip("/")
MONGO_URI = os.getenv("MONGO_URI")
DATABASE_NAME = os.getenv("MONGO_DB", "openf1_data")
SESSION_KEY = int(os.getenv("SESSION_KEY", "9159"))
MEETING_KEY = int(os.getenv("MEETING_KEY", "1219"))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "30"))

logger = logging.getLogger(__name__)
_db = None


def connect_to_mongodb():
    """Lê MONGO_URI do .env, verifica a conexão e retorna o banco de dados."""
    if not MONGO_URI:
        raise ValueError("Defina MONGO_URI no arquivo .env ou no ambiente.")
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except PyMongoError:
        client.close()
        raise
    return client[DATABASE_NAME]


def fetch_data(endpoint: str, params: dict) -> list:
    """Consulta um endpoint da OpenF1 e exige uma lista JSON como resposta."""
    response = requests.get(
        f"{API_BASE_URL}/{endpoint.lstrip('/')}",
        params=params,
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, list):
        raise ValueError(f"Resposta inesperada de {endpoint}: era esperada uma lista.")
    return result


def save_to_collection(data: list, collection_name: str, unique_keys: list):
    """Cria índice único e atualiza cada documento pela combinação de chaves."""
    if _db is None:
        raise RuntimeError("Conecte ao MongoDB antes de salvar os dados.")
    collection = _db[collection_name]
    collection.create_index(
        [(key, ASCENDING) for key in unique_keys],
        unique=True,
        name="unique_" + "_".join(unique_keys),
    )
    for document in data:
        if any(document.get(key) is None for key in unique_keys):
            raise ValueError(f"Documento de {collection_name} sem chave única: {document}")
        selector = {key: document[key] for key in unique_keys}
        collection.update_one(selector, {"$set": document}, upsert=True)
    logger.info("%s: %d registros processados", collection_name, len(data))


def main():
    """Busca a sessão, seus pilotos e suas voltas; salva cada conjunto."""
    global _db
    _db = connect_to_mongodb()
    try:
        sessions = fetch_data("sessions", {"session_key": SESSION_KEY})
        if len(sessions) != 1:
            raise ValueError(f"Esperada uma sessão para a chave {SESSION_KEY}; recebidas {len(sessions)}.")
        session = sessions[0]
        if session.get("meeting_key") != MEETING_KEY:
            raise ValueError(
                f"A sessão {SESSION_KEY} pertence ao meeting_key "
                f"{session.get('meeting_key')}, não ao {MEETING_KEY} configurado."
            )
        logger.info(
            "Sessão encontrada: %s, %s, %s (meeting_key=%s)",
            session.get("session_name"), session.get("country_name"),
            session.get("year"), session.get("meeting_key"),
        )
        drivers = fetch_data("drivers", {"session_key": SESSION_KEY})
        laps = fetch_data("laps", {"session_key": SESSION_KEY})

        save_to_collection(sessions, "sessions", ["session_key"])
        save_to_collection(drivers, "drivers", ["session_key", "driver_number"])
        save_to_collection(laps, "laps", ["session_key", "driver_number", "lap_number"])
    finally:
        _db.client.close()
        _db = None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        main()
    except (requests.RequestException, PyMongoError, ValueError, RuntimeError) as exc:
        logger.error("Coleta interrompida: %s", exc)
        sys.exit(1)
