Görev Masası - Kişisel Görev Yöneticisi

Bu uygulama, Python 3.12 ve SQLite kullanarak oluşturulmuş basit bir görev yönetici uygulamasıdır. Uygulama, kullanıcıların görevleri oluşturmak, düzenlemek, silmek ve filtrelemek için kullanılır.

Kullanım

Uygulamayı başlatmak için aşağıdaki komutu kullanın:

python app.py --port 8000 --db /absolute/path/tasks.sqlite

Uygulama, 127.0.0.1:8000 adresinde çalışacaktır.

Demo Hesapları

Uygulama, aşağıdaki iki demo hesabı ile başlatılır:

- Kullanıcı adı: zeka, Parola: deneme-zeka-2026
- Kullanıcı adı: yapar, Parola: deneme-yapar-2026

Veri Saklama

Tüm görevler ve kullanıcı bilgileri SQLite veritabanında saklanır. Veritabanı yolu, --db parametresi ile belirtilen mutlak yol olarak sağlanmalıdır. Veritabanı dosyası, uygulama çalıştırıldığı dizinde oluşturulacaktır.

Güvenlik

- Parolalar salted PBKDF2-HMAC-SHA256 ile hashlenir (100000 iterasyon)
- Oturumlar 24 saat sonra otomatik olarak süresi dolacak
- Çıkış yapıldığında oturumlar geçersiz kılınır
- API istekleri, uygulamanın kendi kökeninden gelmediği durumlarda reddedilecektir

API Dokümantasyonu

Uygulama, aşağıdaki API endpointlerini sağlar:

- GET /health - Sağlık kontrolü
- GET / - Giriş sayfası
- POST /api/login - Giriş yap
- GET /api/me - Mevcut kullanıcı bilgisi
- POST /api/logout - Çıkış yap
- POST /api/tasks - Yeni görev oluştur
- GET /api/tasks/{id} - Görev detayı
- PUT /api/tasks/{id} - Görev güncelle
- DELETE /api/tasks/{id} - Görev sil
- GET /api/tasks - Görevleri listele (filtreleme destekli)
- GET /api/stats - İstatistikler

UI Özellikleri

- Türkçe arayüz
- Mobil uyumlu (390px ve daha düşük genişliklerde)
- Klavye erişilebilir
- Görev filtreleme ve arama
- Durum ve öncelik filtreleri
- Görev oluşturma, düzenleme ve silme

Uygulama, sadece geliştirme amaçlıdır ve üretim ortamında kullanım için tasarlanmamıştır.