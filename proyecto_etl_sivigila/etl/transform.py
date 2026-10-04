"""
================================================================================
 CAPA SILVER - TRANSFORM
================================================================================
Depura, homologa y enriquece lo que extract.py dejo en Bronze.

Salidas en data/silver/:
    sivigila_silver.csv    dataset limpio (todos los eventos), llave unica
                           (anio, cod_evento, departamento, semana)
    reporte_calidad.json   KPIs de calidad + advertencias

Decisiones (v4):
  * Los eventos se identifican por CODIGO (cod_evento), no por nombre: los nombres
    cambian entre anios (tildes, "IRAG", etc.). El nombre canonico es el del anio
    mas reciente.
  * Las filas de subtotales se detectan por varias senales (departamento vacio,
    codigo no numerico, etiqueta "Total ...") y ANTES de borrarlas se usan para
    reconciliar: suma de departamentos == total reportado por el INS.
  * El mes se calcula con el calendario epidemiologico real (no con una formula
    aproximada de 4,33 semanas por mes).
================================================================================
"""

import json
import re
from datetime import datetime

import numpy as np
import pandas as pd

import config as cfg
from etl.utils import get_logger, normalizar_texto, semana_a_mes

log = get_logger("transform")

CLAVE = ["anio", "cod_evento", "departamento", "semana"]


def cargar_bronze() -> tuple[pd.DataFrame, pd.DataFrame]:
    ruta_bronze = cfg.BRONZE_DIR / "sivigila_bronze.csv"
    ruta_pob = cfg.BRONZE_DIR / "poblacion_bronze.csv"
    if not ruta_bronze.exists() or not ruta_pob.exists():
        raise FileNotFoundError("No se encontro Bronze. Corre primero: python run_pipeline.py --desde extract")
    bronze = pd.read_csv(ruta_bronze, dtype={"cod_evento": str})
    pob = pd.read_csv(ruta_pob)
    log.info(f"Bronze leido: {len(bronze)} filas, {len(pob)} filas de poblacion")
    return bronze, pob


# ----------------------------------------------------------------------------
# Subtotales
# ----------------------------------------------------------------------------

def _codigo_numerico(serie: pd.Series) -> pd.Series:
    """Primer grupo de digitos del codigo ('Total 210' -> 210, 'Total general' -> NaN)."""
    return pd.to_numeric(serie.astype(str).str.extract(r"(\d+)")[0], errors="coerce")


def marcar_subtotales(df: pd.DataFrame) -> pd.Series:
    """True para filas que son totales de la tabla dinamica de origen."""
    cod_txt = df["cod_evento"].astype("string").str.strip()
    evento_txt = df["evento"].astype(str).str.strip().str.upper()
    cod_no_numerico = cod_txt.notna() & ~cod_txt.str.fullmatch(r"\d+").fillna(False)
    return (
        df["departamento"].isna()
        | cod_txt.str.match(r"(?i)^total").fillna(False)
        | evento_txt.str.startswith("TOTAL ")
        | cod_no_numerico
    ).astype(bool)


def reconciliar_totales(df: pd.DataFrame, es_total: pd.Series) -> dict:
    """Contrasta la suma por departamento contra el total reportado (cuando existe)."""
    tot = df[es_total].copy()
    tot = tot[tot["departamento"].isna()]
    tot["cod"] = _codigo_numerico(tot["cod_evento"])
    tot = tot[tot["cod"].notna()]
    # Solo etiquetas explicitas de total por evento (descarta 'Total general')
    tot = tot[tot["evento"].astype(str).str.upper().str.startswith("TOTAL")
              | tot["cod_evento"].astype(str).str.match(r"(?i)^total\s+\d+")]
    if tot.empty:
        return {"n_comparaciones": 0, "n_coinciden": 0, "pct_coinciden": None, "anios": []}

    tot = tot.groupby(["anio", "cod", "semana"], as_index=False)["casos"].sum().rename(columns={"casos": "total"})
    det = df[~es_total].copy()
    det["cod"] = _codigo_numerico(det["cod_evento"])
    det = det.groupby(["anio", "cod", "semana"], as_index=False)["casos"].sum().rename(columns={"casos": "suma"})
    cmp_ = tot.merge(det, on=["anio", "cod", "semana"], how="left")
    cmp_["ok"] = cmp_["total"] == cmp_["suma"]
    n, ok = len(cmp_), int(cmp_["ok"].sum())
    if ok < n:
        malos = cmp_[~cmp_["ok"]].head(5).to_dict("records")
        log.warning(f"Reconciliacion: {n - ok} de {n} totales no coinciden. Ejemplos: {malos}")
    return {"n_comparaciones": n, "n_coinciden": ok, "pct_coinciden": round(ok / n * 100, 2),
            "anios": sorted(int(a) for a in cmp_["anio"].unique())}


