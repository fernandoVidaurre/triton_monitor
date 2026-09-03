"""
Suite de Simulación de Caos y Pruebas Forenses - PROYECTO TRITÓN
Integrante 6: Ingeniero de Simulación de Caos y Pruebas Forenses

Este script NO reemplaza al punto de entrada oficial (app_operator.py, responsabilidad
del Integrante 5). Es un arnés de pruebas EXTERNO que:

  1. Lanza la CLI real (app_operator.py) como subproceso, de forma concurrente y masiva,
     variando argumentos para forzar cada rama de fallo semántica:
        - Timeout real agotado (ProviderTimeoutError)
        - Estatus HTTP erróneo / 504 (NetworkPeeringError)
        - Payload corrupto / no serializable (CorruptedPayloadError)
        - Validación de entradas inválidas (cluster-id y timeout fuera de rango)
  2. Fuerza un colapso CATASTRÓFICO de DNS/ruteo en proceso, mediante monkeypatch
     de los endpoints reales de core.py hacia un host inexistente (TLD .invalid,
     reservado por RFC 2606 para nunca resolver), verificando NetworkPeeringError
     ante una caída física real de red.
  3. Imprime un reporte consolidado y termina con código de salida != 0 si algún
     escenario no fue debidamente ejercitado (para integrarse a un pipeline CI).

Uso:
    python chaos_suite.py [--concurrency N]

Requiere que app_operator.py y el paquete triton_telemetry/ estén en el mismo
directorio (o en el PYTHONPATH).
"""

import asyncio
import subprocess
import sys
import time
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from typing import List

# --- Resolución del path del proyecto ---
# Este script vive en tests/, así que src/ es su hermano un nivel arriba.
TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

APP_OPERATOR = SRC_DIR / "app_operator.py"


@dataclass
class ChaosResult:
    scenario: str
    returncode: int
    duration_sec: float
    stdout_tail: str
    stderr_tail: str
    expected_exception: str


# Cada escenario ejercita deliberadamente una rama de captura quirúrgica distinta
# definida en app_operator.py (Integrante 5) y core.py (Integrante 2).
CLI_SCENARIOS = [
    {
        "scenario": "timeout_forzado_AWS",
        "args": ["AWS", "-c", "cluster-us-east-01", "--chaos", "--timeout", "0.1"],
        "expected_exception": "ProviderTimeoutError",
    },
    {
        "scenario": "http_504_Azure",
        "args": ["Azure", "-c", "cluster-us-east-01", "--chaos", "--timeout", "5.0"],
        "expected_exception": "NetworkPeeringError (HTTPStatusError -> 504)",
    },
    {
        "scenario": "payload_corrupto_GCP",
        "args": ["GCP", "-c", "cluster-us-east-01", "--chaos", "--timeout", "5.0"],
        "expected_exception": "CorruptedPayloadError (respuesta no-JSON)",
    },
    {
        "scenario": "cluster_id_invalido",
        "args": ["AWS", "-c", "clusterMALFORMADO", "--timeout", "1.0"],
        "expected_exception": "argparse.ArgumentTypeError (exit code 2)",
    },
    {
        "scenario": "timeout_fuera_de_rango",
        "args": ["AWS", "-c", "cluster-us-east-01", "--timeout", "9.9"],
        "expected_exception": "argparse.ArgumentTypeError (exit code 2)",
    },
]


def _run_cli_scenario(scenario: dict, repeat_id: int) -> ChaosResult:
    """Ejecuta una invocación real y aislada de la CLI como subproceso."""
    cmd = [sys.executable, str(APP_OPERATOR), *scenario["args"]]
    start = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    duration = time.perf_counter() - start
    return ChaosResult(
        scenario=f"{scenario['scenario']}#{repeat_id}",
        returncode=proc.returncode,
        duration_sec=duration,
        stdout_tail="\n".join(proc.stdout.splitlines()[-6:]),
        stderr_tail="\n".join(proc.stderr.splitlines()[-6:]),
        expected_exception=scenario["expected_exception"],
    )


