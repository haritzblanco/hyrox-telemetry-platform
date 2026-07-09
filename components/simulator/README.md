# Simulador IoT Hyrox

Simulador de dispositivos IoT que simula atletas en una sesión Hyrox y publica datos biométricos en un broker MQTT.

## Requisitos

- Python 3.11 o superior
- Broker MQTT accesible (por defecto `localhost:1883`)

## Instalación

```bash
cd components/simulator
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Uso

```bash
hyrox-sim --athlete-id atleta-001 --interval 1.0 --seed 42
```

Argumentos disponibles:

| Argumento       | Descripción                                  | Default       |
|-----------------|----------------------------------------------|---------------|
| `--athlete-id`  | Identificador del atleta                     | `atleta-001`  |
| `--broker-host` | Host del broker MQTT                         | `localhost`   |
| `--broker-port` | Puerto del broker MQTT                       | `1883`        |
| `--interval`    | Intervalo entre publicaciones (segundos)     | `1.0`         |
| `--qos`         | Calidad de servicio MQTT (0, 1, 2)           | `0`           |
| `--seed`        | Semilla para reproducibilidad                | aleatoria     |
| `--log-level`   | Nivel de log (DEBUG, INFO, WARNING, ERROR)   | `INFO`        |

## Modelo de datos

Cada atleta publica en el topic `hyrox/<athlete_id>/biometrics` un mensaje JSON con la siguiente estructura:

```json
{
  "athlete_id": "atleta-001",
  "timestamp": "2026-05-09T18:23:45.123456+00:00",
  "heart_rate": 152,
  "cadence": 168
}
```
