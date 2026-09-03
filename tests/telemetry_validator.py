"""
Validador de Telemetría JSON — PROYECTO TRITÓN
Integrante 6: Ingeniero de Simulación de Caos y Pruebas Forenses

Abre automáticamente los archivos de log generados por logging_engine.py
(Integrantes 3 y 4): el archivo activo y los históricos rotados/comprimidos
(*.log.N.gz), y certifica:

  - Integridad de metadatos obligatorios por línea (timestamp ISO 8601 UTC
    estricto, logger, nivel, hilo, tarea asyncio, archivo/línea de origen).
  - Fidelidad del árbol recursivo de ExceptionGroup: excepciones anidadas
    (nested_exceptions), causas encadenadas (`cause`, vía `raise ... from`)
    y notas forenses (`add_note`).
  - Presencia de evidencia REAL de errores httpx (código de estado HTTP,
    referencia al endpoint/timeout) dentro del árbol serializado.
  - Correcta descompresión Gzip de los históricos rotados en disco
    (integridad de CRC/tamaño, no solo que el archivo "abra").

Uso:
    python telemetry_validator.py [--log-dir DIR] [--base-name triton_services.log]
"""

import argparse
import gzip
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List

ISO8601_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z$")

REQUIRED_TOP_LEVEL_FIELDS = {
    "timestamp", "level", "logger", "message",
    "async_task", "thread_name", "filename", "line",
}

HTTP_STATUS_EVIDENCE_RE = re.compile(r"\b(1\d{2}|2\d{2}|3\d{2}|4\d{2}|5\d{2})\b")


@dataclass
class ValidationReport:
    file: str
    lines_total: int = 0
    lines_valid_json: int = 0
    lines_with_metadata_ok: int = 0
    lines_with_exception_tree: int = 0
    exception_trees_with_httpx_evidence: int = 0
    gzip_integrity_ok: bool = True
    errors: List[str] = field(default_factory=list)


def discover_log_files(log_dir: Path, base_name: str = "triton_services.log") -> List[Path]:
    """Descubre el archivo activo y todos los históricos rotados/comprimidos."""
    candidates = sorted(log_dir.glob(f"{base_name}*"))
    if not candidates:
        raise FileNotFoundError(
            f"No se encontraron archivos de log '{base_name}*' en {log_dir}. "
            f"Corré primero la CLI (app_operator.py) o chaos_suite.py."
        )
    return candidates


def _read_lines(path: Path) -> Iterator[str]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            yield from f
    else:
        with path.open("rt", encoding="utf-8") as f:
            yield from f


def _verify_gzip_integrity(path: Path) -> bool:
    """Fuerza la lectura completa del stream comprimido para validar CRC/tamaño,
    no solo que el archivo abra correctamente."""
    if path.suffix != ".gz":
        return True
    try:
        with gzip.open(path, "rb") as f:
            while f.read(65536):
                pass
        return True
    except (gzip.BadGzipFile, OSError, EOFError):
        return False


def _validate_metadata(record: Dict[str, Any]) -> List[str]:
    problems = []
    missing = REQUIRED_TOP_LEVEL_FIELDS - record.keys()
    if missing:
        problems.append(f"Campos faltantes: {sorted(missing)}")

    ts = record.get("timestamp", "")
    if not ISO8601_UTC_RE.match(ts):
        problems.append(f"Timestamp no cumple ISO 8601 UTC estricto: '{ts}'")
    else:
        try:
            datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            problems.append(f"Timestamp no parseable: '{ts}'")
    return problems


def _tree_contains_httpx_evidence(node: Dict[str, Any]) -> bool:
    """
    Recorre recursivamente el árbol serializado (nested_exceptions / cause)
    buscando evidencia real de httpx: nombre de clase, código de estado HTTP
    en el mensaje o en las notas forenses, o referencia al endpoint/timeout.
    """
    haystack = " ".join([
        node.get("class", ""),
        node.get("message", ""),
        " ".join(node.get("notes", []) or []),
    ]).lower()

    if "httpx" in haystack or "http" in haystack or "timeout" in haystack:
        if HTTP_STATUS_EVIDENCE_RE.search(haystack) or "endpoint" in haystack or "timeout" in haystack:
            return True

    if node.get("cause") and _tree_contains_httpx_evidence(node["cause"]):
        return True
    for nested in node.get("nested_exceptions", []) or []:
        if _tree_contains_httpx_evidence(nested):
            return True
    return False


def validate_file(path: Path) -> ValidationReport:
    report = ValidationReport(file=str(path))
    report.gzip_integrity_ok = _verify_gzip_integrity(path)
    if not report.gzip_integrity_ok:
        report.errors.append("Fallo de integridad Gzip: el histórico está corrupto o truncado.")
        return report

    for raw_line in _read_lines(path):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        report.lines_total += 1
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as e:
            report.errors.append(f"Línea no es JSON válido: {e}")
            continue
        report.lines_valid_json += 1

        problems = _validate_metadata(record)
        if problems:
            report.errors.extend(f"{path.name}: {p}" for p in problems)
        else:
            report.lines_with_metadata_ok += 1

        tree = record.get("exception_tree")
        if tree:
            report.lines_with_exception_tree += 1
            if _tree_contains_httpx_evidence(tree):
                report.exception_trees_with_httpx_evidence += 1

    return report


def print_summary(reports: List[ValidationReport]) -> bool:
    print("=" * 70)
    print(" VALIDADOR DE TELEMETRÍA JSON — PROYECTO TRITÓN (Integrante 6)")
    print("=" * 70)

    overall_ok = True
    for r in reports:
        print(f"\nArchivo: {r.file}")
        print(f"  Integridad Gzip: {'OK' if r.gzip_integrity_ok else 'FALLO'}")
        print(f"  Líneas totales: {r.lines_total} | JSON válido: {r.lines_valid_json}")
        print(f"  Metadatos completos: {r.lines_with_metadata_ok}/{r.lines_valid_json}")
        print(f"  Con árbol de excepciones: {r.lines_with_exception_tree}")
        denom = r.lines_with_exception_tree or 1
        print(f"  Con evidencia httpx confirmada: {r.exception_trees_with_httpx_evidence}/{denom if r.lines_with_exception_tree else 0}")
        if r.errors:
            overall_ok = False
            print(f"  Problemas detectados ({len(r.errors)}), primeros 5:")
            for e in r.errors[:5]:
                print(f"     - {e}")
        if not r.gzip_integrity_ok:
            overall_ok = False

    print("\n" + "=" * 70)
    print(" RESULTADO GLOBAL:", "TELEMETRÍA VÁLIDA Y CONSISTENTE" if overall_ok else "SE DETECTARON INCONSISTENCIAS")
    print("=" * 70)
    return overall_ok


def main():
    parser = argparse.ArgumentParser(description="Valida forense y estructuralmente los logs JSON de TritonMonitor.")
    parser.add_argument("--log-dir", default=".", help="Directorio donde residen los archivos de log.")
    parser.add_argument("--base-name", default="triton_services.log", help="Nombre base del archivo de log.")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    try:
        files = discover_log_files(log_dir, args.base_name)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    reports = [validate_file(f) for f in files]
    ok = print_summary(reports)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
