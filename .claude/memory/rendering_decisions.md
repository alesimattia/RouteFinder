---
name: Scelte di rendering del percorso
description: perchè il fit usa una summed-area table, perchè si disegna dopo il dither, perchè la base si quantizza su bw (rosso spurio del Floyd di Pillow), convenzione della cap height GFX, ancoraggio del segnaposto, logo Google nativo e senza soglia
metadata:
  type: project
---

- **Fit della mappa** (`map_fit.fit_viewport`): per zoom decrescente si rasterizzano i tracciati densificati a passo di 1 px più l'ingombro dei simboli, e una summed-area table dice in O(1) per ogni traslazione quanti pixel cadono nelle zone vietate (riquadro, logo, attribuzione, allargate del padding della linea). Vince lo zoom più alto con almeno una traslazione ammissibile, e fra quelle la più centrata. I vertici si controllano prima della densificazione, che a zoom 18 sarebbe di decine di migliaia di punti. Il centro si arrotonda a sei decimali prima di proiettare, perchè è il valore che va nell'URL della Static API.
- **Disegno dopo il dither**: la base si quantizza, poi tracciati, simboli e riquadro si disegnano sugli indici di palette. Zero errore di diffusione attorno al testo, come `dither_zero_error` della webapp.
- **Base quantizzata su `bw`** qualunque sia `colors` (`renderer.render_route_image`): indici 0/1 = bianco/nero in tutte le palette. Pillow 13, `quantize(FLOYDSTEINBERG)` verso `bwr`/`bwry` con input grigio puro: pixel ROSSI isolati, entro ~60 colonne dal bordo destro (mappa reale 0-3, grigio uniforme 0x50 ~0,1%); threshold, atkinson e bayer no. `dithering.py` non si tocca (copia della webapp, dove lo stesso effetto vale per `/cinema`). Test di guardia: `test_gray_base_never_adds_accent_pixels`.
- **Cap height dei font GFX = `-yOffset` della "H"** (17 px per `FreeSans12pt7b`), la convenzione del firmware (`Layout_common.h`): il glyph è alto 18 perchè l'inchiostro occupa anche la riga della baseline.
- **Segnaposto d'arrivo**: la punta è l'ultima riga d'inchiostro della maschera di `location_on`, non il bordo del quadrato.
- **Logo Google**: per scelta dell'utente resta nella misura nativa, quantizzato insieme alla mappa senza soglia dedicata. L'utente avrebbe voluto toglierlo: i termini non lo consentono.
- **Icone**: Material Symbols Rounded con assi Fill 0, Grade 0, Optical Size 20, Weight 500; a 16-30 px il peso 400 si spezza a 1 bit.

**Why:** l'utente vuole stile coerente col pannello e in stile Material, riquadro informativo in alto a destra mai sovrapposto al tracciato, nessuna strada principale.

**How to apply:** dopo ogni modifica di layout generare l'anteprima a 610x290 e 610x325, in `jpg` e in `arduino` ridecodificato, e guardarla. Vedi [[google_api_facts]].
