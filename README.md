# Grupo 14 - Proyecto Triton — Consola de Telemetria Multicloud y Observabilidad Asincrona
Este repositorio contiene la implementación del Trabajo Práctico N° 1 para la materia Programación para Automatización II de la Tecnicatura Universitaria en Gestión de Infraestructura Cloud y DevOps de la UPATECO.

El Proyecto Tritón es una consola de observabilidad de nivel industrial diseñada para monitorear el estado de los clústeres de computo distribuidos concurrentemente en tres proveedores cloud principales (AWS, Azure y GCP) bajo condiciones de red altamente hostiles e inestables.

---


## 📺 Video de Defensa Oral: 
**Enlace:** https://drive.google.com/drive/folders/1XhhSIx1kWsBZDaZAC68s8R3QNZYshhh2?usp=sharing

---

## 👥 Equipo de Desarrollo y Roles Técnicos - Grupo 14 - print "Ojo de Tigre"
#1 Gustavo Ortiz
#2 Julio Vidaurre 
#3 Maxi Testa 
#4 Tupac Yapura
#5 Gaston Lopez
#6 Emanuel Sanchez


| Integrante | Rol Técnico | Módulos Asociados | Responsabilidades Clave |
| :--- | :--- | :--- | :--- |
| **#1 Gustavo Ortiz** | Ingeniero de Robustez de Entradas y Excepciones | `exceptions.py`<br>`sanitizer.py` | • Definición de excepciones de negocio (`TritonError`, `ProviderTimeoutError`, etc.) evitando secuestrar señales vitales del sistema.<br>• Sanitización estricta por regex de IDs de clústeres y límites de timeouts en la frontera de la CLI. |
| **#2 Julio Vidaurre** | Ingeniero de Concurrencia y Telemetría Asíncrona | `core.py` | • Consumo asíncrono real mediante `httpx.AsyncClient` de endpoints públicos.<br>• Orquestación concurrente no bloqueante mediante `asyncio.TaskGroup`. Enriquecimiento forense con `add_note` y encadenamiento explícito de errores (`raise...from`). |
| **#3  Maxi Testa** | Ingeniero de Formateo Estructurado JSON | `logging_engine.py`<br>(Clase `AsyncJSONFormatter`) | • Construcción del serializador forense JSON.<br>• Extractor recursivo de árboles jerárquicos complejos de `ExceptionGroup`, incluyendo notas, causas internas y metadatos dinámicos. |
| **#4 Tupac Yapura** | Ingeniero de Almacenamiento y Desacoplamiento | `logging_engine.py`<br>(Pipeline asíncrono) | • Desacoplamiento físico de I/O mediante un pipeline de hilos seguro usando `QueueHandler` y `QueueListener`. <br>• Rotación física acotada con `RotatingFileHandler` y compresión atómica en caliente a formato `.gz`. |
| **#5  Gaston Lopez** | Coordinador de Integración y Flujo CLI | `app_operator.py` | • Orquestador principal de la consola de comandos usando `argparse` y sus validadores inyectados.<br>• Captura quirúrgica estructurada mediante `except*` de grupos de excepciones asíncronas y apagado limpio de recursos. |
| **#6 Emanuel Sanchez** | Ingeniero de Simulación de Caos y Pruebas Forenses | `tests/chaos_suite.py`<br>`tests/telemetry_validator.py` | • Automatización de inyección de fallas reales en caliente (monkeypatching de DNS y forzado de timeouts).<br>• Script de auditoría forense para certificar la consistencia del JSON, metadatos, árbol de errores e integridad de compresión Gzip. |

---
# Guía de Operación y Arquitectura del Proyecto Tritón

Este documento sintetiza los requisitos de entorno, el flujo operacional de la consola de automatización y las herramientas de verificación del **Proyecto Tritón, TP-1: Sistema de Telemetría Multicloud y Observabilidad Asíncrona**.

---

## 1. Configuración de Entorno y Aislamiento - Prevención de Shadowing

Para evitar colisiones de nombres, *Local Shadowing* en la jerarquía del sistema de búsqueda de Python (`sys.path`) y garantizar que las dependencias estén aisladas de tu sistema global, sigue estos pasos secuenciales:

* **Requisito Obligatorio:** Se requiere **Python 3.11 o superior**.
* **Creación de Entorno Virtual:** 
  ```powershell
  python -m venv .venv
  ```
