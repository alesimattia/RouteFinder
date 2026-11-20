# routefinder

Servizio FastAPI che calcola il percorso più veloce in auto, col traffico, fra due punti e lo restituisce come immagine: JPEG a colori pieni per il browser, oppure piani 1bpp per display e-paper a pochi colori (`bw`, `bwr`, `bwry`) letti da client embedded. È un **submodule** di `A:\epd` (path `routefinder`, branch `main`, remote `alesimattia/RouteFinder`). È l'analogo della `webapp` cinema (`A:\epd\webapp`), da cui ricalca struttura e deploy.

Il servizio è agnostico rispetto ai client: misure, palette e dither arrivano dalla querystring e nessun layout o costante di un consumer sta nel codice, nei test, nei documenti o nelle memorie. Le regole di un consumer stanno nel suo repository.


## Architettura

| File | Ruolo |
|---|---|
| `main.py` | app FastAPI: `/route`, `/example`, `/health`, `/auth` (GET e POST), `/favicon.ico`, `/` che rimanda a `/docs`; parametri, cache, mappatura degli errori su 400/401/502/503 |
| `auth.py` | token condiviso da header `X-Routefinder-Token`, cookie `routefinder_token` o query `token`; pagina di accesso per il browser |
| `routes_api.py` | `computeRoutes` della Google Routes API, field mask minimo, parsing separato dalla chiamata |
| `polyline.py` | decodifica dell'encoded polyline Google, precisione 5 |
| `map_fit.py` | proiezione Web Mercator e scelta di zoom e centro con il tracciato fuori dalle zone vietate |
| `static_map.py` | base cartografica dalla Maps Static API: `STYLE_CONTENT` (solo nomi delle località, niente simboli), nei colori di Google: una sola base per ogni formato e palette |
| `renderer.py` | composizione in tre passi: `route_layout()` (riquadro e fit della mappa), `compose_route_image()` (base quantizzata, tracciati, simboli di partenza e arrivo, riquadro), codifica; `render_route_image()` li concatena per `/route` |
| `examples.py` | griglia PNG di `/example`, grande quanto la viewport del browser: la cella a colori pieni e ogni palette e dither, con tempo, RAM e peso a 1280x1280 di ciascuna |
| `gfx_font.py` | font Adafruit GFX letti dai `.h` e disegnati a 1 bit |
| `material_icons.py` | icone Material Symbols Rounded a 1 bit |
| `dithering.py`, `arduino_format.py`, `cache.py` | **copie** dei file omonimi della webapp |
| `assets/` | `MaterialSymbolsRounded.ttf` e codepoints, font GFX (`FreeSans*`), licenze |
| `tests/` | pytest: logica pura in `test_core.py`, endpoint in `test_api.py` |

Flusso di `/route`: autenticazione, validazione dei parametri (un JPEG con `dither` senza `colors` si ferma qui con un 307), `computeRoutes` (cache 5 min per origine, destinazione e carburante), misura del riquadro, fit della mappa, download della base a colori, conversione nella palette col dither (con `format=arduino`, o con `format=jpg` e `colors`), disegno di tracciati e riquadro **dopo** il dither, codifica.

Flusso di `/example`: percorso fisso fra `examples.ORIGIN` e `examples.DESTINATION`, luoghi pubblici scritti nel codice per scelta dell'utente (la querystring sceglie solo `fuel`, `width` e `height`), con la stessa autenticazione, validazione e cache del percorso di `/route`. L'immagine finale è grande quanto la **viewport** del browser, in pixel CSS e non fisici: senza `width` e `height` un browser riceve `VIEWPORT_PAGE`, che rilegge lo stesso URL con `innerWidth` e `innerHeight` accodati, prima di ogni chiamata a pagamento; senza browser vale `DEFAULT_CANVAS` (1920x1080); oltre `MAX_CANVAS` (2560x1600) è un 400, per tenere bassa la RAM delle celle. `grid_for()` prova ogni numero di colonne e tiene la disposizione in cui un rettangolo con le proporzioni della tela viene più grande dentro la cella (`_fitted_area()`), così le celle hanno l'orientazione della finestra e nessuna proporzione fissa sta nel codice, e in cui il riquadro informativo entra; se non entra in nessuna è un 400. Che il riquadro entri non dipende da questa scelta ma da misura della tela e righe del riquadro: a 16:9 con il riquadro completo la soglia sta intorno a 1290x725, e cambia con la forma della finestra. Le didascalie vanno a capo secondo la larghezza della cella. Poi `route_layout()` una volta sola, una base Static a colori per quel viewport e ogni cella composta con `compose_route_image()`, quindi identica a ciò che `/route` restituisce con gli stessi parametri e la misura della cella. Le celle stanno in `CELLS` in testa a `examples.py`, in ordine di lettura. Ogni cella si compone una volta sola, che ne dà immagine e tempo; alla prima composizione per una certa misura anche la RAM, con tracemalloc, che vede oggetti Python e array numpy ma non i buffer delle immagini Pillow. La RAM dipende da misura e algoritmo, non dalla mappa, quindi resta in `_peak_cache` per tutta la vita del processo. Tracemalloc triplica il tempo di Atkinson: alla prima misura quella cella si ricompone senza (`_TRACE_SLOWED`). La PNG è a palette: bianco, nero, rosso e giallo con gli indici di `dithering.PALETTES`, poi lo sfondo, e le voci restanti alla cella rgb, ridotta a quei colori. Il peso a 1280x1280 è esatto per i piani `arduino` e stimato per il JPEG, dalla cella portata a `scale=2`.


