/*
  tasteri.ino - 9 tastera (start + 8 staza) za Stopericu
  Radi na Arduino Uno i Mega.

  Povezivanje:
    taster START  : pin D2  <-> GND
    staza 1..8    : pinovi D3..D10 <-> GND
    (interni pull-up; za duge kablove dodati 4,7k ka 5V i 100nF ka GND na svakom ulazu)

  Poruke ka računaru (115200 baud, svaka poruka u svom redu):
    S <ms>        start
    L<n> <ms>     staza n (1..8) dodirnula
    <ms> = millis() Arduina u trenutku pritiska (Stoperica ga za sada ne koristi,
           vreme meri računar; služi za kasniji prelazak na merenje u Arduinu).

  Na upit "?" Arduino odgovara identifikacijom, da Stoperica sama pronađe port:
    MV-TASTERI 1.0

  Probati bez Stoperice: Arduino IDE -> Serial Monitor, 115200, pa kratko spojiti
  pin sa GND žicom.
*/

const char ID_TEXT[] = "MV-TASTERI 1.0";

const uint8_t NUM_INPUTS = 9;
// indeks 0 = start, 1..8 = staze
const uint8_t PINS[NUM_INPUTS] = {2, 3, 4, 5, 6, 7, 8, 9, 10};

// Posle pritiska ulaz se zaključa: odbija treperenje kontakta i dupli pritisak.
// Ponovo se aktivira tek kad je taster pušten i prošlo je LOCKOUT_MS.
const unsigned long LOCKOUT_MS = 500;
// Taster mora biti pušten bar ovoliko da bi se smatrao puštenim.
const unsigned long RELEASE_MS = 50;

const uint8_t LED = LED_BUILTIN;   // kratko zasvetli na svaki pritisak

bool armed[NUM_INPUTS];
unsigned long pressedAt[NUM_INPUTS];
unsigned long releasedSince[NUM_INPUTS];
unsigned long ledOffAt = 0;

void sendEvent(uint8_t i, unsigned long t) {
  if (i == 0) {
    Serial.print('S');
  } else {
    Serial.print('L');
    Serial.print(i);
  }
  Serial.print(' ');
  Serial.println(t);
}

void setup() {
  for (uint8_t i = 0; i < NUM_INPUTS; i++) {
    pinMode(PINS[i], INPUT_PULLUP);
    // Ako je taster pritisnut pri uključenju (ili kabl u kratkom spoju),
    // ne prijavljuje se dok se ne pusti.
    armed[i] = digitalRead(PINS[i]) == HIGH;
    pressedAt[i] = 0;
    releasedSince[i] = 0;
  }
  pinMode(LED, OUTPUT);
  Serial.begin(115200);
  Serial.println(ID_TEXT);
}

void loop() {
  unsigned long now = millis();

  // 1) Najpre očitati sve ulaze, da se istovremeni dodiri ne zakašnjavaju.
  bool low[NUM_INPUTS];
  for (uint8_t i = 0; i < NUM_INPUTS; i++) {
    low[i] = digitalRead(PINS[i]) == LOW;
  }

  // 2) Obrada
  for (uint8_t i = 0; i < NUM_INPUTS; i++) {
    if (low[i]) {
      releasedSince[i] = 0;
      if (armed[i]) {
        armed[i] = false;
        pressedAt[i] = now;
        sendEvent(i, now);
        digitalWrite(LED, HIGH);
        ledOffAt = now + 100;
      }
    } else if (!armed[i]) {
      if (releasedSince[i] == 0) {
        releasedSince[i] = now ? now : 1;
      }
      if (now - releasedSince[i] >= RELEASE_MS && now - pressedAt[i] >= LOCKOUT_MS) {
        armed[i] = true;
      }
    }
  }

  if (ledOffAt && (long)(now - ledOffAt) >= 0) {
    digitalWrite(LED, LOW);
    ledOffAt = 0;
  }

  // 3) Upit za identifikaciju
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '?') {
      Serial.println(ID_TEXT);
    }
  }
}
