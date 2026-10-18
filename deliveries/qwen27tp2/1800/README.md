# Görev Masası

Kişisel, Türkçe bir görev yöneticisi. Python 3.12 standart kütüphanesi + sqlite3 ve vanilla HTML/CSS/JS kullanır.

## Çalıştırma

```
python app.py --port 8000 --db /absolute/path/tasks.sqlite
```

Uygulama yalnızca `127.0.0.1` adresine bağlanır. `index.html` dosyası `app.py` ile aynı dizinde okunur.

## Demo hesaplar

- Kullanıcı: `zeka`, Parola: `deneme-zeka-2026`
- Kullanıcı: `yapar`, Parola: `deneme-yapar-2026`

Bu hesaplar yalnızca veritabanında yoksa oluşturulur. Mevcut veritabanı asla sıfırlanmaz.

## Veri kalıcılığı

Tüm kalıcı yazımlar veritabanının üst dizinine yapılır. Görevler ve kullanıcılar sqlite dosyasında saklanır. Oturumlar bellek içindedir ve en fazla 24 saat geçerlidir.

## Güvenlik notu

Bu uygulama üretim ortamı için tasarlanmamıştır. Parolalar tuzlu PBKDF2-HMAC-SHA256 ile hashlenir; düz metin saklanmaz. Yine de gerçek bir üretim ortamında ek güvenlik katmanları (TLS, rate limiting, denetim kaydı vb.) eklenmelidir.
