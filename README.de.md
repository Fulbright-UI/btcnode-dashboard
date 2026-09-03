# btcnode-dashboard

*[English version](README.md)*

Eine Statusseite für den eigenen Bitcoin-Fullnode. Läuft auf einem Raspberry Pi
neben Bitcoin Core und zeigt im Browser, was der Node gerade tut.

**Der Punkt dabei: Die Seite kann nichts.** Ein Python-Programm fragt den Node
ab und schreibt fertige Dateien in einen Ordner. Der Webserver liefert nur
diese Dateien aus — er kennt den Node nicht, nimmt keine Eingaben entgegen und
führt nichts aus. Wer den Webserver übernimmt, bekommt Dateien.

Kein Framework, keine Fremdpakete, kein Docker. Ein Python-Programm aus der
Standardbibliothek und ein Installationsskript.

**Es installiert weder Bitcoin Core noch einen Electrum-Server.** Das ist
Absicht: Ein Skript, das 750 GB belegt und stundenlang aus der Quelle baut, ist
etwas anderes als eine Statusseite — und es soll niemand ungelesen ausführen.
Läuft bei dir schon ein Node, ist das Dashboard in zwei Minuten eingerichtet.

Die Seite spricht **Deutsch oder Englisch**. Die Installation fragt einmal
danach; ändern lässt es sich später mit einer Zeile in der Konfiguration.

---

## Was es zeigt

- **Fortschritt der Synchronisation** mit Tempo und geschätzter Restzeit
- **Verbundene Knoten** als Fächer: der eigene Node in der Mitte, die
  Gegenstellen links und rechts, mit Adresse, Netzart, Latenz und bewegter
  Datenmenge an der Linie. Beim Überfahren kommen Kennung, Dienste und
  Verbindungsdauer dazu. Wer den letzten Block zuerst angekündigt hat,
  bekommt eine orange Speiche — mit einer 24-Stunden-Rangliste, wer am
  häufigsten zuerst ankündigt. Der eigene Electrum-Server, der wie jede
  Gegenstelle über P2P andockt, bekommt eine eigene Farbe
- **Kettenabgleich**: Bitcoin Core fragt alle paar Minuten einen zufälligen
  Knoten nach seiner Höhe — die Abwehr gegen eine untergeschobene Kette. Die
  Stichproben der letzten Stunde stehen als Punktreihe im Kopf der Netzkarte;
  meldet ein Knoten mehr Blöcke als du hast, erscheint oben eine Meldung
- **Ein Satz aus den frühen Tagen** in der Kopfzeile, getippt wie in ein
  Terminal — Cryptography-Mailingliste, P2P Foundation, bitcointalk —, ein
  Zitat je Datenabruf, Wortlaut gegen das Nakamoto Institute geprüft
- **Systemzustand** des Rechners: die Temperatur des Tages als ein Balken je
  Stunde, CPU, Arbeitsspeicher, Plattenplatz — bei einem Raspberry Pi auch
  die Stromversorgung, weil Unterspannung die häufigste Ursache beschädigter
  Blockchain-Daten ist
- **Die Gebühr, die man nehmen sollte**: Cores sparsame Schätzung für den
  nächsten Block, groß im Kopf, darunter die vorsichtige, wenn sie abweicht
- **Tage bis zur Halbierung**, mit Datum und den Blöcken, die noch fehlen
- **Mempool** (Speicher, wartende Gebühren, Füllstand) und **Kette**
  (Schwierigkeit, nächste Anpassung), dazu eine Schätzung, wie viel Strom
  die Schwierigkeit bedeutet — aus der Hashrate bei angenommener
  Flotteneffizienz, als Balken neben Klimaanlagen, Rechenzentren, Bankwesen
  und Goldförderung
- **Volumen und Gebührenverlauf** der letzten 24 Stunden, ein Balken je
  Block, Gebühren nach Stufe gefärbt; jeder Balken der Seite zeigt beim
  Zeigen seinen Wert
- **Electrum-Server**, falls einer läuft, mit den Adressen für die Wallet,
  einem Kopierknopf daneben und einem Balken, wie weit sein Index ist
