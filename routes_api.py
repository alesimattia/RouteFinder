"""
Client della Google Routes API (computeRoutes v2): percorso più veloce col
traffico e alternativa a minor consumo per il carburante richiesto.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import requests

import polyline


logger = logging.getLogger("routefinder.routes")

ENDPOINT = "https://routes.googleapis.com/directions/v2:computeRoutes"

# Solo i campi disegnati: il field mask è obbligatorio, e senza description nè
# steps si resta anche dentro il divieto SEE di mostrarli accanto a una mappa.
FIELD_MASK = ",".join((
    "routes.duration",
    "routes.staticDuration",
    "routes.distanceMeters",
    "routes.polyline.encodedPolyline",
    "routes.routeLabels",
    "routes.travelAdvisory.fuelConsumptionMicroliters",
))

# TRAFFIC_AWARE_OPTIMAL è la sola preferenza che ammette la rotta eco; la
# latenza è la più alta delle tre, da cui il timeout ampio.
REQUEST_TIMEOUT_S = 15

FUEL_TO_EMISSION_TYPE = {
    "gasoline": "GASOLINE",
    "diesel": "DIESEL",
    "hybrid": "HYBRID",
    "electric": "ELECTRIC",
}

LOCAL_TZ = ZoneInfo("Europe/Rome")


class RoutesError(RuntimeError):
    """La Routes API non ha dato un percorso utilizzabile."""


@dataclass(frozen=True)
class RouteInfo:
    """Una rotta della risposta, già decodificata."""

    duration_s: int
    static_duration_s: Optional[int]
    distance_m: int
    points: tuple[tuple[float, float], ...]
    labels: frozenset[str]
    fuel_microliters: Optional[int]

    @property
    def delay_s(self) -> Optional[int]:
        """Ritardo dovuto al traffico; mai negativo, perchè un traffico più
        scorrevole della media non è un ritardo da mostrare."""
        if self.static_duration_s is None:
            return None
        return max(0, self.duration_s - self.static_duration_s)


@dataclass(frozen=True)
class RouteResult:
    """Rotta più veloce, alternativa eco se diversa, e istante del calcolo."""

    fastest: RouteInfo
    eco: Optional[RouteInfo]
    computed_at: datetime


def parse_duration(value: Optional[str]) -> Optional[int]:
    """Durata protobuf ("123s", "12.5s") in secondi interi; None se assente."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.endswith("s"):
        raise RoutesError(f"durata non riconosciuta: {value!r}")
    try:
        return int(round(float(value[:-1])))
    except ValueError as exc:
        raise RoutesError(f"durata non riconosciuta: {value!r}") from exc


def _parse_route(raw: dict) -> RouteInfo:
    """Una rotta della risposta in RouteInfo; senza durata o tracciato non è disegnabile."""
    duration = parse_duration(raw.get("duration"))
    encoded = (raw.get("polyline") or {}).get("encodedPolyline")
    if duration is None or not encoded:
        raise RoutesError("rotta senza durata o senza tracciato")
    fuel = (raw.get("travelAdvisory") or {}).get("fuelConsumptionMicroliters")
    return RouteInfo(
        duration_s=duration,
        static_duration_s=parse_duration(raw.get("staticDuration")),
        distance_m=int(raw.get("distanceMeters", 0)),
        points=tuple(polyline.decode(encoded)),
        labels=frozenset(raw.get("routeLabels") or ()),
        fuel_microliters=int(fuel) if fuel is not None else None,
    )


def parse_response(payload: dict, computed_at: datetime) -> RouteResult:
    """
    Estrae rotta veloce ed eco dalla risposta di computeRoutes.

    Quando la rotta eco coincide con la più veloce l'API restituisce una sola
    rotta con entrambe le etichette: in quel caso l'alternativa non c'è. Una
    rotta FUEL_EFFICIENT con lo stesso tracciato della veloce vale come assente.
    """
    routes = [_parse_route(r) for r in payload.get("routes") or ()]
    if not routes:
        raise RoutesError("nessuna rotta fra origine e destinazione")
    fastest = next((r for r in routes if "DEFAULT_ROUTE" in r.labels), routes[0])
    eco = next((r for r in routes if "FUEL_EFFICIENT" in r.labels), None)
    if eco is fastest or (eco is not None and eco.points == fastest.points):
        eco = None
    return RouteResult(fastest=fastest, eco=eco, computed_at=computed_at)


def build_request(origin: tuple[float, float], destination: tuple[float, float], fuel: str) -> dict:
    """Corpo JSON di computeRoutes per una coppia di punti e un carburante."""
    def waypoint(point: tuple[float, float]) -> dict:
        return {"location": {"latLng": {"latitude": point[0], "longitude": point[1]}}}

    return {
        "origin": waypoint(origin),
        "destination": waypoint(destination),
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
        "requestedReferenceRoutes": ["FUEL_EFFICIENT"],
        "extraComputations": ["FUEL_CONSUMPTION"],
        "routeModifiers": {"vehicleInfo": {"emissionType": FUEL_TO_EMISSION_TYPE[fuel]}},
        "languageCode": "it",
        "units": "METRIC",
    }


def compute_routes(
    origin: tuple[float, float],
    destination: tuple[float, float],
    fuel: str,
    api_key: str,
    session: Optional[requests.Session] = None,
) -> RouteResult:
    """Chiama computeRoutes e restituisce il risultato; RoutesError su ogni guasto."""
    http = session or requests
    started = time.monotonic()
    try:
        response = http.post(
            ENDPOINT,
            json=build_request(origin, destination, fuel),
            headers={"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": FIELD_MASK},
            timeout=REQUEST_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise RoutesError(f"Routes API non raggiungibile: {exc}") from exc
    elapsed_ms = (time.monotonic() - started) * 1000
    if response.status_code != 200:
        # Il corpo d'errore di Google dice il motivo (chiave, API non abilitata,
        # field mask): va nel log, non nella risposta al client.
        logger.warning("Routes API %d in %.0f ms: %s", response.status_code, elapsed_ms, response.text[:500])
        raise RoutesError(f"Routes API ha risposto {response.status_code}")
    result = parse_response(response.json(), datetime.now(LOCAL_TZ))
    logger.info("Routes API ok in %.0f ms: %d s, %d m, eco %s", elapsed_ms,
                result.fastest.duration_s, result.fastest.distance_m,
                "presente" if result.eco else "assente")
    return result
