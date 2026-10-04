# Pipeline ETL SIVIGILA 2018-2022

Análisis estadístico de eventos de notificación obligatoria en Colombia a partir de los
reportes rutinarios del INS (SIVIGILA). Arquitectura **medallón** (bronze → silver → gold)
con un dashboard autocontenido.

Eventos objetivo: **dengue** (cód. 210), **IRAG inusitada** (348) y **tuberculosis, todas las formas**
(810+820; 813 en 2021).

## Estructura

```
proyecto_etl_sivigila/
├── config.py              # rutas, hojas por año, eventos objetivo (por código), correcciones
├── run_pipeline.py        # orquestador (extract → transform → load → dashboard)
├── etl/
│   ├── extract.py         # BRONZE: Excel → tabla larga (+ metadata de extracción)
│   ├── transform.py       # SILVER: subtotales, reconciliación, homologación, población, calendario epi
│   ├── load.py            # GOLD: esquema estrella en SQLite, indicadores CV, exportes
│   ├── dashboard.py       # inyecta los resultados de Gold en la plantilla HTML
│   └── utils.py           # logging, normalización de texto, semanas epidemiológicas
├── dashboard/
│   ├── dashboard_template.html   # plantilla (editable)
│   └── dashboard_sivigila.html   # resultado generado (abrir en el navegador)
├── data/
│   ├── raw/               # rutinaria_2018..2022.xlsx + poblacion_departamentos.csv
│   ├── bronze/ silver/ gold/
├── tests/test_pipeline.py
└── docs/INFORME_HALLAZGOS.md     # qué se corrigió respecto a v3 y qué sigue pendiente
```

## Uso

```bash
pip install -r requirements.txt
# copiar rutinaria_2018.xlsx ... rutinaria_2022.xlsx en data/raw/

python run_pipeline.py                    # corrida completa (~45 s)
python run_pipeline.py --desde transform  # si Bronze ya existe
python run_pipeline.py --solo dashboard   # solo regenerar el panel
python -m unittest discover -s tests -v   # pruebas
```

Si el pipeline falla por falta de datos, indica qué etapa previa ejecutar.

## Capas

| Capa | Salida | Contenido |
|---|---|---|
| Bronze | `sivigila_bronze.csv`, `metadata_extraccion.json` | Consolidado largo (año, código, evento, depto, semana, casos), con subtotales de origen. La metadata registra hoja, estrategia, filas, advertencias y códigos imputados por archivo. |
| Silver | `sivigila_silver.csv`, `reporte_calidad.json` | Limpio y homologado por **código de evento**; llave única `(anio, cod_evento, departamento, semana)`; mes calendario real; población y tasa por 100.000. |
| Gold | `sivigila_dw.db`, `casos_objetivo.csv`, `indicadores_cv.csv`, `reporte_calidad_datos.json` | Esquema estrella con PK/FK/índices. |

### Esquema Gold (SQLite)

`hecho_casos(evento_id, departamento_id, tiempo_id, casos)` con dimensiones `dim_evento`,
`dim_departamento`, `dim_tiempo` (año, semana, mes, fecha de inicio) y `poblacion_departamento`.
Además `evento_objetivo_map`, `indicadores_cv`, `calidad_datos` y las vistas
`v_casos_objetivo` y `v_casos_mensuales`.

```sql
SELECT grupo, anio, SUM(casos) FROM v_casos_objetivo GROUP BY grupo, anio;
```

## Decisiones metodológicas

- **Eventos por código, no por nombre.** Los nombres cambian entre años (tildes, «IRAG»…).
- **2022 se lee del detalle** (`Datos_para_estadistica_rutinari`, suma de `conteo`): la tabla
  dinámica de ese libro cuenta filas y subestima ~4× (ver `config.ANIOS_DESDE_DETALLE`).
- **Tuberculosis:** en 2021 el INS publica un único código 813 (todas las formas). Para que la serie
  sea comparable se usa «todas las formas» en todos los años.
- **Reconciliación:** antes de retirar los subtotales de origen se verifica que coincidan con la
  suma por departamento (hoy 100 % en 2019-2020; los demás años no traen totales rotulados por evento).
- **Mes:** calendario epidemiológico (semanas domingo-sábado; mes de la semana según su miércoles).
- **CV:** desviación estándar muestral / media, sobre casos nacionales (incluye *Exterior* y
  *Procedencia desconocida*), mensual y semanal.
- **Eventos sin código** en el origen: se recuperan por nombre desde otro año; si no es posible quedan
  fuera del DW y se informan en el reporte (`config.CODIGOS_MANUALES` permite asignarlos).

## Pendientes conocidos

Ver `docs/INFORME_HALLAZGOS.md`. Los principales: población constante en todos los años
(requiere proyecciones DANE por año) y dos eventos sin código (Intoxicaciones 2021, Chagas crónico 2020).
