# Proyecto ETL - SIVIGILA Colombia (2018-2022)

**Universidad Autónoma de Occidente**  
**Facultad de Ingeniería y Ciencias Básicas**  
**Autores:** Juan Pablo Izquierdo Corral, Juan Sebastián Sánchez Urbano, Juan Felipe Enriquez Obando  
**Fecha de actualización:** Octubre de 2026  

---

## 1. Resumen Ejecutivo
El presente proyecto consiste en el diseño e implementación de un pipeline ETL (Extracción, Transformación y Carga) automatizado para procesar, depurar y estandarizar los registros rutinarios del Sistema Nacional de Vigilancia en Salud Pública (SIVIGILA) de Colombia, abarcando el periodo 2018-2022. 

El sistema se enfoca en tres eventos epidemiológicos principales: **Dengue, IRAG inusitada y Tuberculosis**. Mediante una arquitectura de datos en tres capas (Bronze, Silver, Gold), el pipeline consolida los datos de múltiples archivos de Excel heterogéneos, cruza la información con datos demográficos del DANE, genera un almacén de datos (Data Warehouse) en SQLite y despliega los resultados en un Dashboard HTML interactivo.

## 2. Contexto y Definición del Problema
En Colombia, la vigilancia de eventos de interés en salud pública presenta variaciones espaciales y temporales significativas. La información oficial (SIVIGILA) se distribuye anualmente en formatos que cambian constantemente (nombres de hojas, columnas, códigos de eventos y subtotales incrustados). 

**Problema Principal:** La dispersión de datos y la inconsistencia en los formatos generan reprocesos manuales masivos, impidiendo un análisis estadístico ágil sobre la estacionalidad de las enfermedades. Esto dificulta la anticipación y la toma de decisiones basada en datos para la prevención y asignación de recursos sanitarios (ej. sobrecarga de servicios).

**Usuarios Objetivo:** Equipos de vigilancia epidemiológica de las Secretarías de Salud e investigadores del Instituto Nacional de Salud (INS) que requieren información procesada y visualizada sin depender de infraestructura técnica compleja.

## 3. Arquitectura de la Solución (Pipeline ETL)
El proyecto se construyó bajo la metodología de capas (Medallón) utilizando Python y SQLite:

1. **Capa Bronze (Extracción):** El script `etl/extract.py` lee cinco libros de Excel con estructuras distintas. Extrae metadatos y pasa la información de un formato ancho a uno largo, centralizándolo en `sivigila_bronze.csv`.
2. **Capa Silver (Transformación):** El módulo `etl/transform.py` elimina subtotales de origen, imputa códigos de evento faltantes, normaliza nombres de 33 departamentos, calcula el mes epidemiológico y cruza con la población DANE para calcular tasas de incidencia por cada 100.000 habitantes. Genera un reporte automático de calidad de datos (`reporte_calidad_datos.json`).
3. **Capa Gold (Carga y Modelado):** `etl/load.py` implementa un esquema de estrella en una base de datos SQLite (`sivigila_dw.db`). Se filtran los eventos objetivo y se calculan indicadores estadísticos descriptivos.
4. **Visualización (Dashboard):** `etl/dashboard.py` inyecta los resultados consolidados de la capa Gold en una plantilla HTML, generando un panel interactivo (`dashboard_sivigila.html`) que no requiere servidor ni conexión a internet.

## 4. Objetivos y Resultados Clave (OKRs)
* **Objetivo:** Diseñar e implementar un pipeline ETL reproducible que integre y estandarice datos epidemiológicos y demográficos para analizar su comportamiento temporal y territorial.
  * **KR1:** Integrar SIVIGILA (2018-2022) y DANE (Proyecciones de población). *(Cumplido)*
  * **KR2:** Homologar variables de evento, territorio y periodo epidemiológico. *(Cumplido: 86 eventos unificados por código).*
  * **KR3:** Generar indicadores temporales (coeficientes de variación, índice estacional) reproducibles. *(Cumplido)*
  * **KR4:** Documentar la calidad del dato de forma automática (faltantes, duplicados). *(Cumplido)*

## 5. Hallazgos y Resultados Analíticos
Gracias a la estandarización, se corrigieron fallas históricas de conteo encontradas en los datos de origen (por ejemplo, el cálculo original del 2022 subestimaba los casos de Dengue casi cuatro veces al contar filas en lugar de sumar casos).

