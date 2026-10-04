"""
================================================================================
 CAPA GOLD - LOAD
================================================================================
Toma Silver, calcula indicadores y carga el destino analitico:

  * data/gold/sivigila_dw.db  (SQLite, esquema estrella real con PK/FK/indices)
        dim_evento, dim_departamento, dim_tiempo, poblacion_departamento,
        hecho_casos, evento_objetivo_map, indicadores_cv, calidad_datos,
        vistas v_casos_objetivo y v_casos_mensuales
  * data/gold/casos_objetivo.csv         casos semanales de los eventos objetivo
  * data/gold/indicadores_cv.csv         coeficientes de variacion
  * data/gold/reporte_calidad_datos.json reporte de calidad (copia de Silver)

hecho_casos.csv completo (identico a Silver, ~33 MB) ya NO se duplica en Gold;
usa --exportar-hecho-completo si lo necesitas.
================================================================================
"""

import json
import sqlite3

import pandas as pd

import config as cfg
from etl.utils import get_logger, inicio_semana_epi

log = get_logger("load")

DDL = """
PRAGMA foreign_keys = ON;

DROP VIEW  IF EXISTS v_casos_objetivo;
DROP VIEW  IF EXISTS v_casos_mensuales;
DROP TABLE IF EXISTS hecho_casos;
DROP TABLE IF EXISTS evento_objetivo_map;
DROP TABLE IF EXISTS poblacion_departamento;
DROP TABLE IF EXISTS dim_evento;
DROP TABLE IF EXISTS dim_departamento;
DROP TABLE IF EXISTS dim_tiempo;
DROP TABLE IF EXISTS indicadores_cv;
DROP TABLE IF EXISTS calidad_datos;

CREATE TABLE dim_evento (
    evento_id  INTEGER PRIMARY KEY,
    cod_evento INTEGER NOT NULL UNIQUE,
    evento     TEXT    NOT NULL
);
CREATE TABLE dim_departamento (
    departamento_id INTEGER PRIMARY KEY,
    departamento    TEXT    NOT NULL UNIQUE,
    es_territorial  INTEGER NOT NULL CHECK (es_territorial IN (0, 1))
);
CREATE TABLE dim_tiempo (
    tiempo_id           INTEGER PRIMARY KEY,
    anio                INTEGER NOT NULL,
    semana              INTEGER NOT NULL CHECK (semana BETWEEN 1 AND 53),
    mes                 INTEGER NOT NULL CHECK (mes BETWEEN 1 AND 12),
    fecha_inicio_semana TEXT    NOT NULL,
    UNIQUE (anio, semana)
);
CREATE TABLE poblacion_departamento (
    departamento_id INTEGER NOT NULL REFERENCES dim_departamento(departamento_id),
    anio            INTEGER NOT NULL,
    poblacion       INTEGER,
    PRIMARY KEY (departamento_id, anio)
);
CREATE TABLE hecho_casos (
    evento_id       INTEGER NOT NULL REFERENCES dim_evento(evento_id),
    departamento_id INTEGER NOT NULL REFERENCES dim_departamento(departamento_id),
    tiempo_id       INTEGER NOT NULL REFERENCES dim_tiempo(tiempo_id),
    casos           INTEGER NOT NULL CHECK (casos >= 0),
    PRIMARY KEY (evento_id, departamento_id, tiempo_id)
);
CREATE INDEX ix_hecho_tiempo ON hecho_casos(tiempo_id);
CREATE INDEX ix_hecho_depto  ON hecho_casos(departamento_id);

CREATE TABLE evento_objetivo_map (
    grupo      TEXT    NOT NULL,
    anio       INTEGER NOT NULL,
    cod_evento INTEGER NOT NULL,
    PRIMARY KEY (grupo, anio, cod_evento)
);
CREATE TABLE indicadores_cv (
    grupo            TEXT    NOT NULL,
    anio             INTEGER NOT NULL,
    n_meses          INTEGER,
    media_mensual    REAL,
    sd_mensual       REAL,
    cv_mensual_pct   REAL,
    n_semanas        INTEGER,
    media_semanal    REAL,
    sd_semanal       REAL,
    cv_semanal_pct   REAL,
    PRIMARY KEY (grupo, anio)
);
CREATE TABLE calidad_datos (
    metrica TEXT PRIMARY KEY,
    valor   TEXT
);

CREATE VIEW v_casos_objetivo AS
SELECT m.grupo, t.anio, t.semana, t.mes, d.departamento, d.es_territorial,
       SUM(h.casos) AS casos
FROM hecho_casos h
JOIN dim_evento e         ON e.evento_id = h.evento_id
JOIN dim_tiempo t         ON t.tiempo_id = h.tiempo_id
JOIN dim_departamento d   ON d.departamento_id = h.departamento_id
JOIN evento_objetivo_map m ON m.cod_evento = e.cod_evento AND m.anio = t.anio
GROUP BY m.grupo, t.anio, t.semana, t.mes, d.departamento, d.es_territorial;

CREATE VIEW v_casos_mensuales AS
SELECT e.cod_evento, e.evento, t.anio, t.mes, SUM(h.casos) AS casos
FROM hecho_casos h
JOIN dim_evento e ON e.evento_id = h.evento_id
JOIN dim_tiempo t ON t.tiempo_id = h.tiempo_id
GROUP BY e.cod_evento, e.evento, t.anio, t.mes;
"""


