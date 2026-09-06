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
  Stichproben der letzten Stunde stehen als Punktreihe im Kopf der Netzkarte.
  Ein einzelner Fremder, der mehr Blöcke behauptet, ist ein roter Punkt und
  ein ruhiger Satz (Behauptungen sind unbelegt); zwei jüngere zusammen
  erzeugen oben eine Meldung
- **Eine Zeitleiste in der Kopfzeile**, getippt wie in ein Terminal, ein
  Eintrag je Datenabruf und chronologisch: von der Bank of England, Jekyll
  Island und dem Federal Reserve Act über Bretton Woods, 1971 und die
  Cypherpunks zu Satoshis eigenen Worten (gegen das Nakamoto Institute
  geprüft), dem Genesis-Block, Mt. Gox, Silk Road, den Halbierungen und dem
  ETF. Jeder Neustart des Dienstes beginnt oben; vor einem neuen Eintrag
  wird der alte rückwärts gelöscht
- **Systemzustand** des Rechners: die Temperatur des Tages als ein Balken je
  Stunde, CPU, Arbeitsspeicher, Plattenplatz — bei einem Raspberry Pi auch
  die Stromversorgung, weil Unterspannung die häufigste Ursache beschädigter
  Blockchain-Daten ist
- **Die Gebühr, die man nehmen sollte**: Cores sparsame Schätzung für den
  nächsten Block, groß im Kopf, darunter die vorsichtige, wenn sie abweicht
- **Tage bis zur Halbierung**, mit Datum und den Blöcken, die noch fehlen
- **Kette, Mempool und Electrum** in einer Karte: Schwierigkeit, nächste
  Anpassung, Mempool-Speicher, wartende Gebühren und Füllstand in der
  linken Spalte; rechts der Electrum-Server — am Port erkannt, also zählen
  electrs, Fulcrum und ElectrumX gleichermaßen — mit einem Balken, wie weit
  sein Index ist, und darunter die Adressen für die Wallet mit Kopierknopf.
  Ohne Server bleibt die Spalte stehen und sagt das, und warum es zählt
- **Volumen und Gebührenverlauf** der letzten 24 Stunden, ein Balken je
  Block, in drei Stufen — Grau, Grün, Block-Orange — Gebühren nach sat/vB,
  Volumen gegen das Tagesmittel; die Beschriftung nennt die Spitze, jeder
  Balken der Seite zeigt beim Zeigen seinen Wert
- **Hashrate seit 2009** als Kurve hinter dem Zustandsbalken, linear, mit
  der Veränderung zum Vorjahr
- **Protokoll** des Nodes, mitlaufend — angenommene Blöcke orange getönt,
  Fehler und Warnungen rot und gelb, alles andere schlicht. Die Zeilen
  bleiben reiner Text; nur die Farbe kommt aus einer Mustertabelle

Ohne Daten stehen die Karten trotzdem da — mit einem gedämpften Gerüst und
Strichen statt Zahlen. **Nie erfundene Werte:** Auf einer Anzeige, die den
Zustand eines Nodes meldet, weiß auf dem nächsten Bildschirmfoto sonst niemand
mehr, was gemessen und was gemalt war.

---

## Voraussetzungen

- Ein **laufender Bitcoin Core**, Version 26 oder neuer. Wie er aufgesetzt
  wurde, spielt keine Rolle
- **Python 3.9** oder neuer (auf Raspberry Pi OS und Debian ohnehin dabei)
- **nginx**, oder ein beliebiger anderer Webserver für statische Dateien —
  fehlt nginx, bietet das Installationsskript an, es zu installieren