## Contratti da non rompere

- **Formato `format=arduino`**: identico a `/cinema/arduino` della webapp. Solo i piani concatenati, nessun header, MSB-first, bit a 1 = pixel non di quel colore, ordine `black`, `red`, `yellow`. `Content-Length = piani * ceil(width/8) * height`, per esempio 30000 a 400x300 `bwr` (2 * 50 * 300) e 22800 a 301x200 `bwry` (3 * 38 * 200). Un client embedded confronta il `Content-Length` esatto: su `/route` niente gzip (escluso nel middleware) e niente clamp silenzioso della misura, che fuori dai limiti (`main.MIN_SIDE` e `static_map.MAX_SIDE` per lato) dà 400.
- **Querystring del contratto**: `origin`, `destination` come `lat,lng` in gradi decimali (arrotondati a sei decimali), `fuel`, `width`, `height`, `format=arduino`, `colors`, `dither`. Cambiare un nome o un default qui rompe i client già in campo.
- **Header del token**: `X-Routefinder-Token`, il nome che i client già in campo mandano.
- **`/health` resta aperto e senza chiamate a pagamento**: è il bersaglio di un eventuale pre-warm dei client e la liveness probe di render.
- **`/example` chiama Google al più due volte per richiesta**: una Routes API (nessuna se il percorso è in cache) e una Static API. Una cella nuova si aggiunge a `CELLS` e riusa la base già scaricata, senza chiamare di nuovo `fetch_base_map`.
- **Default di `colors` e `dither`**, per formato. `format=arduino`: `colors` vale `main.DEFAULT_COLORS` (`bwr`), `dither` vale `floyd` con qualunque `colors`. `format=jpg` (qualità `renderer.JPEG_QUALITY`): senza `colors` è a colori pieni, con la mappa nei colori di Google; con `colors` è l'anteprima e-paper, mappa convertita nella palette come `format=arduino`, e senza `dither` è a soglia (`no`). Un JPEG con `dither` senza `colors` non si serve: risponde 307 allo stesso URL con `&colors=bwr` in coda, così chi lo apre nel browser vede la palette applicata. La query originale si conserva così com'è, perchè ricodificata avrebbe le virgole di `origin` e `destination` in `%2C`. I client embedded passano sempre `colors` e `dither`, quindi questi default non li toccano. Nessun parametro si ignora, quindi non c'è header di avviso. A decidere fra le due strade è `color_mode == "rgb"` in `renderer._color_table()`, non il formato.
- Le copie `dithering.py`, `arduino_format.py`, `cache.py` vanno tenute allineate alla webapp: una modifica al formato va fatta in entrambi i repository e in ogni client che lo decodifica.


## Vincoli Google Maps Platform

- **Logo e attribuzione Google non si coprono, non si tolgono, non si ridimensionano**: restano nella misura nativa della Static API, quantizzati insieme alla mappa. Il fit li esclude come zona vietata, così il tracciato non ci passa sopra. Una richiesta di rimuoverli va rifiutata spiegando il vincolo dei termini.
- I risultati della Routes API si mostrano **solo su mappa Google**: niente basi OpenStreetMap o di altri fornitori.
- Con fatturazione nello Spazio Economico Europeo non si mostrano `description` nè i passi del percorso accanto alla mappa: il riquadro non li contiene e il field mask non li chiede. Non aggiungere la strada principale.
- La rotta eco richiede `routingPreference=TRAFFIC_AWARE_OPTIMAL`, che porta ogni chiamata nello SKU Compute Routes Pro (5 000 gratuite al mese). Non chiedere `TOLLS` nè `TRAFFIC_ON_POLYLINE` in `extraComputations`: portano allo SKU Enterprise.
- Se la rotta eco coincide con la più veloce l'API restituisce una sola rotta con entrambe le etichette: in quel caso la riga eco non compare. L'eco-routing è ancora marcato Preview, quindi la sua assenza non è un errore.
- La Static API serve al massimo 640 px per lato a `scale=1` e `scale=2`; oltre 640 si chiede metà misura con `scale=2`, oltre 1280 è un 400. Il limite per lato vive solo in `static_map.MAX_SIDE` (`MAX_SIZE * MAX_SCALE`), che `main.py` importa come default e come controllo di `width` e `height`: non riscriverlo a mano altrove nel codice.
- Non conservare a lungo i contenuti della mappa: la cache è di 60 secondi.
- **`ROUTE_CACHE_TTL_S` resta molto sotto l'intervallo con cui un client periodico richiede il percorso** (qualche minuto): ogni richiesta di un client in polling deve andare in miss e portare il traffico aggiornato. La cache serve solo al browser e a `/example`.


