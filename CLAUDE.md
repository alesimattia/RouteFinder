# routefinder

Servizio FastAPI che calcola il percorso più veloce in auto, col traffico, fra due punti e lo restituisce come immagine: JPEG a colori pieni per il browser, oppure piani 1bpp per display e-paper a pochi colori (`bw`, `bwr`, `bwry`) letti da client embedded. È un **submodule** di `A:\epd` (path `routefinder`, branch `main`, remote `alesimattia/RouteFinder`). È l'analogo della `webapp` cinema (`A:\epd\webapp`), da cui ricalca struttura e deploy.

Il servizio è agnostico rispetto ai client: misure, palette e dither arrivano dalla querystring e nessun layout o costante di un consumer sta nel codice, nei test, nei documenti o nelle memorie. Le regole di un consumer stanno nel suo repository.


## Architettura

| File | Ruolo |
|---|---|
| `main.py` | app FastAPI: `/route`, `/example`, `/health`, `/auth` (GET e POST), `/favicon.ico`, `/` che rimanda a `/docs`; parametri, cache, mappatura degli errori su 400/401/502/503 |
| `auth.py` | token condiviso da header `X-Routefinder-Token`, cookie `routefinder_token` o query `token`; pagina di accesso per il browser |
| `routes_api.py` | `computeRoutes` della Google Routes API, field mask minimo, parsing separato dalla chiamata; ogni guasto, anche un 200 con corpo che non è JSON o non ha la forma attesa, esce come `RoutesError` e quindi come 502 |
| `polyline.py` | decodifica dell'encoded polyline Google, precisione 5 |
| `map_fit.py` | proiezione Web Mercator (`image_anchor()` dà il pixel del centro della mappa) e scelta di zoom, passi frazionari e centro con il tracciato fuori dalle zone vietate; con `prefer` riusa una viewport precedente se ha ancora la scala più alta ammissibile |
| `static_map.py` | base cartografica dalla Maps Static API: `STYLE_CONTENT` (solo nomi delle località, niente simboli), nei colori di Google: una sola base per ogni formato e palette; con i passi frazionari la chiede a `scale=2` e la riduce alla viewport |
| `renderer.py` | composizione in tre passi: `route_layout()` (riquadro e fit della mappa, frazionario fino a 640 px per lato; con la chiave del percorso, che passa solo `/route`, riquadro a larghezza fissa e viewport ricordata per percorso e misura), `compose_route_image()` (base quantizzata, tracciati, simboli di partenza e arrivo, riquadro), codifica; `render_route_image()` li concatena per `/route` |
| `examples.py` | griglia PNG di `/example`, grande quanto la viewport del browser: la cella a colori pieni e ogni palette e dither, con tempo, RAM e peso a 1280x1280 di ciascuna |
| `gfx_font.py` | font Adafruit GFX letti dai `.h` e disegnati a 1 bit |
| `material_icons.py` | icone Material Symbols Rounded a 1 bit |
| `dithering.py`, `arduino_format.py`, `cache.py` | **copie** dei file omonimi della webapp |
| `assets/` | `MaterialSymbolsRounded.ttf` e codepoints, font GFX (`FreeSans*`), licenze |
| `tests/` | pytest: logica pura in `test_core.py`, endpoint in `test_api.py` |

Flusso di `/route`: autenticazione, validazione dei parametri (un JPEG con `dither` senza `colors` si ferma qui con un 307), `computeRoutes` (cache di `ROUTE_CACHE_TTL_S`, 60 s, per origine, destinazione, carburante ed `eco`), misura del riquadro, fit della mappa (con la viewport ricordata per quel percorso e quella misura, vedi "Stabilità dell'immagine fra richieste"), download della base a colori (con i passi frazionari a `scale=2` e ridotta), conversione nella palette col dither (con `format=arduino`, o con `format=jpg` e `colors`), disegno di tracciati e riquadro **dopo** il dither, codifica.

