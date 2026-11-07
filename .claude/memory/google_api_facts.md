---
name: Google Routes e Static Maps, fatti verificati
description: cosa è confermato dalla documentazione ufficiale (eco-routing, Italia, SKU, limiti Static API, termini SEE, render free tier) e dalle chiamate reali (staticDuration presente, latenze, logo leggibile a 1 bit)
metadata:
  type: reference
---

Fatti confermati sulla documentazione ufficiale Google Maps Platform e render.com:
- La rotta eco (`requestedReferenceRoutes=[FUEL_EFFICIENT]`) richiede `routingPreference=TRAFFIC_AWARE_OPTIMAL`, `travelMode=DRIVE`, nessun waypoint intermedio. È ancora marcata Preview.
- L'Italia è nell'elenco ufficiale delle regioni dell'eco-routing in auto.
- `emissionType`: `GASOLINE`, `DIESEL`, `HYBRID`, `ELECTRIC` (default `GASOLINE`); niente GPL nè metano.
- Rotta eco uguale alla veloce: una sola rotta con entrambe le etichette `DEFAULT_ROUTE` e `FUEL_EFFICIENT`.
- Field mask obbligatorio (`X-Goog-FieldMask`), chiave in `X-Goog-Api-Key`.
- SKU: `TRAFFIC_AWARE_OPTIMAL` → Compute Routes **Pro**, 5 000 chiamate gratuite al mese, poi 10 $ ogni mille; `TOLLS` o `TRAFFIC_ON_POLYLINE` → Enterprise. Static Maps Essentials, 10 000 gratuite.
- Static API: `size` massimo 640x640 a `scale=1` e `scale=2`; `scale=2` raddoppia i pixel a parità di copertura. Zoom intero, URL fino a 16 384 caratteri.
- Termini: attribuzione e logo non modificabili nè coperti; risultati della Routes API solo su mappa Google. Con fatturazione SEE è vietato mostrare `description` e i passi del percorso con una mappa.
- render.com free: 750 ore-istanza al mese condivise fra tutti i servizi free del workspace, spegnimento dopo 15 min senza traffico, cold start dichiarato di circa un minuto.

Verificato con chiamate reali (Ancona 43.6158,13.5189 -> 43.5,13.3, server locale con `uvicorn --env-file .env`): `routes.staticDuration` arriva (riga del ritardo presente, "+0 min" a traffico scorrevole); `TRAFFIC_AWARE_OPTIMAL` risponde in ~220 ms, ben sotto il timeout di 15 s; Static API 140-300 ms a 610 px, ~650 ms a 1280 (`scale=2`); logo "Google" e "Map data ©2026 Google" leggibili anche dopo il dither a 1 bit. Su quella tratta l'API ha restituito una sola rotta (log "eco assente").

**How to apply:** prima di aggiungere una funzione della Routes API controllare in quale SKU porta la richiesta. Vedi [[rendering_decisions]].