# ----------------------------------------------------------------------------
# Transformacion principal
# ----------------------------------------------------------------------------

def transformar(bronze: pd.DataFrame, pob: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    df = bronze.copy()
    filas_bronze = len(df)
    advertencias: list[str] = []
    reporte: dict = {"generado": datetime.now().isoformat(timespec="seconds")}

    # 1) Subtotales: reconciliar y luego retirar
    es_total = marcar_subtotales(df)
    reconciliacion = reconciliar_totales(df, es_total)
    n_subtotales = int(es_total.sum())
    df = df[~es_total].copy()

    # 1b) Eventos sin codigo recuperable (la tabla dinamica de origen lo trae vacio)
    sin_codigo = df["cod_evento"].isna()
    n_sin_codigo = int(sin_codigo.sum())
    eventos_sin_codigo = (df[sin_codigo].groupby(["anio", "evento"]).size().reset_index(name="filas")
                          .to_dict("records")) if n_sin_codigo else []
    df = df[~sin_codigo].copy()

    # 2) Tipos y homologacion de texto
    df["cod_evento"] = df["cod_evento"].astype(int)
    df["evento_original"] = normalizar_texto(df["evento"])
    df["departamento"] = normalizar_texto(df["departamento"])
    df["departamento"] = df["departamento"].replace({"DESCONOCIDO": "PROCEDENCIA DESCONOCIDA"})

    # 3) Nombre canonico por codigo (el del anio mas reciente)
    canon = (df.sort_values("anio").drop_duplicates("cod_evento", keep="last")
               .set_index("cod_evento")["evento_original"])
    nombres_por_cod = df.groupby("cod_evento")["evento_original"].nunique()
    cods_multi = nombres_por_cod[nombres_por_cod > 1]
    df["evento"] = df["cod_evento"].map(canon)

    # 4) Duplicados exactos y colisiones de codigo
    n0 = len(df)
    df = df.drop_duplicates(subset=CLAVE + ["evento_original"])
    n_duplicados = n0 - len(df)
    n1 = len(df)
    df = df.groupby(CLAVE + ["evento"], as_index=False)["casos"].sum()
    n_colapsadas = n1 - len(df)

    # 5) Rangos validos
    invalido = (df["semana"] < 1) | (df["semana"] > 53) | (df["casos"] < 0)
    n_invalidos = int(invalido.sum())
    df = df[~invalido].copy()

    # 6) Territorio y calendario epidemiologico
    df["es_territorial"] = ~df["departamento"].isin(cfg.DEPARTAMENTOS_NO_TERRITORIALES)
    calendario = {(a, s): semana_a_mes(a, s) for a, s in df[["anio", "semana"]].drop_duplicates().itertuples(index=False)}
    df["mes"] = [calendario[(a, s)] for a, s in zip(df["anio"], df["semana"])]

    # 7) Enriquecimiento demografico
    pob = pob.copy()
    pob["departamento"] = normalizar_texto(pob["departamento"])
    df = df.merge(pob, on=["departamento", "anio"], how="left")
    df["tasa_incidencia_100k"] = np.where(
        df["poblacion"].notna() & (df["poblacion"] > 0),
        df["casos"] / df["poblacion"] * 100_000, np.nan)
    pct_con_pob = df["poblacion"].notna().mean() * 100
    sin_pob = sorted(df.loc[df["es_territorial"] & df["poblacion"].isna(), "departamento"].unique())

    # ---- Advertencias de calidad -------------------------------------------
    var_pob = pob.groupby("departamento")["poblacion"].nunique()
    if len(var_pob) and (var_pob <= 1).all():
        advertencias.append(
            "La poblacion es constante en todos los anios (misma cifra por departamento 2018-2022): "
            "las tasas de incidencia no reflejan el crecimiento poblacional. Usar proyecciones DANE por anio.")
    if sin_pob:
        advertencias.append(f"Departamentos territoriales sin poblacion: {sin_pob}")
    if n_sin_codigo:
        detalle = "; ".join(f"{r['anio']} {r['evento']} ({r['filas']} filas)" for r in eventos_sin_codigo)
        advertencias.append(f"{n_sin_codigo} filas de eventos sin codigo en el archivo de origen y sin equivalente en otros anios "
                            f"quedaron fuera del DW: {detalle}. Se pueden recuperar agregando el codigo en config.CODIGOS_MANUALES.")
    if len(cods_multi):
        advertencias.append(f"{len(cods_multi)} codigos de evento aparecen con mas de un nombre entre anios "
                            f"(se unifican por codigo, revisar que sean el mismo evento): {sorted(cods_multi.index.tolist())}")
    if n_colapsadas:
        advertencias.append(f"{n_colapsadas} filas colapsadas por compartir codigo, departamento y semana con otro nombre.")
    for nombre, spec in cfg.EVENTOS_OBJETIVO.items():
        for anio, cods in spec["codigos_por_anio"].items():
            hay = df[(df["anio"] == anio) & df["cod_evento"].isin(cods)]
            if hay.empty:
                advertencias.append(f"Evento objetivo '{nombre}' sin datos en {anio} (codigos {cods}).")
    semanas_por_anio = df.groupby("anio")["semana"].max().to_dict()
    for anio, sem in semanas_por_anio.items():
        if sem < 52:
            advertencias.append(f"{anio}: solo hay datos hasta la semana {sem}.")
    if reconciliacion["n_comparaciones"] and reconciliacion["pct_coinciden"] < 100:
        advertencias.append("Algunos totales de origen no coinciden con la suma por departamento (ver log).")

    reporte.update({
        "filas_bronze": filas_bronze,
        "filas_subtotal_removidas": n_subtotales,
        "filas_sin_codigo_removidas": n_sin_codigo,
        "filas_duplicadas_removidas": n_duplicados,
        "filas_colapsadas_por_codigo_repetido": n_colapsadas,
        "filas_rango_invalido_removidas": n_invalidos,
        "filas_silver": len(df),
        "pct_registros_territorio_valido": round(df["es_territorial"].mean() * 100, 2),
        "pct_registros_con_poblacion": round(pct_con_pob, 2),
        "eventos_unicos": int(df["cod_evento"].nunique()),
        "departamentos_unicos": int(df["departamento"].nunique()),
        "cobertura_anios": sorted(int(a) for a in df["anio"].unique()),
        "semanas_por_anio": {int(k): int(v) for k, v in semanas_por_anio.items()},
        "casos_por_anio": {int(k): int(v) for k, v in df.groupby("anio")["casos"].sum().items()},
        "reconciliacion_totales": reconciliacion,
        "eventos_objetivo": {k: v["codigos_por_anio"] for k, v in cfg.EVENTOS_OBJETIVO.items()},
        "advertencias": advertencias,
    })

    cols = ["anio", "semana", "mes", "cod_evento", "evento", "departamento", "es_territorial",
            "casos", "poblacion", "tasa_incidencia_100k"]
    df = df[cols].sort_values(["anio", "cod_evento", "departamento", "semana"]).reset_index(drop=True)

    log.info(f"{filas_bronze} (Bronze) -> {len(df)} (Silver): {n_subtotales} subtotales, "
             f"{n_duplicados} duplicados, {n_colapsadas} colapsadas, {n_invalidos} invalidas")
    for a in advertencias:
        log.warning(f"CALIDAD: {a}")
    return df, reporte


def main():
    cfg.SILVER_DIR.mkdir(parents=True, exist_ok=True)
    log.info("=" * 60)
    log.info("INICIO CAPA SILVER (transform)")

    bronze, pob = cargar_bronze()
    silver, reporte = transformar(bronze, pob)

    silver.to_csv(cfg.SILVER_DIR / "sivigila_silver.csv", index=False)
    with open(cfg.SILVER_DIR / "reporte_calidad.json", "w", encoding="utf-8") as f:
        json.dump(reporte, f, ensure_ascii=False, indent=2, default=str)

    log.info(f"Silver escrito en {cfg.SILVER_DIR} ({len(silver)} filas)")
    log.info("FIN CAPA SILVER")
    return silver, reporte


if __name__ == "__main__":
    main()
