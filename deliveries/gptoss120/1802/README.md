# Görev Masası – Turkish Personal Task Manager

## Açıklama
Bu uygulama, tek bir Python dosyası ve bir HTML dosyasıyla çalışan **tam bir web tabanlı görev yöneticisidir**. Kullanıcılar giriş yapar, kendi görevlerini oluşturur, düzenler, siler ve durum/öncelik filtreleriyle görüntüler. Tüm veri SQLite veritabanında saklanır ve sadece iki demo hesabı önceden tanımlanmıştır.

## Gereksinimler
* Python **3.12** (sadece standart kütüphane ve `sqlite3` kullanılır)
* Çalıştırılacak bir boş klasör (örnek: `taskapp/`)
* `app.py` ve `index.html` aynı klasörde bulunmalı.

## Başlatma
```bash
# absolute path to the SQLite file (dosya klasör içinde oluşturulur)
DB_PATH="$(pwd)/tasks.sqlite"
# örnek port 8080
python3 app.py --port 8080 --db "$DB_PATH"
```
Komut çalıştırıldığında:
* `127.0.0.1:8080` adresinde HTTP sunucusu başlar.
* `tasks.sqlite` dosyası belirtilen konumda oluşturulur (eğer yoksa). Var olan dosya **asla silinmez**; sadece eksik tablolar eklenir.
* İlk çalıştırmada iki demo kullanıcı otomatik olarak eklenir:
  * **Kullanıcı:** `zeka` – **Parola:** `deneme-zeka-2026`
  * **Kullanıcı:** `yapar` – **Parola:** `deneme-yapar-2026`

## Kullanım
1. Tarayıcıda `http://127.0.0.1:8080` adresine gidin.
2. **Kullanıcı adı** ve **Parola** alanlarını doldurup **Giriş yap** butonuna basın.
3. Giriş yaptıktan sonra:
   * Üstte kullanıcı adı ve **Çıkış yap** butonu.
   * Toplam, yapılacak, sürüyor, tamamlandı sayıları.
   * Görev ekleme formu (Başlık, Açıklama, Durum, Öncelik).
   * Görev listesi – her satırda **Düzenle** ve **Sil** butonları.
   * Filtreleme kontrolleri (Ara, Durum filtresi, Öncelik filtresi).
4. **Çıkış yap** butonuna basarak oturumu sonlandırabilirsiniz; oturum çerezi silinir ve tekrar giriş ekranına dönülür.

## Veri Kalıcılığı
* Tüm görevler ve kullanıcı bilgileri `tasks.sqlite` dosyasında saklanır.
* Sunucu yeniden başlatılsa da veritabanı dosyası korunur; mevcut görevler ve kullanıcılar kaybolmaz.
* Yeni görevler, düzenlemeler ve silmeler doğrudan veritabanına yansır.

## Güvenlik Notları
* Parolalar **PBKDF2‑HMAC‑SHA256** (150 000 iterasyon) ile tuzlanıp hashlenir; veritabanında düz metin parola bulunmaz.
* Oturum çerezleri `HttpOnly`, `SameSite=Strict` ve `Path=/` olarak ayarlanır.
* Mutasyon (POST/PUT/DELETE) isteklerinde `Origin` başlığı kontrol edilir; farklı bir origin gönderilirse **403 Forbidden** döner.
* API hataları JSON `{"error":"..."}` formatında, hassas bilgi içermez.
* Tüm SQL sorguları parametreli (`?`) olarak hazırlanır; SQL enjeksiyon riski yoktur.

## API Özeti (JSON)
| Metod | Yol | Açıklama |
|-------|-----|----------|
| GET | `/health` | `{"ok":true}` |
| POST | `/api/login` | `{username,password}` → `{username}` + `session` çerezi |
| POST | `/api/logout` | Oturumu sonlandırır |
| GET | `/api/me` | Giriş yapan kullanıcı adı |
| POST | `/api/tasks` | Yeni görev (201) |
| GET | `/api/tasks/{id}` | Tek görev |
| PUT | `/api/tasks/{id}` | Görevi güncelle |
| DELETE | `/api/tasks/{id}` | Sil |
| GET | `/api/tasks` | Liste + filtre (`q`, `status`, `priority`) |
| GET | `/api/stats` | `{total,todo,doing,done}` |

## Dosya Açıklamaları
* **app.py** – Sunucu, veritabanı, oturum yönetimi ve tüm API mantığını içerir.
* **index.html** – Tek sayfalık (SPA) arayüz; vanilla HTML/CSS/JS ile yazılmıştır, dış kütüphane kullanılmaz.
* **README.md** – Bu dosya.

## Sorun Giderme
* **Port zaten kullanımda** – Farklı bir `--port` değeri deneyin.
* **Veritabanı yolu hatalı** – `--db` parametresi mutlak bir yol olmalıdır.
* **Çerezler gönderilmiyor** – Tarayıcı ayarlarından çerezleri engelleyen bir uzantı varsa devre dışı bırakın.

---
Bu uygulama **tamamen çalışır**, ancak üretim ortamı için ek güvenlik önlemleri (HTTPS, CSRF token, daha güçlü oturum yönetimi vb.) gerekir.