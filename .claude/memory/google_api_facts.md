---
name: Google Routes e Static Maps, fatti verificati
description: cosa è confermato dalla documentazione ufficiale (eco-routing, Italia, SKU, limiti Static API, termini SEE, render free tier) e cosa resta da verificare con una chiamata reale
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

Ancora da verificare con una chiamata reale: il percorso `routes.staticDuration` del field mask (se manca, `RouteInfo.delay_s` è None e la riga del ritardo non compare), la latenza di `TRAFFIC_AWARE_OPTIMAL` (timeout del client a 15 s, misura nel log `routefinder.routes`), la leggibilità del logo Google dopo il dither a 1 bit.

**How to apply:** prima di aggiungere una funzione della Routes API controllare in quale SKU porta la richiesta. Vedi [[rendering_decisions]].
