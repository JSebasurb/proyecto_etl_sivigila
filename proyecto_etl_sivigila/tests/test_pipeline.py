"""
Pruebas del pipeline ETL SIVIGILA. Usan datos sinteticos pequenos (no requieren los
xlsx originales) y se ejecutan con:

    python -m unittest discover -s tests -v        (o: pytest tests/)
"""

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as cfg
from etl import extract, load, transform
from etl.utils import inicio_semana_epi, normalizar_texto, semana_a_mes


def bronze_sintetico() -> pd.DataFrame:
    """Dos anios, dos departamentos + EXTERIOR, un evento con subtotal, un evento sin codigo."""
    filas = []
    for anio in (2019, 2020):
        for sem in (1, 2, 3):
            for dep, casos in (("ANTIOQUIA", 5), ("VALLE", 3), ("EXTERIOR", 1)):
                filas.append((anio, "210", "DENGUE", dep, sem, casos))
            filas.append((anio, "Total 210", "Total DENGUE", None, sem, 9))  # subtotal de origen
    filas.append((2020, None, "EVENTO SIN CODIGO", "ANTIOQUIA", 1, 4))
    return pd.DataFrame(filas, columns=extract.COLUMNAS_BRONZE)


def poblacion_sintetica() -> pd.DataFrame:
    return pd.DataFrame({
        "departamento": ["ANTIOQUIA", "VALLE"] * 2,
        "anio": [2019, 2019, 2020, 2020],
        "poblacion": [1_000_000, 500_000, 1_100_000, 550_000],
    })


class TestUtils(unittest.TestCase):
    def test_normalizar_texto(self):
        s = normalizar_texto(pd.Series([" Nariño ", "BOGOTÁ", None]))
        self.assertEqual(s.iloc[0], "NARINO")
        self.assertEqual(s.iloc[1], "BOGOTA")
        self.assertTrue(pd.isna(s.iloc[2]))

    def test_semana_epidemiologica(self):
        # Semana 1 de 2018 inicia el domingo 31-dic-2017; la de 2020, el 29-dic-2019
        self.assertEqual(inicio_semana_epi(2018, 1), date(2017, 12, 31))
        self.assertEqual(inicio_semana_epi(2020, 1), date(2019, 12, 29))
        self.assertEqual(inicio_semana_epi(2020, 1).weekday(), 6)  # domingo

    def test_semana_a_mes_cubre_el_anio(self):
        meses = {semana_a_mes(2019, s) for s in range(1, 53)}
        self.assertEqual(meses, set(range(1, 13)))
        self.assertEqual(semana_a_mes(2020, 53), 12)


class TestExtract(unittest.TestCase):
    def test_reshape_largo_ancho_a_largo(self):
        ancho = pd.DataFrame({
            "cod_evento": ["210", None], "evento": ["DENGUE", None],
            "departamento": ["ANTIOQUIA", "VALLE"],
            "SEMANA_01": [1, 2], "SEMANA_02": [3, "x"],
        })
        largo = extract._reshape_largo(ancho, 2019)
        self.assertEqual(len(largo), 4)
        self.assertEqual(set(largo["semana"]), {1, 2})
        self.assertEqual(largo["casos"].sum(), 6)          # "x" -> 0
        self.assertTrue((largo["cod_evento"] == "210").all())  # ffill dentro del bloque

    def test_ffill_no_arrastra_codigo_entre_eventos(self):
        """Regresion 2021: evento con codigo vacio no debe heredar el del anterior."""
        ancho = pd.DataFrame({
            "cod_evento": ["813", None, None, None],
            "evento": ["TUBERCULOSIS", None, "TUBERCULOSIS FARMACORRESISTENTE", None],
            "departamento": ["EXTERIOR", "ANTIOQUIA", "EXTERIOR", "ANTIOQUIA"],
            "SEMANA_01": [1, 1, 1, 1],
        })
        largo = extract._reshape_largo(ancho, 2021)
        farma = largo[largo["evento"] == "TUBERCULOSIS FARMACORRESISTENTE"]
        self.assertTrue(farma["cod_evento"].isna().all())

    def test_imputar_codigo_por_nombre(self):
        df = pd.DataFrame({
            "anio": [2020, 2021], "cod_evento": ["825", pd.NA],
            "evento": ["TUBERCULOSIS FÁRMACORRESISTENTE", "TUBERCULOSIS FARMACORRESISTENTE"],
            "departamento": ["VALLE", "VALLE"], "semana": [1, 1], "casos": [1, 2],
        })
        df["cod_evento"] = df["cod_evento"].astype("string")
        meta: dict = {}
        out = extract._imputar_codigos_faltantes(df, meta)
        self.assertEqual(out.loc[1, "cod_evento"], "825")
        self.assertIn(2021, meta)


