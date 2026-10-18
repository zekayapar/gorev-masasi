# Görev Masası

Python 3.12, standart kütüphane, SQLite ve düz HTML/CSS/JavaScript ile çalışan Türkçe kişisel görev uygulaması. Ek paket veya indirme gerekmez.

## Çalıştırma

`app.py` ve `index.html` dosyalarını aynı klasöre yerleştirin. Bu klasörde aşağıdaki komutu çalıştırın:

```sh
python app.py --port 8080 --db /absolute/path/tasks.sqlite
```

`--db` için mutlak bir dosya yolu kullanın. Veritabanının üst klasörü yoksa oluşturulur. Tarayıcıda `http://127.0.0.1:8080` adresini açın. Sunucu yalnızca `127.0.0.1` üzerinde dinler. Farklı bir port için `8080` yerine atanmış portu yazın. Durdurmak için Ctrl+C kullanın.

## Deneme hesapları

| Kullanıcı adı | Parola |
| --- | --- |
| zeka | deneme-zeka-2026 |
| yapar | deneme-yapar-2026 |

Hesaplar yalnızca eksik olduklarında oluşturulur. Her hesap yalnızca kendi görevlerini görür ve değiştirir. Kayıt olma ekranı yoktur.

## Kullanım

Giriş yaptıktan sonra Başlık, Açıklama, Durum ve Öncelik alanlarını doldurup Kaydet düğmesine basın. Düzenle görevi forma yükler; Vazgeç düzenlemeyi iptal eder. Sil işlemi onay ister.

Ara alanı başlık ve açıklamada Unicode casefold ile büyük/küçük harf duyarsız, düz metin araması yapar. Durum ve öncelik filtreleri aramayla birlikte uygulanır. Filtreler değişince liste yenilenir; Filtrele düğmesi de kullanılabilir. Liste en yeni görevden başlar. Dört sayaç filtrelerden bağımsız olarak hesabın bütün görevlerini gösterir.

## Kalıcılık ve oturumlar

Kullanıcılar ve görevler belirtilen SQLite dosyasında saklanır. Mevcut veritabanı başlangıçta sıfırlanmaz. Aynı `--db` yoluyla yeniden başlatmak kayıtları korur. SQLite yardımcı dosyaları da veritabanının üst klasöründe tutulur; uygulama başka konumlara kalıcı veri yazmaz.

Parolalar rastgele tuz ve 240000 turlu PBKDF2-HMAC-SHA256 ile özetlenir. Oturumlar bellekte tutulur, en fazla 12 saat geçerlidir ve sunucu yeniden başlayınca sona erer. Sayfayı yenilemek geçerli oturumu korur. Çıkış yap yalnızca kullanılan oturumu geçersiz kılar. Aynı hesap birden fazla oturum açabilir.

API JSON kullanır. Sağlık kontrolü `GET /health` adresindedir. İstek gövdeleri en fazla 16384 bayt olabilir. Değişiklik yapan isteklerde Origin varsa, çalışmakta olan sunucunun tam `http://127.0.0.1:PORT` adresiyle eşleşmelidir. Origin içermeyen komut satırı istemcileri desteklenir.

Bu uygulama yerel kullanım ve değerlendirme içindir; üretime hazır güvenlik iddiası taşımaz. Deneme parolaları herkese açıktır; sunucu HTTP kullanır.
