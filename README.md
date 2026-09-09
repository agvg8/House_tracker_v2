# budujemy - planer budowy/mieszkania/remontu

## Uruchomienie
1. pip install -r requirements.txt
2. python app.py
3. otwórz http://127.0.0.1:5000

WAŻNE: usuń domybudujesz.db przed uruchomieniem - struktura tabel się zmieniła
(nowe tabele Material, Task; nowa kolumna Item.will_change; inny domyślny
zestaw sekcji/pokoi).

## Co nowego w tej turze
- Typ projektu wpływa teraz na to, co się seeduje:
  - dom: pełne 5 sekcji (stan 0, surowy otwarty, surowy zamknięty, instalacje,
    wykończenie), instalacje = elektryczna+wodno-kanalizacyjna+ogrzewanie.
  - mieszkanie/remont: tylko 2 sekcje (instalacje, wykończenie), instalacje =
    tylko elektryczna+wodno-kanalizacyjna, bo reszta stanu budynku już istnieje.
- Instalacje w mieszkaniu/remoncie mają na starcie pytanie "czy będzie
  zmieniana?" (domyślnie nie). Jeśli przełączysz na "będzie zmieniana",
  podstrona dzieli się na 2 sekcje:
  - materiały: kafelki (nazwa, sklep, wycena, opis, link), edytowalne,
    usuwalne, z checkboxem "liczy się do budżetu" (3 kwadratowe przyciski:
    klucz/kosz/sakiewka, tak samo jak wszędzie indziej).
  - usługi: dokładnie ten sam mechanizm co w projekcie domu (materiał+
    robocizna, grupy, "wykonamy to sami") - budżet pozycji to materiały
    (zawsze sumowane) + widełki z usług (min-max alternatyw).
- Wykończenie: domyślne, stałe pokoje to teraz tylko strefa dzienna, kuchnia,
  łazienka (wcześniej było 5, w tym korytarz i sypialnia) - resztę dodaje
  użytkownik przez "+ dodaj więcej". Dotyczy to każdego typu projektu.
- Każdy pokój w wykończeniu ma teraz 4 sekcje: wykonawcy (jak wcześniej -
  usługi z tagami), zadania (prosta lista do zrobienia z checkboxem),
  produkty (kafelki materiałów - ten sam mechanizm co materiały w
  instalacjach), inspiracje (bez zmian).

## Model danych (dodatki)
- Item.will_change: None = nie dotyczy (dom, pokoje), False/True = instalacje
  w mieszkaniu/remoncie.
- Material: nazwa, sklep, wycena, opis, link, include_in_budget - używany
  zarówno w zakładce "materiały" instalacji jak i w "produktach" pokoju.
- Task: prosty tekst + done, per pokój.
- item_options()/segment_range() liczą teraz: materiały (zawsze sumowane) +
  widełki z usług (min/max alternatyw z grup wariantów), a dla pokoi:
  wyceny wykonawców + materiały.

## Czego jeszcze nie ma
- reklamy w wersji darmowej + subskrypcja 10 zł/mies bez reklam
- reset hasła przez e-mail, weryfikacja e-maila
- edycja nazwy/metrażu/budżetu domu po utworzeniu
- limit rozmiaru/kompresja uploadowanych zdjęć

## Poprawka: pokoje wg typu projektu
- dom: pełny, oryginalny zestaw 5 pokoi (korytarz, kuchnia, strefa dzienna,
  sypialnia, łazienka).
- mieszkanie/remont: 3 pokoje (strefa dzienna, kuchnia, łazienka).
- Kategorie inspiracji tworzą się automatycznie dopasowane do faktycznych
  pokoi danego typu (nie osobna, sztywna lista).
- "wykonamy to sami" usunięte z pozycji typu "urząd" (dokument+opłata) -
  tam nie ma sensu, zostaje przy materiał+wykonawca i szacowana wycena.

## Wdrożenie na Render

### Opcja A: jednym kliknięciem (Blueprint)
1. Wrzuć ten folder do repozytorium na GitHubie/GitLabie.
2. Na render.com: New → Blueprint → wskaż repo. Render odczyta `render.yaml`
   i sam utworzy usługę web + darmową bazę PostgreSQL, wygeneruje SECRET_KEY
   i podłączy DATABASE_URL automatycznie.
3. Kliknij Apply - gotowe.