Flusso di `/example`: percorso fisso fra `examples.ORIGIN` e `examples.DESTINATION`, luoghi pubblici scritti nel codice per scelta dell'utente (la querystring sceglie solo `fuel`, `width` e `height`), con la stessa autenticazione, validazione e cache del percorso di `/route`. L'immagine finale è grande quanto la **viewport** del browser, in pixel CSS e non fisici: senza `width` e `height` un browser riceve `VIEWPORT_PAGE`, che rilegge lo stesso URL con `innerWidth` e `innerHeight` accodati, prima di ogni chiamata a pagamento; senza browser vale `DEFAULT_CANVAS` (1920x1080); oltre `MAX_CANVAS` (2560x1600) è un 400, per tenere bassa la RAM delle celle. `grid_for()` prova ogni numero di colonne e tiene la disposizione in cui un rettangolo con le proporzioni della tela viene più grande dentro la cella (`_fitted_area()`), così le celle hanno l'orientazione della finestra e nessuna proporzione fissa sta nel codice, e in cui il riquadro informativo entra; se non entra in nessuna è un 400. Che il riquadro entri non dipende da questa scelta ma da misura della tela e righe del riquadro: a 16:9 con il riquadro completo (riga eco compresa, 255x88 di testo reale a `scale=1`) la soglia sta a 1180x664 (senza la riga eco 1080x608), e cambia con la forma della finestra; in una finestra verticale le celle restano verticali solo da circa 1108 px di larghezza, dove entrano quattro colonne di riquadri. `/example` usa il riquadro del testo reale, non quello a larghezza fissa di `/route`. Le didascalie vanno a capo secondo la larghezza della cella. Poi `route_layout()` una volta sola, una base Static a colori per quel viewport e ogni cella composta con `compose_route_image()`, quindi identica a `render_route_image()` senza chiave di percorso, cioè a ciò che `/route` restituisce con gli stessi parametri e la misura della cella a meno del riquadro a larghezza fissa e della viewport ricordata, che `/example` non usa. Le celle stanno in `CELLS` in testa a `examples.py`, in ordine di lettura. Ogni cella si compone una volta sola, che ne dà immagine e tempo; alla prima composizione per una certa misura anche la RAM, con tracemalloc, che vede oggetti Python e array numpy ma non i buffer delle immagini Pillow. La RAM dipende da misura e algoritmo, non dalla mappa, quindi resta in `_peak_cache` per tutta la vita del processo. Tracemalloc triplica il tempo di Atkinson: alla prima misura quella cella si ricompone senza (`_TRACE_SLOWED`). La PNG è a palette: bianco, nero, rosso e giallo con gli indici di `dithering.PALETTES`, poi lo sfondo, e le voci restanti alla cella rgb, ridotta a quei colori. Il peso a 1280x1280 è esatto per i piani `arduino` e stimato per il JPEG, dalla cella portata a `scale=2`.


## Contratti da non rompere

- **Formato `format=arduino`**: identico a `/cinema/arduino` della webapp. Solo i piani concatenati, nessun header, MSB-first, bit a 1 = pixel non di quel colore, ordine `black`, `red`, `yellow`. `Content-Length = piani * ceil(width/8) * height`, per esempio 30000 a 400x300 `bwr` (2 * 50 * 300) e 22800 a 301x200 `bwry` (3 * 38 * 200). Un client embedded confronta il `Content-Length` esatto: su `/route` niente gzip (escluso nel middleware) e niente clamp silenzioso della misura, che fuori dai limiti (`main.MIN_SIDE` e `static_map.MAX_SIDE` per lato) dà 400.
- **Querystring del contratto**: `origin`, `destination` come `lat,lng` in gradi decimali (arrotondati a sei decimali), `fuel`, `eco`, `width`, `height`, `format=arduino`, `colors`, `dither`. `eco` vale `true` per default (più veloce più alternativa a minor consumo, se diversa); `eco=false` chiede la sola rotta più veloce, e con lei `fuel` non cambia l'immagine. Cambiare un nome o un default qui rompe i client già in campo.
- **Header del token**: `X-Routefinder-Token`, il nome che i client già in campo mandano.
- **`/health` resta aperto e senza chiamate a pagamento**: è il bersaglio di un eventuale pre-warm dei client e la liveness probe di render.
- **`/example` chiama Google al più due volte per richiesta**: una Routes API (nessuna se il percorso è in cache) e una Static API. Una cella nuova si aggiunge a `CELLS` e riusa la base già scaricata, senza chiamare di nuovo `fetch_base_map`.
- **Default di `colors` e `dither`**, per formato. `format=arduino`: `colors` vale `main.DEFAULT_COLORS` (`bwr`), `dither` vale `floyd` con qualunque `colors`. `format=jpg` (qualità `renderer.JPEG_QUALITY`): senza `colors` è a colori pieni, con la mappa nei colori di Google; con `colors` è l'anteprima e-paper, mappa convertita nella palette come `format=arduino`, e senza `dither` è a soglia (`no`). Un JPEG con `dither` senza `colors` non si serve: risponde 307 allo stesso URL con `&colors=bwr` in coda, così chi lo apre nel browser vede la palette applicata. La query originale si conserva così com'è, perchè ricodificata avrebbe le virgole di `origin` e `destination` in `%2C`. I client embedded passano sempre `colors` e `dither`, quindi questi default non li toccano. Nessun parametro si ignora, quindi non c'è header di avviso. A decidere fra le due strade è `color_mode == "rgb"` in `renderer._color_table()`, non il formato.
- Le copie `dithering.py`, `arduino_format.py`, `cache.py` vanno tenute allineate alla webapp: una modifica al formato va fatta in entrambi i repository e in ogni client che lo decodifica.


