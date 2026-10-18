Görev Masası - Kişisel Görev Yöneticisi

Bu basit web uygulaması, SQLite veritabanını kullanarak görevleri yönetmenizi sağlar. Uygulama yalnızca yerel makinede çalışır ve HTTP üzerinden erişilebilir.

Kurulum ve Çalıştırma

1. Gerekli dosyaları indirin veya kopyalayın:
   - app.py
   - index.html
   - README.md

2. Uygulamayı başlatın:

```bash
python app.py --port 8000 --db /tmp/tasks.sqlite
```

Bu komut, uygulamanın 127.0.0.1:8000 adresinde çalışmasını sağlar ve veritabanını /tmp/tasks.sqlite konumunda oluşturur.

Demo Hesapları

Uygulama iki demo hesabı ile başlatılır:

- Kullanıcı adı: zeka, Parola: deneme-zeka-2026
- Kullanıcı adı: yapar, Parola: deneme-yapar-2026

Veri Persistansı

Tüm görevler ve kullanıcı bilgileri SQLite veritabanında saklanır. Veritabanı dosyası her çalıştırmada aynı kalır, yeni başlatımlar mevcut verileri silmez.

API Dokümantasyonu

Uygulama aşağıdaki API endpoint'lerini sağlar:

- GET /health - Sağlık kontrolü
- GET / - Ana sayfa (giriş formu)
- POST /api/login - Giriş yap
- GET /api/me - Mevcut kullanıcı bilgisi
- POST /api/logout - Çıkış yap
- GET /api/tasks - Görevleri listele
- POST /api/tasks - Yeni görev oluştur
- GET /api/tasks/{id} - Görevi al
- PUT /api/tasks/{id} - Görevi güncelle
- DELETE /api/tasks/{id} - Görevi sil
- GET /api/stats - İstatistikler

UI Kullanımı

1. Giriş yap butonuna tıklayarak demo hesapları ile giriş yapın
2. Görev eklemek için formu doldurun ve 'Kaydet' butonuna tıklayın
3. Görevleri düzenlemek veya silmek için ilgili butonları kullanın
4. Filtreler kullanarak görevleri filtreleyin
5. Çıkış yap butonuna tıklayarak oturumu sonlandırın

Uyarılar

- Uygulama yalnızca yerel makinede çalışır
- Parolalar salted PBKDF2-HMAC-SHA256 ile hashlenir
- Veritabanı dosyası her çalıştırmada aynı kalır
- Uygulama yalnızca HTTP üzerinden erişilebilir
- Çoklu oturum desteklenir
- Oturumlar 24 saat sonra otomatik olarak sona erer

Destek

Sorunlar veya sorular için lütfen README dosyasını dikkatlice okuyun. Uygulama yalnızca yerel kullanım için tasarlanmıştır.