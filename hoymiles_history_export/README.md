# Hoymiles History Export

⚠️ **Proof of Concept – ungetestet in dieser Form.** Dieses Add-on ist das
Ergebnis einer Recherche/eines Experiments während einer Home-Assistant-
Fehlersuche, kein fertig getestetes Produkt. Die Kernlogik (Login bei der
Hoymiles-Cloud, Tages-Historie abrufen, in Home Assistant importieren) wurde
erfolgreich gegen einen echten Hoymiles-Account und eine echte Home-
Assistant-Instanz getestet – allerdings manuell, Schritt für Schritt, mit
fest eingetragenen Geräte-IDs. Die hier vorliegende, verallgemeinerte
Add-on-Verpackung (automatische Geräte-Erkennung, freie Konfiguration für
beliebige Anlagen) wurde **nicht** selbst als fertiges Add-on durchgetestet.

Veröffentlicht, damit andere es ausprobieren können – um zu bestätigen, dass
es funktioniert, es für den eigenen Anwendungsfall zu erweitern, oder
zurückzumelden, was nicht funktioniert (bitte über die
[Issues](https://github.com/DrdotHouse2106/Hoymiles-History-Export-f-r-HomeAssistant/issues)
dieses Repos).

## Was es tut

Ein einmalig laufendes Home-Assistant-Add-on, das **fehlende historische
Solar-Ertragsdaten** in eine Home-Assistant-Statistik nachträgt, indem es
Tages-Leistungskurven direkt aus der **Hoymiles S-Miles Cloud** abruft und
zu Stundenwerten integriert.

## Wann das nützlich ist

- Euer lokales Hoymiles-Gateway/Add-on war für eine Weile falsch
  konfiguriert (falsche IP, offline o.ä.), und Home Assistant hat dadurch
  eine Lücke in der Solar-Verlaufsgrafik – obwohl der eigentliche
  Wechselrichter die ganze Zeit brav an die Hoymiles-Cloud/App gemeldet hat.
- Ihr nutzt Home Assistant erst seit einem Zeitpunkt **nach** der
  PV-Installation und wollt die komplette Historie bis zum Installationstag
  nachtragen.

Es rührt eure laufenden Daten oder euer bestehendes Hoymiles-Add-on/die
Integration nicht an – es füllt nur die Historie der Ziel-Statistik für den
von euch angegebenen Zeitraum, und hängt sich (falls direkt danach schon
Daten vorhanden sind) über einen einzigen sauberen Verschiebungs-Schritt
nahtlos daran an, ohne Sprung oder Doppelzählung.

## Einrichtung

1. Repository zum Add-on Store hinzufügen (*Einstellungen → Add-ons →
   Add-on Store → ⋮ → Repositories*), dann **Hoymiles History Export**
   installieren.
2. Konfigurieren:
   - `hoymiles_user` / `hoymiles_password` – euer Hoymiles-/S-Miles-Cloud-
     Account (derselbe wie in der Hoymiles-App).
   - `hoymiles_plant_id` – eure Anlagen-/Station-ID (in der Hoymiles-App
     sichtbar, oder in euren bestehenden Hoymiles-Entitäten, z.B.
     `sensor.hoymiles_gateway_solarh_<plant_id>_...`).
   - `statistic_id` – die Home-Assistant-Energiestatistik, die aufgefüllt
     werden soll, z.B. `sensor.hoymiles_gateway_solarh_3023680_today_eq`
     (sollte ein `total_increasing`/`total`-Energiesensor sein, typisch der
     „heute produziert"-Zähler eurer Hoymiles-Integration).
   - `start_date` / `end_date` – **der Zeitraum, den ihr nachtragen wollt**,
     frei wählbar (`JJJJ-MM-TT`, beide Tage eingeschlossen). Schaut vorher in
     eurer Verlaufsgrafik/Statistik nach, welche Tage genau fehlen.
   - `time_zone` – eure Home-Assistant-Zeitzone (muss passen, damit
     Tagesgrenzen korrekt liegen).
   - `days_per_batch` – wie viele Tage vor jedem Import-Aufruf verarbeitet
     werden; der Standardwert (30) ist ein vernünftiges, rücksichtsvolles
     Tempo gegenüber den Hoymiles-Servern.
3. Add-on starten und das Log beobachten. Es protokolliert für jeden Tag den
   berechneten Ertrag und schließt mit einer Zusammenfassung ab. Das Add-on
   beendet sich danach von selbst (Container stoppt) – das ist bei einem
   Einmal-Werkzeug normal, kein Fehler.
4. Energie-Dashboard/Verlaufsgrafik der `statistic_id` neu laden, um zu
   prüfen, ob die Lücke gefüllt ist.

## Wie es funktioniert

- Login bei der Hoymiles-Cloud auf demselben Weg wie das Community-Add-on
  „HoyMiles Solar Gateway" (Argon2-Login, mit automatischem Rückfall auf den
  älteren MD5-Login, falls das für den jeweiligen Account nicht verfügbar
  ist).
- Erkennt automatisch alle Mikro-Wechselrichter unter der angegebenen
  Anlage.
- Ruft für jeden Tag im Zeitraum über Hoymiles' `count_by_day`-Endpunkt pro
  Wechselrichter eine untertägige Leistungskurve ab (`MI_POWER`, je nach
  Tageszeit alle 15–60 Minuten ein Messpunkt) und integriert sie numerisch
  zu 24 Stunden-Wh-Werten, summiert über alle gefundenen Wechselrichter.
- Schreibt diese Stundenwerte per `recorder/import_statistics` in Home
  Assistants Langzeitstatistik, angehängt an den Summenwert, den die
  Statistik unmittelbar vor `start_date` bereits hat (oder bei 0 beginnend,
  falls davor noch gar nichts existiert).
- Falls direkt nach `end_date` schon Daten existieren, wird einmalig ein
  `recorder/adjust_sum_statistics`-Sprung angewendet, damit sich beide
  Abschnitte exakt aneinanderfügen, ohne sonst etwas zu verändern.

Spricht mit Home Assistant über die Supervisor-eigene API-Weiterleitung
(`homeassistant_api: true`) – es muss also nirgends ein Long-Lived-Token
manuell eingetragen werden.

## Genauigkeit

Die numerische Integration einer abgetasteten Leistungskurve ist eine
Näherung, kein perfekter Zählerstand – rechnet mit ca. 1–2% Abweichung pro
Tag gegenüber einem durchgehend messenden Zähler. Für eine wieder
vollständige Verlaufsgrafik ausreichend genau, aber nicht als
abrechnungsgenaue Rekonstruktion gedacht.

## Danksagung

Der Hoymiles-Cloud-Login-Ablauf folgt demselben Verfahren wie
[dmslabsbr/hoymiles](https://github.com/dmslabsbr/hoymiles) (Community-
Home-Assistant-Add-on). Das Protobuf-Schema der `count_by_day`-Antwort
wurde vom Projekt
[ioBroker.hoymiles](https://github.com/Eistee82/ioBroker.hoymiles)
reverse-engineered (MIT-Lizenz, Copyright (c) 2026 Eistee82); dieses Add-on
implementiert einen eigenen, neu geschriebenen Decoder gegen dieses
veröffentlichte Schema.

## Haftungsausschluss

Inoffizielles Community-Tool, Proof of Concept. Nicht von Hoymiles
autorisiert oder unterstützt. Nutzt eine undokumentierte Cloud-API, die
Hoymiles jederzeit ändern oder einschränken könnte.
