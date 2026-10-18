# Görev Masası

Bu proje, **Türkçe** bir kişisel görev yöneticisi (to‑do list) uygulamasıdır. Hem bir **REST API** hem de basit bir **web arayüzü** içerir. Sadece Python 3.12 standart kütüphanesi ve SQLite kullanır; dış bağımlılık yoktur.

---

## Çalıştırma

```bash
python3 app.py --port 8000 --db /tam/yol/tasks.sqlite
```

* `--port`  : Sunucunun dinleyeceği yerel port (örnek: 8000). Sunucu sadece `127.0.0.1` üzerinden erişilebilir.
* `--db`    : SQLite veritabanının **tam** yolu. Dosya yoksa oluşturulur, mevcutsa üzerine yazılmaz.

Sunucu başlatıldığında konsola:
```
Server listening on http://127.0.0.1:8000
```
mesajı çıkar. Tarayıcıda `http://127.0.0.1:8000` adresine giderek arayüzü görebilirsiniz.

---

## Demo Hesaplar

Uygulama ilk çalıştırıldığında aşağıdaki iki demo kullanıcı otomatik olarak eklenir (eğer veritabanında zaten yoksa).

| Kullanıcı adı | Parola                |
|---------------|-----------------------|
| `zeka`        | `deneme-zeka-2026`    |
| `yapar`       | `deneme-yapar-2026`   |

Bu hesaplarla giriş yaparak uygulamayı hemen test edebilirsiniz.

---

## Özellikler

* **Kullanıcı kimlik doğrulama** – Oturum çerezleri (`session`) ile yönetilir, 24 saat geçerlidir.
* **Görev CRUD** – Başlık, açıklama, durum (`todo/doing/done`) ve öncelik (`low/normal/high`).
* **Filtreleme & Arama** – Başlık/ açıklama içinde Unicode case‑fold arama, durum ve öncelik filtreleri.
* **İstatistik** – Toplam, yapılacak, sürüyor, tamamlandı sayıları.
* **Güvenlik** –
  * Parolalar PBKDF2‑HMAC‑SHA256 (200 000 iterasyon) ile tuzlanarak saklanır.
  * Tüm veritabanı sorguları parametreli (`?`) hazırlanır.
  * Mutasyon (POST/PUT/DELETE) isteklerinde `Origin` başlığı kontrol edilir; farklı bir origin 403 döndürür.
  * Çerezler `HttpOnly`, `SameSite=Strict` ve `Path=/` olarak ayarlanır.
* **Kısıtlamalar** – JSON gövde boyutu 16 KB, geçersiz girişler 400, yetkisiz erişim 401, bulunamayan kaynak 404, metod izin dışı 405, çok büyük gövde 413.
* **Responsive UI** – 390 px genişliğe kadar uyumlu, tamamen yerel (CDN yok).

---

## Dosya Açıklamaları

| Dosya       | Açıklama |
|------------|----------|
| `app.py`   | HTTP sunucusu, API mantığı, veritabanı ve oturum yönetimi. |
| `index.html`| Tek sayfalık kullanıcı arayüzü (HTML, CSS, vanilla JS). |
| `README.md`| Bu belge.

---

## Veri Kalıcılığı

Tüm görevler ve kullanıcı bilgileri `--db` ile verilen SQLite dosyasında saklanır. Sunucu yeniden başlatılsa bile veriler korunur. Oturum bilgileri bellek içinde tutulur; sunucu kapanınca kaybolur.

---

## Güvenlik Notu

Bu uygulama eğitim ve demo amaçlıdır. Üretim ortamı için ek önlemler (HTTPS, CSRF token, daha güçlü oturum yönetimi vb.) gereklidir.

---

## Lisans

MIT Lisansı – istediğiniz gibi kullanabilirsiniz.
