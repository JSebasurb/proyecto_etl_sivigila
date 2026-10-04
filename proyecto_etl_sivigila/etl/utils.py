"""Utilidades compartidas: logging, normalizacion de texto y calendario epidemiologico."""

import logging
import unicodedata
from datetime import date, timedelta

import pandas as pd


def get_logger(nombre: str) -> logging.Logger:
    """Logger con formato uniforme. basicConfig se configura una sola vez."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)-7s | %(name)-9s | %(message)s",
            datefmt="%H:%M:%S",
        )
    return logging.getLogger(nombre)


def normalizar_texto(serie: pd.Series) -> pd.Series:
    """MAYUSCULAS, sin tildes ni espacios sobrantes (NARIÑO -> NARINO)."""
    def _norm(x):
        if pd.isna(x):
            return x
        x = str(x).strip().upper()
        return unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode("ascii")
    return serie.map(_norm)


# ----------------------------------------------------------------------------
# Calendario epidemiologico (SIVIGILA usa semanas epidemiologicas tipo MMWR:
# domingo a sabado; la semana 1 es la primera que contiene al menos 4 dias de enero)
# ----------------------------------------------------------------------------

def inicio_semana_epi(anio: int, semana: int) -> date:
    """Fecha (domingo) en que inicia la semana epidemiologica `semana` del `anio`."""
    ene1 = date(anio, 1, 1)
    dow = (ene1.weekday() + 1) % 7          # domingo=0 ... sabado=6
    inicio_s1 = ene1 - timedelta(days=dow) if dow <= 3 else ene1 + timedelta(days=7 - dow)
    return inicio_s1 + timedelta(weeks=semana - 1)


def semana_a_mes(anio: int, semana: int) -> int:
    """Mes calendario al que pertenece la semana epidemiologica (por su miercoles)."""
    return (inicio_semana_epi(anio, semana) + timedelta(days=3)).month
