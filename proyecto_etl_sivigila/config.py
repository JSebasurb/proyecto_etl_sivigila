"""
Configuracion central del pipeline ETL SIVIGILA.

Todo lo que cambia entre corridas o entre anios (rutas, hojas, eventos objetivo,
codigos de evento) vive aqui, para no repetirlo en extract / transform / load.
"""

from pathlib import Path

# ----------------------------------------------------------------------------
# Rutas
# ----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
BRONZE_DIR = DATA_DIR / "bronze"
SILVER_DIR = DATA_DIR / "silver"
GOLD_DIR = DATA_DIR / "gold"
DASHBOARD_DIR = BASE_DIR / "dashboard"

DB_PATH = GOLD_DIR / "sivigila_dw.db"
POBLACION_PATH = RAW_DIR / "poblacion_departamentos.csv"

# ----------------------------------------------------------------------------
# Fuentes crudas
# ----------------------------------------------------------------------------
ANIOS = [2018, 2019, 2020, 2021, 2022]
YEAR_FILES = {a: RAW_DIR / f"rutinaria_{a}.xlsx" for a in ANIOS}

# Hojas candidatas (tabla dinamica evento x departamento x semana) por anio.
SHEET_CANDIDATES = {
    2018: ["EventoDepartamento", "Evento _Departamento_Municpio", "Departamento_municipio_evento"],
    2019: ["Eventos", "Departamentos", "Municipal"],
    2020: ["Por eventos", "Por departamentos", "Por municipios"],
    2021: ["Evento", "Municipio"],
    2022: ["Departamento", "Datos_para_estadistica_rutinari"],
}

# CORRECCION CLAVE (v4): en el libro de 2022 la hoja "Departamento" es una tabla
# dinamica que CUENTA FILAS del detalle en vez de SUMAR la columna `conteo`
# (cada fila del detalle es un municipio-semana con conteo >= 1).
# Resultado: dengue 2022 aparecia con 12.513 casos cuando el real es 65.691.
# Para esos anios se lee la hoja de detalle y se agrega sumando `conteo`.
ANIOS_DESDE_DETALLE = {
    2022: {
        "hoja": "Datos_para_estadistica_rutinari",
        "col_cod": "COD_EVE",
        "col_evento": "Nombre_evento",
        "col_depto": "Departamento_ocurrencia",
        "col_semana": "SEMANA",
        "col_casos": "conteo",
    },
}

COD_EVENTO_CANDIDATES = ["COD_EVE", "Codigo de evento", "Código de Evento"]
EVENTO_CANDIDATES = ["Evento", "evento", "NOM_EVE", "Nombre del evento", "Nombre_evento"]
DEPARTAMENTO_CANDIDATES = [
    "Departamento", "NDEP_PROCE", "Departamento_Ocurrencia",
    "Nombre departamento", "Departamento_ocurrencia",
]
WEEK_PATTERN = r"(?i)^semana[_ ]?(\d{1,2})$"

DEPARTAMENTOS_COLOMBIA = [
    "ANTIOQUIA", "ATLANTICO", "BOGOTA", "BOLIVAR", "BOYACA", "CALDAS", "CAQUETA",
    "CASANARE", "CAUCA", "CESAR", "CHOCO", "CORDOBA", "CUNDINAMARCA", "GUAINIA",
    "GUAJIRA", "GUAVIARE", "HUILA", "MAGDALENA", "META", "NARINO", "NORTE SANTANDER",
    "PUTUMAYO", "QUINDIO", "RISARALDA", "SAN ANDRES", "SANTANDER", "SUCRE", "TOLIMA",
    "VALLE", "VAUPES", "VICHADA", "AMAZONAS", "ARAUCA",
]
DEPARTAMENTOS_NO_TERRITORIALES = {"EXTERIOR", "PROCEDENCIA DESCONOCIDA", "DESCONOCIDO"}

# ----------------------------------------------------------------------------
# Eventos objetivo del analisis (definidos por CODIGO, no por nombre)
# ----------------------------------------------------------------------------
# Los nombres cambian entre anios (tildes, "IRAG", etc.); los codigos son estables.
# La tuberculosis cambio de estructura en 2021: hasta 2020 y en 2022 se reporta
# separada en 820 (pulmonar) y 810 (extrapulmonar); en 2021 aparece un unico
# codigo 813 "TUBERCULOSIS" (todas las formas). Antes de v4 el 813 se renombraba
# como "pulmonar", mezclando series no comparables. Ahora se compara la serie
# "todas las formas": 810+820 (o 813 en 2021).
EVENTOS_OBJETIVO = {
    "DENGUE": {
        "etiqueta": "Dengue",
        "codigos_por_anio": {a: [210] for a in ANIOS},
    },
    "IRAG INUSITADA": {
        "etiqueta": "IRAG inusitada",
        "codigos_por_anio": {a: [348] for a in ANIOS},
    },
    "TUBERCULOSIS (TODAS LAS FORMAS)": {
        "etiqueta": "Tuberculosis (todas las formas)",
        "codigos_por_anio": {2018: [810, 820], 2019: [810, 820], 2020: [810, 820],
                             2021: [813], 2022: [810, 820]},
    },
}

# Codigos de evento que la tabla dinamica trae vacios y que no se pueden recuperar por
# nombre desde otro anio. Clave: (anio, NOMBRE EN MAYUSCULAS SIN TILDES) -> codigo.
CODIGOS_MANUALES: dict[tuple[int, str], int] = {}