- **Protokoll** des Nodes, mitlaufend — angenommene Blöcke orange, ihre
  Ankündigungen gedämpft, Stichproben des Kettenabgleichs grün, Fehler und
  Warnungen rot und gelb. Die Zeilen bleiben reiner Text; nur die Farbe
  kommt aus einer Mustertabelle

Ohne Daten stehen die Karten trotzdem da — mit einem gedämpften Gerüst und
Strichen statt Zahlen. **Nie erfundene Werte:** Auf einer Anzeige, die den
Zustand eines Nodes meldet, weiß auf dem nächsten Bildschirmfoto sonst niemand
mehr, was gemessen und was gemalt war.

---

## Voraussetzungen

- Ein **laufender Bitcoin Core**, Version 26 oder neuer. Wie er aufgesetzt
  wurde, spielt keine Rolle
- **Python 3.9** oder neuer (auf Raspberry Pi OS und Debian ohnehin dabei)
- **nginx**, oder ein beliebiger anderer Webserver für statische Dateien
- Schreibzugriff auf die `bitcoin.conf`

Getestet auf Raspberry Pi OS Lite 64-bit (Debian 13) mit Bitcoin Core 31.1.

---

## Einrichten

```bash
git clone https://github.com/Fulbright-UI/btcnode-dashboard.git
cd btcnode-dashboard
sudo bash install.sh
```

Das Skript stellt **eine Frage** — die Sprache der Seite — und läuft danach
ohne weitere Rückfragen durch: Es findet das Datenverzeichnis selbst, legt
einen **nur lesenden** RPC-Zugang an, richtet den Generator als Dienst ein und
begrenzt die Firewall auf das Heimnetz.

In eine Pipe geleitet, wo niemand antworten könnte, nimmt es Englisch. Wer die
Frage überspringen will:

```bash
sudo bash install.sh --language de
```

Einen Schritt macht es bewusst nicht von selbst — **bitcoind neu starten**.
Ohne Neustart kennt der Node den neuen Zugang nicht, und bis dahin zeigt das
Dashboard „Node nicht erreichbar". Der Neustart kostet während einer laufenden
Erstsynchronisation den warmen Zwischenspeicher, und das ist eine Entscheidung
des Betreibers:

```bash
sudo systemctl restart bitcoind
```

Wer das gleich miterledigt haben will, hängt `--restart` an.

Wenn die Erkennung scheitert:

```bash
sudo bash install.sh --datadir /mnt/bitcoin/bitcoin --subnet 192.168.1.0/24
```

| Option | Bedeutung |
|---|---|
| `--language de\|en` | Sprache der Seite; überspringt die Frage |
| `--datadir PFAD` | Datenverzeichnis von Bitcoin Core |
| `--port N` | Port der Statusseite, Vorgabe 80 |
| `--subnet CIDR` | Heimnetz für die Firewall, z. B. `192.168.1.0/24` |
| `--restart` | bitcoind am Ende neu starten |
| `--uninstall` | Dienst, Programm, Seite und Nutzer wieder entfernen |

Alles ist wiederholbar: Zweimal ausführen macht nichts kaputt, und ein
bestehendes RPC-Passwort bleibt unverändert.

### Bei fertigen Bausätzen

Umbrel, Start9 und MyNode verwalten die `bitcoin.conf` selbst und überschreiben
sie beim Neustart. Dort gehören diese Zeilen an die vom Bausatz vorgesehene
Stelle für eigene Ergänzungen:

```
rpcauth=dashboard:<salz>$<pruefsumme>
rpcwhitelist=dashboard:getblockchaininfo,getnetworkinfo,getmempoolinfo,getconnectioncount,uptime,estimatesmartfee,getblockstats,getblockhash,getblockheader,getpeerinfo,getnetworkhashps
rpcwhitelistdefault=0
```

Die `rpcauth`-Zeile erzeugst du so — das Passwort danach in
`/etc/node-dashboard.conf` eintragen:

```bash
python3 - <<'PY'
import hashlib, hmac, os, secrets
passwort = secrets.token_urlsafe(32)
salz = os.urandom(16).hex()
pruef = hmac.new(salz.encode(), passwort.encode(), hashlib.sha256).hexdigest()
print(f"rpcauth=dashboard:{salz}${pruef}")
print(f"Passwort: {passwort}")
PY
```

