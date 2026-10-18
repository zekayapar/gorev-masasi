# Görev Masası

Bu, Python 3.12 standart kütüphanesi ve sqlite3 ile yazılmış küçük bir kişisel görev yöneticisidir. Harici paket, CDN veya ağ erişimi kullanmaz.

## Çalıştırma

Uygulama yalnızca `127.0.0.1` adresine bağlanır. Veritabanı dosyası, `--db` ile verilen mutlak yolun ebeveyn dizininde saklanır.

```bash
python app.py --port 8000 --db /absolute/path/tasks.sqlite
```

Tarayıcıda `http://127.0.0.1:8000` adresini açın.

## Deneme hesapları

İlk çalıştırmada yalnızca eksikse iki deneme hesabı oluşturulur:

- Kullanıcı adı: `zeka`, Parola: `deneme-zeka-2026`
- Kullanıcı adı: `yapar`, Parola: `deneme-yapar-2026`

Mevcut veritabanı asla sıfırlanmaz.

## Veri kalıcılığı

Görevler ve kullanıcılar `sqlite3` veritabanında saklanır. Oturumlar bellek içindedir; yeniden başlatmada oturumlar kaybolur, veritabanındaki görevler korunur.

## Güvenlik notu

Bu uygulama eğitim amaçlıdır ve üretim ortamına hazır değildir. Parolalar tuzlu PBKDF2-HMAC-SHA256 ile saklanır; ancak gerçek bir üretim sistemi için ek katmanlar gerekir.
