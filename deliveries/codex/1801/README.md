# Görev Masası

Python 3.12, SQLite ve vanilla HTML/CSS/JavaScript ile çalışan Türkçe kişisel görev yöneticisi. Ek paket veya internet bağlantısı gerekmez.

## Başlatma

`app.py` ve `index.html` dosyalarını aynı dizine koyun. Bu dizinde şu komutu çalıştırın:

```text
python app.py --port 8000 --db /absolute/path/tasks.sqlite
```

`/absolute/path/tasks.sqlite` yerine ayrılmış veri dizininizdeki gerçek mutlak veritabanı yolunu, `8000` yerine ayrılmış portu kullanın. Tarayıcıda `http://127.0.0.1:8000` adresini açın. Sunucu yalnızca `127.0.0.1` üzerinde dinler. Durdurmak için Ctrl+C kullanın.

## Deneme hesapları

| Kullanıcı adı | Parola |
| --- | --- |
| zeka | deneme-zeka-2026 |
| yapar | deneme-yapar-2026 |

Her hesap yalnızca kendi görevlerini görebilir, değiştirebilir ve silebilir. Kullanıcı kaydı yoktur. Hesaplar yalnızca eksiklerse oluşturulur; mevcut hesapların parolaları başlangıçta değiştirilmez.

## Kullanım

Giriş yaptıktan sonra Başlık, Açıklama, Durum ve Öncelik alanlarıyla görev ekleyin. Düzenle görevi forma yükler; Kaydet değişiklikleri uygular, Vazgeç düzenlemeyi iptal eder. Sil işleminden önce onay istenir.

Ara alanı başlık ve açıklamada büyük/küçük harf duyarsız düz metin araması yapar. Durum filtresi ve Öncelik filtresi ile birlikte Filtrele düğmesine basın. Üstteki dört sayaç filtrelerden bağımsız olarak hesabın bütün görevlerini gösterir. Görevler en yeni eklenen önce olacak şekilde sıralanır.

## Verilerin saklanması

Hesaplar ve görevler `--db` ile belirtilen SQLite dosyasında kalıcı olarak saklanır. SQLite günlük dosyaları da veritabanının yanında oluşturulur. Aynı veritabanı yoluyla yeniden başlatma mevcut verileri korur; başlangıçta veriler sıfırlanmaz. Yedek almak için sunucuyu durdurup veritabanı dosyasını kopyalayın.

Parolalar her hesaba özel rastgele tuz ve 200.000 yinelemeli PBKDF2-HMAC-SHA256 ile özetlenir. Oturumlar bellektedir; 12 saat içinde sona erer. Sayfayı yenilemek oturumu korur, çıkış yapmak ilgili oturumu geçersiz kılar; sunucuyu yeniden başlatmak bütün oturumları sonlandırır. Aynı hesap birden fazla oturum açabilir.

Bu uygulama yerel kullanım ve deneme içindir; üretime hazır bir güvenlik çözümü değildir. Deneme parolaları herkese açıktır. HTTP kullanır ve yerel arayüz dışına açılacak şekilde tasarlanmamıştır.

## HTTP arayüzü

- `GET /health`: sağlık kontrolü.
- `POST /api/login`: JSON `username` ve `password` ile oturum açar; `session` çerezini ayarlar.
- `GET /api/me`: oturumdaki kullanıcı adı.
- `POST /api/logout`: mevcut oturumu kapatır.
- `GET /api/tasks`: hesaba ait görevler; isteğe bağlı `q`, `status`, `priority` filtreleri.
- `POST /api/tasks`: görev oluşturur.
- `GET /api/tasks/{id}`, `PUT /api/tasks/{id}`, `DELETE /api/tasks/{id}`: görev okuma, değiştirme ve silme.
- `GET /api/stats`: hesabın toplam, yapılacak, süren ve tamamlanan görev sayıları.

Görev yazma gövdesi `title` ve isteğe bağlı `description`, `status`, `priority` alanlarını kabul eder. PUT tüm kullanıcı alanlarını değiştirir; gönderilmeyen isteğe bağlı alanlar varsayılanlarına döner. Durum değerleri `todo`, `doing`, `done`; öncelik değerleri `low`, `normal`, `high` şeklindedir. JSON gövdesi sınırı 16.384 bayttır. API hataları JSON `error` alanıyla bildirilir.

Değişiklik yapan isteklerde Origin varsa sunucunun gerçek `http://127.0.0.1:PORT` adresiyle birebir eşleşmelidir. Origin içermeyen yerel istemci istekleri kabul edilir.
