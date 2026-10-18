# Görev Masası

Bir kişisel görev yöneticisi uygulaması.

## Kurulum ve Çalıştırma

```bash
python app.py --port 8080 --db /home/user/tasks.sqlite
```

## Demo Hesaplar

- **Kullanıcı adı:** zeka / **Parola:** deneme-zeka-2026
- **Kullanıcı adı:** yapar / **Parola:** deneme-yapar-2026

## Veri Kalıcılığı

Veriler SQLite veritabanında saklanır. Varsayılan veritabanı yolu `/home/user/tasks.sqlite` olarak ayarlanmıştır. Veritabanı dosyası uygulama başlatıldığında otomatik olarak oluşturulur ve şema ilk çalıştırma sırasında doldurulur.

## Notlar

- Uygulama sadece 127.0.0.1 üzerinde çalışır.
- Şifreler PBKDF2-HMAC-SHA256 ile şifrelenerek saklanır.
- Oturum bilgileri sunucu belleğinde tutulur ve 24 saat sonra sona erer.
- Bu uygulama eğitim amaçlıdır ve production ortamlar için yeterli güvenlik önlemlerine sahip değildir.