## Vincoli Google Maps Platform

- **Logo e attribuzione Google non si coprono e non si tolgono**, e restano quantizzati insieme alla mappa. A zoom intero sono nella misura nativa della Static API. Con lo zoom frazionario l'intera base, logo compreso, è una mappa a `scale=2` rimpicciolita, quindi crescono da 1 a 2 volte la misura a `scale=1`, mai meno, con le proporzioni mantenute. È una scelta dell'utente, appoggiata alla pagina "Get Started" della Static API, che descrive come uso normale un'immagine a `scale=2` rimpicciolita dal browser, e ai Terms §3(b), che vietano di modificare, coprire o togliere l'attribuzione, non di rimpicciolire l'immagine in proporzione. Il fit li esclude come zona vietata, allargata con l'ingrandimento, così il tracciato non ci passa sopra. Una richiesta di rimuoverli o di renderli più piccoli della misura a `scale=1` va rifiutata spiegando il vincolo dei termini.
- I risultati della Routes API si mostrano **solo su mappa Google**: niente basi OpenStreetMap o di altri fornitori.
- Con fatturazione nello Spazio Economico Europeo non si mostrano `description` nè i passi del percorso accanto alla mappa: il riquadro non li contiene e il field mask non li chiede. Non aggiungere la strada principale.
- La rotta eco richiede `routingPreference=TRAFFIC_AWARE_OPTIMAL`, che porta ogni chiamata nello SKU Compute Routes Pro (5 000 gratuite al mese). Non chiedere `TOLLS` nè `TRAFFIC_ON_POLYLINE` in `extraComputations`: portano allo SKU Enterprise.
- Se la rotta eco coincide con la più veloce l'API restituisce una sola rotta con entrambe le etichette: in quel caso la riga eco non compare. L'eco-routing è ancora marcato Preview, quindi la sua assenza non è un errore.
- La Static API serve al massimo 640 px per lato a `scale=1` e `scale=2`; oltre 640 si chiede metà misura con `scale=2`, oltre 1280 è un 400. Lo zoom frazionario vale solo fino a 640 px per lato: lì la base si chiede a `scale=2` con una `size` entro 640 e si riduce, oltre resta lo zoom intero. Il limite per lato vive solo in `static_map.MAX_SIDE` (`MAX_SIZE * MAX_SCALE`), che `main.py` importa come default e come controllo di `width` e `height`: non riscriverlo a mano altrove nel codice.
- Non conservare a lungo i contenuti della mappa: la cache dei percorsi e quella delle immagini sono di 60 secondi (`ROUTE_CACHE_TTL_S` in `main.py`). Resta in memoria per ore solo la viewport (centro e zoom, numeri e non contenuto cartografico) di `renderer._viewport_cache`.
- **`ROUTE_CACHE_TTL_S` resta molto sotto l'intervallo con cui un client periodico richiede il percorso** (qualche minuto): ogni richiesta di un client in polling deve andare in miss e portare il traffico aggiornato. In pratica la cache ripaga solo i refresh ravvicinati del browser e `/example`.


## Stile dell'immagine