def cargar_silver() -> tuple[pd.DataFrame, dict]:
    ruta_silver = cfg.SILVER_DIR / "sivigila_silver.csv"
    ruta_reporte = cfg.SILVER_DIR / "reporte_calidad.json"
    if not (ruta_silver.exists() and ruta_reporte.exists()):
        raise FileNotFoundError("No se encontro Silver. Corre primero: python run_pipeline.py --desde transform")
    silver = pd.read_csv(ruta_silver)
    with open(ruta_reporte, encoding="utf-8") as f:
        reporte = json.load(f)
    log.info(f"Silver leido: {len(silver)} filas")
    return silver, reporte


# ----------------------------------------------------------------------------
# Eventos objetivo e indicadores
# ----------------------------------------------------------------------------

def construir_objetivo(silver: pd.DataFrame) -> pd.DataFrame:
    """Casos semanales por departamento de los eventos objetivo (por codigo y anio)."""
    piezas = []
    for grupo, spec in cfg.EVENTOS_OBJETIVO.items():
        for anio, cods in spec["codigos_por_anio"].items():
            sub = silver[(silver["anio"] == anio) & silver["cod_evento"].isin(cods)]
            if sub.empty:
                continue
            g = (sub.groupby(["anio", "semana", "mes", "departamento", "es_territorial", "poblacion"],
                             as_index=False, dropna=False)["casos"].sum())
            g.insert(0, "evento", grupo)
            piezas.append(g)
    obj = pd.concat(piezas, ignore_index=True)
    obj["tasa_incidencia_100k"] = (obj["casos"] / obj["poblacion"] * 100_000).where(obj["poblacion"] > 0)
    return obj.sort_values(["evento", "anio", "departamento", "semana"]).reset_index(drop=True)


def calcular_indicadores_cv(objetivo: pd.DataFrame) -> pd.DataFrame:
    """CV (nacional, todas las procedencias) mensual y semanal por evento y anio."""
    def _cv(s: pd.Series) -> tuple[float, float, float]:
        media, sd = s.mean(), s.std(ddof=1)
        return media, sd, (100 * sd / media if media else float("nan"))

    filas = []
    for (grupo, anio), g in objetivo.groupby(["evento", "anio"]):
        mensual = g.groupby("mes")["casos"].sum()
        semanal = g.groupby("semana")["casos"].sum()
        mm, ms, mc = _cv(mensual)
        sm, ss, sc = _cv(semanal)
        filas.append({"grupo": grupo, "anio": int(anio), "n_meses": len(mensual),
                      "media_mensual": round(mm, 2), "sd_mensual": round(ms, 2), "cv_mensual_pct": round(mc, 1),
                      "n_semanas": len(semanal), "media_semanal": round(sm, 2),
                      "sd_semanal": round(ss, 2), "cv_semanal_pct": round(sc, 1)})
    return pd.DataFrame(filas)


# ----------------------------------------------------------------------------
# Carga a SQLite (esquema estrella)
# ----------------------------------------------------------------------------

