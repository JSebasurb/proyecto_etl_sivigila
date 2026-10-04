"""
================================================================================
 CAPA BRONZE - EXTRACT
================================================================================
Lee los 5 reportes rutinarios de SIVIGILA y la fuente demografica y los deja en
Bronze con el menor procesamiento posible (solo estructural: ancho -> largo).

Salidas en data/bronze/:
    sivigila_bronze.csv        consolidado largo 2018-2022 (incluye filas de
                               subtotales de origen; se depuran en Silver)
    poblacion_bronze.csv       poblacion por departamento-anio
    metadata_extraccion.json   log por archivo: hoja, filas, casos, advertencias

Estrategias de lectura por anio:
    pivot    -> hoja tabla dinamica (evento x departamento x semanas), la
                que usan 2018-2021.
    detalle  -> 2022: hoja de detalle (municipio-semana) sumando `conteo`.
                La tabla dinamica de 2022 cuenta filas en vez de sumar casos
                (ver config.ANIOS_DESDE_DETALLE), por eso no se usa como fuente.
================================================================================
"""

import json
import re
from datetime import datetime

import numpy as np
import pandas as pd

import config as cfg
from etl.utils import get_logger, normalizar_texto

log = get_logger("extract")

WEEK_RE = re.compile(cfg.WEEK_PATTERN)
COLUMNAS_BRONZE = ["anio", "cod_evento", "evento", "departamento", "semana", "casos"]


# ============================================================================
# Estrategia "pivot": tabla dinamica ancha -> largo
# ============================================================================

def _encontrar_hoja(xls: pd.ExcelFile, candidatos: list[str]) -> str | None:
    hojas = xls.sheet_names
    for c in candidatos:
        if c in hojas:
            return c
    hojas_lower = {h.lower(): h for h in hojas}
    for c in candidatos:
        for hl, h in hojas_lower.items():
            if c.lower() in hl:
                return h
    return None


def _detectar_fila_encabezado(raw: pd.DataFrame, max_filas: int = 15) -> int | None:
    for i in range(min(max_filas, len(raw))):
        valores = [str(v) for v in raw.iloc[i].tolist()]
        if any(v in cfg.COD_EVENTO_CANDIDATES for v in valores) and \
           any(v in cfg.EVENTO_CANDIDATES for v in valores):
            return i
    return None


def _resolver_columna(columnas: list, candidatos: list[str]) -> str | None:
    for c in candidatos:
        if c in columnas:
            return c
    return None


def _reshape_largo(data: pd.DataFrame, anio: int) -> pd.DataFrame:
    data = data.copy()
    # Relleno hacia abajo POR BLOQUE de evento. Un ffill global arrastra el codigo del
    # evento anterior cuando el codigo viene vacio (2021: TB farmacorresistente, malaria,
    # intoxicaciones), fusionando eventos distintos. Un bloque inicia en una fila con
    # nombre de evento (que no sea una etiqueta "Total ...") o con codigo.
    etiqueta_total = data["evento"].astype(str).str.strip().str.upper().str.startswith("TOTAL")
    inicio = (data["evento"].notna() & ~etiqueta_total) | data["cod_evento"].notna()
    bloque = inicio.cumsum()
    data["cod_evento"] = data["cod_evento"].groupby(bloque).ffill()
    data["evento"] = data["evento"].groupby(bloque).ffill()

    col_semanas = [c for c in data.columns if c not in ("cod_evento", "evento", "departamento")]
    largo = data.melt(
        id_vars=["cod_evento", "evento", "departamento"],
        value_vars=col_semanas, var_name="semana_raw", value_name="casos",
    )
    largo["semana"] = largo["semana_raw"].str.extract(r"(\d+)").astype(int)
    largo["casos"] = pd.to_numeric(largo["casos"], errors="coerce").fillna(0).astype(int)
    largo["anio"] = anio
    return largo[COLUMNAS_BRONZE]


