# routefinder

Servizio FastAPI che calcola il percorso più veloce in auto, col traffico, fra due punti e lo restituisce come immagine: JPEG a colori pieni per il browser, oppure piani 1bpp pronti per il pannello e-paper di [ePaper-weather-dashboard](https://github.com/alesimattia/ePaper-weather-dashboard), di cui è un submodule. Il firmware lo mostra al posto della locandina cinema nella fascia del mattino.

L'immagine è una mappa Google con il tracciato più veloce in rosso, l'alternativa a minor consumo per il carburante scelto in nero tratteggiato (solo se diversa), i simboli di partenza e arrivo e, in alto a destra, un riquadro con tempo stimato, ritardo dovuto al traffico, distanza, risparmio dell'alternativa eco e ora del calcolo. Testo e icone usano gli stessi font del pannello (FreeSans di Adafruit GFX e Material Symbols Rounded), e il tracciato non passa mai sotto il riquadro nè sopra logo e attribuzione Google.

## Endpoint

| Percorso | Uso |
|---|---|
| `GET /route` | immagine del percorso, richiede il token |
| `GET /example` | griglia PNG con l'immagine in ogni palette e dither, richiede il token |
| `GET /` | rimanda a `/docs`, la documentazione interattiva dei parametri |
| `GET /health` | liveness probe e bersaglio del pre-warm del firmware, aperto |
| `GET /auth`, `POST /auth` | pagina in cui incollare il token da browser |

Parametri di `/route`:

| Parametro | Default | Valori |
|---|---|---|
| `origin`, `destination` | obbligatori | `lat,lng` in gradi decimali |
| `fuel` | `gasoline` | `gasoline`, `diesel`, `hybrid`, `electric` |
| `width`, `height` | la misura massima | da 64 a 1280 px per lato |
| `format` | `jpg` | `jpg` (qualità 80), `arduino` |
| `colors` | `bwr` con `arduino`; nessuno con `jpg`, a colori pieni | `bw`, `bwr`, `bwry` |
| `dither` | `floyd` con `arduino`, con ogni `colors`; nessuno con `jpg`, a soglia se c'è `colors` | `no`, `floyd`, `atkinson`, `bayer` |

Con `format=arduino` il corpo sono i soli piani concatenati, MSB-first, bit a 1 = pixel non di quel colore, nello stesso formato di `/cinema/arduino` della webapp: `Content-Length = piani * ceil(width/8) * height`, per esempio 44660 byte a 610x290 `bwr`. Nessuna compressione su questo percorso, perchè il firmware confronta il `Content-Length`. Con `format=jpg` senza `colors` l'immagine è a colori pieni, con la mappa nei colori di Google. Con `colors` il JPEG è l'anteprima del pannello: la stessa mappa a colori convertita nella palette come per `arduino`, a soglia se manca `dither`. Un `dither` senza `colors` rimanda con un 307 allo stesso URL con `colors=bwr` aggiunto, così la palette applicata resta visibile nella barra degli indirizzi.

`/example` disegna un percorso fisso, `ORIGIN` e `DESTINATION` in testa a `examples.py`, e accetta solo `fuel`, `width` e `height`. Restituisce un'unica PNG grande quanto la viewport del browser: aperto senza `width` e `height`, il browser riceve una pagina che rilegge lo stesso URL con le misure della finestra in pixel CSS; da curl o con `width` e `height` vale la misura indicata, di default 1920x1080, al massimo 2560x1600. Dentro ci sono la cella a colori pieni, le palette `bw`, `bwr`, `bwry` con dither `no`, `floyd` e `bayer`, una cella `bwry` con `atkinson` e un riepilogo, in celle piccole quanto basta a stare nella finestra: il numero di colonne si sceglie perchè le celle somiglino al pannello, e sotto ~1440x790 il riquadro informativo non entra più e la risposta è un 400. Sotto ogni cella ci sono il suffisso dell'URL `/route` che la riproduce, tempo e RAM di generazione e il peso del corpo di `/route` a 1280x1280 (esatto per `arduino`, stimato per il JPEG). Ogni richiesta fa una chiamata alla Routes API (nessuna se il percorso è in cache) e una alla Static API: la base a colori, che ogni cella converte nella sua palette.

## Autenticazione

Ogni chiamata a `/route` consuma quota delle API Google, quindi serve il token configurato in `ROUTEFINDER_TOKEN`. Il firmware lo manda nell'header `X-Routefinder-Token`; da browser, senza token, compare una pagina in cui incollarlo, che lo salva in un cookie HttpOnly e torna alla pagina richiesta. Senza `ROUTEFINDER_TOKEN` sul servizio nessuna richiesta passa.

## Configurazione e deploy

Render.com, free tier, come la webapp cinema (`render.yaml`). Variabili da impostare dal pannello di render:

- `GOOGLE_MAPS_API_KEY`: chiave di un progetto Google Cloud con fatturazione attiva e con **Routes API** e **Maps Static API** abilitate; conviene limitarla a quelle due API.
- `ROUTEFINDER_TOKEN`: stringa casuale lunga, la stessa che va in `Env.h` del firmware.

Costi: la preferenza `TRAFFIC_AWARE_OPTIMAL`, l'unica che ammette la rotta eco, porta ogni richiesta nello SKU Compute Routes Pro (5 000 chiamate gratuite al mese); la Static API sta in Essentials (10 000). Con un fetch ogni 10 minuti per due ore al giorno si resta abbondantemente nella soglia gratuita. Le risposte restano in cache 5 minuti, così un refresh del browser non paga una seconda chiamata.

## Sviluppo

```sh
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
GOOGLE_MAPS_API_KEY=... ROUTEFINDER_TOKEN=... .venv/bin/uvicorn main:app --reload
```

I test sostituiscono le API Google con fixture, quindi girano senza chiave.

| File | Ruolo |
|---|---|
| `main.py` | app, parametri, cache, errori |
| `auth.py` | token, cookie, pagina di accesso |
| `routes_api.py` | `computeRoutes` con field mask minimo, parsing della risposta |
| `polyline.py` | decodifica dell'encoded polyline |
| `map_fit.py` | proiezione Web Mercator, scelta di zoom e centro con il tracciato fuori dalle zone vietate |
| `static_map.py` | base cartografica dalla Static API, nei colori di Google per ogni formato |
| `renderer.py` | composizione di base, tracciati, simboli e riquadro |
| `examples.py` | griglia di `/example` con le misure di ogni cella |
| `gfx_font.py`, `material_icons.py` | testo e icone a 1 bit |
| `dithering.py`, `arduino_format.py`, `cache.py` | copie della webapp, da tenere allineate |

## Termini d'uso Google

Logo e attribuzione Google restano nella misura nativa in basso, non coperti, e i risultati della Routes API si mostrano solo su mappa Google, come chiedono i termini di Google Maps Platform. Con un account di fatturazione nello Spazio Economico Europeo non si possono mostrare descrizione e passi del percorso accanto alla mappa: il riquadro non li contiene e il field mask non li chiede.

## Licenze

Codice GPL-3.0 (`LICENSE`). Font: Material Symbols Rounded, Apache 2.0 (`assets/material/LICENSE`); FreeSans di Adafruit GFX, BSD (`assets/gfx/LICENSE`).
