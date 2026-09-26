import os

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()


def conectar_mongodb():
    """Retorna cliente e banco, verificando a disponibilidade do MongoDB."""
    uri = os.getenv("MONGO_URI")
    if not uri:
        raise ValueError("Defina MONGO_URI no arquivo .env")
    cliente = MongoClient(uri, serverSelectionTimeoutMS=5000)
    try:
        cliente.admin.command("ping")
    except Exception:
        cliente.close()
        raise
    return cliente, cliente[os.getenv("MONGO_DB", "openf1_data")]


def listar_sessoes(db):
    """Lista as sessões existentes sem expor o _id interno do MongoDB."""
    return list(db.sessions.find({}, {"_id": 0}).sort("session_key", -1))


def listar_pilotos(db, session_key):
    """Busca apenas os pilotos associados à sessão selecionada."""
    return list(db.drivers.find(
        {"session_key": session_key},
        {"_id": 0, "driver_number": 1, "full_name": 1, "broadcast_name": 1},
    ).sort("driver_number", 1))


def buscar_voltas(db, session_key, numeros_pilotos):
    """Busca voltas da sessão e dos pilotos escolhidos, ordenadas por número."""
    if not numeros_pilotos:
        return []
    return list(db.laps.find(
        {"session_key": session_key, "driver_number": {"$in": numeros_pilotos}},
        {"_id": 0, "session_key": 1, "driver_number": 1, "lap_number": 1,
         "lap_duration": 1, "is_pit_out_lap": 1, "date_start": 1},
    ).sort([("lap_number", 1), ("driver_number", 1)]))
