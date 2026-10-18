Görev Masası - Kişisel Görev Yöneticisi

Bu uygulama, Python 3.12 standart kütüphanesi ve SQLite kullanarak oluşturulmuş basit bir görev yönetici uygulamasıdır. Uygulama yalnızca yerel makinede çalışır ve HTTP API ile etkileşim sağlar.

Kurulum ve Çalıştırma

1. Gerekli dosyaları indirin veya kopyalayın:
   - app.py
   - index.html
   - README.md

2. Veritabanı dosyasının saklanacağı bir dizin seçin ve mutlak yolunu not edin.

3. Uygulamayı aşağıdaki komutla başlatın:

```bash
python app.py --port 8000 --db /absolute/path/to/tasks.sqlite
```

Bu komut, uygulamanın 127.0.0.1:8000 adresinde çalışmasını sağlar. Veritabanı dosyası /absolute/path/to/tasks.sqlite olarak oluşturulur veya mevcut veritabanı kullanılır.

Demo Hesapları

Uygulama başlatıldığında, aşağıdaki iki demo hesabı otomatik olarak oluşturulur (veritabanı mevcut değilse):

- Kullanıcı adı: zeka, Şifre: deneme-zeka-2026
- Kullanıcı adı: yapar, Şifre: deneme-yapar-2026

Veri Persistansı

Uygulama tüm verileri SQLite veritabanında saklar. Veritabanı dosyası herhangi bir yerde saklanabilir ve uygulama her başlatıldığında aynı veritabanını kullanır. Veritabanı dosyasını taşırsanız, verilerinizi başka bir makinaya taşımış olursunuz.

API Kullanımı

Uygulama aşağıdaki API endpointlerini sağlar:

- GET /health - Sağlık kontrolü
- POST /api/login - Giriş yap
- POST /api/logout - Çıkış yap
- GET /api/me - Mevcut kullanıcı bilgisini al
- GET /api/tasks - Görevleri listele
- POST /api/tasks - Yeni görev oluştur
- GET /api/tasks/{id} - Görev detayları
- PUT /api/tasks/{id} - Görevi güncelle
- DELETE /api/tasks/{id} - Görevi sil
- GET /api/stats - İstatistikler

UI Kullanımı

Uygulama basit bir web arayüzü sunar:

1. Giriş sayfasında kullanıcı adı ve şifre girin
2. Giriş yaptıktan sonra görevlerinizi görüntüleyebilir, yeni görev ekleyebilir, mevcut görevleri düzenleyebilir veya silebilirsiniz
3. Filtreler kullanarak görevlerinizi filtreleyebilirsiniz
4. Çıkış yap butonuna tıklayarak oturumu sonlandırabilirsiniz

Güvenlik Notları

- Tüm şifreler salted PBKDF2-HMAC-SHA256 ile hashlenir
- Oturumlar 24 saat sonra otomatik olarak süresi dolur
- Çoklu oturum desteklenir
- API endpointleri yalnızca uygulamanın kendi kökeninden gelen istekleri kabul eder
- Veritabanı dosyası yalnızca uygulama tarafından okunur/güncellenir

Destek

Bu uygulama yalnızca yerel geliştirme ve test amaçlıdır. Üretim kullanımı için uygun değildir.

Lisans

Bu projede herhangi bir lisans belirtilmemiştir. Lütfen kullanımınızı uygun bulunun.