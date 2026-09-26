# wsdarr – **W**eb**s**hare **d**ownlo**a**d**arr**

**Stahování z [Webshare.cz](https://webshare.cz) přímo ze Sonarru a Radarru.**

wsdarr je malá služba, která napojí Webshare.cz na Sonarr a Radarr. Ty pak na Webshare hledají
a stahují stejně jako z jakéhokoli jiného zdroje: najdou díl nebo film, vyberou nejlepší verzi podle
tvých quality profilů (rozlišení, CZ dabing, titulky…), wsdarr ji stáhne a Sonarr/Radarr ji
automaticky naimportují do knihovny.

### Co umí

* **Hledání na Webshare podle toho, co Sonarr/Radarr chtějí** – z ID seriálu/filmu zjistí anglické
  i české názvy (Sonarr/Radarr, volitelně TMDB), prohledá Webshare a vyřadí nesprávné díly, roky,
  samply a heslem chráněné soubory.
* **Názvy, kterým Sonarr/Radarr rozumí** – soubor `Perníkový táta S01E02 (CZ) 720p.avi` nabídne jako
  `Breaking.Bad.S01E02.720p.CZ-WS`, včetně kvality a jazyka (CZ/SK dabing vs. titulky).
* **Stahování** – fronta, souběžná stahování, navázání přerušeného stahování, opakování při chybě,
  limit rychlosti; hotové soubory si Sonarr/Radarr samy naimportují.
* **Webové rozhraní** (česky) – stav Webshare účtu a VIP, fronta a historie, ruční hledání
  a hodnoty pro nastavení v Sonarr/Radarr.

### Jak to funguje

wsdarr se vůči Sonarru/Radarru tváří jako dvě věci, které znají nativně – nic se v nich neupravuje
ani neinstalují žádné pluginy:

* **Newznab indexer** – přes něj Sonarr/Radarr hledají na Webshare.
* **SABnzbd download client** – Sonarr/Radarr mu pošlou vybraný release a wsdarr ho stáhne z Webshare.

```
Sonarr/Radarr ──(Newznab)──► wsdarr  /newznab/api?t=tvsearch&tvdbid=81189&season=1&ep=2
     wsdarr: ID ─► názvy (Sonarr/Radarr + TMDB cs-CZ) ─► Webshare hledání ─► filtr (správný díl/rok)
             ─► přejmenování na „scene“ název:  Breaking.Bad.S01E02.1080p.BluRay.x264.CZ-WS
Sonarr vybere release podle profilu ─► stáhne „NZB“ (obálka s Webshare identem) ─► pošle do „SABnzbd“ = wsdarr
wsdarr stáhne soubor do /downloads/complete/tv/<release>/<release>.mkv ─► Sonarr naimportuje a položku smaže
```

Potřebuješ **účet na Webshare.cz**; **VIP** je silně doporučený – bez něj je stahování pomalé a omezené.
wsdarr neobchází žádná omezení Webshare, používá jeho API s tvým účtem.

> Prowlarr podporovaný není: indexery v Prowlarru se berou z jeho seznamu definic a Webshare mezi nimi
> není. wsdarr se proto přidává přímo do Sonarru a Radarru.

## Instalace (Docker)

Image: `ghcr.io/weselinka/wsdarr:latest`

1. Zkopíruj [`docker-compose.example.yml`](docker-compose.example.yml) a vyplň:
   * `WEBSHARE_USERNAME`, `WEBSHARE_PASSWORD`,
   * `SONARR_URL`/`SONARR_API_KEY` a `RADARR_URL`/`RADARR_API_KEY` (*Settings → General → API Key*
     v Sonarru/Radarru) – wsdarr z nich bere názvy titulů,
   * volitelně `TMDB_API_KEY` pro české názvy (zdarma na [themoviedb.org](https://www.themoviedb.org/settings/api)).
2. `docker compose pull && docker compose up -d`
3. Zjisti **API klíč wsdarr** – vygeneruje se při prvním startu:
   ```
   docker exec wsdarr wsdarr apikey
   ```
   Klíč je také v logu po startu (`docker logs wsdarr`) a na stránce *Přehled* ve web UI
   (`http://<host>:9797/`). Vlastní klíč můžeš nastavit proměnnou `WSDARR_API_KEY`.
4. Nastav Sonarr a Radarr podle návodu níže. Web UI (*Přehled*) ukazuje přesně ty hodnoty,
   které máš vyplnit.

## Nastavení v Sonarru a Radarru

Postup je v obou aplikacích stejný. Adresa wsdarr je v příkladech `wsdarr:9797` (název kontejneru
ve sdílené Docker síti); pokud Sonarr/Radarr běží jinde, použij IP/hostname serveru a stejnou
adresu dej i do `PUBLIC_URL`.

### 1. Download client

*Settings → Download Clients → + → **SABnzbd***

| Pole | Hodnota |
| --- | --- |
| Name | `wsdarr` |
| Host | `wsdarr` |
| Port | `9797` |
| Use SSL | vypnuto |
| URL Base | `sabnzbd` |
| API Key | API klíč wsdarr |
| Username / Password | prázdné |
| Category | Sonarr: `tv`, Radarr: `movies` |

Ulož tlačítkem *Test* → *Save*. Pokud už používáš skutečný SABnzbd/NZBGet, nech ho, jen v kroku 2
naváž indexer na klienta `wsdarr`.

### 2. Indexer

*Settings → Indexers → + → **Newznab** → **Custom***

| Pole | Hodnota |
| --- | --- |
| Name | `Webshare` |
| Enable RSS / Automatic Search / Interactive Search | zapnuto |
| URL | `http://wsdarr:9797/newznab` |
| API Path | `/api` |
| API Key | API klíč wsdarr (stejný jako u download clienta) |
| Categories | Sonarr: `TV` (5000, 5030, 5040, 5045), Radarr: `Movies` (2000, 2030, 2040, 2045) |
| Download Client (*Show Advanced*) | `wsdarr` |

*Download Client = wsdarr* zajistí, že releasy z Webshare nikdy neodejdou do jiného (skutečného
usenet) klienta. Ulož tlačítkem *Test* → *Save*.

### 3. Ověření

* Na seriálu/filmu otevři *Interactive Search* – výsledky z indexeru *Webshare* mají názvy končící `-WS`.
* Po stažení se položka objeví v *Activity → Queue* a po dokončení se automaticky naimportuje.

### Cesty

wsdarr hlásí stažené soubory v `DOWNLOAD_DIR/complete/<kategorie>/<release>/`. Sonarr/Radarr
musí tuto cestu vidět **pod stejnou cestou** (sdílený volume, jako v ukázkovém compose), nebo
nastav *Settings → Download Clients → Remote Path Mappings*.

## Jak wsdarr hledá a pojmenovává

* **Z ID na názvy.** Sonarr posílá `tvdbid` + `season` + `ep`, Radarr `tmdbid`/`imdbid`. wsdarr
  z knihovny Sonarr/Radarr vezme název, rok a alternativní názvy, z TMDB (volitelné,
  `TMDB_API_KEY`) doplní **české a slovenské názvy** – na Webshare je většina souborů pojmenovaná česky.
* **Hledání na Webshare.** Pro díl např. `Perníkový táta S01E02`, `Pernikovy tata 1x02`,
  `Breaking Bad S01E02` a samotné názvy (se stránkováním); výsledky se cachují.
* **Filtr.** Z názvu souboru se parsuje (guessit + česká pravidla) název, rok, série/díl, rozlišení,
  zdroj, kodeky a jazyk (`CZ dabing`, `CZ titulky`, `SK`, `CZtit` …). Soubory jiného dílu/roku,
  samply, heslem chráněné, ne-video a příliš malé soubory se zahodí; duplicitní uploady se sloučí.
* **Pojmenování.** Shodný soubor dostane název, který Sonarr/Radarr namapují zpět:

  | Soubor na Webshare | Release pro Sonarr/Radarr |
  | --- | --- |
  | `Perníkový táta S01E02 (CZ) 720p.avi` | `Breaking.Bad.S01E02.720p.CZ-WS` |
  | `Breaking.Bad.S01E02.720p.WEB-DL.CZ.titulky.mkv` | `Breaking.Bad.S01E02.720p.WEB-DL.CZ.SUBS-WS` |
  | `Pán prstenů - Návrat krále (2003) 1080p CZ dabing.mkv` | `The.Lord.of.the.Rings.The.Return.of.the.King.2003.1080p.CZ-WS` |
  | `Simpsonovi 12x05 CZ.avi` | `The.Simpsons.S12E05.SDTV.CZ-WS` |

  `CZ` / `SK` = český / slovenský zvuk (Sonarr/Radarr ho rozpoznají jako jazyk *Czech* / *Slovak*),
  `CZ.SUBS` = jen české titulky. Do Newznab atributů jde i `language`, `size`, `tvdbid`/`imdb`/`tmdbid`.
  Původní název souboru je v popisu releasu a v UI.
* **Soubory bez značek kvality** by Sonarr/Radarr vyhodnotily jako *Unknown* a výchozí profily je
  odmítnou. wsdarr jim proto (výchozí `UNKNOWN_QUALITY=extension`) přidá kvalitu, kterou by jim
  Sonarr/Radarr samy přiřadily podle přípony (`.mkv` → HDTV-720p, `.avi`/`.mp4` → SDTV, …).
  `UNKNOWN_QUALITY=keep` to vypne.

### Doporučené nastavení Sonarr/Radarr

* V quality profilu povol kvality, které na Webshare reálně jsou (často **SDTV**, **HDTV-720p/1080p**,
  **WEBDL**, **Bluray**).
* Pro preferenci českého dabingu vytvoř *Settings → Custom Formats → +* s podmínkou
  *Language: Czech* a dej mu v profilu kladné skóre (případně druhý formát *Language: Slovak*).
  V Radarru nastav v profilu *Language* na *Any*, jinak odmítne releasy, které nejsou v původním jazyce.
* *Completed Download Handling* nech zapnuté, u klienta wsdarr ideálně *Remove Completed*.

## Konfigurace

Proměnné prostředí (nebo `/config/config.yml` se stejnými klíči malými písmeny; YAML umožňuje
i více instancí, např. `sonarr: [{name: sonarr-4k, url: ..., api_key: ..., category: tv-4k}]`).

| Proměnná | Výchozí | Popis |
| --- | --- | --- |
| `WEBSHARE_USERNAME`, `WEBSHARE_PASSWORD` | – | Webshare účet |
| `PUBLIC_URL` | `http://wsdarr:9797` | adresa, na které wsdarr vidí Sonarr/Radarr (odkazy na NZB ve výsledcích hledání) |
| `WSDARR_API_KEY` | vygenerováno | API klíč pro indexer i download client (`docker exec wsdarr wsdarr apikey`) |
| `SONARR_URL`, `SONARR_API_KEY` | – | názvy seriálů pro hledání + okamžitý import po stažení |
| `RADARR_URL`, `RADARR_API_KEY` | – | názvy filmů pro hledání + okamžitý import po stažení |
| `TMDB_API_KEY` / `TMDB_TOKEN` | – | české názvy z TMDB (doporučeno) |
| `TMDB_LANGUAGE` | `cs-CZ` | jazyk lokalizovaných názvů |
| `DOWNLOAD_DIR` | `/downloads` | kořen pro `incomplete/` a `complete/` |
| `CONFIG_DIR` | `/config` | databáze, token, `config.yml` |
| `TV_CATEGORY`, `MOVIE_CATEGORY` | `tv`, `movies` | kategorie download clienta |
| `MAX_CONCURRENT_DOWNLOADS` | `2` | souběžná stahování |
| `SPEED_LIMIT_KBPS` | `0` | limit rychlosti (0 = bez limitu) |
| `DOWNLOAD_RETRIES` | `3` | pokusy před nahlášením chyby (Sonarr pak zkusí jiný release) |
| `MIN_FILE_SIZE_MB` | `50` | menší soubory se ignorují |
| `SEARCH_MAX_PAGES` | `3` | stránek (po 100) na jeden obecný dotaz |
| `MAX_ALT_TITLES` | `4` | kolik názvů se použije k hledání |
| `MATCH_THRESHOLD` | `88` | minimální shoda názvu (0–100) |
| `UNKNOWN_QUALITY` | `extension` | `extension` / `keep` (viz výše) |
| `RSS_MODE` | `recent` | `recent` = RSS vrací nejnovější videa z Webshare, `off` = nic |
| `UI_USERNAME`, `UI_PASSWORD` | – | basic auth pro web UI |
| `PUID`, `PGID`, `UMASK` | `1000`, `1000`, `002` | vlastník stažených souborů (Docker) |

## Web UI

`http://<host>:9797/` – přehled (Webshare účet a VIP dny, stav spojení, volné místo, API klíč
a hodnoty pro nastavení Sonarr/Radarr), fronta a historie (pauza, smazání, opakování), ruční hledání
(ukazuje i jak release uvidí Sonarr/Radarr, s možností stáhnout do kategorie) a nastavení
(aktuální konfigurace, test spojení).

## CLI

V Dockeru přes `docker exec wsdarr …`:

```
wsdarr apikey             # vypíše API klíč (funguje i vedle běžícího serveru)
wsdarr search "Breaking Bad S01E02" [--kind tv|movie]   # hledání jako Sonarr/Radarr (ladění)
wsdarr serve              # web server (výchozí příkaz kontejneru)
```

## Omezení a co ověřit na reálném účtu

* Denní (datumové) seriály a anime s absolutním číslováním zatím nejsou podporované.
* Webshare API dokumentace nebyla při vývoji dostupná, implementace vychází z existujících
  klientů. Na reálném účtu ověř: přihlášení (*Přehled* ukáže VIP dny), hledání (*Hledání* v UI),
  že test indexeru v Sonarru/Radarru projde (RSS dotaz bez hledaného textu musí něco vrátit),
  stažení a import jednoho dílu a navázání přerušeného stahování (restart kontejneru během stahování).
* Tvar dotazů na Webshare (`název S01E02` / `název 1x02` / samotný název) je potřeba doladit podle
  toho, jak Webshare vyhledávání reálně tokenizuje – příkaz `wsdarr search` ukáže, co se najde.

## Vývoj

```
pip install -e ".[dev]"
pytest            # unit testy + celý tok proti falešnému Webshare a falešným *arr API
ruff check src tests && ruff format --check src tests
```

`tests/fake_webshare.py` je spustitelný stub Webshare API (`python tests/fake_webshare.py`
a `WEBSHARE_BASE_URL=http://127.0.0.1:9999/api/`) pro lokální end-to-end zkoušky se skutečnými
Sonarr/Radarr kontejnery.
