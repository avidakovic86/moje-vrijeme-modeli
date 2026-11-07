# Javna meteorološka obrada

Ovaj repozitorij sadrži **samo obradu meteoroloških podataka, testove, workflow i tehničke upute**. Nema izvornog koda aplikacije, HTML-a, CSS-a, dizajna, rendererova, grafova ili ikona. Ne čita privatni repozitorij i nema Cloudflare pristup.

## Modeli i integritet

- ECMWF IFS ENS: sredina svih 50 javno distribuiranih perturbiranih članova, T850 i MSLP; kontrola nije dio korištenog javnog `enfo-ef` proizvoda.
- NOAA GEFS: službena ensemble mean polja T850 i MSLP.
- DWD ICON-EU: deterministički native `v1` podaci, bez arhivskog EPS-a ili determinističke zamjene za ENS modele.

Ostaju preflight svih koraka, provjere varijabli/jedinica/runova, SHA256 svih polja i pločica, geografski rubovi, native horizon i prethodni valjani run pri pogrešci. Novi nepotpuni run ne mijenja zadnji valjani podatkovni paket. Nema prognoze izvan stvarne pokrivenosti niti povećavanja rezolucije modela.

## Razdvajanje javnog i privatnog dijela

1. Javni workflow obrađuje stvarne GRIB podatke i proizvodi samo `forecast-bundle.tar.gz`.
2. Paket sadrži samo `maps/catalog.json`, metapodatke i binarne T850/MSLP arhive. Ne može sadržavati aplikacijski kod.
3. Paket se nakon odobrenog pokretanja pohranjuje kao GitHub Actions artifact s nazivom koji uključuje tri runa. Podaci nisu spremljeni u Git povijest.
4. Privatni workflow kasnije, uz zasebno odobrenje, preuzima podatke iz točno odabranog uspješnog javnog runa, provjerava ih, spaja s privatnom aplikacijom i može ažurirati postojeći TEST. Privatni kod nikad se ne vraća javnom runneru.

**Nema dodatnog pristupa privatnom repozitoriju iz javnog workflowa.** Za međurepozitorijsko preuzimanje artifacts privatni workflow koristi zaseban token samo za čitanje Actions podataka ovog javnog repozitorija. Taj token ne smije biti pohranjen ovdje. Javni workflow koristi samo vlastiti kratkotrajni `GITHUB_TOKEN`.

## Ručno pokretanje i kvote

Trenutačno su pripremljeni samo `workflow_dispatch` okidači. **Nema cron rasporeda, automatskog pokretanja pri pushu, privatnog pozivanja ni Cloudflare deploymenta. Workflow u pripremnom koraku nije pokrenut.**

Prije kasnijeg odobrenog pokretanja vlasnik treba:

- Provjeriti besplatne kvote i ograničiti dodatnu potrošnju računa na 0 USD.
- Popuniti `automation/public-quota.example.json` stvarnim raspoloživim zajedničkim artifact budžetom. Predložak namjerno blokira rad.
- Tek nakon provjere postaviti `PUBLIC_QUOTA_REVIEW_JSON` i `MODELS_INGEST_APPROVED=true` u **Variables**, ne unositi tajne. Pregled kvota vrijedi samo za pregledani mjesec.
- Pokrenuti workflow ručno na `main` i provjeriti sve modele i izlazni artifact. Ponovno preuzimanje GRIB-a izbjegava se pomoću metapodataka zadnjeg valjanog artifacts i HEAD provjera novih runova.

Standardni `ubuntu-latest` runner u javnom repozitoriju ne troši uključeni compute kredit. Veći runneri nisu dio ove konfiguracije. Artifact pohrana ima zasebnu kvotu: najviše dva paketa, svaki do 200 MB prije male ZIP ovojnice, uz zadržavanje jedan dan. Prije novog uploada kandidat se potpuno provjerava, stariji paketi se prorjeđuju, a zadnji valjani ostaje. Nepovezani korisnički artifacts ne brišu se. Ako raspoloživi budžet nije dovoljan, upload se blokira. Brisanje ne poništava već nastalu potrošnju.

Ako artifacts isteknu, bootstrap dohvaća samo javno dostupne prostorne podatke postojećeg TEST-a; nema privatnog koda. Cloudflare potom i dalje zadržava posljednji objavljeni valjani run. Automatizacija nije uključena ovom pripremom.

## Sigurnost

- U workflowu nema reference na korisničke `secrets.*`, Cloudflare token ni privatni checkout.
- `contents: read`; `actions: write` samo u proizvođačkom jobu radi prorjeđivanja vlastitih podatkovnih artifacts. Checkout ne zadržava vjerodajnice.
- Samo ručno pokretanje na `main` očekivanog repozitorija. Nema `pull_request_target`, `workflow_run` ili PR okidača.
- Službene Actions vezane su uz pregledane pune commit SHA-ove.
- Potrošač prihvaća samo uspješan workflow očekivane javne repo identifikacije, grane i putanje. Odbija fork/PR, pogrešan digest, nepoznate datoteke, HTML/JS/CSS, path traversal, symlinkove, duplikate i prevelike podatke. Primljene skripte nikad ne izvršava.
- Token za GitHub API ne prati preusmjeravanje prema potpisanom storage URL-u. Potpisani URL ne ispisuje se pri pogrešci.
- Provjera popisa i postavki Secrets povezanom integracijom vraćala je 403. Zato nije potvrđeno postoje li ranije dodane tajne u postavkama. Vlasnik treba to ručno provjeriti u Settings → Secrets and variables → Actions; ovaj workflow ne treba korisničke Secrets. Ništa nije dodavano ni mijenjano u tim postavkama.

## Lokalni testovi

```sh
pip install -r automation/requirements.txt -r automation/requirements-test.txt
python -m unittest discover -s tests -v
```

Male sintetičke matrice koriste se samo unutar izoliranih testova. Nisu prognoze i ne ulaze u isporuku. Testovi ne objavljuju artifacts niti pokreću Cloudflare.

## Izvori

[GitHub Actions obračun](https://docs.github.com/en/billing/concepts/product-billing/github-actions), [artifact prijenos između repozitorija](https://github.com/actions/download-artifact), [sigurna uporaba Actionsa](https://docs.github.com/en/actions/reference/security/secure-use).

Izvorni podaci: [ECMWF Open Data](https://confluence.ecmwf.int/spaces/DAC/pages/272310539/ECMWF+open+data+real-time+forecasts+from+IFS+and+AIFS), [NOAA GEFS](https://noaa-gefs-pds.s3.amazonaws.com/), [DWD native ICON-EU](https://opendata.dwd.de/weather/nwp/v1/m/icon-eu/). ECMWF i DWD: CC BY 4.0; NOAA: otvoreni podaci američke vlade. Izvori i obrada navedeni su u manifestima.
