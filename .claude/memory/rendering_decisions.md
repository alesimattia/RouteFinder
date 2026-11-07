---
name: Scelte di rendering del percorso
description: perchè il fit usa una summed-area table, perchè si disegna dopo il dither, base sempre a colori convertita nella palette completa (mappa in grigi scartata), costi di tempo e RAM di ogni dither e come si misurano, convenzione della cap height GFX, ancoraggio del segnaposto, logo Google nativo e senza soglia
metadata:
  type: project
---

- **Fit della mappa** (`map_fit.fit_viewport`): per zoom decrescente si rasterizzano i tracciati densificati a passo di 1 px più l'ingombro dei simboli, e una summed-area table dice in O(1) per ogni traslazione quanti pixel cadono nelle zone vietate (riquadro, logo, attribuzione, allargate del padding della linea). Vince lo zoom più alto con almeno una traslazione ammissibile, e fra quelle la più centrata. I vertici si controllano prima della densificazione, che a zoom 18 sarebbe di decine di migliaia di punti. Il centro si arrotonda a sei decimali prima di proiettare, perchè è il valore che va nell'URL della Static API.
- **Disegno dopo il dither**: la base si quantizza, poi tracciati, simboli e riquadro si disegnano sugli indici di palette. Zero errore di diffusione attorno al testo, come `dither_zero_error` della webapp.
- **Base sempre a colori** (scelta esplicita dell'utente): una sola Static API nei colori di Google (`STYLE_CONTENT`, nessuno stile di colore), convertita da `compose_route_image` nella palette completa di `colors` col dither richiesto. Conseguenze viste su mappa reale (Ancona, 470x293): `no` e `bayer` quasi tutto bianco (terra, mare e strade chiari finiscono sul bianco, restano nomi e logo); `floyd` trama fitta su tutta la terra, con puntini rossi (`bwr`) o gialli (`bwry`) sui rilievi accanto al tracciato rosso; `bwr` e `bwry` diversi. Una mappa in grigi (`saturation:-100` più grigi fissi per strade e acqua) è stata usata e scartata: teneva il rosso libero per il tracciato ma rendeva `bwr` = `bwry`. Pillow 13, `quantize(FLOYDSTEINBERG)` verso `bwr`/`bwry` con input grigio puro: pixel rossi isolati entro ~60 colonne dal bordo destro; `dithering.py` non si tocca (copia della webapp).
- **Costi dei dither** (Mac, tracemalloc): Atkinson per antidiagonali numpy (identico bit per bit al vecchio ciclo pixel per pixel, che a 1280x1280 prendeva 1,5 s e ~300 MB): 50 ms e 5 MB a 470x293, 384 ms e 61 MB a 1280x1280, tempo triplicato sotto tracemalloc; Bayer ~108 byte/pixel (15 MB a 470x293); Floyd e soglia ~0,3 MB. tracemalloc non vede i buffer Pillow (immagine da 4 MB = 460 B tracciati). JPEG a 1280: la proporzione sui pixel della cella sbaglia di 2 volte, la cella portata a scale=2 di +20% (0,26 contro 0,22 MB reali).
- **Tempi di `/example`** (server locale, chiamate reali): 1920x1080 prima richiesta 0,78 s (Routes ~210 ms + Static ~260 ms + ~300 ms di celle), RAM già in cache 0,50 s, cache dell'immagine <1 ms; prima delle ottimizzazioni 3,2 s, quasi tutti Atkinson sotto tracemalloc. Ora le chiamate Google sono la parte maggiore.
- **Cap height dei font GFX = `-yOffset` della "H"** (17 px per `FreeSans12pt7b`), la convenzione del firmware (`Layout_common.h`): il glyph è alto 18 perchè l'inchiostro occupa anche la riga della baseline.
- **Segnaposto d'arrivo**: la punta è l'ultima riga d'inchiostro della maschera di `location_on`, non il bordo del quadrato.
- **Logo Google**: per scelta dell'utente resta nella misura nativa, quantizzato insieme alla mappa senza soglia dedicata. L'utente avrebbe voluto toglierlo: i termini non lo consentono.
- **Icone**: Material Symbols Rounded con assi Fill 0, Grade 0, Optical Size 20, Weight 500; a 16-30 px il peso 400 si spezza a 1 bit.

**Why:** l'utente vuole stile coerente col pannello e in stile Material, riquadro informativo in alto a destra mai sovrapposto al tracciato, nessuna strada principale.

**How to apply:** dopo ogni modifica di layout generare l'anteprima a 610x290 e 610x325, in `jpg` e in `arduino` ridecodificato, e guardarla. Vedi [[google_api_facts]].