Pensata per display e-paper a pochi colori e in stile Material: card bianca ad angoli arrotondati con bordo nero, bassa per coprire poca mappa in verticale, dove il tracciato ha più bisogno di spazio (padding 6, interlinea 4, icone alte 1,2 volte le maiuscole), elementi icona più testo su due colonne con le icone di ogni colonna allo stesso x (tempo stimato e ora del calcolo, poi ritardo e distanza, poi la riga eco che parte da sinistra e può occupare tutta la larghezza), tempo stimato in `FreeSans17pt7b` (generato con `fontconvert` di Adafruit GFX da `FreeSans.ttf` di GNU FreeFont 20120503, come gli altri font GFX: non è fra quelli della libreria), righe in `FreeSans12pt7b`, ora del calcolo in `FreeSans9pt7b`. Icone Material Symbols Rounded vuote, soglia a 128. Tracciato veloce rosso pieno con bordo bianco, eco nero tratteggiato, ritardo in rosso oltre 5 minuti. Il testo del riquadro è **ASCII a 7 bit**, perchè i font GFX in uso coprono solo i caratteri da 0x20 a 0x7E: niente lettere accentate nelle stringhe disegnate. Testo, icone e tracciati si disegnano sugli indici di palette dopo il dither, quindi non hanno errore di diffusione attorno. La base è sempre la mappa nei colori di Google, per scelta dell'utente: con la palette e-paper si converte per intero col dither richiesto, quindi strade gialle e arancioni, verde e azzurro diventano trame di nero, rosso e (con `bwry`) giallo, anche accanto al tracciato rosso.

Le misure stanno in testa a `renderer.py` a `scale=1` e raddoppiano con `scale=2`. Dopo una modifica di layout si controlla l'immagine a occhio a due misure diverse, per esempio 600x300 e 400x500, sia `jpg` sia `arduino` ridecodificato.


## Stabilità dell'immagine fra richieste

Vale solo per `/route`, che passa a `renderer.route_layout()` la chiave del percorso (origine, destinazione, carburante, `eco`); `/example` non la passa e compone come prima, con il riquadro del testo reale e il fit da zero, perchè la sua soglia di misura non deve salire.

Due richieste dello stesso percorso devono dare la stessa mappa e lo stesso dither, così un client che confronta i frame successivi vede cambiare solo ciò che è cambiato davvero. Un pixel di scarto nel bordo del riquadro basta a spostare il fit e a cambiare il dither su tutta l'immagine.

- **Riquadro a larghezza fissa**: con la chiave del percorso `renderer.card_size(rows, stable=True)` misura il testo con le avanzate dei glifi (`GfxFont.advance()`), non con l'inchiostro, e per ogni colonna prende il massimo fra i testi reali e un testo di riserva per ogni elemento possibile, presente o no (`LEFT_COLUMN_KINDS`, `RIGHT_COLUMN_KINDS`, `FULL_ROW_KINDS`): durata `59 min` e ritardo `+59 min` a sinistra, aggiornamento `agg. 88:88` e distanza `99,9 km` a destra, eco `+59 min -9,9 L` su riga piena. A `scale=1` il riquadro è largo 259 px (129 di colonna sinistra, 10 di spazio, 108 di colonna destra, 2 x 6 di padding) anche senza ritardo o eco; un testo più largo della riserva, per esempio un tragitto di un'ora o più, lo allarga. L'altezza segue le righe presenti: 64 px, 88 con la riga eco. `draw_card()` riceve lo stesso `stable` del layout, così disegno e fit usano la stessa misura e la colonna destra non si sposta. Senza la chiave la colonna sinistra e la riga eco misurano l'inchiostro del testo reale, la colonna destra l'avanzata, che la allinea. Il colore e la soglia del ritardo (`DELAY_ALERT_S`) sono una scelta dell'utente.
- **Viewport stabile**: `route_layout()` ricorda in `_viewport_cache`, per chiave più larghezza, altezza e `scale`, l'ultima `Viewport` (solo numeri: i termini Google vietano di conservare a lungo la cartografia). Alla richiesta successiva passa quella viewport a `fit_viewport(prefer=...)`, che la restituisce identica solo se zoom e passi frazionari coincidono con quelli della ricerca e il centro è ancora ammissibile: una scala più alta vince sempre, così una deviazione eco o un'alternativa più larga non tengono la mappa piccola per tutta la mattina. La voce dura 6 ore, si rinnova a ogni richiesta e la cache tiene al più 32 chiavi. Con la cache vuota il risultato è quello del fit normale.


## Ambiente di sviluppo