**Casos Notificados Consolidados (2018-2022):**
| Año | Dengue | IRAG Inusitada | Tuberculosis (todas las formas) |
|---|---|---|---|
| 2018 | 43.652 | 1.288 | 13.998 |
| 2019 | 122.998 | 835 | 14.255 |
| 2020 | 76.220 | 21.765 | 11.023 |
| 2021 | 52.376 | 954 | 14.132 |
| 2022 | 65.691 | 107.290 | 17.154 |

**Análisis del Coeficiente de Variación (CV) Mensual:**
* **Dengue:** Presentó su pico máximo de casos en 2019 con la menor dispersión de la serie (24,2%), sugiriendo un brote sostenido. La mayor dispersión se dio en 2020 (74,6%).
* **Tuberculosis:** Demostró ser el evento más estable a nivel estacional, con un CV mensual consistente entre el 12,8% y el 21,6% a lo largo del quinquenio.
* **IRAG Inusitada:** Muestra picos atípicos en 2020 (CV: 118,0%) y 2022 (CV: 83,9%), claramente influenciados por la dinámica de la pandemia COVID-19 y posibles cambios en la notificación técnica.

## 6. Limitaciones y Trabajo Futuro
* **Datos Poblacionales (DANE):** Actualmente la base asume una población constante en los 5 años. *Acción propuesta:* Interpolar proyecciones exactas por año para ajustar la exactitud de las tasas poblacionales.
* **Anomalía IRAG 2022:** Se evidenció un quiebre en la semana 16 del 2022 con un alza masiva de casos (más de 3.000 por semana). Debe triangularse con boletines oficiales del INS.
* **Factores Climáticos:** Queda pendiente la integración de los datos del IDEAM para buscar correlaciones entre clima y el CV de enfermedades transmitidas por vectores.
* **Pruebas de Hipótesis:** Aplicar pruebas estadísticas formales (p < 0.05) para confirmar la significancia de los patrones temporales descubiertos.

---

## 7. Estructura del Repositorio
```text
proyecto_etl_sivigila/
│
├── config.py                  # Parámetros y configuraciones globales
├── run_pipeline.py            # Orquestador principal del proceso ETL
├── requirements.txt           # Dependencias del entorno de Python
│
├── data/
│   ├── raw/                   # Datos originales crudos (Excel SIVIGILA, CSV DANE)
│   ├── silver/                # Datos intermedios limpios (Generados dinámicamente)
│   └── gold/                  # Archivos analíticos CSV y BD SQLite final
│
├── etl/                       # Módulos del pipeline (Core del sistema)
│   ├── extract.py             # Lectura y consolidación Bronze
│   ├── transform.py           # Limpieza y reglas de negocio Silver
│   ├── load.py                # Carga a SQLite y cálculo de KPIs Gold
│   ├── dashboard.py           # Inyección de datos al template HTML
│   └── utils.py               # Funciones auxiliares generales
│
├── dashboard/                 # Plantilla y salida HTML interactiva
│   ├── dashboard_template.html
│   └── dashboard_sivigila.html # (Dashboard final autogenerado)
│
├── docs/                      # Documentación del proyecto (Avances, informes)
│
└── tests/                     # Suite de pruebas automáticas
    └── test_pipeline.py
```

## 8. Instrucciones de Ejecución

### Requisitos Previos
Asegúrate de tener Python 3.8 o superior instalado. Instala las librerías necesarias con el siguiente comando:
```bash
pip install -r requirements.txt
```

### Ejecución del Pipeline Completo
Para correr el proceso de principio a fin (Extracción -> Transformación -> Carga -> Dashboard), ejecuta en la raíz del proyecto:
```bash
python run_pipeline.py
```

*Opciones avanzadas:*
* `--desde [etapa]`: Inicia el pipeline desde una etapa específica (ej: `extract`, `transform`, `load`, `dashboard`).
* `--solo [etapa]`: Ejecuta únicamente una etapa aislada.

### Pruebas Automáticas (Testing)
El sistema cuenta con 21 pruebas automáticas para garantizar la integridad de las transformaciones y correcciones (como la corrección del conteo 2022). Para ejecutarlas, utiliza:
```bash
python -m unittest discover -s tests
```