## Stile dell'immagine

Pensata per display e-paper a pochi colori e in stile Material: card bianca ad angoli arrotondati con bordo nero, righe icona più testo, tempo stimato in `FreeSansBold18pt7b`, righe in `FreeSans12pt7b`, ora del calcolo in `FreeSans9pt7b`. Icone Material Symbols Rounded vuote, soglia a 128. Tracciato veloce rosso pieno con bordo bianco, eco nero tratteggiato, ritardo in rosso oltre 5 minuti. Il testo del riquadro è **ASCII a 7 bit**, perchè i font GFX in uso coprono solo i caratteri da 0x20 a 0x7E: niente lettere accentate nelle stringhe disegnate. Testo, icone e tracciati si disegnano sugli indici di palette dopo il dither, quindi non hanno errore di diffusione attorno. La base è sempre la mappa nei colori di Google, per scelta dell'utente: con la palette e-paper si converte per intero col dither richiesto, quindi strade gialle e arancioni, verde e azzurro diventano trame di nero, rosso e (con `bwry`) giallo, anche accanto al tracciato rosso.

Le misure stanno in testa a `renderer.py` a `scale=1` e raddoppiano con `scale=2`. Dopo una modifica di layout si controlla l'immagine a occhio a due misure diverse, per esempio 600x300 e 400x500, sia `jpg` sia `arduino` ridecodificato.


## Ambiente di sviluppo

- **Il venv sta solo in `routefinder\.venv`** (gitignored), creato da `C:\Python314`. Non crearne altrove, nemmeno in `A:\tmp\sandbox`.
- Dipendenze di produzione in `requirements.txt`, di test in `requirements-dev.txt`, sempre con vincoli `>=`: con Python 3.14 in locale i pin esatti cercano di compilare da sorgente. La versione di Python del deploy la fissa `.python-version` (3.14, la stessa del venv su cui girano i test), che render legge anche per un servizio creato dalla dashboard; `render.yaml` non la ripete, perchè `PYTHON_VERSION` avrebbe la precedenza.
- `tzdata` è una dipendenza vera: su Windows `zoneinfo` non ha il database dei fusi, e l'ora del calcolo si stampa in `Europe/Rome` mentre render gira in UTC.
- Test: `.venv\Scripts\python -m pytest -q -p no:cacheprovider`. Le API Google sono sostituite da fixture, quindi girano senza chiave. `tests/test_core.py` contiene anche l'encoder di polilinea usato per costruire le fixture.
- Avvio locale con chiave vera: `GOOGLE_MAPS_API_KEY=... ROUTEFINDER_TOKEN=... .venv\Scripts\uvicorn main:app --reload`.
- I moduli stanno nella root, non in un pacchetto: `tests/conftest.py` aggiunge la root a `sys.path`.


## Deploy

render.com free tier, regione Frankfurt, `uvicorn --workers 1`, come la webapp. `GOOGLE_MAPS_API_KEY` e `ROUTEFINDER_TOKEN` si impostano dal pannello di render (`sync: false` in `render.yaml`), mai nel repository. Senza `ROUTEFINDER_TOKEN` il servizio rifiuta ogni richiesta a `/route`. Le 750 ore-istanza gratuite al mese sono condivise con la webapp: il servizio si spegne dopo 15 minuti senza traffico in ingresso.

- Il servizio su render è stato creato dalla dashboard e non come Blueprint, quindi `render.yaml` documenta la configurazione ma render non la applica: Build Command e Start Command vanno tenuti allineati a mano nella dashboard.
- Il log di accesso di uvicorn non contiene il health check interno di render (`/health` riuscito da IP privato, ogni pochi secondi): lo scarta `RenderHealthCheckFilter` in `main.py`. Le richieste pubbliche, compreso un eventuale pre-warm dei client, arrivano dal proxy con l'IP reale di `X-Forwarded-For` e restano nel log, come un health check fallito.
- Dopo `/auth` senza destinazione si torna a `/docs` (`auth.DEFAULT_NEXT`): `/route` senza parametri risponde 400 e `/example` chiama Google.


## Git

- Solo branch `main`, come gli altri submodule di `A:\epd`: niente branch per singolo client.
- Le modifiche si committano in questo repository e nel padre si aggiorna il puntatore del submodule; prima di committare il padre la commit di `routefinder` va pushata.


## Convenzioni

- Commenti e documentazione in italiano, identificatori in inglese. Docstring breve sopra ogni funzione, che dice cosa fa e soprattutto perchè.
- Logging con `logging` (logger `routefinder.*`), mai `print`. Il corpo d'errore di Google va nel log, non nella risposta al client.
- Memoria persistente del progetto in `routefinder\.claude\memory\` (indice `MEMORY.md`), separata da quella del repository padre.
