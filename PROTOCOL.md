# Tam uygulama teslim protokolü — ZAI-36

Dört yerel model + Codex GPT-6.1 Sol medium; model başına üç bağımsız tekrar (seed1800/1801/1802 yerel, cloud bağımsız freshsession). Aynı BRIEF ve SYSTEM. Her teslim app.py/index.html/README.md tam JSON map; markdown fence tek bütün JSON etrafındaysa mekanik kaldırılır ve kaydedilir. Model koduna insan düzeltmesi yok. Statik dosya teslimi ve dışarıdan test/geri bildirim: açık uçlu araç kullanan ajan benchmarkı değildir. Codex'in araç çağrıları yasak; tespit edilirse koşu protokol ihlali. Başlangıç projesi boş; sabit üç dosyalı Python/SQLite/vanillaJS kapsamı.

Bir ilk teslim + en fazla iki düzeltme. Düzeltmeler test isimleri ve gözlenen hata mesajlarını alır, kabul test kodunu almaz; önceki tam teslim ve özgün BRIEF aynı bağlamda. Bütün kabul kontrolleri geçince erken durur. İlk/son sonuç, ek çağrılar ve bütçeler ayrı. Tarayıcı değerlendirmesi son teslimler için sonraki ayrı aşama; browser bulgularıyla ana ölçümde onarım yapılmaz. Başlangıç/bitiş Unix zamanı, HTTP/CLI süreleri, token usage, finish_reason, ham metin ve reasoning ayrı saklanır. Local max16384 çıktı (düşünme dahil), 32K context; temperature.2/top_p1/top_k-1/penalties0/repetition1. Qwen thinkingfalse. GPTOSS sağlayıcı doğal reasoning. Codexmedium sağlayıcı CLI sınırı, local ile aynı16384 taahhüdü yok. Tam önceki teslimli repair32K'ya sığmazsa max_tokens, gerçek tokenizer sayımı sonrası kalan bağlama indirilir; metin sessizce kırpılmaz ve bütçe kayda girer.

Qwen ayrı32K TP2/MTP3, diğerleri önceki doğrulanmış profillerle sırayla CUDA'da. Toplam96GB üç ayrı32GBkart; farklı quant/parallel/graph kombinasyonları tek değişken model ölçümü değildir. GPU telemetry1sn; enerji opsiyonel, priz ölçümü yok. Ağırlıklar/kurulum/ısınma teslim süresinden ayrı. 80°C güvenlik durdurması ve restorefinally + systemdExecStopPost. Gateway/kimlik/source dosyaları değiştirilmez.

HTTP kabulü + parola/veri kayıt denetimi: ölçülebilir işlev/izolasyon puanı. Referans ve negatif kontrol geçmeden model yanıtı alınmaz. Native guard outbound/process/dosyayı sınırlar ama OScontainer/sandbox değildir. GUI testleri CUAChrome ile; yerel browserdata sentetik. Son teslimlerin çalıştırılabilir olanları aynı10adımlıUIlistesiyle değerlendirilir; başlangıçta format/startup geçmeyene UI not_applicable. Model başına en iyi örnek eşitpuan en düşükseed ile yalnız görsel sergi; başarı oranı15teslimin tamamından. AnaAPI testi tam geçiş != tarayıcı tam geçiş != production-ready.

Bittiğinde modelleri gerçekQwen/coder/embedding istekleriyle geri doğrula. Bulgular, örnekekranlar, grafikler ve siteyazısı taslağı Proyz'e; kullanıcı istemedikçe publicsite/sosyal yayın yok.

## Hazırlık turundan sonra32K protokolü
İlk16K/8192output hazırlık turu, bütünCodex/yarımQwen dahilpilot-16k içinde anaölçümdışı. Ana tur baştan32K/16384output; DevstralFP8E4M3KV32Kiçin, diğerprofilleröncekiKV; Qwen ayrı32Kbenchmarkserver,prodGPUprofildeğişmez. Çıktıbütçesi/bağlamfarkı açık, Codexproviderlimit aynıüstlimit değildir.

## Tarayıcı ölçüm sınırı
Hazırlıktaki ilk nativeconfirm açılışında Chrome kontrol aracının focuskomutu kilitlendi; yeni sekmeyle bağlantı kurtarıldı. Ana turda bu altyapı sorununu ürün hatası saymamak için silmeiptal/onay her modelde UIölçülmemiş(null); HTTPdelete ayrı40kontroliçinde. Kalan9akış herçalıştırılabilir finalde aynıCUAlistesiyle uygulanır. 10/10tarayıcıtamamlandı iddiası yok. GözleneninnerWidth390olmalı; domainzoometkisi varsa gerçekboyutkaydıylaayrılır. Bu karar ilkana32Kyanıtından önce.

Qwen32K başlatıcısı çalışanüretimdeki seq10/graph128/MTP3 kombinasyonunu korur; benchmark32Kbağlam ve cacheoff farklıdır. İstemcitekistekgönderir; diğeradaylarseq1/graph1. Bu parametrefarklarımodel/ayartekdeğişkenlikiddiasına dönüştürülmez.