class TestTransform(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.silver, cls.reporte = transform.transformar(bronze_sintetico(), poblacion_sintetica())

    def test_marcar_subtotales(self):
        b = bronze_sintetico()
        marca = transform.marcar_subtotales(b)
        self.assertEqual(int(marca.sum()), int((b["departamento"].isna()).sum()))
        self.assertFalse(marca[b["cod_evento"] == "210"].any())

    def test_subtotales_fuera_y_reconciliados(self):
        self.assertEqual(self.reporte["filas_subtotal_removidas"], 6)
        self.assertFalse(self.silver["departamento"].isna().any())

    def test_reconciliacion_detecta_descuadre(self):
        b = bronze_sintetico()
        rec_ok = transform.reconciliar_totales(b, transform.marcar_subtotales(b))
        self.assertEqual(rec_ok["n_comparaciones"], 6)
        self.assertEqual(rec_ok["n_coinciden"], 6)       # 5+3+1 = 9
        b.loc[b["cod_evento"] == "Total 210", "casos"] = 10
        rec_mal = transform.reconciliar_totales(b, transform.marcar_subtotales(b))
        self.assertEqual(rec_mal["n_coinciden"], 0)

    def test_evento_sin_codigo_se_excluye_y_se_avisa(self):
        self.assertEqual(self.reporte["filas_sin_codigo_removidas"], 1)
        self.assertTrue(any("sin codigo" in a for a in self.reporte["advertencias"]))
        self.assertNotIn("EVENTO SIN CODIGO", set(self.silver["evento"]))

    def test_llave_unica(self):
        self.assertFalse(self.silver.duplicated(transform.CLAVE).any())

    def test_tasa_incidencia(self):
        fila = self.silver[(self.silver["anio"] == 2019) & (self.silver["departamento"] == "ANTIOQUIA")
                           & (self.silver["semana"] == 1)].iloc[0]
        self.assertAlmostEqual(fila["tasa_incidencia_100k"], 5 / 1_000_000 * 100_000)

    def test_exterior_sin_poblacion_ni_territorio(self):
        ext = self.silver[self.silver["departamento"] == "EXTERIOR"]
        self.assertTrue(ext["poblacion"].isna().all())
        self.assertTrue(ext["tasa_incidencia_100k"].isna().all())
        self.assertFalse(ext["es_territorial"].any())

    def test_advierte_poblacion_constante(self):
        pob = poblacion_sintetica()
        pob["poblacion"] = pob["departamento"].map({"ANTIOQUIA": 1_000_000, "VALLE": 500_000})
        _, rep = transform.transformar(bronze_sintetico(), pob)
        self.assertTrue(any("constante" in a for a in rep["advertencias"]))
        self.assertFalse(any("constante" in a for a in self.reporte["advertencias"]))

    def test_casos_se_conservan(self):
        d = self.silver[self.silver["cod_evento"] == 210]
        self.assertEqual(int(d["casos"].sum()), 2 * 3 * (5 + 3 + 1))


class TestLoad(unittest.TestCase):
    def test_estrella_integridad_y_cv(self):
        silver, reporte = transform.transformar(bronze_sintetico(), poblacion_sintetica())
        # el evento sintetico (210) debe pertenecer a un grupo objetivo para el test
        objetivo_original = cfg.EVENTOS_OBJETIVO
        gold_original, db_original = cfg.GOLD_DIR, cfg.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            cfg.GOLD_DIR, cfg.DB_PATH = Path(tmp), Path(tmp) / "dw.db"
            load.cfg.GOLD_DIR, load.cfg.DB_PATH = cfg.GOLD_DIR, cfg.DB_PATH
            try:
                cfg.EVENTOS_OBJETIVO = {"DENGUE": {"etiqueta": "Dengue", "codigos_por_anio": {2019: [210], 2020: [210]}}}
                load.cfg.EVENTOS_OBJETIVO = cfg.EVENTOS_OBJETIVO
                objetivo = load.construir_objetivo(silver)
                cv = load.calcular_indicadores_cv(objetivo)
                load.cargar_a_sqlite(silver, cv, reporte)

                con = sqlite3.connect(cfg.DB_PATH)
                self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(con.execute("SELECT COUNT(*) FROM hecho_casos").fetchone()[0], len(silver))
                self.assertEqual(con.execute("SELECT COUNT(*) FROM dim_evento").fetchone()[0], 1)
                total_vista = con.execute("SELECT SUM(casos) FROM v_casos_objetivo").fetchone()[0]
                self.assertEqual(total_vista, int(silver["casos"].sum()))
                con.close()

                # CV mensual: 3 semanas en enero (+ semanas 1-3 caen en enero) -> un solo mes
                self.assertEqual(set(cv["n_semanas"]), {3})
                fila = cv[(cv["grupo"] == "DENGUE") & (cv["anio"] == 2019)].iloc[0]
                self.assertAlmostEqual(fila["media_semanal"], 9.0)
                self.assertAlmostEqual(fila["sd_semanal"], 0.0)
            finally:
                cfg.EVENTOS_OBJETIVO = objetivo_original
                load.cfg.EVENTOS_OBJETIVO = objetivo_original
                cfg.GOLD_DIR, cfg.DB_PATH = gold_original, db_original
                load.cfg.GOLD_DIR, load.cfg.DB_PATH = gold_original, db_original


class TestConfig(unittest.TestCase):
    def test_codigos_objetivo_cubren_todos_los_anios(self):
        for nombre, spec in cfg.EVENTOS_OBJETIVO.items():
            self.assertEqual(set(spec["codigos_por_anio"]), set(cfg.ANIOS), nombre)

    def test_2022_se_lee_desde_detalle(self):
        self.assertIn(2022, cfg.ANIOS_DESDE_DETALLE)


@unittest.skipUnless((cfg.GOLD_DIR / "casos_objetivo.csv").exists(), "Gold no generado")
class TestResultadosReales(unittest.TestCase):
    """Chequeos de cordura sobre la ultima corrida (se omiten si no existe Gold)."""

    def test_dengue_2022_no_esta_subcontado(self):
        o = pd.read_csv(cfg.GOLD_DIR / "casos_objetivo.csv")
        dengue = o[o["evento"] == "DENGUE"].groupby("anio")["casos"].sum()
        self.assertGreater(dengue[2022], 50_000)  # v3 reportaba 12.513 por error

    def test_reconciliacion_perfecta(self):
        rep = json.loads((cfg.GOLD_DIR / "reporte_calidad_datos.json").read_text(encoding="utf-8"))
        self.assertEqual(rep["reconciliacion_totales"]["pct_coinciden"], 100.0)

    def test_los_tres_eventos_tienen_los_cinco_anios(self):
        o = pd.read_csv(cfg.GOLD_DIR / "casos_objetivo.csv")
        for ev, g in o.groupby("evento"):
            self.assertEqual(sorted(g["anio"].unique()), cfg.ANIOS, ev)


if __name__ == "__main__":
    unittest.main()