---

## Wie sicher ist das

Das ist die eigentliche Entwurfsfrage, deshalb ausführlich.

**Der Node ist vom Dashboard getrennt.** Es gibt keinen Weg von der Webseite
zurück zum Node. Der Generator schreibt Dateien, der Webserver liest sie. Mehr
passiert nicht.

**Der RPC-Zugang darf nur lesen.** In der `bitcoin.conf` steht
`rpcwhitelistdefault=0` und eine ausdrückliche Liste von zehn Methoden. Selbst
wenn das Passwort abhanden käme, ließe sich damit nichts anstellen: keine
Wallet, kein Senden, keine Konfiguration, kein Herunterfahren. Andere
RPC-Nutzer sind davon nicht betroffen.

**Der Dienst läuft als eigener Systemnutzer** ohne Anmelderechte, abgeschottet
über systemd (`ProtectSystem=strict`), mit genau einem beschreibbaren Pfad.

**Fremder Text wird nie zu Markup.** Protokollzeilen, Peer-Adressen und
Kennungen anderer Knoten bestimmt nicht dieses Programm, sondern die
Gegenstelle. Sie werden serverseitig maskiert und im Browser ausschließlich
über `textContent` gesetzt, wo per Bauart kein Markup entstehen kann.

**Die Content-Security-Policy kommt ohne `unsafe-inline` aus.** Genau deshalb
liegen Stil und Skript in eigenen Dateien und nicht im HTML.

**Der Generator geht nie ins Internet.** Er spricht ausschließlich mit
`127.0.0.1` — und die Dienstdefinition sagt das auch dem Kernel
(`IPAddressDeny=any`, `IPAddressAllow=localhost`), das Versprechen hält also
auch gegen einen Fehler im Programm.

**Kein Knopf erreicht den Node.** Ein Neustart-Knopf bräuchte einen Endpunkt,
der Eingaben annimmt, und einen Rechteweg bis zu systemd. Das macht aus
„schlimmstenfalls liest jemand eine Datei" ein „schlimmstenfalls stoppt jemand
den Node". Die einzigen Schaltflächen auf der Seite kopieren eine Adresse in
die Zwischenablage des Browsers — sonst nichts.

**Die Firewall begrenzt den Zugriff auf das Heimnetz**, sofern `ufw` vorhanden
ist. Die Seite hat keine Anmeldung — stell sie nicht ins Internet.

---

## JavaScript

Die Seite trägt sich selbst nach, ohne neu zu laden. Das ändert am Prinzip
nichts: **Die Schnittstelle ist eine statische Datei.** Der Generator schreibt
`status.json` genauso wie das HTML, das Skript im Browser holt sie und trägt
die Werte nach. Es gibt keinen Endpunkt, der etwas entgegennimmt.

Ohne JavaScript funktioniert alles weiter — die Seite holt sich dann über ein
`<meta http-equiv=refresh>` neu.

Erzeugt werden:

| Datei | Takt | Inhalt |
|---|---|---|
| `index.html` | 30 s | vollständige Seite |
| `chronik.json` | einmalig | die Zitate für die Kopfzeile |
| `status.json` | 30 s | dieselben Bausteine plus Peers als reine Struktur |
| `log.txt` | 5 s | Journalzeilen, reiner Text, ohne jedes Markup |
| `stil.css`, `dash.js` | einmalig | ändern sich nur beim Programmtausch |

`dash.js` trägt die Beschriftungen der eingestellten Sprache und wird deshalb
je Installation geschrieben. Sein Fingerabdruck wird über den fertigen Text
gebildet — ein Sprachwechsel taucht also als neue Datei auf und nicht als alte
aus dem Zwischenspeicher des Browsers.

---

## Einstellungen

In `/etc/node-dashboard.conf`, danach `sudo systemctl restart node-dashboard`:

