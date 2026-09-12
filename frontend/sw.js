// Tarayıcı arka planında her 1 dakikada bir yapay zeka ajanını tetikler
chrome.alarms.create("aiMarketScanner", { periodInMinutes: 1 });

chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === "aiMarketScanner") {
        console.log("🤖 Yapay Zeka arka planda küresel haberleri ve X gündemini tarıyor...");
    }
});