### Opcja B: ręcznie
1. New → Web Service → wskaż repo.
2. Build Command: `pip install -r requirements.txt`
3. Start Command: `gunicorn app:app`
4. W Environment Variables ustaw:
   - `SECRET_KEY` - dowolny losowy ciąg znaków (np. `python -c "import secrets; print(secrets.token_hex(32))"`)
   - `DATABASE_URL` - jeśli chcesz trwałą bazę, dodaj osobno darmową bazę
     PostgreSQL w Render (New → PostgreSQL) i wklej tu jej "Internal
     Connection String". Bez tego apka spadnie na SQLite w lokalnym
     systemie plików kontenera - a ten **kasuje się przy każdym redeployu**,
     więc do produkcji koniecznie użyj PostgreSQL.

### Uwaga: zdjęcia inspiracji
Render ma efemeryczny system plików (poza płatnym "Persistent Disk") -
przesłane zdjęcia w `static/uploads/` znikną przy restarcie/redeployu
kontenera, nawet z podłączoną bazą PostgreSQL. Do trwałego przechowywania
zdjęć w produkcji docelowo warto podłączyć zewnętrzny storage (np. S3,
Cloudinary) - to nie jest jeszcze zrobione w tej wersji.

### Zmienne środowiskowe (podsumowanie)
- `SECRET_KEY` - wymagane w produkcji (inaczej sesje logowania są niebezpieczne)
- `DATABASE_URL` - opcjonalne; brak = SQLite lokalnie (nietrwałe na Render)
- `PORT` - Render ustawia automatycznie, nie trzeba nic robić
- `FLASK_DEBUG` - ustaw na `0` w produkcji (już w render.yaml)

## Responsywność (ta tura)
- Menu w prawym górnym rogu: na szerokich ekranach zwykłe linki w poziomie,
  poniżej ~720px zwija się do przycisku hamburgera - otwiera wysuwany panel
  z prawej strony z przyciemnionym tłem (kliknięcie w tło albo w link zamyka).
- Przełącznik jasny/ciemny motyw jest zawsze widoczny, niezależnie od stanu menu.
- Karty z przyciskami akcji (edytuj/usuń/liczy się do budżetu) - te 3 kwadratowe
  ikonki są teraz w prawym górnym rogu KARTY, w tym samym wierszu co pierwsza
  linia tekstu (nie jako osobny rząd nad treścią). Na wąskich ekranach ikonki
  i zarezerwowany margines się zmniejszają.

## Przebudowa "robocze" (ta tura)
- Robocze to teraz 3 równorzędne biblioteki: usługi, produkty, inspiracje -
  każda w osobnym modelu (SavedService, SavedProduct, SavedInspiration),
  per konto (nie per projekt).
- Strona /robocze: 3 kafelki "dodaj X" otwierające okienko modalne, poniżej
  siatka 3 kolumn (responsywna - 2 kolumny <720px, 1 kolumna <480px) z tym,
  co już dodane - każdy kafelek edytowalny i usuwalny.
- "Dodaj z roboczych" wpięte w: dodawanie usługi w wykończeniu, dodawanie
  materiału/produktu (instalacje-materiały i pokoje-produkty), dodawanie
  wariantu materiał+wykonawca (dawne "dodaj z zapisanych"), dodawanie
  inspiracji do pokoju.
- Główna strona inspiracji ma teraz na górze sekcję "z roboczych" - pokazuje
  niewykorzystane jeszcze inspiracje z biblioteki, z selectem pokoju i
  przyciskiem "+" żeby przypisać bez otwierania konkretnego pokoju.
- Stary model SavedSolution (jedna wspólna "biblioteka materiałów") został
  zastąpiony przez SavedProduct - wymaga usunięcia bazy.

## Poprawki (ta tura)
- Metraż i liczba pokoi domu aktualizują się automatycznie na podstawie
  pokoi: metraż = suma metraży wszystkich pokoi, które go mają ustawionego;
  liczba pokoi = sypialnie + pokoje dziecięce + gabinety + wszystkie pokoje
  dodane ręcznie przez użytkownika (niezależnie od tagu). Domyślne pokoje
  bez tagu (kuchnia, łazienka, strefa dzienna, korytarz) się NIE liczą.
- "Dodaj wycenę" w pokoju: żadna opcja nie jest domyślnie zaznaczona i żaden
  formularz się nie pokazuje, dopóki nie wybierzesz "sam(a) wykonam" albo
  "zatrudniam fachowca".
- Przycisk na stronie /robocze: "wypełnij przykładowymi usługami (do testów)"
  - dodaje 10 przykładowych firm z różnymi (czasem kilkoma) tagami, żeby móc
    testować przepływ "dodaj z roboczych" bez ręcznego wymyślania danych.
    Pomija duplikaty po nazwie firmy przy ponownym kliknięciu.