- Schreibzugriff auf die `bitcoin.conf`
- Ein **Electrum-Server** ist optional. Fehlt er, zeigt die Seite die Lücke —
  siehe [Electrum-Server](#electrum-server) unten

Getestet auf Raspberry Pi OS Lite 64-bit (Debian 13) mit Bitcoin Core 31.1.

---

## Einrichten

```bash
git clone https://github.com/Fulbright-UI/btcnode-dashboard.git
cd btcnode-dashboard
sudo bash install.sh
```

Das Skript stellt **höchstens drei Fragen** — die Sprache der Seite, ob es
nginx installieren soll, falls der fehlt, und ob es bitcoind am Ende neu
startet — und macht alles andere selbst: Es findet das Datenverzeichnis, legt
einen **nur lesenden** RPC-Zugang an, richtet den Generator als Dienst ein,
liefert die Seite aus und begrenzt die Firewall auf das Heimnetz. Am Ende
steht die Adresse für den Browser, und es prüft, dass die Seite antwortet.

Jede Frage hat eine Vorgabe (groß geschrieben); Enter nimmt sie. In eine Pipe
geleitet oder mit `--yes` gelten durchgehend die Vorgaben.

Der Neustart ist der eine Schritt, den es nicht von selbst macht: bitcoind
kennt den neuen Zugang erst nach einem Neustart, und ein Neustart während der
Erstsynchronisation kostet den warmen Zwischenspeicher. Das Skript fragt den
Node, wie weit er ist, und schlägt entsprechend vor — „ja", wenn die Kette
steht, „nein", solange sie noch synchronisiert.

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
| `--electrum-port N` | Port des Electrum-Servers, Vorgabe 50001 |
| `--restart` | bitcoind am Ende neu starten, ohne Frage |
| `--yes` | jede Frage mit ihrer Vorgabe beantworten |
| `--uninstall` | Dienst, Programm, Seite und Nutzer wieder entfernen |

Alles ist wiederholbar: Zweimal ausführen macht nichts kaputt, und ein
bestehendes RPC-Passwort bleibt unverändert.

---

## Electrum-Server

Eine Wallet spricht nicht direkt mit Bitcoin Core, sondern mit einem
Electrum-Server, der die Kette nach Adressen indiziert. Ohne einen eigenen
fragt die Wallet den Server von jemand anderem — und der erfährt, welche
Adressen dir gehören. Für die meisten ist genau das der Grund, überhaupt
einen Node zu betreiben.

Das Dashboard installiert keinen; das ist ein eigener Bau (auf dem Pi
üblicherweise electrs — aus der Quelle, dynamisch gegen die RocksDB des
Systems, danach Stunden Indexlauf). Was das Dashboard tut:

- es sucht am Electrum-Port nach einem Server (`50001` als Vorgabe,
  `--electrum-port` oder `ELECTRS_PORT` für einen anderen) — electrs,
  Fulcrum und ElectrumX antworten alle dort;
- es zeigt, ob der Server läuft und antwortet, wie weit sein Index ist, und
  die zwei Adressen für die Wallet — Heimnetz und, falls ein Tor-Dienst
  dafür besteht, die Onion-Adresse — mit Kopierknopf;
- ohne Server sagt die Spalte das und warum es zählt, in einem gedämpften
  Satz. Kein Alarm: dem Node selbst fehlt nichts.

Der Indexstand kommt aus dem eigenen Metrik-Endpunkt von electrs, solange
es noch indiziert, und aus dem Electrum-Protokoll
(`blockchain.headers.subscribe`), sobald es bedient — dieselbe Frage, die
jede Wallet als Erstes stellt. Nichts davon verlässt den Rechner.

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

### Warum kein Docker

Es gibt kein Container-Image, und es ist keines geplant. Nicht aus
Abneigung — die Abschottung, auf der dieses Projekt beruht, kommt von
systemd, und ein Container würde sie schwächer machen, nicht stärker:

- Die Unit sperrt den Generator mit `ProtectSystem=strict`, einem einzigen
  beschreibbaren Pfad, `IPAddressDeny=any` und einem leeren Capability-Set
  ein. Ein Container bringt ein ganzes Userland mit — Shell, Paketmanager
  und einen Netz-Namensraum, der zum Node hin ohnehin geöffnet werden muss.
- Der Generator braucht drei Dinge vom Host: den RPC-Port auf `127.0.0.1`,
  das Journal von `bitcoind` für das Protokoll und `hwmon` für die
  Versorgungsspannung des Pi. Jedes davon ist ein Loch, das man in einen
  Container schlagen muss; auf dem Host sind es die Gruppe `systemd-journal`
  und ein einziges `ReadWritePaths`.
- Was Docker brächte — reproduzierbare Installation auf jeder Distribution —
  braucht das Projekt nicht: eine Python-Datei aus der Standardbibliothek
  und ein Shell-Skript, nichts zu bauen.

Wer trotzdem einen Container will, baut ihn selbst; schwer ist es nicht.
Er braucht Python 3.9, die eine Datei, `--network host` (oder den
weitergereichten RPC-Port), das Journal von `bitcoind` nur lesend
eingehängt und das `OUT_DIR` geteilt mit dem Webserver, der es ausliefert.
Der RPC-Nutzer bleibt in der `bitcoin.conf` nur lesend, genau wie
`install.sh` ihn anlegt — das ist in jeder Betriebsart gleich und ist das,
was den Node wirklich schützt.

---

## JavaScript

Die Seite trägt sich selbst nach, ohne neu zu laden. Das ändert am Prinzip
nichts: **Die Schnittstelle ist eine statische Datei.** Der Generator schreibt
`status.json` genauso wie das HTML, das Skript im Browser holt sie und trägt
die Werte nach. Es gibt keinen Endpunkt, der etwas entgegennimmt.

Ohne JavaScript funktioniert alles weiter — die Seite holt sich dann über ein
`<meta http-equiv=refresh>` in `<noscript>` neu. Absichtlich in `<noscript>`:
ein Refresh außerhalb wird vom Browser beim Parsen eingeplant, und ein
Skript, das das Element danach entfernt, hebt nichts auf — die Seite lud
sich unter dem Skript jeden Takt neu (3.4).

Erzeugt werden:

| Datei | Takt | Inhalt |
|---|---|---|
| `index.html` | 30 s | vollständige Seite |
| `status.json` | 30 s | dieselben Bausteine plus Peers als reine Struktur |
| `log.txt` | 5 s | Journalzeilen, reiner Text, ohne jedes Markup |
| `chronik.json` | einmalig | die Zeitleiste für die Kopfzeile, mit der Startzeit des Generators |
| `stil.css`, `dash.js`, `bitcoin.png` | einmalig | ändern sich nur beim Programmtausch |

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
| `ELECTRS_PORT` | 50001 | Port des Electrum-Servers; dort schaut die Seite nach |
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

```bash
bash tests/install-test.sh                  # der Firewall-Teil von install.sh
```

führt die Firewall-Funktion aus `install.sh` gegen eine `ufw`-Attrappe im
`$PATH` aus — ohne Root, ohne echte Firewall. Sie gibt es, weil nichts in
`probelauf.py` eine Zeile des Installers ausführt, und der erste dort
gefundene Fehler Regeln löschte (06.09.2026).

```bash
cd tests && npm install --no-save jsdom && cd ..
node tests/dash-test.js                     # dash.js gegen die erzeugte Seite
```

führt das Browser-Skript unter jsdom gegen die Seite, `status.json`,
`log.txt` und `chronik.json` aus, die der Generator gerade geschrieben
hat — Detailkasten, Netzkarte, Kopierknöpfe, Protokollfarben, ein
fehlgeschlagener Abruf und die Tippschleife der Chronik mit angehaltener
Uhr. `probelauf.py` ruft ihn von selbst auf, wenn `node` und jsdom da
sind, und sagt es, wenn nicht.

`tests/geometrie.py` läuft innerhalb von `probelauf.py` und prüft die
berechnete Geometrie jeder Zeichnung der Seite: nichts außerhalb der
eigenen viewBox, keine abgeschnittene Textzeile, keine runde Ecke in einem
gestreckten SVG, jeder Füllbalken so voll, wie seine beiden Zahlen es
sagen, Säulen in der Reihenfolge ihrer Werte und ohne Überlappung, und in
der Netzkarte je Gegenstelle eine Zeile mit Punkt, Speiche und
Beschriftung auf einer Höhe. Das Orakel ist die Zahl im `<title>` der
Zeichnung, nicht die Formel, die sie gezeichnet hat.

Rund 220 Prüfungen laufen dabei durch, in beiden Sprachen: Wohlgeformtheit des
HTML, keine Schaltflächen, die den Node erreichen, keine Inline-Stile, der
richtige Dezimaltrenner, Maskierung fremder Werte, das Toleranzfenster in beide
Richtungen, die Geometrie der Netzkarte, der Weg des letzten Blocks und der
Kettenabgleich — und mehrere, die aus echtem Schaden entstanden sind: Jeder
sichtbare Text braucht eine Übersetzung, jede im Markup benutzte CSS-Klasse
muss es in der Stilvorlage geben, jedes Feld, das das Browser-Skript liest,
muss in `status.json` stehen und jedes `data-`-Attribut, das es liest, auf
der Seite, der Meta-Refresh muss in `<noscript>` sitzen, und die Zeile zur
Stromversorgung muss erscheinen, obwohl die Attrappe — wie der abgeschottete
Dienst — `vcgencmd` nicht aufrufen kann.

---

## Sprache des Quelltextes

Code, Kommentare und Bezeichner sind englisch; die Oberfläche spricht beides.
Die Kommentare halten fest, **warum** etwas so gebaut ist, meist mit Datum und
der Messung, die den Fall entschieden hat. Sie sind der wertvollste Teil dieses
Repositoriums — bitte lass sie das, wenn du etwas änderst.

---

## Lizenz

MIT — siehe [LICENSE](LICENSE).
