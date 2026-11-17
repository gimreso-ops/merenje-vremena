# Merenje vremena u plivanju

Kućni sistem za merenje vremena plivača (start + 8 staza), sa ciljem da se rezultati šalju u Splash Meet Manager.

## Sadržaj

| Fajl | Namena |
|---|---|
| `stoperica.py` | Merenje sa tastature: START taster pokreće/zaustavlja vreme, Esc resetuje; režim 2–8 staza (tasteri 1–8). Vremena se upisuju u `rezultati/rezultati_GGGGMMDD.txt`. |
| `com_sniffer.py` | Praćenje i snimanje saobraćaja na COM portu (ALGE ↔ Splash) u log fajl. Režimi `list`, `sniff`, `bridge` (com0com). |
| `snimi_bridge.bat` | Primer pokretanja `com_sniffer.py` u bridge režimu. |

## Pokretanje

```
pip install pyserial          # samo za com_sniffer.py
python stoperica.py
python com_sniffer.py list
```

## Plan

1. Merenje sa tastature i upis u txt ✔
2. Snimanje ALGE → Splash protokola
3. Arduino (Uno/Mega): 9 tastera, precizan takt (DS3231), slanje vremena preko USB-a
4. Emulacija ALGE protokola za Splash Meet Manager