- Kwoty w tys zł na dashboardzie mają teraz 2 miejsca po przecinku w stylu
  polskim (np. "565,12 tys zł" zamiast zaokrąglenia do "565").
- Przycisk "zarejestruj się" w górnym menu (dla niezalogowanych) jest różowy
  (styl .btn-primary) z grafitowym tekstem dla kontrastu.
- Dodana stopka na dole każdej strony.

## Szerokość aplikacji (ta tura)
- Cała aplikacja (nagłówek, treść, stopka) jest teraz spójnie ograniczona do
  60% szerokości ekranu (max. 960px, żeby nie rozciągało się w nieskończoność
  na bardzo szerokich monitorach) - wcześniej sam navbar/stopka były
  pełnej szerokości, a tylko treść była węższa, co wyglądało niespójnie.
- Puste marginesy po bokach (ok. 20% z każdej strony na typowym ekranie) to
  docelowo miejsce pod banery Google AdSense - nic tam jeszcze nie ma, ale
  layout już zostawia na to przestrzeń.
- Poniżej 900px szerokości ekranu (tablety/telefony) aplikacja wraca do
  pełnej szerokości - węższy layout na małym ekranie byłby bezużyteczny.

## Navbar i stopka na całą szerokość (ta tura)
- Tło paska nawigacji (.topbar) i stopki (.site-footer) znów rozciąga się na
  100% szerokości ekranu - tak jak w typowych layoutach.
- Ich ZAWARTOŚĆ (logo, linki, tekst stopki) jest wewnątrz nowego kontenera
  .topbar-inner (i istniejącego .site-footer .wrap), który ma tę samą
  szerokość 60%/max 960px co główna treść (.wrap) - więc wszystko nadal
  wizualnie się wyrównuje w jedną kolumnę, tylko tła paska górnego i stopki
  sięgają brzegów ekranu.

## Audyt bezpieczeństwa (ta tura)

### Naprawione krytyczne luki (IDOR - dostęp do cudzych danych przez ID w URL)
Dziesiątki tras (warianty, materiały, wyceny pokoi, usługi, zadania, pozycje,
sekcje, kafelki inspiracji) pobierały obiekty po samym ID bez sprawdzenia,
czy należą do zalogowanego użytkownika. Dodano komplet funkcji
`get_owned_*()`, które to weryfikują - każda taka trasa teraz zwraca 404
zamiast pozwolić na podejrzenie/edycję/usunięcie cudzych danych. Przetestowane
end-to-end na dwóch kontach (użytkownik B nie widzi ani nie może modyfikować
niczego z konta użytkownika A).

### CSRF protection
Dodano Flask-WTF (CSRFProtect) - token wstrzyknięty automatycznie do
wszystkich 48 formularzy w aplikacji. Żądanie POST bez poprawnego tokenu
jest odrzucane (400). Bez tego dowolna złośliwa strona mogłaby wysłać
żądanie w imieniu zalogowanego użytkownika (np. usuń mój dom) samym linkiem
lub ukrytym formularzem.

### Walidacja liczb i tekstu po stronie serwera
- Nowe funkcje `clamp_number()`/`clamp_int()` - wszystkie ceny/kwoty/metraże
  w całej aplikacji są teraz przycinane do sensownego zakresu (nie ujemne,
  nie absurdalnie duże) i odporne na śmieciowe dane (wcześniej np. wpisanie
  liter zamiast liczby w cenę powodowało błąd 500).
- Wszystkie pola tekstowe (nazwa, firma, sklep, link, opis) mają teraz limit
  długości dopasowany do kolumny w bazie - na SQLite brak limitu nie szkodził,
  ale na PostgreSQL (produkcja) przekroczenie limitu VARCHAR rzuca twardy błąd.

### Open redirect
Dwa miejsca (`/login?next=...` i parametr `next` przy dodawaniu/usuwaniu
inspiracji) pozwalały przekierować użytkownika po akcji na DOWOLNY zewnętrzny
adres - potencjalne wykorzystanie do phishingu. Dodano `safe_next_redirect()`,
które akceptuje tylko względne ścieżki lub adresy tej samej domeny.