def _leer_pivot(anio: int, hoja: str | None = None) -> tuple[pd.DataFrame, dict]:
    ruta = cfg.YEAR_FILES[anio]
    meta: dict = {}
    xls = pd.ExcelFile(ruta)
    hoja = hoja or _encontrar_hoja(xls, cfg.SHEET_CANDIDATES[anio])
    if hoja is None:
        raise ValueError(f"Ninguna hoja candidata encontrada entre {xls.sheet_names}")
    meta["hoja"] = hoja

    raw = pd.read_excel(ruta, sheet_name=hoja, header=None)
    fila_header = _detectar_fila_encabezado(raw)
    if fila_header is None:
        raise ValueError("No se pudo detectar automaticamente la fila de encabezado")
    meta["fila_encabezado"] = fila_header

    data = raw.iloc[fila_header + 1:].copy()
    data.columns = raw.iloc[fila_header]
    data = data.reset_index(drop=True)

    col_cod = _resolver_columna(list(data.columns), cfg.COD_EVENTO_CANDIDATES)
    col_evt = _resolver_columna(list(data.columns), cfg.EVENTO_CANDIDATES)
    col_dep = _resolver_columna(list(data.columns), cfg.DEPARTAMENTO_CANDIDATES)
    col_semanas = [c for c in data.columns if isinstance(c, str) and WEEK_RE.match(c)]

    faltantes = [n for n, c in [("evento/codigo", col_cod), ("evento/nombre", col_evt),
                                ("departamento", col_dep)] if c is None]
    if not col_semanas:
        faltantes.append("semanas")
    if faltantes:
        raise ValueError(f"Esquema invalido, columnas no encontradas: {faltantes}")

    meta["filas_crudas"] = len(data)
    meta["n_semanas_detectadas"] = len(col_semanas)
    meta["columnas_resueltas"] = {"evento_cod": col_cod, "evento_nombre": col_evt, "departamento": col_dep}

    data = data.rename(columns={col_cod: "cod_evento", col_evt: "evento", col_dep: "departamento"})
    data = data[["cod_evento", "evento", "departamento"] + col_semanas]
    return _reshape_largo(data, anio), meta


# ============================================================================
# Estrategia "detalle": hoja municipio-semana, suma de `conteo`
# ============================================================================

def _leer_detalle(anio: int) -> tuple[pd.DataFrame, dict]:
    spec = cfg.ANIOS_DESDE_DETALLE[anio]
    ruta = cfg.YEAR_FILES[anio]
    det = pd.read_excel(ruta, sheet_name=spec["hoja"], dtype=str)

    faltan = [spec[k] for k in ("col_cod", "col_evento", "col_depto", "col_semana", "col_casos")
              if spec[k] not in det.columns]
    if faltan:
        raise ValueError(f"Hoja de detalle sin columnas esperadas: {faltan}")

    det = det.rename(columns={
        spec["col_cod"]: "cod_evento", spec["col_evento"]: "evento",
        spec["col_depto"]: "departamento", spec["col_semana"]: "semana", spec["col_casos"]: "casos",
    })
    det["semana"] = pd.to_numeric(det["semana"], errors="coerce")
    det["casos"] = pd.to_numeric(det["casos"], errors="coerce")
    sin_dato = det["semana"].isna() | det["casos"].isna() | det["departamento"].isna()
    n_descartadas = int(sin_dato.sum())
    det = det[~sin_dato].copy()
    det["semana"] = det["semana"].astype(int)

    meta = {
        "hoja": spec["hoja"], "filas_detalle": len(det) + n_descartadas,
        "filas_detalle_descartadas": n_descartadas,
        "casos_detalle": int(det["casos"].sum()),
    }

    # Agregar: evento x departamento x semana
    agg = (det.groupby(["cod_evento", "evento", "departamento", "semana"], as_index=False)["casos"].sum())

    # Densificar a semana completa (las tablas dinamicas traen ceros explicitos):
    # para cada par (evento, departamento) observado se generan todas las semanas.
    max_sem = int(agg["semana"].max())
    pares = agg[["cod_evento", "evento", "departamento"]].drop_duplicates()
    semanas = pd.DataFrame({"semana": range(1, max_sem + 1)})
    base = pares.merge(semanas, how="cross")
    largo = base.merge(agg, on=["cod_evento", "evento", "departamento", "semana"], how="left")
    largo["casos"] = largo["casos"].fillna(0).astype(int)
    largo["anio"] = anio
    meta["n_semanas_detectadas"] = max_sem
    meta["filas_crudas"] = len(pares)
    return largo[COLUMNAS_BRONZE], meta


