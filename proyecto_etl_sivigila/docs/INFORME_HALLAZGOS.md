# Informe de revisión v3 → v4

## 1. Correcciones que cambian resultados

| # | Problema en v3 | Causa | Solución en v4 |
|---|---|---|---|
| 1 | Todo 2022 subestimado (~4×): dengue 12.513 en vez de 65.691; total 215.210 en vez de 823.705 | La hoja `Departamento` del libro 2022 es una tabla dinámica que **cuenta filas** del detalle en vez de **sumar `conteo`** | Se lee la hoja de detalle y se suma `conteo` (`config.ANIOS_DESDE_DETALLE`). La metadata deja constancia de la discrepancia. |
| 2 | Eventos distintos fusionados en 2021 (TB farmacorresistente, malaria falciparum y vivax) | `ffill` global arrastraba el código del evento anterior cuando venía vacío | Relleno por bloque de evento + recuperación del código por nombre desde otros años (825, 470, 490) |
| 3 | Serie de tuberculosis no comparable | En 2021 el INS publica un único código 813 (todas las formas) que v3 renombraba «pulmonar» | Serie «todas las formas»: 810+820 (813 en 2021) |
| 4 | Dashboard con cifras erróneas incrustadas | Datos de ejemplo embebidos + carga manual de un CSV de 33 MB | `etl/dashboard.py` incrusta los resultados de la última corrida |
| 5 | `dim_evento` con 15 códigos repetidos; «esquema estrella» sin llaves | Nombres usados como clave; tablas sin PK/FK | Esquema estrella real con llaves sustitutas, PK/FK, índices y `PRAGMA foreign_key_check` |
| 6 | Mes aproximado (`(semana-1)*12//52+1`) | Fórmula simplificada | Calendario epidemiológico real (`dim_tiempo`) |

### Efecto en cifras clave

| Evento | 2022 en v3 | 2022 en v4 |
|---|---|---|
| Dengue (casos) | 12.513 | 65.691 |
| IRAG inusitada (casos) | 12.667 | 107.290 |
| Tuberculosis (v3: «pulmonar»; v4: todas las formas) | 5.619 | 17.154 |
| CV mensual dengue 2022 | 16,0 % | 26,3 % |
| CV mensual TB 2021 | 18,0 % (pulmonar = 813 completo) | 16,7 % (todas las formas) |

Los CV de 2018-2021 también cambian levemente por el nuevo cálculo del mes.

## 2. Validaciones realizadas

- **Reconciliación de subtotales:** 7.245 de 7.245 totales de origen (2019 y 2020) coinciden con la suma por departamento. 2018 y 2021 no traen totales rotulados por evento, y 2022 se lee del detalle (sin totales), por lo que no se reconcilian con este método.
- **Contraste con las hojas municipales (2018-2021):** dengue, IRAG inusitada y tuberculosis extrapulmonar coinciden con la hoja por evento. En TB pulmonar 2020 la hoja municipal suma más porque incluye filas de subtotal propias de esa hoja, no un error del pipeline.
- **2022:** el detalle (215.210 filas, suma de `conteo` = 823.705) es la fuente más completa del libro.
- **Integridad del DW:** `integrity_check` y `foreign_key_check` sin hallazgos; `hecho_casos` = filas de Silver.
- **Pruebas automáticas:** 21 pruebas (`tests/test_pipeline.py`), incluida la regresión del error de 2022.

## 3. Pendientes y riesgos

1. **Población constante.** `poblacion_departamentos.csv` repite la misma cifra para 2018-2022 (44.164.417 a nivel nacional). Las tasas por 100.000 están sesgadas. Se necesita la proyección DANE por departamento y año. El pipeline ya avisa mientras la población sea constante.
2. **Eventos sin código en el origen:** «Intoxicaciones» 2021 (18.901 casos, agregado sin código; en otros años está separado por tipo, cód. 360-414) y «Chagas crónico» 2020 (98 casos). Quedan fuera del DW y avisados en el reporte. Si se decide un código, se asigna en `config.CODIGOS_MANUALES`.
3. **IRAG inusitada 2022 presenta un quiebre.** Casi 0 casos por semana hasta la semana 16 y luego 3.000-4.800 por semana; Bogotá concentra ~34 %. El patrón es compatible con un cambio en la definición o en la notificación del evento, pero no se pudo verificar la causa con una fuente externa. Antes de interpretar el CV o la estacionalidad de IRAG 2022, conviene confirmarlo con el INS. IRAG 2020 (21.765) sí es consistente con la pandemia.
4. **Nombres con más de un valor por código** (11 códigos, p. ej. 205 Chagas agudo/crónico, 340 Hepatitis B). Se unifican por código; si se necesita distinguir subtipos, habría que conservar el nombre original.
5. **Códigos 345 (ESI-IRAG centinela) y 348 (IRAG inusitada)** son eventos distintos. El análisis usa solo 348, igual que v3.
6. **El CV mezcla «Exterior» y «Procedencia desconocida»** con los departamentos (nivel nacional). Si se quiere solo territorio nacional, filtrar por `es_territorial`.
7. **Subregistro y definiciones:** los totales son casos notificados (no confirmados); 2020-2021 están afectados por la pandemia.

## 4. Siguientes pasos sugeridos

1. Cargar la población DANE por año y regenerar (`python run_pipeline.py --desde transform`).
2. Confirmar el quiebre de IRAG 2022 y decidir cómo tratarlo en las pruebas de estacionalidad.
3. Definir el tratamiento de Intoxicaciones 2021 y Chagas crónico 2020.
4. Si se amplía a más eventos, agregarlos en `config.EVENTOS_OBJETIVO` (sin tocar el código de las etapas).
