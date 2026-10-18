# Görev Masası – Kişisel Görev Yöneticisi

## Açıklama
Bu uygulama, iki demo hesabı ("zeka" ve "yapar") ile çalışan basit bir kişisel görev yöneticisidir. Tarayıcı üzerinden giriş yapabilir, görev ekleyebilir, düzenleyebilir, silebilir ve durum/öncelik filtreleriyle listeleyebilirsiniz. Tüm veriler bir SQLite veritabanında saklanır ve oturumlar çerez (cookie) üzerinden yönetilir.

## Gereksinimler
* Python **3.12** (standart kütüphane yeterlidir, ek paket gerekmez)
* Çalıştırma ortamının **127.0.0.1** üzerindeki bir TCP portuna bağlanma izni

## Kurulum & Çalıştırma
```bash
# app.py ve index.html aynı klasörde olmalı
python3 app.py --port 8000 --db /tam/yol/tasks.sqlite
```
* `--port` : Uygulamanın dinleyeceği port numarası (örnek: 8000)
* `--db`   : SQLite veritabanının tam yolu. Dosya yoksa otomatik olarak oluşturulur; mevcutsa içeriği korunur.

Uygulama başlatıldıktan sonra tarayıcınızda aşağıdaki adrese gidin:
```
http://127.0.0.1:8000/
```

## Demo Hesapları
| Kullanıcı adı | Parola |
|---------------|--------|
| **zeka**      | deneme-zeka-2026 |
| **yapar**     | deneme-yapar-2026 |

Bu hesaplar uygulama ilk kez başlatıldığında otomatik olarak oluşturulur. Parolalar veritabanında **salt + PBKDF2‑HMAC‑SHA256** ile güvenli bir şekilde saklanır.

## API (JSON) – Özet
| Metod | Yol | Açıklama |
|------|-----|----------|
| `GET` | `/health` | `{"ok":true}` – servis sağlığı |
| `POST`| `/api/login` | `{username,password}` → `{username}` + `session` çerezi |
| `GET` | `/api/me` | Oturumlu kullanıcı adı |
| `POST`| `/api/logout` | Oturumu sonlandırır |
| `POST`| `/api/tasks` | Yeni görev oluştur (`title` zorunlu) |
| `GET` | `/api/tasks/{id}` | Tek görev |
| `PUT` | `/api/tasks/{id}` | Görevi güncelle (tam yenileme) |
| `DELETE`| `/api/tasks/{id}` | Görevi sil |
| `GET` | `/api/tasks` | Liste, `q`, `status`, `priority` filtreleri |
| `GET` | `/api/stats` | Kullanıcının görev istatistikleri |

Tüm mutasyon (POST/PUT/DELETE) isteklerinde **Origin** başlığı `http://127.0.0.1:<PORT>` ile eşleşmezse `403 Forbidden` döner.

## Veri Modeli
* **Kullanıcı** – `id`, `username`, `password_hash`, `salt`, `iterations`
* **Görev** – `id`, `owner_id`, `title`, `description`, `status` (`todo|doing|done`), `priority` (`low|normal|high`)

## Güvenlik Notları
* Parolalar veritabanında asla düz metin olarak saklanmaz.
* Oturum çerezleri **HttpOnly**, **SameSite=Strict** ve 24 saat geçerlidir.
* API hataları JSON `{"error":"..."}` formatında döner; içlerinde yığın izleri, SQL ya da dosya yolları bulunmaz.
* Tüm SQL sorguları parametreli (prepared) olarak çalıştırılır.

## UI Özellikleri (Türkçe)
* Giriş formu: **Kullanıcı adı**, **Parola**, **Giriş yap**
* Başarılı giriş sonrası: Kullanıcı adı, **Çıkış yap** butonu, görev listesi, istatistikler
* Görev formu: **Başlık**, **Açıklama**, **Durum** (Yapılacak, Sürüyor, Tamamlandı), **Öncelik** (Düşük, Normal, Yüksek), **Kaydet**, **Vazgeç**
* Her görev satırında **Düzenle**, **Sil** butonları
* Filtreler: **Ara**, **Durum filtresi**, **Öncelik filtresi**, **Filtrele**
* Mobil uyumlu, maksimum 390 px genişlik

## Veri Kalıcılığı
Veritabanı dosyası (`tasks.sqlite`) belirtilen konumda kalıcıdır. Uygulama yeniden başlatılsa bile kullanıcılar ve görevler korunur.

## Sorun Giderme
* **Port çakışması** – Farklı bir `--port` değeri deneyin.
* **Veritabanı erişim hatası** – `--db` yolunun yazılabilir olduğundan emin olun.
* **Tarayıcı çerezleri engelliyorsa** – Çerezleri kabul edecek şekilde ayarlayın.

---
Bu uygulama eğitim amaçlıdır; üretim ortamı için ek güvenlik önlemleri (HTTPS, CSRF token, daha güçlü oturum yönetimi vb.) gereklidir.
