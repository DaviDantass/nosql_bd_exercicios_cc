import io
import json
import logging
import os
import sys
from datetime import datetime, timezone

import geopandas as gpd
import pandas as pd
import requests
from dotenv import load_dotenv
from pymongo import GEOSPHERE, MongoClient, UpdateOne
from pymongo.errors import PyMongoError

load_dotenv()
LOG = logging.getLogger(__name__)
SOURCE_URL = os.getenv(
    "SOURCE_URL",
    "https://servicodados.ibge.gov.br/api/v3/malhas/estados/25?formato=application/vnd.geo%2Bjson",
)
SOURCE_FORMAT = os.getenv("SOURCE_FORMAT", "geojson").lower()
MONGO_DB = os.getenv("MONGO_DB", "georreferencia_db")
COLLECTION = os.getenv("MONGO_COLLECTION", "locais")
ID_COLUMN = os.getenv("ID_COLUMN", "codarea")
LAT_COLUMN = os.getenv("LAT_COLUMN", "latitude")
LON_COLUMN = os.getenv("LON_COLUMN", "longitude")
CSV_SEPARATOR = os.getenv("CSV_SEPARATOR", ",")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))


def conectar_mongodb():
    """Abre o banco indicado pelo .env e confirma que o servidor responde."""
    uri = os.getenv("MONGO_URI")
    if not uri:
        raise ValueError("Defina MONGO_URI no .env")
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except PyMongoError:
        client.close()
        raise
    return client[MONGO_DB]


def baixar_dados():
    """Baixa os bytes da fonte e interrompe o processo se o HTTP falhar."""
    resposta = requests.get(SOURCE_URL, timeout=REQUEST_TIMEOUT)
    resposta.raise_for_status()
    return resposta.content


def transformar_geojson(conteudo):
    """Lê uma FeatureCollection, valida o CRS e converte para WGS84."""
    objeto = json.loads(conteudo)
    if objeto.get("type") != "FeatureCollection" or not isinstance(objeto.get("features"), list):
        raise ValueError("A fonte não retornou uma FeatureCollection GeoJSON")
    if not objeto["features"]:
        raise ValueError("GeoJSON vazio; a coleção foi preservada")
    features = gpd.GeoDataFrame.from_features(objeto["features"], crs="EPSG:4326")
    return features.to_crs("EPSG:4326")


def transformar_csv(conteudo):
    """Converte colunas de latitude/longitude em pontos GeoJSON WGS84."""
    tabela = pd.read_csv(io.BytesIO(conteudo), sep=CSV_SEPARATOR, low_memory=False)
    for coluna in (ID_COLUMN, LAT_COLUMN, LON_COLUMN):
        if coluna not in tabela.columns:
            raise ValueError(f"Coluna ausente no CSV: {coluna}")
    tabela[LAT_COLUMN] = pd.to_numeric(tabela[LAT_COLUMN].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    tabela[LON_COLUMN] = pd.to_numeric(tabela[LON_COLUMN].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    tabela = tabela.dropna(subset=[ID_COLUMN, LAT_COLUMN, LON_COLUMN])
    tabela = tabela[tabela[LAT_COLUMN].between(-90, 90) & tabela[LON_COLUMN].between(-180, 180)]
    if tabela.empty:
        raise ValueError("CSV sem coordenadas válidas; a coleção foi preservada")
    return gpd.GeoDataFrame(
        tabela, geometry=gpd.points_from_xy(tabela[LON_COLUMN], tabela[LAT_COLUMN]), crs="EPSG:4326"
    )


def salvar_dados(db, geodados):
    """Faz upsert por ID e cria um índice 2dsphere na geometria GeoJSON."""
    if ID_COLUMN not in geodados.columns:
        raise ValueError(f"Campo de identificação ausente: {ID_COLUMN}")
    if geodados[ID_COLUMN].isna().any() or geodados[ID_COLUMN].duplicated().any():
        raise ValueError("IDs ausentes ou duplicados; a coleção foi preservada")
    if geodados.geometry.isna().any() or geodados.geometry.is_empty.any():
        raise ValueError("Geometrias ausentes ou vazias; a coleção foi preservada")
    # A coleção recebe somente geometrias válidas. Para polígonos, corrige anéis simples.
    if not geodados.geometry.is_valid.all():
        raise ValueError("Geometria inválida na fonte; a coleção foi preservada")
    colecao = db[COLLECTION]
    instante = datetime.now(timezone.utc)
    operacoes = []
    # to_json serializa NaN e tipos numéricos do pandas em JSON compatível.
    for feature in json.loads(geodados.to_json(drop_id=True))["features"]:
        propriedades = feature["properties"]
        identificador = str(propriedades.pop(ID_COLUMN))
        documento = {
            "geometry": feature["geometry"],
            "properties": propriedades,
            "fonte": SOURCE_URL,
            "timestamp_coleta": instante,
        }
        operacoes.append(UpdateOne({"_id": identificador}, {"$set": documento}, upsert=True))
    colecao.create_index([("geometry", GEOSPHERE)], name="geometry_2dsphere")
    resultado = colecao.bulk_write(operacoes, ordered=False)
    LOG.info("%d registros processados (%d novos, %d atualizados)", len(operacoes), resultado.upserted_count, resultado.modified_count)


def main():
    """Orquestra extração, transformação e gravação com fechamento da conexão."""
    if SOURCE_FORMAT not in {"geojson", "csv"}:
        raise ValueError("SOURCE_FORMAT deve ser geojson ou csv")
    LOG.info("Baixando dados: %s", SOURCE_URL)
    conteudo = baixar_dados()
    geodados = transformar_geojson(conteudo) if SOURCE_FORMAT == "geojson" else transformar_csv(conteudo)
    LOG.info("%d geometrias prontas para gravação", len(geodados))
    db = conectar_mongodb()
    try:
        salvar_dados(db, geodados)
    finally:
        db.client.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        main()
    except (requests.RequestException, PyMongoError, ValueError, json.JSONDecodeError, pd.errors.ParserError) as erro:
        LOG.error("Coleta interrompida: %s", erro)
        sys.exit(1)
