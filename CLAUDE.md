# routefinder

Servizio FastAPI che calcola il percorso più veloce in auto, col traffico, fra due punti e lo restituisce come immagine: JPEG a colori pieni per il browser, oppure piani 1bpp per il pannello e-paper del firmware `ePaper-weather-dashboard`. È un **submodule** di `A:\epd` (path `routefinder`, branch `main`, remote `alesimattia/RouteFinder`): il firmware lo mostra al posto della locandina cinema nella fascia del mattino. È l'analogo della `webapp` cinema (`A:\epd\webapp`), da cui ricalca struttura e deploy.

Il firmware che lo consuma sta nella cartella padre: `Routefinder.h` (impostazioni, fascia, sorgente del riquadro), `fetchRouteImage()` nel `.ino`, `CinemaStore.h` (slot in flash), pagina `/route` in `Maintenance.h`. Le regole di quel repository sono in `A:\epd\CLAUDE.md`.


## Architettura

| File | Ruolo |
|---|---|
| `main.py` | app FastAPI: `/route`, `/health`, `/auth` (GET e POST), `/favicon.ico`; parametri, cache, mappatura degli errori su 400/401/502/503 |
| `auth.py` | token condiviso da header `X-Routefinder-Token`, cookie `routefinder_token` o query `token`; pagina di accesso per il browser |
| `routes_api.py` | `computeRoutes` della Google Routes API, field mask minimo, parsing separato dalla chiamata |
| `polyline.py` | decodifica dell'encoded polyline Google, precisione 5 |
| `map_fit.py` | proiezione Web Mercator e scelta di zoom e centro con il tracciato fuori dalle zone vietate |
| `static_map.py` | base cartografica dalla Maps Static API: `STYLE_CONTENT` (solo nomi delle località, niente simboli) sempre, `STYLE_GRAY` (grigi per il tre colori) solo nella palette del pannello, cioè con `format=arduino` o con `format=jpg` e `colors` |
| `renderer.py` | composizione: base, tracciati, simboli di partenza e arrivo, riquadro informativo |
| `gfx_font.py` | font Adafruit GFX letti dai `.h` e disegnati a 1 bit, parser portato da `tools/layout_preview.py` del firmware |
| `material_icons.py` | icone Material Symbols Rounded a 1 bit |
| `dithering.py`, `arduino_format.py`, `cache.py` | **copie** dei file omonimi della webapp |
| `assets/` | `MaterialSymbolsRounded.ttf` e codepoints (identici a `A:\epd\icons\weather\source`), font GFX del pannello, licenze |
| `tests/` | pytest: logica pura in `test_core.py`, endpoint in `test_api.py` |

Flusso di `/route`: autenticazione, validazione dei parametri (un JPEG con `dither` senza `colors` si ferma qui con un 307), `computeRoutes` (cache 5 min per origine, destinazione e carburante), misura del riquadro, fit della mappa, download della base, quantizzazione su bianco e nero (con `format=arduino`, o con `format=jpg` e `colors`), disegno di tracciati e riquadro **dopo** il dither, codifica.


## Contratti da non rompere

- **Formato `format=arduino`**: identico a `/cinema/arduino` della webapp. Solo i piani concatenati, nessun header, MSB-first, bit a 1 = pixel non di quel colore, ordine `black`, `red`, `yellow`. `Content-Length = piani * ceil(width/8) * height`, per esempio 44660 a 610x290 `bwr` e 50050 a 610x325. Il firmware confronta il `Content-Length` esatto: su `/route` niente gzip (escluso nel middleware) e niente clamp silenzioso della misura, che fuori dai limiti dà 400.
- **Querystring che il firmware compone** (`Routefinder::buildUrl()`): `origin`, `destination` come `lat,lng` a sei decimali, `fuel`, `width`, `height`, `format=arduino`, `colors=bwr`, `dither=floyd`. Cambiare un nome o un default qui richiede di cambiare anche il firmware.
- **Header del token**: `X-Routefinder-Token`, lo stesso nome nel firmware.
- **`/health` resta aperto e senza chiamate a pagamento**: è il bersaglio del pre-warm del firmware e la liveness probe di render.
- **Default di `colors` e `dither`**, per formato. `format=arduino`: `colors` vale `main.DEFAULT_COLORS` (`bwr`), `dither` vale `floyd` con qualunque `colors`. `format=jpg` (qualità `renderer.JPEG_QUALITY`): senza `colors` è a colori pieni, con la mappa nei colori di Google; con `colors` è l'anteprima del pannello, mappa in grigi quantizzata come `format=arduino`, e senza `dither` è a soglia (`no`). Un JPEG con `dither` senza `colors` non si serve: risponde 307 allo stesso URL con `&colors=bwr` in coda, così chi lo apre nel browser vede la palette applicata. La query originale si conserva così com'è, perchè ricodificata avrebbe le virgole di `origin` e `destination` in `%2C`. Il firmware passa sempre `colors` e `dither`, quindi questi default non lo toccano. Nessun parametro si ignora, quindi non c'è header di avviso. A decidere fra le due strade è `color_mode == "rgb"`, non il formato: lo usano sia lo stile della Static API sia `renderer._color_table()`.
- Le copie `dithering.py`, `arduino_format.py`, `cache.py` vanno tenute allineate alla webapp: una modifica al formato va fatta in entrambi i repository e nel firmware.


## Vincoli Google Maps Platform

