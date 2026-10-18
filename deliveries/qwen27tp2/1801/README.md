# Görev Masası

Kişisel, Türkçe bir görev yöneticisi. Python 3.12 standart kütüphanesi + sqlite3 ve saf HTML/CSS/JS kullanır.

## Çalıştırma

```
python app.py --port 8000 --db /absolute/path/tasks.sqlite
```

Uygulama yalnızca `127.0.0.1` adresine bağlanır. `index.html` dosyası `app.py` ile aynı dizinde okunur.

## Demo hesaplar

- `zeka` / `deneme-zeka-2026`
- `yapar` / `deneme-yapar-2026`

Bu hesaplar yalnızca veritabanında hiç kullanıcı yoksa eklenir. Mevcut veritabanı asla sıfırlanmaz.

## Veri kalıcılığı

Veriler `--db` ile verilen SQLite dosyasında saklanır. Oturumlar bellek içindedir ve en fazla 24 saat geçerlidir; çıkış yapınca oturum geçersiz kılınır. Tarayıcı yenilendiğinde oturum çerezi sayesinde korunur.

## Güvenlik notu

Bu uygulama eğitim amaçlıdır; üretim ortamı için yeterli güvenlik önlemleri içermez. Parolalar tuzlu PBKDF2-HMAC-SHA256 ile hashlenir; düz metin saklanmaz.
