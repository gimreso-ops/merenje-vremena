@echo off
REM Snimanje saobracaja ALGE <-> Splash preko com0com para.
REM COM3  = pravi port na koji je prikljucen ALGE
REM COM10 = kraj virtuelnog para za skriptu (Splash podesiti na COM11)
REM Brzinu/paritet uskladiti sa podesavanjem u Splash-u.
cd /d "%~dp0"
python com_sniffer.py bridge --real COM3 --virtual COM10 --baud 9600 --parity N --minutes 30
pause