| Schlüssel | Vorgabe | Bedeutung |
|---|---|---|
| `LANGUAGE` | `en` | Sprache der Seite: `de` oder `en` |
| `INTERVAL` | 30 | Takt der Node-Abfrage in Sekunden |
| `LOG_INTERVAL` | 5 | Takt der Protokollanzeige |
| `LOG_SERVICES` | `bitcoind` | Quellen, mit Komma getrennt. Leer schaltet ab |
| `LOG_LINES` | 150 | Rücklauf im Protokoll. Es füllt die Spalte und rollt innen |
| `RPC_TIMEOUT` | 45 | Zeitlimit je Abfrage in Sekunden |
| `TOLERANCE` | 3 | erfolglose Abfragen, bevor Alarm geschlagen wird |
| `PEERS_MAX` | 64 | Höchstzahl der Punkte in der Netzkarte |
| `ELECTRS_PORT` | 50001 | Port des Electrum-Servers, falls einer läuft |
| `ELECTRS_METRICS` | 127.0.0.1:4224 | Prometheus-Anschluss von electrs; wird für den Index-Balken gelesen, solange er noch nicht bedient |

Die Sprache betrifft mehr als Wörter: Deutsch schreibt `1.234.567,8`, Englisch
`1,234,567.8` — Punkt und Komma tauschen beide die Rolle. Deshalb geht jede
Zahl auf der Seite durch eine Formatierung, und der Probelauf prüft beide
Schreibweisen.

### Warum es ein Toleranzfenster gibt

Während der Erstsynchronisation hält Bitcoin Core seine Abfrageschnittstelle
an, solange es den Zwischenspeicher auf die Platte schreibt. Auf langsamer
Hardware dauert das regelmäßig länger als das Zeitlimit einer Abfrage.

Ein Dashboard, das daraufhin „Node nicht erreichbar" meldet und alle Zahlen auf
null setzt, ist schlimmer als keins — es meldet einen Ausfall, den es nicht
gibt. Deshalb hält dieses hier den letzten gemessenen Stand und sagt leise, wie
alt er ist. Die rote Karte kommt erst, wenn `TOLERANCE` Abfragen hintereinander
erfolglos waren.

---

## Entwickeln

Es gibt einen vollständigen Probelauf, der ohne Node und ohne Raspberry Pi
auskommt:

```bash
python3 tests/probelauf.py                  # Kette steht, alle Karten
python3 tests/probelauf.py --case sync      # Erstsynchronisation
python3 tests/probelauf.py --case leer      # Node antwortet, liefert nichts
python3 tests/probelauf.py --language en    # die englische Seite
```

`tests/attrappe.py` ist ein echter HTTP-Server mit Basic Auth und JSON-RPC —
kein Ersatz für die Abfrageschicht, damit im Test derselbe Code läuft wie
später, Fehlerbehandlung eingeschlossen. Unbekannte Methoden beantwortet sie
mit HTTP 403, so wie eine fehlende Whitelist-Eintragung es täte.

Die erzeugte Seite liegt danach in `tests/ausgabe/index.html` und lässt sich im
Browser öffnen.

Rund 170 Prüfungen laufen dabei durch, in beiden Sprachen: Wohlgeformtheit des
HTML, keine Schaltflächen, die den Node erreichen, keine Inline-Stile, der
richtige Dezimaltrenner, Maskierung fremder Werte, das Toleranzfenster in beide
Richtungen, die Geometrie der Netzkarte, der Weg des letzten Blocks und der
Kettenabgleich — und mehrere, die aus echtem Schaden entstanden sind: Jeder
sichtbare Text braucht eine Übersetzung, jede im Markup benutzte CSS-Klasse
muss es in der Stilvorlage geben, jedes Feld, das das Browser-Skript liest,
muss in `status.json` stehen, und die Zeile zur Stromversorgung muss
erscheinen, obwohl die Attrappe — wie der abgeschottete Dienst — `vcgencmd`
nicht aufrufen kann.

---

## Sprache des Quelltextes

Code, Kommentare und Bezeichner sind englisch; die Oberfläche spricht beides.
Die Kommentare halten fest, **warum** etwas so gebaut ist, meist mit Datum und
der Messung, die den Fall entschieden hat. Sie sind der wertvollste Teil dieses
Repositoriums — bitte lass sie das, wenn du etwas änderst.

---

## Lizenz

MIT — siehe [LICENSE](LICENSE).