- **Logo e attribuzione Google non si coprono, non si tolgono, non si ridimensionano**: restano nella misura nativa della Static API, quantizzati insieme alla mappa. Il fit li esclude come zona vietata, così il tracciato non ci passa sopra. Una richiesta di rimuoverli va rifiutata spiegando il vincolo dei termini.
- I risultati della Routes API si mostrano **solo su mappa Google**: niente basi OpenStreetMap o di altri fornitori.
- Con fatturazione nello Spazio Economico Europeo non si mostrano `description` nè i passi del percorso accanto alla mappa: il riquadro non li contiene e il field mask non li chiede. Non aggiungere la strada principale.
- La rotta eco richiede `routingPreference=TRAFFIC_AWARE_OPTIMAL`, che porta ogni chiamata nello SKU Compute Routes Pro (5 000 gratuite al mese). Non chiedere `TOLLS` nè `TRAFFIC_ON_POLYLINE` in `extraComputations`: portano allo SKU Enterprise.
- Se la rotta eco coincide con la più veloce l'API restituisce una sola rotta con entrambe le etichette: in quel caso la riga eco non compare. L'eco-routing è ancora marcato Preview, quindi la sua assenza non è un errore.
- La Static API serve al massimo 640 px per lato a `scale=1` e `scale=2`; oltre 640 si chiede metà misura con `scale=2`, oltre 1280 è un 400. Il limite per lato vive solo in `static_map.MAX_SIDE` (`MAX_SIZE * MAX_SCALE`), che `main.py` importa come default e come controllo di `width` e `height`: non riscriverlo a mano altrove nel codice.
- Non conservare a lungo i contenuti della mappa: la cache è di 5 minuti e il firmware smette di mostrare il percorso dopo 3 cadenze (sorgente Automatica) o un'ora (sorgente Percorso).


## Stile dell'immagine

Coerente col pannello e in stile Material: card bianca ad angoli arrotondati con bordo nero, righe icona più testo, tempo stimato in `FreeSansBold18pt7b`, righe in `FreeSans12pt7b`, ora del calcolo in `FreeSans9pt7b`. Icone Material Symbols Rounded vuote, soglia a 128 come `tools/weather_icons.py` del firmware. Tracciato veloce rosso pieno con bordo bianco, eco nero tratteggiato, ritardo in rosso oltre 5 minuti. Il testo del riquadro è **ASCII a 7 bit**, come i font del firmware: niente lettere accentate nelle stringhe disegnate. Testo, icone e tracciati si disegnano sugli indici di palette dopo il dither, quindi non hanno errore di diffusione attorno. La base in grigi si quantizza sempre sulla palette `bw`, i cui indici 0 e 1 valgono in ogni palette: verso `bwr` il Floyd-Steinberg di Pillow sceglie a volte il rosso anche per un grigio puro, soprattutto vicino al bordo destro. Così rosso e giallo restano solo a tracciati, testo e icone.

Le misure stanno in testa a `renderer.py` a `scale=1` e raddoppiano con `scale=2`. Dopo una modifica di layout si controlla l'immagine a occhio, a 610x290 e 610x325, sia `jpg` sia `arduino` ridecodificato.


## Ambiente di sviluppo

- **Il venv sta solo in `routefinder\.venv`** (gitignored), creato da `C:\Python314`. Non crearne altrove, nemmeno in `A:\tmp\sandbox`.
- Dipendenze di produzione in `requirements.txt`, di test in `requirements-dev.txt`, sempre con vincoli `>=`: con Python 3.14 in locale i pin esatti cercano di compilare da sorgente. Il deploy usa Python 3.11.9 (`render.yaml`).
- `tzdata` è una dipendenza vera: su Windows `zoneinfo` non ha il database dei fusi, e l'ora del calcolo si stampa in `Europe/Rome` mentre render gira in UTC.
- Test: `.venv\Scripts\python -m pytest -q -p no:cacheprovider`. Le API Google sono sostituite da fixture, quindi girano senza chiave. `tests/test_core.py` contiene anche l'encoder di polilinea usato per costruire le fixture.
- Avvio locale con chiave vera: `GOOGLE_MAPS_API_KEY=... ROUTEFINDER_TOKEN=... .venv\Scripts\uvicorn main:app --reload`.
- I moduli stanno nella root, non in un pacchetto: `tests/conftest.py` aggiunge la root a `sys.path`.


## Deploy

render.com free tier, regione Frankfurt, `uvicorn --workers 1`, come la webapp. `GOOGLE_MAPS_API_KEY` e `ROUTEFINDER_TOKEN` si impostano dal pannello di render (`sync: false` in `render.yaml`), mai nel repository. Senza `ROUTEFINDER_TOKEN` il servizio rifiuta ogni richiesta a `/route`. Le 750 ore-istanza gratuite al mese sono condivise con la webapp: il servizio resta sveglio solo nella fascia del mattino più 15 minuti.


## Git

- Solo branch `main`, come gli altri submodule di `A:\epd`: niente branch di pannello o di layout qui.
- Le modifiche si committano in questo repository e nel padre si aggiorna il puntatore del submodule; prima di committare il padre la commit di `routefinder` va pushata.


## Convenzioni

- Commenti e documentazione in italiano, identificatori in inglese. Docstring breve sopra ogni funzione, che dice cosa fa e soprattutto perchè.
- Logging con `logging` (logger `routefinder.*`), mai `print`. Il corpo d'errore di Google va nel log, non nella risposta al client.
- Memoria persistente del progetto in `routefinder\.claude\memory\` (indice `MEMORY.md`), separata da quella del firmware.
