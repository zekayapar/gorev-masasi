# Görev Masası

Bireysel görev yönetimi uygulaması.

## Kurulum ve Çalıştırma

```bash
python app.py --port 8080 --db /absolute/path/tasks.sqlite
```

## Demo Hesaplar

- Kullanıcı adı: `zeka`, Parola: `deneme-zeka-2026`
- Kullanıcı adı: `yapar`, Parola: `deneme-yapar-2026`

## Özellikler

- Görev ekleme, düzenleme ve silme
- Görev durumu: Yapılacak, Sürüyor, Tamamlandı
- Görev önceliği: Düşük, Normal, Yüksek
- Görev filtreleme (arama, durum, öncelik)
- İstatistikler (toplam, yapılacak, sürüyor, tamamlandı)

## Veri Saklama

Veriler SQLite veritabanında saklanır. Parolalar PBKDF2-HMAC-SHA256 ile şifrelenir.

## Not

Bu uygulama eğitim amaçlıdır ve production ortamında kullanılmamalıdır.