def _casos_pivot_2022(anio: int) -> int | None:
    """Total que reporta la tabla dinamica (solo para documentar la discrepancia)."""
    try:
        piv, _ = _leer_pivot(anio)
        piv = piv[piv["departamento"].notna()]
        return int(piv["casos"].sum())
    except Exception as e:  # informativo, nunca debe frenar la extraccion
        log.warning(f"{anio}: no se pudo leer la tabla dinamica para contrastar ({e})")
        return None


def _imputar_codigos_faltantes(bronze: pd.DataFrame, metadata: dict) -> pd.DataFrame:
    """Recupera el codigo de evento cuando la tabla dinamica lo trae vacio.

    Busca el mismo nombre (sin tildes) en otros anios y toma el codigo del anio mas
    cercano; si no existe, usa cfg.CODIGOS_MANUALES. Lo imputado queda en la metadata.
    """
    bronze = bronze.copy()
    faltan = bronze["cod_evento"].isna() & bronze["evento"].notna()
    if not faltan.any():
        return bronze

    es_num = bronze["cod_evento"].astype(str).str.fullmatch(r"\d+")
    ref = bronze.loc[es_num, ["anio", "cod_evento", "evento"]].drop_duplicates().copy()
    ref["nombre"] = normalizar_texto(ref["evento"])

    bronze["_nombre"] = normalizar_texto(bronze["evento"])
    pendientes = bronze.loc[faltan, ["anio", "_nombre"]].drop_duplicates()
    for anio, nombre in pendientes.itertuples(index=False):
        cand = ref[ref["nombre"] == nombre].copy()
        origen = "mismo nombre en otro anio"
        if cand.empty:
            cod = cfg.CODIGOS_MANUALES.get((anio, nombre))
            origen = "config.CODIGOS_MANUALES"
        else:
            cand["dist"] = (cand["anio"] - anio).abs()
            cod = cand.sort_values("dist").iloc[0]["cod_evento"]
        mask = faltan & (bronze["anio"] == anio) & (bronze["_nombre"] == nombre)
        meta = metadata.setdefault(int(anio), {}).setdefault("codigos_imputados", [])
        if cod is None:
            log.warning(f"{anio}: '{nombre}' sin codigo y sin equivalente; sus {int(mask.sum())} filas quedaran fuera de Silver (ver advertencias del reporte de calidad)")
            meta.append({"evento": nombre, "cod_evento": None, "origen": "SIN RESOLVER"})
            continue
        bronze.loc[mask, "cod_evento"] = str(cod)
        meta.append({"evento": nombre, "cod_evento": str(cod), "origen": origen})
        log.warning(f"{anio}: '{nombre}' venia sin codigo -> {cod} ({origen})")
    return bronze.drop(columns="_nombre")


# ============================================================================
# Orquestacion de la extraccion SIVIGILA
# ============================================================================

