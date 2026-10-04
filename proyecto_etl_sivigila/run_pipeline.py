"""
Orquestador del pipeline ETL SIVIGILA.

Uso:
    python run_pipeline.py                      # extract -> transform -> load -> dashboard
    python run_pipeline.py --desde transform    # re-ejecuta desde Silver (Bronze ya existe)
    python run_pipeline.py --solo dashboard     # solo regenera el dashboard desde Gold
    python run_pipeline.py --exportar-hecho-completo
"""

import argparse
import sys
import time

from etl import dashboard, extract, load, transform
from etl.utils import get_logger

log = get_logger("pipeline")

ETAPAS = ["extract", "transform", "load", "dashboard"]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Pipeline ETL SIVIGILA (bronze -> silver -> gold)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--desde", choices=ETAPAS, default="extract", help="etapa inicial (default: extract)")
    g.add_argument("--solo", choices=ETAPAS, help="ejecuta unicamente esta etapa")
    p.add_argument("--exportar-hecho-completo", action="store_true",
                   help="ademas exporta data/gold/hecho_casos.csv (copia completa de Silver, ~33 MB)")
    args = p.parse_args(argv)

    etapas = [args.solo] if args.solo else ETAPAS[ETAPAS.index(args.desde):]
    t0 = time.time()
    try:
        for etapa in etapas:
            t = time.time()
            if etapa == "extract":
                extract.main()
            elif etapa == "transform":
                transform.main()
            elif etapa == "load":
                load.main(exportar_hecho_completo=args.exportar_hecho_completo)
            elif etapa == "dashboard":
                dashboard.main()
            log.info(f"[{etapa}] terminada en {time.time() - t:.1f}s")
    except FileNotFoundError as e:
        log.error(str(e))
        return 1
    log.info(f"Pipeline completo en {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
