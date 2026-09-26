from datetime import datetime

import pandas as pd
import streamlit as st
from pymongo.errors import PyMongoError

from db_utils import buscar_voltas, conectar_mongodb, listar_pilotos, listar_sessoes

st.set_page_config(page_title="OpenF1 Data Explorer", page_icon="🏎️", layout="wide")
st.title("OpenF1 Data Explorer")
st.caption("Sessões, pilotos e voltas já armazenados no MongoDB")


def ano_da_sessao(sessao):
    """Obtém o ano do campo year ou, quando necessário, da data da sessão."""
    if sessao.get("year") is not None:
        return int(sessao["year"])
    data = sessao.get("date_start", "")
    return int(str(data)[:4]) if str(data)[:4].isdigit() else None


def rotulo_sessao(sessao):
    """Identifica a sessão sem presumir que toda sessão é uma corrida."""
    local = sessao.get("location") or sessao.get("circuit_short_name") or sessao.get("country_name") or "Local desconhecido"
    return f"{local} — {sessao.get('session_name', 'Sessão')} (chave {sessao['session_key']})"


try:
    cliente, db = conectar_mongodb()
    try:
        sessoes = listar_sessoes(db)
        if not sessoes:
            st.warning("Nenhuma sessão encontrada. Execute primeiro o coletor OpenF1.")
            st.stop()

        anos = sorted({ano_da_sessao(s) for s in sessoes if ano_da_sessao(s) is not None}, reverse=True)
        if not anos:
            st.error("As sessões não têm ano ou data válidos.")
            st.stop()
        ano = st.sidebar.selectbox("Ano", anos)
        sessoes_do_ano = [s for s in sessoes if ano_da_sessao(s) == ano]
        sessao = st.sidebar.selectbox("Sessão", sessoes_do_ano, format_func=rotulo_sessao)
        session_key = sessao["session_key"]

        st.subheader(rotulo_sessao(sessao))
        coluna1, coluna2, coluna3 = st.columns(3)
        coluna1.metric("País", sessao.get("country_name") or "Não informado")
        coluna2.metric("Circuito", sessao.get("circuit_short_name") or sessao.get("location") or "Não informado")
        data_inicio = str(sessao.get("date_start") or "")
        coluna3.metric("Data de início", data_inicio[:10] if data_inicio else "Não informada")

        pilotos = listar_pilotos(db, session_key)
        if not pilotos:
            st.info("Nenhum piloto encontrado para esta sessão.")
            st.stop()
        nomes = {
            p["driver_number"]: p.get("full_name") or p.get("broadcast_name") or f"Piloto {p['driver_number']}"
            for p in pilotos if p.get("driver_number") is not None
        }
        selecionados = st.multiselect(
            "Pilotos para comparar", options=list(nomes),
            default=list(nomes)[:2], format_func=lambda numero: f"{nomes[numero]} (#{numero})",
        )
        if not selecionados:
            st.info("Selecione ao menos um piloto para visualizar as voltas.")
            st.stop()
        voltas = buscar_voltas(db, session_key, selecionados)
    finally:
        cliente.close()
except (PyMongoError, ValueError) as erro:
    st.error(f"Não foi possível consultar o MongoDB: {erro}")
    st.stop()

if not voltas:
    st.info("Nenhuma volta encontrada para os pilotos selecionados.")
    st.stop()

tabela = pd.DataFrame(voltas)
tabela["lap_number"] = pd.to_numeric(tabela.get("lap_number"), errors="coerce")
tabela["lap_duration"] = pd.to_numeric(tabela.get("lap_duration"), errors="coerce")
tabela["Piloto"] = tabela["driver_number"].map(lambda numero: f"{nomes.get(numero, 'Piloto')} (#{numero})")
grafico = tabela.dropna(subset=["lap_number", "lap_duration"]).copy()

st.subheader("Tempo por volta")
if grafico.empty:
    st.warning("As voltas armazenadas não contêm tempos numéricos para gerar o gráfico.")
else:
    st.line_chart(grafico, x="lap_number", y="lap_duration", color="Piloto", x_label="Volta", y_label="Duração (s)")
st.caption("Voltas sem duração numérica ficam fora do gráfico e continuam na tabela.")
with st.expander(f"Ver tabela de dados ({len(tabela)} voltas)"):
    colunas = [c for c in ["Piloto", "lap_number", "lap_duration", "is_pit_out_lap", "date_start"] if c in tabela.columns]
    st.dataframe(tabela[colunas].rename(columns={"lap_number": "Volta", "lap_duration": "Duração (s)", "is_pit_out_lap": "Saída dos boxes", "date_start": "Início"}), use_container_width=True, hide_index=True)
