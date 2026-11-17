# Merenje vremena u plivanju

Kućni sistem za merenje vremena plivača (start + 8 staza), sa ciljem da se rezultati šalju u Splash Meet Manager.

## Sadržaj

| Fajl | Namena |
|---|---|
| `stoperica.py` | Merenje sa tastature: START taster pokreće/zaustavlja vreme, Esc resetuje; režim 2–8 staza (tasteri 1–8). Vremena se upisuju u `rezultati/rezultati_GGGGMMDD.txt`, a po završetku trke pravi se `splash/EventB-HeatC.txt` za Splash. |
| `com_sniffer.py` | Praćenje i snimanje saobraćaja na COM portu (ALGE ↔ Splash) u log fajl. Režimi `list`, `sniff`, `bridge` (com0com). |
| `snimi_bridge.bat` | Primer pokretanja `com_sniffer.py` u bridge režimu. |

## Pokretanje

```
pip install pyserial          # samo za com_sniffer.py
python stoperica.py
python com_sniffer.py list
```

## Gotovi .exe fajlovi

GitHub Actions posle svake izmene na `main` pravi `Stoperica.exe` i `ComSniffer.exe`
(kartica **Actions** → poslednje pokretanje → **Artifacts**). Za trajno izdanje:
`git tag v1.0` i `git push origin v1.0` — fajlovi se pojavljuju u **Releases**.

## Program takmičenja i prazne staze

`program.txt` pored programa (primer: `program_primer.txt`), jedan red po disciplini:

```
# disciplina;dužina;broj grupa;naziv
1;50;3;50m slobodno žene
7F;100;1;100m leđno finale
```

Posle svake trke Stoperica prelazi na sledeću grupu, a posle poslednje grupe na sledeću disciplinu.
Pored svake staze je dugme **isključi** (radi i tokom trke) — isključena staza se ne čeka i ne ide u Splash fajl.

## Splash Meet Manager

Splash ima interfejs **Generic Txt Heat Files** ([Swim Wiki](https://wiki.swimrankings.net/index.php/Meet_Manager:Timing_System_Generic_Txt_Heat_Files)):
jedan CSV fajl (separator `;`) po grupi, ime `EventB-HeatC.txt` ili `SessionA-EventB-HeatC.txt`.

```
LANE;TIME50
1;35.22
2;1:02.10
```

U Splash-u izabrati ovaj sistem za merenje i usmeriti ga na folder `splash/`.
Novije verzije Splash-a (11.81460+) imaju i Generic Timing System JSON REST API.

## Plan

1. Merenje sa tastature i upis u txt ✔
2. Izvoz za Splash (Generic Txt Heat Files) ✔ — proveriti uvoz u Splash
3. Arduino (Uno/Mega): 9 tastera, precizan takt (DS3231), slanje vremena preko USB-a
4. Opciono: Splash JSON REST API umesto fajlova