def run_massive_concurrent_cli(concurrency: int = 3) -> List[ChaosResult]:
    """
    Lanza `concurrency` réplicas de CADA escenario de la CLI real en paralelo,
    usando un pool de hilos (subprocess.run libera el GIL mientras espera I/O
    de red real hacia jsonplaceholder/httpbin).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    jobs = [(scenario, i) for scenario in CLI_SCENARIOS for i in range(concurrency)]

    results: List[ChaosResult] = []
    with ThreadPoolExecutor(max_workers=max(len(jobs), 1)) as pool:
        futures = {pool.submit(_run_cli_scenario, s, i): s for s, i in jobs}
        for future in as_completed(futures):
            results.append(future.result())
    return results


async def force_dns_collapse(concurrency: int = 3) -> List[dict]:
    """
    Fuerza un NetworkPeeringError REAL modificando en caliente (monkeypatch) los
    endpoints del módulo core.py hacia un host inexistente, y dispara
    `concurrency` escaneos concurrentes reales vía asyncio.TaskGroup + gather.
    """
    from triton_telemetry import core as core_module
    from triton_telemetry.exceptions import NetworkPeeringError

    unreachable_host = "https://host-inexistente.triton-caos-test.invalid/status"
    original_endpoints = dict(core_module.PROVIDER_ENDPOINTS)
    core_module.PROVIDER_ENDPOINTS = {k: unreachable_host for k in original_endpoints}

    try:
        async def _one_run(run_id: int):
            # 'return' no puede aparecer dentro de un bloque except*, así que
            # acumulamos el resultado en una variable local y retornamos al final.
            outcome = {"run": run_id, "status": "INESPERADO_SIN_FALLO"}
            try:
                await core_module.scan_all_providers(["AWS", "Azure", "GCP"], timeout=2.0)
            except* NetworkPeeringError as group:
                outcome = {
                    "run": run_id,
                    "status": "NetworkPeeringError_CONFIRMADO",
                    "incidentes": len(group.exceptions),
                }
            except* Exception as group:
                outcome = {
                    "run": run_id,
                    "status": f"EXCEPCION_INESPERADA:{[type(e).__name__ for e in group.exceptions]}",
                }
            return outcome

        return list(await asyncio.gather(*(_one_run(i) for i in range(concurrency))))
    finally:
        # Restaurar el estado real del módulo para no contaminar otras corridas/tests
        core_module.PROVIDER_ENDPOINTS = original_endpoints


def print_report(cli_results: List[ChaosResult], dns_results: List[dict]) -> bool:
    print("=" * 70)
    print(" REPORTE DE SIMULACIÓN DE CAOS — PROYECTO TRITÓN (Integrante 6)")
    print("=" * 70)

    all_ok = True
    for r in sorted(cli_results, key=lambda x: x.scenario):
        anomaly_logged = ("ANOMALÍA" in r.stdout_tail) or ("ADVERTENCIA" in r.stdout_tail)
        exited_clean_error = r.returncode == 2  # argparse
        marker = "OK" if (anomaly_logged or exited_clean_error) else "REVISAR"
        if marker == "REVISAR":
            all_ok = False
        print(f"[{marker}] {r.scenario:<30} exit={r.returncode:<3} {r.duration_sec:5.2f}s -> esperado: {r.expected_exception}")

    print("-" * 70)
    print(" Colapso de DNS/ruteo forzado (monkeypatch a host .invalid):")
    for outcome in dns_results:
        confirmed = outcome["status"].startswith("NetworkPeeringError")
        if not confirmed:
            all_ok = False
        print(f"   run {outcome['run']}: {outcome['status']}")

    print("=" * 70)
    print(" RESULTADO GLOBAL:", "TODOS LOS ESCENARIOS EJERCITADOS CORRECTAMENTE" if all_ok else "HAY ESCENARIOS QUE REQUIEREN REVISIÓN")
    print("=" * 70)
    return all_ok


def main():
    parser = argparse.ArgumentParser(description="Suite de simulación de caos para TritonMonitor.")
    parser.add_argument("--concurrency", type=int, default=3, help="Réplicas concurrentes por escenario.")
    args = parser.parse_args()

    if not APP_OPERATOR.exists():
        print(f"ERROR: no se encontró app_operator.py en {PROJECT_ROOT}", file=sys.stderr)
        sys.exit(1)

    print(f"Lanzando {len(CLI_SCENARIOS)} escenarios x {args.concurrency} réplicas concurrentes contra la CLI real...")
    cli_results = run_massive_concurrent_cli(concurrency=args.concurrency)

    print("Forzando colapso real de DNS/ruteo en proceso...")
    dns_results = asyncio.run(force_dns_collapse(concurrency=args.concurrency))

    ok = print_report(cli_results, dns_results)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
