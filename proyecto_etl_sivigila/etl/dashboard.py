"""
================================================================================
 DASHBOARD - genera dashboard/dashboard_sivigila.html desde Gold
================================================================================
Antes (v3) el HTML llevaba incrustados datos de ejemplo y habia que cargar a mano
un CSV de 33 MB. Ahora el pipeline agrega los resultados de Gold (casos_objetivo.csv,
indicadores_cv.csv, reportes JSON) y los incrusta en la plantilla, de modo que el
panel siempre refleja la ultima corrida. El archivo resultante es autocontenido
(sin librerias externas ni conexion).
================================================================================
"""

import json

import pandas as pd

import config as cfg
from etl.utils import get_logger

log = get_logger("dashboard")

PLANTILLA = cfg.DASHBOARD_DIR / "dashboard_template.html"
SALIDA = cfg.DASHBOARD_DIR / "dashboard_sivigila.html"
MARCADOR = "/*__DATA_JSON__*/null"


def construir_payload(objetivo: pd.DataFrame, cv: pd.DataFrame, reporte: dict, extraccion: dict | None) -> dict:
    eventos = list(cfg.EVENTOS_OBJETIVO)

    semanal = (objetivo.groupby(["evento", "anio", "semana"], as_index=False)["casos"].sum()
               .sort_values(["evento", "anio", "semana"]))
    anual = objetivo.groupby(["evento", "anio"], as_index=False)["casos"].sum()

    # Indice estacional: casos por mes (todos los anios) / media mensual * 100
    mensual = objetivo.groupby(["evento", "mes"])["casos"].sum()
    meses = sorted(int(m) for m in objetivo["mes"].unique())
    series = {}
    for ev in eventos:
        vals = [float(mensual.get((ev, m), 0)) for m in meses]
        media = sum(vals) / len(vals) if vals else 0
        series[ev] = [round(v / media * 100, 1) if media > 0 else 0 for v in vals]

    terr = objetivo[objetivo["es_territorial"]]
    top = (terr.groupby(["evento", "departamento"], as_index=False)["casos"].sum()
           .sort_values(["evento", "casos"], ascending=[True, False]).groupby("evento").head(8))

    cv_payload = [{"evento": r.grupo, "anio": int(r.anio), "media": r.media_mensual, "sd": r.sd_mensual,
                   "cv_pct": r.cv_mensual_pct, "cv_semanal_pct": r.cv_semanal_pct}
                  for r in cv.itertuples()]

    calidad = dict(reporte)
    calidad.update({
        "filas_extraidas_total": reporte.get("filas_bronze"),
        "filas_finales": reporte.get("filas_silver"),
    })

    return {
        "semanal": semanal.to_dict("records"),
        "anual": anual.to_dict("records"),
        "indice_estacional": {"meses": meses, "series": series},
        "top_departamentos": top.to_dict("records"),
        "cv": cv_payload,
        "calidad": calidad,
        "extraccion": extraccion,
    }


def main():
    log.info("=" * 60)
    log.info("INICIO DASHBOARD")
    objetivo = pd.read_csv(cfg.GOLD_DIR / "casos_objetivo.csv")
    cv = pd.read_csv(cfg.GOLD_DIR / "indicadores_cv.csv")
    with open(cfg.GOLD_DIR / "reporte_calidad_datos.json", encoding="utf-8") as f:
        reporte = json.load(f)
    ruta_meta = cfg.BRONZE_DIR / "metadata_extraccion.json"
    extraccion = None
    if ruta_meta.exists():
        with open(ruta_meta, encoding="utf-8") as f:
            extraccion = json.load(f)

    payload = construir_payload(objetivo, cv, reporte, extraccion)
    html = PLANTILLA.read_text(encoding="utf-8")
    if MARCADOR not in html:
        raise RuntimeError(f"La plantilla no contiene el marcador {MARCADOR}")
    datos = json.dumps(payload, ensure_ascii=False, default=str).replace("</", "<\\/")
    SALIDA.write_text(html.replace(MARCADOR, datos), encoding="utf-8")
    log.info(f"Dashboard escrito en {SALIDA} ({SALIDA.stat().st_size / 1024:.0f} KB)")
    log.info("FIN DASHBOARD")


if __name__ == "__main__":
    main()
