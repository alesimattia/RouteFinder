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
- render.com free: 0,1 CPU e 512 MB; 750 ore-istanza al mese condivise fra tutti i servizi free del workspace, spegnimento dopo 15 min senza traffico in ingresso (il health check interno non lo impedisce), cold start dichiarato di circa un minuto.
- render.com, servizio Python: versione da `PYTHON_VERSION`, poi da `.python-version` nella root, poi dal default legato alla data di creazione (3.14.10 dal 2026-11-03). Il servizio è stato creato dalla dashboard, non come Blueprint, quindi `render.yaml` non si applica: build e start command vanno allineati a mano, la versione la fissa `.python-version`.
- render.com, log: il health check interno arriva ogni pochi secondi da un IP privato 10.x senza `X-Forwarded-For`; il traffico pubblico passa dal proxy, che uvicorn considera fidato, e appare con l'IP reale e porta `:0`. `RenderHealthCheckFilter` in `main.py` scarta solo `/health` 2xx da IP privato non loopback.
- Cache del percorso a 60 s: ogni richiesta del firmware, a un ciclo di ~301 s dalla precedente, va in miss. Il firmware chiede il percorso solo nella fascia percorso, 24 volte al giorno con 07:00-09:00, e manda `/health` di sveglia solo se il servizio non ha risposto negli ultimi 15 min.

Misure di CPU su PC-030 (percorso sintetico, base finta): per una richiesta del firmware a 610x290 fit 35-80 ms, composizione Floyd 30-50 ms, codifica arduino 1,5 ms; `map_fit._densify()` è il 76% del fit. Sul free tier da 0,1 CPU la parte di CPU sale nell'ordine del secondo.

Verificato con chiamate reali (Ancona 43.6158,13.5189 -> 43.5,13.3, server locale con `uvicorn --env-file .env`): `routes.staticDuration` arriva (riga del ritardo presente, "+0 min" a traffico scorrevole); `TRAFFIC_AWARE_OPTIMAL` risponde in ~220 ms, ben sotto il timeout di 15 s; Static API 140-300 ms a 610 px, ~650 ms a 1280 (`scale=2`); logo "Google" e "Map data ©2026 Google" leggibili anche dopo il dither a 1 bit. Su quella tratta l'API ha restituito una sola rotta (log "eco assente").

**How to apply:** prima di aggiungere una funzione della Routes API controllare in quale SKU porta la richiesta. Vedi [[rendering_decisions]].
