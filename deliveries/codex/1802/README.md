# Görev Masası

Python 3.12 standart kütüphanesi, SQLite ve vanilla HTML/CSS/JavaScript ile çalışan Türkçe kişisel görev yöneticisi. Paket kurulumu veya harici servis gerekmez.

## Çalıştırma

Üç dosyayı aynı dizine kaydedin. Bu dizinden tam başlatma komutu:

```text
python app.py --port 8000 --db /absolute/path/tasks.sqlite
```

`/absolute/path/tasks.sqlite` yerine atanmış veri dizininizdeki mutlak veritabanı yolunu, `8000` yerine atanmış portu kullanın. Tarayıcıda `http://127.0.0.1:8000/` adresini açın; portu değiştirdiyseniz adresi de değiştirin. Sunucu yalnızca `127.0.0.1` üzerinde dinler. `index.html`, `app.py` dosyasının yanından okunur.

## Demo hesapları

- Kullanıcı adı: `zeka` — Parola: `deneme-zeka-2026`
- Kullanıcı adı: `yapar` — Parola: `deneme-yapar-2026`

Eksik demo hesapları başlangıçta oluşturulur. Mevcut hesaplar, parolalar ve görevler başlangıçta sıfırlanmaz. Her hesap yalnızca kendi görevlerini görebilir ve değiştirebilir.

## Kullanım

Giriş yaptıktan sonra başlık ve isteğe bağlı açıklama, durum ve öncelik ile görev ekleyin. Düzenle düğmesi görevi forma yükler; Kaydet değişiklikleri kaydeder, Vazgeç düzenlemeyi iptal eder. Sil düğmesi onay ister.

Ara, Durum filtresi ve Öncelik filtresi alanlarını doldurup Filtrele düğmesine basın. Filtreler birlikte uygulanır. Arama, başlık ve açıklamada Unicode casefold ile gerçek alt metin araması yapar; yüzde ve alt çizgi joker karakter değildir. Dört sayaç tüm hesabın görevlerini sayar ve filtrelerden etkilenmez.

## Kalıcılık ve oturumlar

Görevler ve parola özetleri belirtilen SQLite dosyasında saklanır. Veritabanı dizini gerekirse oluşturulur; veritabanı ve SQLite yardımcı dosyaları bu dizinde tutulur. Aynı `--db` yolu ile yeniden başlatmak verileri korur. Sayfa yenilemek oturumu ve görevleri korur.

Parolalar rastgele tuz ve 260000 yinelemeli PBKDF2-HMAC-SHA256 ile özetlenir. Oturumlar bellekte tutulur, en fazla 12 saat yaşar ve sunucu yeniden başladığında sona erer. Aynı hesap birden fazla oturum açabilir. Çıkış yap yalnızca mevcut oturumu iptal eder. Oturum çerezi HttpOnly, SameSite=Strict ve Path=/ özelliklerini taşır. Yerel uygulama HTTP kullandığından çerez Secure özelliği taşımaz.

API JSON döndürür. Görev işlemleri kimlik doğrulaması gerektirir ve sahiplik sunucuda belirlenir. Gövde okuyan giriş ve görev yazma uçlarında JSON sınırı 16384 bayttır. Origin başlığı bulunan değiştirici isteklerde kaynak tam olarak uygulamanın `http://127.0.0.1:PORT` adresi olmalıdır; Origin içermeyen yerel istemci isteklerine izin verilir. Sağlık kontrolü `GET /health` yolundadır.

Bu uygulama yerel kullanım ve değerlendirme içindir; üretime hazır güvenlik iddiası taşımaz. Demo parolaları herkese açıktır. Gerçek dağıtım için HTTPS, giriş denemesi sınırlaması, hesap yönetimi ve operasyonel güvenlik ayrıca ele alınmalıdır.