### Rate limiting (ochrona przed brute-force)
Flask-Limiter: rejestracja (10/h), logowanie (15/h), zmiana hasła (10/h),
domyślnie 200 żądań/h na resztę aplikacji per adres IP. Uwaga: limiter
trzyma stan w pamięci procesu (storage_uri="memory://") - przy kilku
workerach gunicorn na Renderze limity nie będą w pełni spójne między nimi;
do prawdziwej produkcji z wieloma workerami warto podłączyć Redis.

### Zabezpieczenia sesji i uploadu
- Ciasteczko sesji: `HttpOnly`, `SameSite=Lax`, `Secure` (tylko HTTPS) gdy
  `FLASK_DEBUG` nie jest ustawione na "1".
- Limit rozmiaru żądania/uploadu: 10 MB (`MAX_CONTENT_LENGTH`).

### Czego świadomie NIE zrobiono w tej turze (do rozważenia później)
- Walidacja rzeczywistej zawartości/rozmiaru przesyłanych zdjęć (obecnie
  tylko rozszerzenie pliku jest sprawdzane).
- Weryfikacja e-maila przy rejestracji, reset hasła przez e-mail.
- Redis dla Flask-Limiter (potrzebne dopiero przy wielu workerach/instancjach).
- Automatyczne testy bezpieczeństwa jako część CI (obecnie przetestowane
  ręcznie w tej sesji, nie ma stałego zestawu testów w repo).

## Produkty na m² i wysokość ścian (ta tura)

### Wymiary pokoi w metrażu
- Trzeci kształt pokoju: "ze skosem (poddasze)" - obok prostokątnego i
  nieregularnego. Ma boki A/B (jak prostokątny) + wysokość niska i wysokość
  przy kalenicy zamiast jednej wysokości.
- Powierzchnia ścian liczy się automatycznie (`Item.wall_area`):
  - prostokątny: obwód (2×(bok A+bok B)) × wysokość
  - ze skosem: obwód × uśredniona wysokość ((niska+wysoka)/2) - to
    powszechnie stosowane przybliżenie, nie dokładna geometria stropu skośnego
  - nieregularny: opcjonalnie można podać obwód ręcznie, wtedy też liczy się
    ściany (obwód × wysokość)
- Powierzchnia podłogi (`computed_area`) działa tak samo dla prostokątnego
  i ze skosem (bok A × bok B).

### Produkt na m²
Nowa sekcja "produkt na m²" na górze wykończenia: podajesz nazwę, cenę za m²,
czy liczyć od podłogi czy od ścian, i zaznaczasz checkboxami dowolną liczbę
pokoi. System liczy dla KAŻDEGO zaznaczonego pokoju osobno (cena za m² ×
jego własna powierzchnia) i tworzy tam gotową pozycję w "produktach" -
przetestowane: ten sam produkt zastosowany do 2 różnych pokoi dał 2 różne,
poprawnie wyliczone kwoty. Pokój bez podanych wymiarów w metrażu jest
pomijany (nie da się policzyć). Wyliczenie jest migawką w momencie
dodania - zmiana wymiarów pokoju później NIE przelicza wstecznie już
dodanych pozycji (można je zawsze wyedytować ręcznie).

### Widoczne podsumowania kwot
- Góra każdej sekcji (w tym wykończenia): wyróżniony pasek "razem w tej sekcji".
- Góra każdego pokoju: łączny pasek (wykonawcy+produkty razem) + osobne
  podsumowania w sekcji "wykonawcy" i "produkty".
- Kafelek pokoju na liście w sekcji wykończenie pokazuje teraz sumę
  wykonawców I produktów razem (wcześniej tylko wykonawców).

## Sekcja "Działka" i poprawki stan 0 (ta tura)

### Nowa sekcja "Działka"
- Dodana jako pierwszy segment (tylko dla typu "dom" - mieszkanie/remont jej
  nie mają, bo tam nie kupuje się osobno gruntu).
- Domyślna pozycja "koszt działki" - nowy, 4. szablon formularza: prosty
  koszt (kwota + link + opis), bez pola wykonawcy. Kilka pozycji tego
  szablonu w jednym itemie SUMUJE się (tak jak urząd), nie tworzy widełek -
  bo dodatkowe koszty (podatek, badanie gruntu, biuro nieruchomości) to
  osobne, jednoczesne wydatki, nie alternatywy.
- Sekcja "dokumenty" na górze działki: nazwa + opis + plik (PDF lub zdjęcie),
  edytowalne/usuwalne z podwójnym potwierdzeniem.

### Stan 0
- Przyłącza (woda/prąd/gaz/szambo) pogrupowane pod nagłówkiem "media" z
  wizualną przerwą, potem architekt, urząd, geodeta, ogrodzenie, fundamenty.