def cargar_a_sqlite(silver: pd.DataFrame, cv: pd.DataFrame, reporte: dict) -> None:
    cfg.GOLD_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(cfg.DB_PATH)
    try:
        con.executescript(DDL)

        dim_evento = (silver[["cod_evento", "evento"]].drop_duplicates("cod_evento")
                      .sort_values("cod_evento").reset_index(drop=True))
        dim_evento.insert(0, "evento_id", range(1, len(dim_evento) + 1))

        dim_depto = (silver[["departamento", "es_territorial"]].drop_duplicates("departamento")
                     .sort_values("departamento").reset_index(drop=True))
        dim_depto.insert(0, "departamento_id", range(1, len(dim_depto) + 1))
        dim_depto["es_territorial"] = dim_depto["es_territorial"].astype(int)

        dim_t = silver[["anio", "semana", "mes"]].drop_duplicates().sort_values(["anio", "semana"]).reset_index(drop=True)
        dim_t["fecha_inicio_semana"] = [inicio_semana_epi(a, s).isoformat() for a, s in zip(dim_t["anio"], dim_t["semana"])]
        dim_t.insert(0, "tiempo_id", range(1, len(dim_t) + 1))

        pob = (silver[["departamento", "anio", "poblacion"]].dropna().drop_duplicates()
               .merge(dim_depto[["departamento_id", "departamento"]], on="departamento")
               [["departamento_id", "anio", "poblacion"]])
        pob["poblacion"] = pob["poblacion"].astype(int)

        hechos = (silver.merge(dim_evento[["evento_id", "cod_evento"]], on="cod_evento")
                        .merge(dim_depto[["departamento_id", "departamento"]], on="departamento")
                        .merge(dim_t[["tiempo_id", "anio", "semana"]], on=["anio", "semana"])
                  [["evento_id", "departamento_id", "tiempo_id", "casos"]])
        assert len(hechos) == len(silver), "El join a dimensiones perdio o duplico filas"

        mapa = pd.DataFrame([{"grupo": g, "anio": a, "cod_evento": c}
                             for g, spec in cfg.EVENTOS_OBJETIVO.items()
                             for a, cods in spec["codigos_por_anio"].items() for c in cods])

        calidad = pd.DataFrame([{"metrica": k, "valor": json.dumps(v, ensure_ascii=False)
                                 if isinstance(v, (list, dict)) else str(v)} for k, v in reporte.items()])

        for nombre, df in [("dim_evento", dim_evento), ("dim_departamento", dim_depto), ("dim_tiempo", dim_t),
                           ("poblacion_departamento", pob), ("hecho_casos", hechos),
                           ("evento_objetivo_map", mapa), ("indicadores_cv", cv), ("calidad_datos", calidad)]:
            df.to_sql(nombre, con, if_exists="append", index=False, chunksize=50_000)

        con.commit()
        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise RuntimeError(f"Violaciones de llave foranea en el DW: {fk[:5]}")
        log.info(f"SQLite escrito en {cfg.DB_PATH} ({len(hechos)} filas en hecho_casos, integridad FK ok)")
    finally:
        con.close()


def exportar_csv_json(objetivo: pd.DataFrame, cv: pd.DataFrame, reporte: dict,
                      silver: pd.DataFrame, completo: bool) -> None:
    objetivo.to_csv(cfg.GOLD_DIR / "casos_objetivo.csv", index=False)
    cv.to_csv(cfg.GOLD_DIR / "indicadores_cv.csv", index=False)
    with open(cfg.GOLD_DIR / "reporte_calidad_datos.json", "w", encoding="utf-8") as f:
        json.dump(reporte, f, ensure_ascii=False, indent=2, default=str)
    if completo:
        silver.to_csv(cfg.GOLD_DIR / "hecho_casos.csv", index=False)
    log.info("Exportes CSV/JSON de Gold escritos")


def main(exportar_hecho_completo: bool = False):
    log.info("=" * 60)
    log.info("INICIO CAPA GOLD (load)")

    silver, reporte = cargar_silver()
    objetivo = construir_objetivo(silver)
    cv = calcular_indicadores_cv(objetivo)
    cargar_a_sqlite(silver, cv, reporte)
    exportar_csv_json(objetivo, cv, reporte, silver, exportar_hecho_completo)

    log.info("FIN CAPA GOLD")
    return objetivo, cv, reporte


if __name__ == "__main__":
    main()