def extraer_sivigila() -> tuple[pd.DataFrame, dict]:
    piezas, metadata = [], {}

    for anio, ruta in cfg.YEAR_FILES.items():
        meta = {"archivo": ruta.name, "anio": anio, "estado": "pendiente"}
        try:
            if not ruta.exists():
                raise FileNotFoundError(f"No se encontro el archivo {ruta}")
            meta["tamano_kb"] = round(ruta.stat().st_size / 1024, 1)
            meta["modificado"] = datetime.fromtimestamp(ruta.stat().st_mtime).isoformat(timespec="seconds")

            if anio in cfg.ANIOS_DESDE_DETALLE:
                meta["estrategia"] = "detalle"
                largo, extra = _leer_detalle(anio)
                meta.update(extra)
                casos_pivot = _casos_pivot_2022(anio)
                if casos_pivot is not None:
                    meta["casos_tabla_dinamica"] = casos_pivot
                    if casos_pivot != meta["casos_detalle"]:
                        meta["advertencia"] = (
                            f"La tabla dinamica reporta {casos_pivot:,} casos pero el detalle suma "
                            f"{meta['casos_detalle']:,}: la tabla cuenta filas, no suma 'conteo'. "
                            f"Se usa el detalle."
                        )
                        log.warning(f"{anio}: {meta['advertencia']}")
            else:
                meta["estrategia"] = "pivot"
                largo, extra = _leer_pivot(anio)
                meta.update(extra)

            piezas.append(largo)
            meta["filas_largo"] = len(largo)
            meta["estado"] = "ok"
            log.info(f"{anio}: OK [{meta['estrategia']}] hoja '{meta['hoja']}' -> {len(largo)} filas largo")

        except Exception as e:
            meta["estado"] = "error"
            meta["error"] = str(e)
            log.error(f"{anio}: FALLO -> {e}")

        metadata[anio] = meta

    bronze = pd.concat(piezas, ignore_index=True) if piezas else pd.DataFrame(columns=COLUMNAS_BRONZE)
    bronze["cod_evento"] = bronze["cod_evento"].astype("string").str.strip().replace({"": pd.NA})
    bronze = _imputar_codigos_faltantes(bronze, metadata)
    return bronze, metadata


# ============================================================================
# Fuente demografica
# ============================================================================

def extraer_demografia() -> pd.DataFrame:
    if cfg.POBLACION_PATH.exists():
        pob = pd.read_csv(cfg.POBLACION_PATH)
    else:
        filas = [{"departamento": d, "anio": a, "poblacion": np.nan}
                 for d in sorted(cfg.DEPARTAMENTOS_COLOMBIA) for a in cfg.ANIOS]
        pob = pd.DataFrame(filas)
        plantilla = cfg.BRONZE_DIR / "poblacion_departamentos_PLANTILLA.csv"
        pob.to_csv(plantilla, index=False)
        log.warning(f"No existe {cfg.POBLACION_PATH.name}; plantilla vacia en {plantilla.name}. "
                    f"Completa 'poblacion' (proyecciones DANE por anio) y guardala en {cfg.POBLACION_PATH}.")

    cobertura = pob["poblacion"].notna().mean() * 100
    log.info(f"Demografia: {cobertura:.1f}% de celdas departamento-anio con poblacion")
    return pob


# ============================================================================
# Punto de entrada de la etapa
# ============================================================================

def main():
    cfg.RAW_DIR.mkdir(parents=True, exist_ok=True)
    cfg.BRONZE_DIR.mkdir(parents=True, exist_ok=True)
    log.info("=" * 60)
    log.info("INICIO CAPA BRONZE (extract)")

    bronze, metadata = extraer_sivigila()
    pob = extraer_demografia()

    if metadata and all(m["estado"] == "error" for m in metadata.values()):
        raise RuntimeError("No se pudo extraer ningun archivo; revisa data/raw/ y el log anterior.")

    bronze.to_csv(cfg.BRONZE_DIR / "sivigila_bronze.csv", index=False)
    pob.to_csv(cfg.BRONZE_DIR / "poblacion_bronze.csv", index=False)
    with open(cfg.BRONZE_DIR / "metadata_extraccion.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2, default=str)

    log.info(f"Bronze escrito en {cfg.BRONZE_DIR} ({len(bronze)} filas)")
    log.info("FIN CAPA BRONZE")
    return bronze, pob, metadata


if __name__ == "__main__":
    main()