- Nowa domyślna pozycja "architekt" (szablon 2) - ma też własną sekcję
  dokumentów (np. projekt architektoniczny), tak jak każda pozycja teraz.

### Dokumenty (nowy, ogólny mechanizm)
Nowy model Document - PDF lub zdjęcie, może być przypięty do konkretnej
pozycji (np. architekt) ALBO do całej sekcji bez konkretnej pozycji (np.
akt notarialny w działce). Sekcja "dokumenty" pojawia się teraz w widoku
KAŻDEJ pozycji (nie tylko architekta), nie tylko w działce - to ogólna
funkcja dostępna wszędzie.

### "Nie wliczam w koszta" - uproszczone
Po zaznaczeniu, cały formularz wyceny znika - zostaje tylko komunikat i
przycisk "cofnij". Dokumenty przy tej pozycji nadal się pokazują (nie są
związane z tym, czy coś liczy się do budżetu).

### "Zlecam fachowcowi" - materiał opcjonalny
Sekcja materiału jest teraz domyślnie ukryta w trybie "zlecam fachowcowi" -
zamiast niej jest przycisk "+ dodaj materiał" (dla przypadków, gdy kupujesz
materiał osobno, a fachowiec robi tylko robociznę). Checkbox "montaż
wliczony w cenę materiału" został usunięty całkowicie - materiał i
robocizna to teraz zawsze dwie osobne, jasne kwoty, bez dwuznaczności.

## Współpraca nad projektem (ta tura)

### Zaproszenia
- Właściciel domu zaprasza inną osobę **po adresie e-mail** ze strony
  "współpraca" (dostępnej z dashboardu). Osoba musi mieć już konto w apce.
- Zaproszenie NIE daje dostępu samo z siebie - to tylko rekord oczekujący
  (HouseInvite). Zaproszony widzi je dopiero po zalogowaniu, na liście
  swoich projektów, z przyciskami "akceptuj"/"odrzuć".
- Akceptacja tworzy pełnoprawny dostęp (HouseCollaborator) - współpracownik
  widzi i edytuje WSZYSTKO w projekcie tak samo jak właściciel (pozycje,
  wyceny, dokumenty, metraż itd.).
- Tylko właściciel może zapraszać/anulować zaproszenia/usuwać dostęp -
  współpracownik może sam siebie usunąć przyciskiem "opuść projekt".
- Przetestowane end-to-end na dwóch kontach: brak dostępu przed
  zaproszeniem (404), widoczność zaproszenia, pełny dostęp po akceptacji,
  blokada zapraszania przez nie-właściciela (403), blokada dostępu po
  opuszczeniu projektu.
- Na liście "twoje projekty" projekty, w których jesteś współpracownikiem
  (nie właścicielem), mają widoczną plakietkę "współpraca".

### Model danych
- `HouseCollaborator(house_id, user_id)` - aktywny dostęp.
- `HouseInvite(house_id, invited_email, invited_by_id)` - oczekujące
  zaproszenie, kasowane przy akceptacji (staje się HouseCollaborator) albo
  odrzuceniu.
- `get_owned_house()` - centralna funkcja autoryzacji używana przez
  praktycznie każdą trasę - rozszerzona o sprawdzanie współpracowników,
  więc dostęp propaguje się automatycznie do wszystkich pozycji, wariantów,
  dokumentów itd. bez osobnych zmian w każdej trasie.

## Poprawki responsywności (ta tura)
- Wiersz wyboru pokoju przy "produkt na m²" (checkbox + nazwa + info o
  metrażu) zawija się teraz poprawnie na wąskich ekranach zamiast
  wychodzić poza kartę.
- Wiersze współpracownika i zaproszenia (e-mail + przycisk) też się
  zawijają - długi adres e-mail nie psuje już układu na telefonie.

## Poprawka: sekcja "działka" dla istniejących projektów
Nowa sekcja "działka" wcześniej trafiała tylko do NOWO tworzonych domów -
projekty założone przed dodaniem tej funkcji jej nie miały. Dodano
`ensure_dzialka_segment()`, wywoływane przy każdym wejściu na dashboard
(i bezpośrednio na /segment/dzialka) - dogania brakującą sekcję automatycznie,
bez potrzeby usuwania bazy czy zakładania projektu od nowa. Przetestowane:
istniejący dom bez działki dostaje ją po prostu odwiedzeniu strony, bez
duplikatów przy kolejnych wizytach.