- **Il venv sta solo in `routefinder\.venv`** (gitignored), creato da `C:\Python314`. Non crearne altrove, nemmeno in `A:\tmp\sandbox`.
- Dipendenze di produzione in `requirements.txt`, di test in `requirements-dev.txt`, sempre con vincoli `>=`: con Python 3.14 in locale i pin esatti cercano di compilare da sorgente. La versione di Python del deploy la fissa `.python-version` (3.14, la stessa del venv su cui girano i test), che render legge anche per un servizio creato dalla dashboard; `render.yaml` non la ripete, perchè `PYTHON_VERSION` avrebbe la precedenza.
- `tzdata` è una dipendenza vera: su Windows `zoneinfo` non ha il database dei fusi, e l'ora del calcolo si stampa in `Europe/Rome` mentre render gira in UTC.
- Test: `.venv\Scripts\python -m pytest -q -p no:cacheprovider`. Le API Google sono sostituite da fixture, quindi girano senza chiave. `tests/test_core.py` contiene anche l'encoder di polilinea usato per costruire le fixture. La stessa suite gira su GitHub Actions (`.github/workflows/CI.yml`) a ogni push su `main`, a ogni pull request e ogni lunedì (i vincoli `>=` cambiano le dipendenze senza commit), con la versione di `.python-version`; prima `ruff check --select F,E9 .`, dopo l'avvio con lo `startCommand` di `render.yaml` e `/health` e `/route` senza token interrogati con curl.
- `tests/test_memory.py` misura il picco di RSS di `/route` e `/example` alla misura massima in un processo separato con `MALLOC_MMAP_THRESHOLD_` di `render.yaml`, contro un tetto di 400 MB sotto i 512 del piano gratuito. Gira solo su Linux, quindi in CI: in locale risulta skipped.
- Avvio locale con chiave vera: `GOOGLE_MAPS_API_KEY=... ROUTEFINDER_TOKEN=... .venv\Scripts\uvicorn main:app --reload`.
- I moduli stanno nella root, non in un pacchetto: `tests/conftest.py` aggiunge la root a `sys.path`.


## Deploy

render.com free tier, regione Frankfurt, `uvicorn --workers 1`, come la webapp. `GOOGLE_MAPS_API_KEY` e `ROUTEFINDER_TOKEN` si impostano dal pannello di render (`sync: false` in `render.yaml`), mai nel repository. Senza `ROUTEFINDER_TOKEN` il servizio rifiuta ogni richiesta a `/route`. Le 750 ore-istanza gratuite al mese sono condivise con la webapp: il servizio si spegne dopo 15 minuti senza traffico in ingresso.

- Il servizio su render è stato creato dalla dashboard e non come Blueprint, quindi `render.yaml` documenta la configurazione ma render non la applica: Build Command e Start Command vanno tenuti allineati a mano nella dashboard, e lo stesso vale per `MALLOC_MMAP_THRESHOLD_=262144`, che nella dashboard va impostata a mano.
- Auto-Deploy su "After CI Checks Pass" (`autoDeployTrigger: checksPass` in `render.yaml`, da impostare anche nella dashboard): render deploya una commit di `main` solo se il workflow `tests` di GitHub Actions è passato.
- Il log di accesso di uvicorn non contiene il health check interno di render (`/health` riuscito da IP privato, ogni pochi secondi): lo scarta `RenderHealthCheckFilter` in `main.py`. Le richieste pubbliche, compreso un eventuale pre-warm dei client, arrivano dal proxy con l'IP reale di `X-Forwarded-For` e restano nel log, come un health check fallito.
- Dopo `/auth` senza destinazione si torna a `/docs` (`auth.DEFAULT_NEXT`): `/route` senza parametri risponde 400 e `/example` chiama Google.


## Git

- Solo branch `main`, come gli altri submodule di `A:\epd`: niente branch per singolo client.
- Le modifiche si committano in questo repository e nel padre si aggiorna il puntatore del submodule; prima di committare il padre la commit di `routefinder` va pushata.


## Convenzioni

- Commenti e documentazione in italiano, identificatori in inglese. Docstring breve sopra ogni funzione, che dice cosa fa e soprattutto perchè.
- Logging con `logging` (logger `routefinder.*`), mai `print`. Il corpo d'errore di Google va nel log, non nella risposta al client.
- Memoria persistente del progetto in `routefinder\.claude\memory\` (indice `MEMORY.md`), separata da quella del repository padre.