* **Activación en Windows (PowerShell):**
  Si tu sistema restringe la ejecución de scripts, otorga permisos temporales y activa el entorno:
  ```powershell
  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
  .venv\Scripts\Activate.ps1
  ```
  *(Verás la marca `(.venv)` al inicio de tu terminal indicando el aislamiento exitoso).*
* **Instalación de Dependencias:** Instala el cliente HTTP asíncrono y las librerías necesarias:
  ```powershell
  pip install -r requirements.txt
  ```

---

## 2. Escenarios Operacionales de la CLI - `app_operator.py`

La consola de administración permite ejecutar e interactuar con la infraestructura multicloud simulada en tres escenarios clave:

### Escenario A: Operación Nominal Completa - Éxito
Realiza consultas asíncronas concurrentes a las APIs de los proveedores (AWS, Azure y GCP) usando parámetros seguros y un formato de clúster válido. Las solicitudes de red se despachan en paralelo usando `httpx.AsyncClient` dentro de un bloque `asyncio.TaskGroup`.
```powershell
python src/app_operator.py AWS Azure GCP --cluster-id cluster-us-east-01 --timeout 2.5 --mode nominal
```

### Escenario B: Validación Temprana - Frontera de Seguridad
La CLI intercepta datos corruptos o fuera de rango **antes** de inicializar el bucle de eventos o consumir recursos de red. El sanitizador valida los argumentos y arroja un error controlado `ArgumentTypeError`, abortando inmediatamente con código de salida `2`:
```powershell
# Falla por formato de clúster inválido (Regex estricto):
python src/app_operator.py AWS -c cl_malo -t 2.5

# Falla por timeout fuera de rango (Rango válido: 0.1 a 5.0 segundos):
python src/app_operator.py AWS -c cluster-us-east-01 -t 9.9
```

### Escenario C: Inyección de Caos - Captura Quirurgica Concurrentes
Forzamos una tormenta de red crítica activando `--chaos` y limitando el timeout de las APIs a un segundo. Bajo este entorno, los tres proveedores colapsarán simultáneamente de diferentes formas:
* **AWS:** Supera el tiempo de espera (timeout real de 3s), lanzando `ProviderTimeoutError`.
* **Azure:** Devuelve un código de estado de red 504 Gateway Timeout, relanzando un `NetworkPeeringError`.
* **GCP:** Responde con un formato XML corrupto e inesperado, lanzando `CorruptedPayloadError`.

```powershell
python src/app_operator.py AWS Azure GCP -c cluster-us-east-01 -t 1.0 --chaos
```
El sistema empaqueta todas estas excepciones paralelas en un `ExceptionGroup`, el cual es capturado quirurgicamente en la CLI usando la sintaxis moderna:
```python
try:
    # Lógica de escaneo asíncrono
except* ProviderTimeoutError as eg:
    # Manejo específico de timeouts de proveedores
except* NetworkPeeringError as eg:
    # Manejo de caídas de red de enlace
except* CorruptedPayloadError as eg:
    # Manejo de payloads corruptos
```

---

## 3. Pruebas Automatizadas y Auditoría de Telemetría

Para asegurar la calidad de software y mantener la trazabilidad forense de todos los eventos del sistema, el proyecto incluye dos herramientas automáticas de verificación:

### Suite de Caos (`tests/chaos_suite.py`)
Pruebas que ejecutan subprocesos de la CLI inyectando configuraciones catastróficas y manipulando temporalmente las variables del sistema (*monkeypatching*) hacia dominios no resolubles (`.invalid`) para simular caídas de DNS físicas. Genera un reporte resumido de la resiliencia del software.
```powershell
python tests/chaos_suite.py
```

### Validador Forense - `tests/telemetry_validator.py`
Audita los archivos de registro (`triton_services.log`) generados en disco para verificar los siguientes estándares industriales:
1. **Formato JSON Estructurado:** Valida que cada línea del archivo de log sea un JSON único y procesable formato NDJSON.
2. **Estándar de Tiempo:** Certifica el uso de la hora UTC en formato ISO 8601 terminada estrictamente en "Z".
3. **Análisis Jerárquico:** Evalúa que el formateador asíncrono haya desarmado recursivamente los `ExceptionGroup` guardando la causa raíz y las notas forenses inyectadas con `add_note`.
4. **Integridad Física de Archivos Rotados:** Realiza una descompresión en caliente de los logs históricos comprimidos en formato `.gz` para verificar que no haya archivos corruptos.
```powershell
python tests/telemetry_validator.py
```
```


